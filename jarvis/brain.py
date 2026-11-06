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
- Your replies are read aloud, so keep them to one or two short sentences (under 35 words), even for big topics. Give the single most useful point; the user will ask if they want more. No markdown, lists, emojis or code.
- Answer general-knowledge questions yourself, without tools. Only use web_search when the user explicitly asks you to search, look something up online, or show results.
- If you're not sure a fact is right, say so briefly instead of guessing. Don't add filler like "this information is from general sources".
- When the user asks you to do something on the PC, call the matching tool once. Never claim you did something unless a tool result confirms it.
- After a tool succeeds, just confirm it briefly. Do not call the same tool again.
- If a tool returns an error, repeat its reason briefly. Don't guess at other causes.
- If the user repeats a request or asks again after an error, call the tool again. Never answer from an earlier result.
- Anything that changes over time (time left on timers, the current time, what's playing, the volume) must come from a fresh tool call every time it's asked, even if you answered a moment ago. Never work it out from earlier messages.
- Your input comes from speech-to-text and names are often misheard ("Eraser" or "Wacer" for "Racer"). If the user asks to open something, ALWAYS call a tool with the name as heard; the tools match names by sound. Never say something doesn't exist without a tool result saying so.
- Numbers get misheard too: "thin" or "tin" means ten, "for" four, "to" or "too" two, "won" one, "ate" eight. Use the number that fits instead of asking.
- "City timer" or "sit a timer" means "set a timer" (no label). "Table", "timber" or "time" next to cancel, change or "how long" usually means "timer". If a cancel or change names a timer that doesn't exist, call the tool anyway; it will list the real timers.
- Short follow-ups refer to your last action. After setting a timer, "make it ten" or "actually two minutes" means change_timer, not a new timer. "Restart the timer" and "name it pizza" (often misheard, e.g. "memit pizza") are change_timer too. Unnamed timers are called by their length, e.g. "the 2 minute timer".
- If the user asks to open something that sounds like one of their Unity projects (listed below), use open_unity_project. Otherwise use open_app.
- Users often add a description after a name ("Racer, as in the Formula 1 game"). Use only the name itself, or pick the project that fits the description.
- If a tool result says it needs the user's OK, ask that question and stop. Only call it again with confirmed=true after the user says yes; if they say no, drop it.
- Right after a shutdown or restart was started, a bare "cancel" or "stop" means power cancel_shutdown.
- "Close" an app means window close; "quit", "kill" or "exit" it, or closing something that hides in the tray, means window quit.
- When the user asks you to remember, note or not forget something, call remember. If they say a remembered fact changed, call remember with replaces. If they ask you to forget something, call forget. Never say you'll remember or have forgotten something without the tool.
- Remembered facts are written about the user in the third person. When you talk to them, say "your" and "you", never their name: "Your deadline is Monday", not "Sooriyan's deadline is Monday".
- Questions about what you remember ("what's my deadline?", "what do you know about me?") are answered from the remembered list below. If it isn't there, say you don't have that saved.
- If a request is unclear or no tool fits, say so in one sentence.
- Be helpful with a dry, understated wit, like the JARVIS from Iron Man. Address the user as "sir" occasionally, not every time.

Current date and time: {now}.
Calendar (use it for any date; never work weekdays out yourself): {calendar}.
"This Friday" means the next Friday on or after today; "next Friday" means the one after that.{extra}"""


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
        max_sentences: int = 3,
        user_name: str = "the user",
        extra_context=None,
        debug: bool = False,
    ):
        self.model = model
        self.url = url.rstrip("/")
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.history_turns = history_turns
        self.history_timeout_s = history_timeout_s
        self.max_tool_rounds = max_tool_rounds
        self.max_sentences = max_sentences
        self.user_name = user_name
        # Optional callable returning extra facts for the system prompt (e.g. project names).
        self.extra_context = extra_context
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
        tools.begin_turn()

        # Tool calls already run during this command, so the model can't open Discord twice.
        done: dict[str, str] = {}

        reply = ""
        nudged = False
        for _ in range(self.max_tool_rounds + 1):
            message = self._chat()
            content = _clean(message.get("content", ""))
            calls = message.get("tool_calls") or []

            # Small models sometimes say "I've set a timer" without calling anything.
            # Catch that once: tell it nothing happened and make it try again.
            if not calls and not done and not nudged and claims_action(content):
                nudged = True
                print(f"[brain] claimed an action without a tool call: {content!r}; retrying")
                self._history.append({"role": "assistant", "content": content})
                self._history.append({"role": "user", "content": _NUDGE})
                message = self._chat()
                del self._history[-2:]  # keep the slip and the nudge out of the conversation
                content = _clean(message.get("content", ""))
                calls = message.get("tool_calls") or []
                if not calls and claims_action(content):
                    content = "Sorry, I didn't catch what you wanted me to do. Could you say it again?"

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

        # A tool asked for the user's OK: say exactly that question, whatever the model wrote
        # (a small model might say "Shutting down now" when nothing has happened).
        for result in done.values():
            m = re.search(r'Ask them exactly: "(.+?)"', result)
            if m:
                reply = m.group(1)
        reply = shorten(reply, self.max_sentences)
        if self._history and self._history[-1]["role"] == "assistant":
            self._history[-1]["content"] = reply  # remember what was actually said
        self._trim_history()
        return reply

    def remember(self, text: str, reply: str, calls: list[tuple[str, dict, str]] = ()) -> None:
        """Record a command handled without the LLM (the fast path), so follow-ups still make sense.

        It's stored exactly like a real tool-using turn. If memory showed commands
        answered without tools, the model would copy that and make answers up.
        """
        if time.time() - self._last_turn > self.history_timeout_s:
            self._history.clear()
        self._last_turn = time.time()
        self._history.append({"role": "user", "content": text})
        if calls:
            self._history.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": name, "arguments": args}} for name, args, _ in calls],
            })
            for name, _, result in calls:
                self._history.append({"role": "tool", "content": result, "tool_name": name})
        self._history.append({"role": "assistant", "content": reply})
        self._trim_history()

    def reset(self) -> None:
        self._history.clear()

    # --- Internals -----------------------------------------------------

    def _chat(self) -> dict:
        extra = ""
        if self.extra_context:
            try:
                extra = "\n" + self.extra_context()
            except Exception:
                pass
        system = SYSTEM_PROMPT.format(
            user=self.user_name,
            now=dt.datetime.now().strftime("%A %d %B %Y, %H:%M"),
            calendar=_calendar(),
            extra=extra,
        )
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *self._history],
            "tools": self._tool_schemas,
            "stream": False,
            "think": False,  # disables reasoning on hybrid models; instruct models ignore it
            "keep_alive": self.keep_alive,
            # num_predict caps reply length (and keeps replies fast); tool calls fit easily.
            "options": {"num_ctx": self.num_ctx, "temperature": 0.3, "num_predict": 200},
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


_NUDGE = ("(System check, not from the user: you did not call any tool, so nothing was done. "
          "If the user asked for an action, call the right tool now. If you're not sure what "
          "they meant, ask them briefly. Never say you did something without a tool result.)")

# Replies that only make sense if a tool ran: "I've set a timer", "Timer has been cancelled",
# "PC is shutting down now", "Chrome and Spotify are open", "Volume decreased to 20%"...
_ACTION_VERBS = (r"set|started|opened|launched|cancel+ed|changed|renamed|restarted|paused|"
                 r"resumed|played|skipped|muted|unmuted|turned|created|closed|searched|stopped|"
                 r"locked|minimi[sz]ed|maximi[sz]ed|switched|quit|killed|decreased|increased|"
                 r"lowered|raised|adjusted|reduced|remember|noted|saved|stored|forgotten|forgot|deleted|removed|erased")
_STATES = (r"playing|paused|open|closed|locked|minimi[sz]ed|maximi[sz]ed|muted|unmuted|"
           r"running|shutting down|restarting|signing out|going to sleep|set to|at \d+")
_CLAIM = re.compile(
    rf"\b(i['’]?ve|i have|i['’]?ll|i will|i just|i went ahead and)\s+(\w+\s+)?({_ACTION_VERBS}|set up)\b"
    rf"|\b(has|have|was|were|is)\s+(now\s+)?been\s+({_ACTION_VERBS})\b"
    rf"|\b(is|are)\s+(now\s+)?({_STATES})\b"
    rf"|\b({_ACTION_VERBS})\s+(by|to)\s+\d+"
    rf"|\b(shutting down|restarting|locking|signing out)\b"
    rf"|\b(current|volume)\s+(level\s+)?(is\s+)?(at\s+)?\d+\s*(%|percent)"
    rf"|\b(keep that in mind|make a note|made a note|noted that|got it noted|in my memory)\b"
    rf"|^(timer set|all set|done|opened|cancel+ed|paused|playing|muted|started|closed|switched|noted|saved|forgotten)\b",
    re.IGNORECASE,
)


def claims_action(text: str) -> bool:
    """True if a reply says something was done (which needs a tool call to be true)."""
    return bool(_CLAIM.search(text.strip()))


def _calendar(days: int = 21) -> str:
    """'Fri 6 Nov 2026 (today), Sat 7 Nov 2026, ...': small models get weekday maths wrong."""
    today = dt.date.today()
    out = []
    for i in range(days):
        d = today + dt.timedelta(days=i)
        label = f"{d:%a} {d.day} {d:%b %Y}"
        out.append(label + (" (today)" if i == 0 else " (tomorrow)" if i == 1 else ""))
    return ", ".join(out)


def shorten(text: str, max_sentences: int = 3) -> str:
    """Hard limit on spoken length: keep the first few sentences.

    The prompt asks for short replies, but a small model will sometimes give a
    lecture anyway (and cut off mid-sentence at the token limit).
    """
    if max_sentences <= 0:
        return text
    # Split after . ! ? that end a sentence (not "Dr." or "3.5").
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text.strip())
    kept = " ".join(parts[:max_sentences])
    if kept and kept[-1] not in ".!?":
        # Cut off by the token limit: end on the last full sentence if there is one.
        last = max(kept.rfind(". "), kept.rfind("! "), kept.rfind("? "))
        kept = kept[: last + 1] if last > 0 else kept + "."
    return kept


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
