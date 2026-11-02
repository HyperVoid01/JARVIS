"""The brain: a local LLM (via Ollama) that decides what to do with a command.

Flow for one command:
    user text -> LLM -> wants a tool? -> run it -> send result back -> LLM -> ...
                    \\-> plain answer -> return it

Talks to Ollama's HTTP API directly (http://localhost:11434/api/chat), so
there's no extra SDK to install.

Use an *instruct* (non-reasoning) model. Reasoning models like
qwen3:4b-thinking spend seconds "thinking" before every reply, which is far
too slow for voice. If one is used anyway, its reasoning is stripped out.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time

import requests

from . import tools

SYSTEM_PROMPT = """You are JARVIS, a voice assistant running on {user}'s Windows PC.

Rules:
- Your replies are read aloud, so answer in one or two short sentences. No markdown, lists, emojis or code.
- When the user asks you to do something on the PC, call the matching tool once. Never claim you did something unless a tool result confirms it.
- After a tool succeeds, just confirm it briefly. Do not call the same tool again.
- If a tool returns an error, repeat its reason briefly. Don't guess at other causes.
- If the user repeats a request or asks again after an error, call the tool again. Never answer from an earlier result.
- If a request is unclear or no tool fits, say so in one sentence.
- Be helpful with a dry, understated wit, like the JARVIS from Iron Man. Address the user as "sir" occasionally, not every time.

Current date and time: {now}."""


class OllamaError(RuntimeError):
    pass


class Brain:
    def __init__(
        self,
        model: str = "qwen3:4b-instruct-2507-q4_K_M",
        url: str = "http://localhost:11434",
        num_ctx: int = 4096,
        keep_alive: str = "30m",
        history_turns: int = 6,
        history_timeout_s: float = 300,
        max_tool_rounds: int = 4,
        user_name: str = "the user",
        debug: bool = False,
    ):
        self.model = model
        self.url = url.rstrip("/")
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.history_turns = history_turns
        self.history_timeout_s = history_timeout_s
        self.max_tool_rounds = max_tool_rounds
        self.user_name = user_name
        self.debug = debug

        self._history: list[dict] = []
        self._last_turn = 0.0
        self._tool_schemas = tools.schemas()

    # --- Setup ---------------------------------------------------------

    def check(self) -> None:
        """Fail early with a clear message if Ollama isn't running or the model is missing."""
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=5)
            r.raise_for_status()
        except requests.RequestException as e:
            raise OllamaError(
                f"Can't reach Ollama at {self.url}. Is it running? Start the Ollama app "
                f"or run 'ollama serve'. ({e})"
            ) from e

        names = {m["name"] for m in r.json().get("models", [])}
        wanted = self.model if ":" in self.model else self.model + ":latest"
        if wanted not in names:
            raise OllamaError(
                f"Model '{self.model}' isn't downloaded. Run: ollama pull {self.model}\n"
                f"        Installed models: {', '.join(sorted(names)) or 'none'}"
            )

    def warm_up(self) -> None:
        """Load the model into VRAM now so the first command isn't slow."""
        requests.post(
            f"{self.url}/api/generate",
            json={"model": self.model, "keep_alive": self.keep_alive},
            timeout=120,
        ).raise_for_status()

    # --- Conversation --------------------------------------------------

    def ask(self, text: str) -> str:
        """Handle one user command and return JARVIS's reply."""
        # Forget the conversation after a quiet spell, so old context doesn't confuse new commands.
        if time.time() - self._last_turn > self.history_timeout_s:
            self._history.clear()
        self._last_turn = time.time()

        self._history.append({"role": "user", "content": text})
        turn_start = len(self._history) - 1

        # Tool calls already run during this command, so the model can't open Discord twice.
        done: dict[str, str] = {}

        reply = ""
        for _ in range(self.max_tool_rounds + 1):
            message = self._chat()
            content = _clean(message.get("content", ""))
            calls = message.get("tool_calls") or []

            # Store only the final answer, never leaked reasoning, so it can't snowball.
            self._history.append({
                "role": "assistant",
                "content": content,
                **({"tool_calls": calls} if calls else {}),
            })

            if not calls:
                reply = content
                break

            new_calls = 0
            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", {})
                key = f"{name}:{json.dumps(args, sort_keys=True) if isinstance(args, dict) else args}"

                if key in done:
                    result = f"Already done: {done[key]} Do not repeat it; just reply to the user."
                    if self.debug:
                        print(f"[tool] skipped duplicate {name}({_fmt_args(args)})")
                else:
                    result = tools.call(name, args)
                    done[key] = result
                    new_calls += 1
                    print(f"[tool] {name}({_fmt_args(args)}) -> {result}")
                self._history.append({"role": "tool", "content": result, "tool_name": name})

            if new_calls == 0:
                # Model is only repeating itself. Stop and report what already happened.
                break
        else:
            reply = "Sorry, I got stuck in a loop on that one."

        if not reply:
            # No spoken answer: fall back to the last real tool result.
            reply = next(iter(reversed(list(done.values()))), "") or "Done."
            self._history.append({"role": "assistant", "content": reply})

        self._trim_history()
        return reply

    def reset(self) -> None:
        self._history.clear()

    # --- Internals -----------------------------------------------------

    def _chat(self) -> dict:
        system = SYSTEM_PROMPT.format(
            user=self.user_name,
            now=dt.datetime.now().strftime("%A %d %B %Y, %H:%M"),
        )
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *self._history],
            "tools": self._tool_schemas,
            "stream": False,
            "think": False,  # disables reasoning on hybrid models; instruct models ignore it
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.num_ctx, "temperature": 0.3},
        }
        start = time.perf_counter()
        try:
            r = requests.post(f"{self.url}/api/chat", json=payload, timeout=120)
        except requests.RequestException as e:
            raise OllamaError(f"Ollama request failed: {e}") from e

        if r.status_code == 400 and "think" in r.text.lower():
            # Some models reject the think flag entirely; retry without it.
            payload.pop("think")
            r = requests.post(f"{self.url}/api/chat", json=payload, timeout=120)
        if r.status_code != 200:
            raise OllamaError(f"Ollama returned {r.status_code}: {r.text[:300]}")

        message = r.json().get("message", {})
        if self.debug:
            print(f"[llm] {time.perf_counter() - start:.2f}s  {json.dumps(message)[:400]}")
        return message

    def _trim_history(self) -> None:
        """Keep only the last N user turns (with their tool calls and replies)."""
        user_idx = [i for i, m in enumerate(self._history) if m["role"] == "user"]
        if len(user_idx) > self.history_turns:
            self._history = self._history[user_idx[-self.history_turns]:]


def _clean(text: str) -> str:
    """Strip anything that shouldn't be spoken: reasoning, markdown, emojis."""
    # Thinking models open <think> inside the chat template, so the output often
    # contains only the closing tag. Everything before the last </think> is reasoning.
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    text = re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.DOTALL)
    text = re.sub(r"[*_`#]+", "", text)
    text = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", text)
    return " ".join(text.split())


def _fmt_args(args) -> str:
    if isinstance(args, dict):
        return ", ".join(f"{k}={v!r}" for k, v in args.items())
    return str(args)
