"""The brain: a local LLM (via Ollama) that decides what to do with a command.

Flow for one command:
    user text -> LLM -> wants a tool? -> run it -> send result back -> LLM -> ...
                    \-> plain answer -> return it

Talks to Ollama's HTTP API directly (http://localhost:11434/api/chat), so
there's no extra SDK to install.
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
- When the user asks you to do something on the PC, call the matching tool. Never claim you did something unless a tool result confirms it.
- If a tool returns an error, briefly say what went wrong.
- If a request is unclear or no tool fits, say so in one sentence.
- Be helpful with a dry, understated wit, like the JARVIS from Iron Man. Address the user as "sir" occasionally, not every time.

Current date and time: {now}."""


class OllamaError(RuntimeError):
    pass


class Brain:
    def __init__(
        self,
        model: str = "qwen3:4b",
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
                f"Model '{self.model}' isn't downloaded. Run: ollama pull {self.model}"
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

        reply = ""
        for _ in range(self.max_tool_rounds + 1):
            message = self._chat()
            calls = message.get("tool_calls") or []
            self._history.append({
                "role": "assistant",
                "content": message.get("content", ""),
                **({"tool_calls": calls} if calls else {}),
            })

            if not calls:
                reply = _clean(message.get("content", ""))
                break

            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", {})
                result = tools.call(name, args)
                print(f"[tool] {name}({_fmt_args(args)}) -> {result}")
                self._history.append({"role": "tool", "content": result, "tool_name": name})
        else:
            reply = "Sorry, I got stuck in a loop on that one."

        if not reply:
            # Model went straight from tool result to silence: fall back to the last tool result.
            last_tool = next((m for m in reversed(self._history[turn_start:]) if m["role"] == "tool"), None)
            reply = last_tool["content"] if last_tool else "Done."

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
            "think": False,  # Qwen3's reasoning mode: far too slow for voice commands
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.num_ctx, "temperature": 0.3},
        }
        start = time.perf_counter()
        try:
            r = requests.post(f"{self.url}/api/chat", json=payload, timeout=120)
        except requests.RequestException as e:
            raise OllamaError(f"Ollama request failed: {e}") from e
        if r.status_code != 200:
            raise OllamaError(f"Ollama returned {r.status_code}: {r.text[:300]}")

        message = r.json().get("message", {})
        if self.debug:
            print(f"[llm] {time.perf_counter() - start:.2f}s  {json.dumps(message)[:300]}")
        return message

    def _trim_history(self) -> None:
        """Keep only the last N user turns (with their tool calls and replies)."""
        user_idx = [i for i, m in enumerate(self._history) if m["role"] == "user"]
        if len(user_idx) > self.history_turns:
            self._history = self._history[user_idx[-self.history_turns]:]


def _clean(text: str) -> str:
    """Strip anything that shouldn't be spoken: leftover <think> blocks, markdown, emojis."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"[*_`#]+", "", text)
    text = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", text)
    return " ".join(text.split())


def _fmt_args(args) -> str:
    if isinstance(args, dict):
        return ", ".join(f"{k}={v!r}" for k, v in args.items())
    return str(args)
