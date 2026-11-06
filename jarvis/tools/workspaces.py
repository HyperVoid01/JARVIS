"""Workspaces: "let's get to work" opens everything you need for a project.

    "Let's get to work"            -> "What are we working on, sir?"
    "Rat Attack"                   -> opens the saved Rat Attack workspace, or, if
                                      there isn't one yet: "What should I open for Rat Attack?"
    "Unity, VS Code and GitHub"    -> saves that as the Rat Attack workspace and opens it
    "Add Discord to Rat Attack"    -> workspace(add)
    "Let's work on Rat Attack"     -> skips the first question

Items are resolved when they're saved, so opening a workspace later is
predictable: "Unity" becomes the project itself (in the right editor version),
"GitHub" becomes that project's repo (read from its .git folder), "music"
becomes Spotify, and anything else is an app or website. Only items that
actually opened are saved, so a misheard app doesn't end up in the list.

The two questions are answered by fastpath.py (see `answer_pending`), so the
answer can't be misread by the LLM as a brand new command.
Stored in workspaces.json in the project folder (kept out of git).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from pathlib import Path
from typing import Literal

from . import confirmation, tool
from .speech_match import best_match, clean_name

_path = Path("workspaces.json")
_lock = threading.Lock()

PENDING_S = 30  # how long a question ("what should I open?") waits for its answer

# Spoken words -> what they mean in a workspace.
_UNITY_WORDS = {"unity", "the project", "project", "the game", "game", "unity project",
                "the unity project", "editor", "the editor", "unity editor", "engine"}
_GITHUB_WORDS = {"github", "git hub", "the repo", "repo", "my repo", "repository", "the repository",
                 "github repo", "the github repo", "the github", "source control", "git"}
_MUSIC_WORDS = {"music", "tunes", "spotify", "playlist", "my playlist"}
_SITES = {
    "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com",
    "email": "https://mail.google.com", "my email": "https://mail.google.com",
    "chatgpt": "https://chatgpt.com", "chat gpt": "https://chatgpt.com",
    "claude": "https://claude.ai", "itch": "https://itch.io", "itch io": "https://itch.io",
    "itch.io": "https://itch.io", "linkedin": "https://www.linkedin.com",
    "google drive": "https://drive.google.com", "drive": "https://drive.google.com",
    "google docs": "https://docs.google.com", "docs": "https://docs.google.com",
    "trello": "https://trello.com", "miro": "https://miro.com",
}

# Filler before an answer, ways of saying "I'm working on X", and filler before item lists.
_LEAD_FILLER = re.compile(r"^((ok(ay)?|so|well|right|um+|uh+|erm|hmm)[\s,]+)+", re.I)
_WORKING_ON = re.compile(
    r"^((we're|we are|i'm|i am|we'll|i'll|let's|lets|i want to|i'd like to)\s+)?"
    r"((be\s+)?(working|work|going to work|gonna work|doing|continuing|carrying on)\s+)?(on|with)\s+", re.I)
_ITEM_FILLER = re.compile(
    r"^((please|just|open( up)?|i('ll| will)? need|i want|i'd like|we need|let's have|give me|also)\s+)+", re.I)
_SPLIT = re.compile(r"\s*(?:,|;|&|\bplus\b|\band then\b|\bthen\b|\band\b|\balso\b|\bwith\b)\s*", re.I)
_NOT_AN_ANSWER = re.compile(
    r"^(what|how|why|who|when|where|can you|could you|would you|is|are|do you|set|close|quit|"
    r"pause|play|stop|skip|next|turn|volume|mute|search|remember|forget|switch|lock|shut)\b", re.I)
_CANCEL = re.compile(r"^(no|nah|nothing|never ?mind|cancel|forget it|not now|nothing yet|none)\b", re.I)


def configure(path: str | Path) -> None:
    global _path
    _path = Path(path)


# --- Storage ---------------------------------------------------------------

def load() -> dict[str, dict]:
    """{name: {"items": [{"type", "target", "label"}], "saved": iso}}"""
    try:
        data = json.loads(_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    ws = data.get("workspaces", {}) if isinstance(data, dict) else {}
    return {k: v for k, v in ws.items() if isinstance(v, dict) and isinstance(v.get("items"), list)}


def _save(workspaces: dict[str, dict]) -> None:
    with _lock:
        tmp = _path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"workspaces": workspaces}, indent=2), encoding="utf-8")
        tmp.replace(_path)


def prompt_line() -> str:
    names = sorted(load())
    return ("Saved workspaces: " + ", ".join(names) + ".") if names else "No workspaces saved yet."


# --- Names -----------------------------------------------------------------

def _unity():
    from . import unity
    return unity


def _canonical(name: str, saved: dict[str, dict]) -> str:
    """The name to file a workspace under: a saved workspace, a Unity project, or the name as said."""
    hit = best_match(name, list(saved), cutoff=0.6) if saved else None
    if hit:
        return hit
    project = _unity().match_project(name)
    if project:
        return project
    cleaned = clean_name(name, project=False).strip(" .,!?")
    cleaned = re.sub(r"^((my|the|our|a)\s+)+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+(workspace|setup|stuff)$", "", cleaned, flags=re.I).strip() or cleaned
    return cleaned[:1].upper() + cleaned[1:] if cleaned else name.strip()


def _github_url(project: str | None) -> str:
    """The project's repo URL from its .git/config, or GitHub's front page."""
    if project:
        path = _unity().find_projects().get(project)
        if path:
            try:
                config = (path / ".git" / "config").read_text(encoding="utf-8", errors="ignore")
            except OSError:
                config = ""
            m = re.search(r"url\s*=\s*(\S+)", config)
            if m:
                url = m.group(1).strip()
                url = re.sub(r"^git@([^:]+):", r"https://\1/", url)   # git@github.com:me/repo.git
                return url.removesuffix(".git")
    return "https://github.com"


# --- Items -----------------------------------------------------------------

def parse_items(text: str) -> list[str]:
    """'Unity, VS Code and GitHub please' -> ['Unity', 'VS Code', 'GitHub']"""
    text = _ITEM_FILLER.sub("", _LEAD_FILLER.sub("", text.strip().rstrip(".!?")))
    text = re.sub(r"\b(please|thanks|thank you|for me|as well|too)\b", "", text, flags=re.I)
    parts = [p.strip(" .,!?") for p in _SPLIT.split(text)]
    out = []
    for p in parts:
        p = re.sub(r"^((the|my|a|an|open|some)\s+)+(?=\w)", "", p, flags=re.I)
        if p and p.lower() not in {"the", "my", "and", "a", "it", "that"}:
            out.append(p)
    return out


def resolve(phrase: str, workspace: str) -> dict:
    """What a spoken item means: {"type": unity|app|url, "target": ..., "label": ...}."""
    low = " ".join(phrase.lower().split())
    project = _unity().match_project(workspace)

    if low in _UNITY_WORDS:
        if project:
            return {"type": "unity", "target": project, "label": "Unity"}
        return {"type": "app", "target": "Unity Hub", "label": "Unity Hub"}
    if low in _GITHUB_WORDS:
        return {"type": "url", "target": _github_url(project), "label": "GitHub"}
    if low in _MUSIC_WORDS:
        return {"type": "app", "target": "Spotify", "label": "Spotify"}
    if low in _SITES:
        return {"type": "url", "target": _SITES[low], "label": phrase}
    if re.match(r"^(https?://)?[\w-]+(\.[\w-]+)+(/\S*)?$", low):
        return {"type": "url", "target": phrase, "label": phrase}
    other = _unity().match_project(phrase)
    if other and len(low) > 3:
        return {"type": "unity", "target": other, "label": f"{other} in Unity"}
    return {"type": "app", "target": phrase, "label": phrase}


def _open(item: dict) -> str:
    from . import system
    if item["type"] == "unity":
        return _unity().open_unity_project(item["target"])
    if item["type"] == "url":
        return system.open_website(item["target"])
    return system.open_app(item["target"])


def _open_all(items: list[dict]) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Open items (Unity first, it's the slowest). Returns (opened, [(failed, error)])."""
    opened, failed = [], []
    for item in sorted(items, key=lambda i: i["type"] != "unity"):
        result = _open(item)
        if result.startswith("Error"):
            failed.append((item, result))
        else:
            opened.append(item)
        time.sleep(0.15)  # a burst of launches can make Windows drop one
    return opened, failed


def _join(labels: list[str]) -> str:
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def _failures(failed: list[tuple[dict, str]]) -> str:
    if not failed:
        return ""
    unity_errors = [e for i, e in failed if i["type"] == "unity"]
    names = [i["label"] for i, _ in failed if i["type"] != "unity"]
    out = ""
    if names:
        out += f" I couldn't find {_join(names)}."
    if unity_errors:
        out += " " + unity_errors[0].removeprefix("Error: ").split(". Pass")[0].rstrip(".") + "."
    return out


# --- Pending questions -----------------------------------------------------
# "What are we working on?" / "What should I open for X?" wait for the next command.

_pending: dict | None = None


def _ask(kind: str, name: str, question: str) -> str:
    global _pending
    _pending = {"kind": kind, "name": name, "at": time.time()}
    return f'Not opened yet. Ask them exactly: "{question}"'


def pending() -> dict | None:
    if _pending and time.time() - _pending["at"] < PENDING_S:
        return _pending
    return None


def answer_pending(text: str) -> tuple[str, dict] | None:
    """If `text` answers a question this module just asked, the (tool, arguments) to call.

    Called by fastpath.py for every command. Any command that isn't an answer
    cancels the question, so it can't hijack something said later.
    """
    global _pending
    p = pending()
    _pending = None
    if p is None:
        return None
    said = text.strip().rstrip(".!?")
    if _CANCEL.match(said):
        return "workspace", {"action": "cancel", "name": p["name"]}
    if _NOT_AN_ANSWER.match(_LEAD_FILLER.sub("", said)):
        return None

    if p["kind"] == "project":
        name = _WORKING_ON.sub("", _LEAD_FILLER.sub("", said))
        name = re.sub(r"^(the|my)\s+", "", name, flags=re.I)
        name = re.sub(r"\s+(today|tonight|now|again|please)$", "", name, flags=re.I).strip()
        if not name or len(name.split()) > 6:
            return None
        return "workspace", {"action": "start", "name": name}

    items = parse_items(said)
    if not items:
        return None
    return "workspace", {"action": "save", "name": p["name"], "items": ", ".join(items)}


# --- Tool ------------------------------------------------------------------

@tool
def workspace(
    action: Literal["start", "save", "add", "remove", "list", "delete"],
    name: str = "",
    items: str = "",
    confirmed: bool = False,
) -> str:
    """Start or change a work setup: the apps, websites and Unity project the user opens to work on something. Use start when the user says "let's get to work" or what they're working on.

    Args:
        action: start (open a workspace; asks what to open if it isn't saved), save (create or replace one), add or remove items, list, or delete.
        name: The project or workspace as heard, e.g. "Rat Attack". Leave empty for start if they didn't say.
        items: For save/add/remove: the things to open, comma-separated, e.g. "Unity, VS Code, GitHub, Spotify".
        confirmed: Only for delete, after the user has said yes.
    """
    global _pending
    saved = load()

    if action == "cancel":  # the user said "never mind" to a question
        return "Very well, sir. Say the word when you're ready."

    if action == "list":
        if not saved:
            return "No workspaces saved yet. Say \"let's get to work\" to set one up."
        return "Workspaces: " + "; ".join(
            f"{n} ({_join([i['label'] for i in w['items']])})" for n, w in sorted(saved.items())) + "."

    if not name.strip():
        if action == "start":
            return _ask("project", "", "What are we working on, sir?")
        return "Error: which workspace?"

    key = _canonical(name, saved)

    if action == "start":
        ws = saved.get(key)
        if not ws:
            return _ask("items", key, f"What should I open for {key}?")
        opened, failed = _open_all(ws["items"])
        if not opened:
            return f"Error: nothing in the {key} workspace would open.{_failures(failed)}"
        return f"Opening {key}: {_join([i['label'] for i in opened])}.{_failures(failed)}"

    if action == "delete":
        if key not in saved:
            return f"Error: there's no workspace called {name}. {prompt_line()}"
        ask = confirmation(f"workspace-delete:{key}", f"Delete the {key} workspace?", confirmed)
        if ask:
            return ask
        del saved[key]
        _save(saved)
        return f"Deleted the {key} workspace."

    phrases = parse_items(items)
    if not phrases:
        return _ask("items", key, f"What should I open for {key}?") if action == "save" \
            else "Error: which items?"

    if action == "remove":
        ws = saved.get(key)
        if not ws:
            return f"Error: there's no workspace called {name}."
        before = ws["items"]
        drop = {p.lower() for p in phrases}
        keep = [i for i in before
                if i["label"].lower() not in drop and i["target"].lower() not in drop
                and not (i["type"] == "unity" and drop & _UNITY_WORDS)
                and not (i["label"] == "GitHub" and drop & _GITHUB_WORDS)
                and not (i["target"] == "Spotify" and drop & _MUSIC_WORDS)]
        if len(keep) == len(before):
            return f"Error: {_join(phrases)} isn't in the {key} workspace. It has {_join([i['label'] for i in before])}."
        ws["items"] = keep
        _save(saved)
        return f"Removed it. {key} now opens {_join([i['label'] for i in keep]) or 'nothing'}."

    # save / add: resolve each item and open it now, which also checks it exists.
    new = [resolve(p, key) for p in phrases]
    existing = saved.get(key, {}).get("items", []) if action == "add" else []
    fresh = [i for i in new if not any(i["type"] == e["type"] and i["target"] == e["target"]
                                       for e in existing)]
    opened, failed = _open_all(fresh)
    if not opened and not existing:
        return f"Error: none of that would open, so I didn't save it.{_failures(failed)}"

    saved[key] = {"items": existing + opened, "saved": dt.datetime.now().strftime("%Y-%m-%dT%H:%M")}
    _save(saved)
    if action == "add":
        if not opened:
            return f"Error: couldn't add that.{_failures(failed)}"
        return f"Added {_join([i['label'] for i in opened])} to {key}, and opened it.{_failures(failed)}"
    return (f"Saved the {key} workspace and opened {_join([i['label'] for i in opened])}."
            f"{_failures(failed)}")
