"""Long-term memory: facts the user asks JARVIS to remember, kept across restarts.

    "Remember that my WIL deadline is Friday"  -> remember("Sooriyan's WIL deadline is Friday 14 November 2026")
    "My deadline moved to Monday"               -> remember(..., replaces="WIL deadline")
    "Forget my deadline"                        -> forget("deadline")
    "What do you remember about me?"            -> answered from the list in the system prompt

Only things the user explicitly asks to keep are saved. Every saved fact is
put in the system prompt (see prompt_block), so JARVIS "knows" them without a
tool call. They live in memory.json in the project folder, which .gitignore
keeps out of GitHub.
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import os
import re
import threading
from pathlib import Path

from . import confirmation, tool

_path = Path("memory.json")
_user = "the user"
_lock = threading.Lock()
MAX_FACTS = 200        # oldest are dropped past this
PROMPT_FACTS = 60      # most recent facts put in the system prompt

_STOP_WORDS = {
    "the", "a", "an", "my", "is", "are", "was", "to", "of", "and", "in", "on", "at", "for",
    "that", "this", "it", "i", "me", "about", "with", "be", "has", "have", "s", "his", "her",
    "their", "user", "user's", "remember", "forget",
}


def configure(path: str | os.PathLike, user: str | None = None) -> None:
    global _path, _user
    _path = Path(path)
    if user:
        _user = user


# --- Storage ---------------------------------------------------------------

def load() -> list[dict]:
    """[{"text": ..., "saved": "2026-11-06T02:55"}], oldest first."""
    try:
        data = json.loads(_path.read_text(encoding="utf-8"))
        facts = data.get("facts", []) if isinstance(data, dict) else []
        return [f for f in facts if isinstance(f, dict) and f.get("text")]
    except (OSError, ValueError):
        return []


def _save(facts: list[dict]) -> None:
    facts = facts[-MAX_FACTS:]
    tmp = _path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"facts": facts}, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _path)  # never leaves a half-written file if JARVIS is closed mid-save


def prompt_block(user: str = "the user") -> str:
    """The facts as text for the system prompt."""
    facts = load()[-PROMPT_FACTS:]
    if not facts:
        return f"You haven't been asked to remember anything about {user} yet."
    lines = [f"- {f['text']} (saved {_short_date(f.get('saved', ''))})" for f in facts]
    return (f"Things {user} asked you to remember (use them when relevant; the save date "
            f"tells you how old each one is):\n" + "\n".join(lines))


# --- Matching --------------------------------------------------------------

def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in _STOP_WORDS}


def _score(query: str, fact: str) -> float:
    q, f = _words(query), _words(fact)
    if not q:
        return 0.0
    overlap = len(q & f) / len(q)
    ratio = difflib.SequenceMatcher(None, query.lower(), fact.lower()).ratio()
    return max(overlap, ratio)


def _find(query: str, facts: list[dict], threshold: float = 0.5) -> list[int]:
    """Indexes of facts matching `query`, best first."""
    scored = sorted(((_score(query, f["text"]), i) for i, f in enumerate(facts)), reverse=True)
    return [i for s, i in scored if s >= threshold]


def _short_date(iso: str) -> str:
    try:
        d = dt.datetime.fromisoformat(iso)
    except ValueError:
        return "unknown date"
    return f"{d.day} {d:%b %Y}"


_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]
_DATED = re.compile(
    r"\b(?P<wd>" + "|".join(_DAYS) + r"),?\s+(the\s+)?(?P<day>\d{1,2})(st|nd|rd|th)?\s+(of\s+)?"
    r"(?P<month>" + "|".join(_MONTHS) + r")(\s+(?P<year>\d{4}))?\b",
    re.IGNORECASE,
)


def fix_weekdays(text: str) -> str:
    """"Friday 14 November 2026" -> "Friday 13 November 2026".

    The model writes the weekday the user said, then often gets the date wrong.
    The weekday is what the user actually said, so keep it and move the date to
    the nearest day (within 3) that really is that weekday.
    """
    def fix(m: re.Match) -> str:
        wanted = _DAYS.index(m["wd"].lower())
        year = int(m["year"]) if m["year"] else dt.date.today().year
        try:
            date = dt.date(year, _MONTHS.index(m["month"].lower()) + 1, int(m["day"]))
        except ValueError:
            return m.group(0)
        if date.weekday() == wanted:
            return m.group(0)
        shift = (wanted - date.weekday()) % 7
        date += dt.timedelta(days=shift if shift <= 3 else shift - 7)
        return f"{m['wd']} {date.day} {date:%B}" + (f" {date.year}" if m["year"] else "")

    return _DATED.sub(fix, text)


# --- Turning "my WIL is due on Friday" into a fact ---------------------------

_MODALS = {"can", "can't", "cannot", "will", "won't", "would", "should", "could", "must",
           "might", "may", "did", "didn't", "am", "was", "wasn't"}
_IRREGULAR = {"am": "is", "have": "has", "do": "does", "don't": "doesn't", "go": "goes", "was": "was"}


def _third_person_verb(verb: str) -> str:
    v = verb.lower()
    if v in _IRREGULAR:
        return _IRREGULAR[v]
    if v in _MODALS or v.endswith("ed"):
        return verb
    if v.endswith(("s", "sh", "ch", "x", "z", "o")):
        return verb + "es"
    if v.endswith("y") and len(v) > 2 and v[-2] not in "aeiou":
        return verb[:-1] + "ies"
    return verb + "s"


def _resolve_dates(text: str, today: dt.date | None = None) -> str:
    """today / tomorrow / (this|next) Friday -> real dates, e.g. "on Friday 13 November 2026".

    A bare weekday always means an upcoming one, never today ("due on Friday",
    said on a Friday, is next week; for today people say "today").
    "Next Monday" is the Monday after the coming one.
    """
    today = today or dt.date.today()
    days = "|".join(_DAYS)

    def full(d: dt.date) -> str:
        return f"{d:%A} {d.day} {d:%B %Y}"

    def repl(m: re.Match) -> str:
        word = m["word"].lower()
        prep = (m["prep"] or "").strip().lower()
        if word in _DAYS and _DATED.match(m["word"] + text[m.end():m.end() + 25]):
            return m.group(0)  # already followed by a date: "Friday 13 November"
        if word == "today":
            date = today
        elif word == "tomorrow":
            date = today + dt.timedelta(days=1)
        else:
            ahead = ((_DAYS.index(word) - today.weekday() - 1) % 7) + 1  # 1..7: never today
            if prep == "next" and ahead < 7:
                ahead += 7
            date = today + dt.timedelta(days=ahead)
        keep = prep if prep in ("on", "by", "before", "until", "from", "after") else "on"
        return f"{keep} {full(date)}"

    return re.sub(
        rf"(?P<prep>\b(on|by|before|until|from|after|this|next|coming)\s+)?\b(?P<word>today|tomorrow|{days})\b",
        repl, text, flags=re.IGNORECASE)


def said_back(said: str) -> str:
    """What the user said, turned around to speak back: "my WIL is due Friday" -> "your WIL is due on Friday 13 November 2026"."""
    s = " ".join(said.split()).strip(" .!?")
    for pattern, repl in ((r"\b(i am|i'm)\b", "you're"), (r"\bi've\b", "you've"), (r"\bi'll\b", "you'll"),
                          (r"\bi'd\b", "you'd"), (r"\bI\b", "you"), (r"\bmy\b", "your"),
                          (r"\bmine\b", "yours"), (r"\bmyself\b", "yourself"), (r"\bme\b", "you")):
        s = re.sub(pattern, repl, s, flags=re.IGNORECASE)
    if s.lower().startswith("to "):
        s = "you need " + s
    return _resolve_dates(s)


def to_fact(said: str, user: str | None = None) -> str:
    """What the user said -> a standalone fact about them.

    "my favourite food is pizza" -> "Sooriyan's favourite food is pizza"
    "I like Unity"               -> "Sooriyan likes Unity"
    "WIL is due on Friday"       -> "WIL is due on Friday 13 November 2026"
    """
    u = user or _user
    s = " ".join(said.split()).strip(" .!?")
    s = re.sub(r"\b(i am|i'm)\b", f"{u} is", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(i've)\b", f"{u} has", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(i'll)\b", f"{u} will", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(i'd)\b", f"{u} would", s, flags=re.IGNORECASE)
    s = re.sub(r"\bI\s+([a-z']+)\b", lambda m: f"{u} {_third_person_verb(m[1])}", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(my|mine)\b", f"{u}'s", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(me|myself)\b", u, s, flags=re.IGNORECASE)
    if s.lower().startswith("to "):
        s = f"{u} needs {s}"                            # "remember to call Kairav"
    s = _resolve_dates(s)
    return s[:1].upper() + s[1:]


def _subject(fact: str) -> str:
    """"Sooriyan's favourite food is pizza" -> "sooriyansfavouritefood" (what the fact is about)."""
    m = re.match(r"^(.+?)\s+(is|are|was|were)\s+", fact, re.IGNORECASE)
    return re.sub(r"[^a-z0-9]", "", m[1].lower()) if m else ""


# --- Tools -----------------------------------------------------------------

@tool
def remember(fact: str, replaces: str = "") -> str:
    """Save something the user explicitly asks you to remember (a preference, plan, name, deadline...). Also use it when they say a remembered fact has changed.

    Args:
        fact: One short standalone sentence in third person, with real dates instead of "Friday" or "tomorrow", e.g. "Sooriyan's WIL deadline is Friday 14 November 2026".
        replaces: If this updates an old fact, a few words of the old one (e.g. "WIL deadline"), so it gets replaced instead of kept.
    """
    fact = fix_weekdays(" ".join(fact.split()).strip())
    if not fact:
        return "Error: there's nothing to remember."
    with _lock:
        facts = load()
        old = ""
        if replaces.strip():
            hits = _find(replaces, facts)
            if hits:
                old = facts.pop(hits[0])["text"]
        # Saying the same thing twice, or a new value for the same thing
        # ("favourite food is sushi" after "favourite food is pizza"), replaces the old fact.
        subject = _subject(fact)
        for i, f in enumerate(facts):
            same_text = difflib.SequenceMatcher(None, f["text"].lower(), fact.lower()).ratio() > 0.85
            same_subject = subject and _subject(f["text"]) == subject
            if same_text or same_subject:
                popped = facts.pop(i)["text"]
                old = old or popped
                break
        facts.append({"text": fact, "saved": dt.datetime.now().isoformat(timespec="minutes")})
        _save(facts)
    if old:
        return f"Updated: '{old}' is now '{fact}'."
    return f"Saved to memory: {fact}"


@tool
def forget(about: str = "", everything: bool = False, confirmed: bool = False) -> str:
    """Delete something from memory when the user asks you to forget it.

    Args:
        about: A few words describing what to forget, e.g. "WIL deadline".
        everything: True only if the user wants all memories wiped.
        confirmed: Only true after the user said yes to a confirmation question from this tool.
    """
    with _lock:
        facts = load()
        if everything:
            if not facts:
                return "Memory is already empty."
            ask = confirmation("forget:all", f"Forget all {len(facts)} things I remember?", confirmed)
            if ask:
                return ask
            _save([])
            return f"Forgot all {len(facts)} memories."

        if not about.strip():
            return "Error: what should I forget?"
        hits = _find(about, facts)
        if not hits:
            if not facts:
                return "Memory is empty; there's nothing to forget."
            return f"Error: nothing in memory matches '{about}'. Remembered: " + \
                "; ".join(f["text"] for f in facts[-10:])

        best = _score(about, facts[hits[0]]["text"])
        close = [i for i in hits if _score(about, facts[i]["text"]) >= best - 0.05]
        if len(close) > 1:
            options = "; ".join(f"'{facts[i]['text']}'" for i in close[:4])
            return f"Not done yet: several memories match. Ask the user which one: {options}"
        removed = facts.pop(hits[0])["text"]
        _save(facts)
    return f"Forgot: {removed}"
