"""Everyday PC tools: time, apps, websites, timers. (Volume and media live in media.py.)"""

from __future__ import annotations

import datetime as dt
import difflib
import os
import re
import shutil
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from . import notify, tool
from .speech_match import clean_name, speech_key

IS_WINDOWS = sys.platform == "win32"

# Spoken name -> Start Menu shortcut name (or a key in APP_URIS / an exe name).
APP_ALIASES: dict[str, str] = {
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "code": "Visual Studio Code",
    "visual studio": "Visual Studio 2022",
    "chrome": "Google Chrome",
    "edge": "Microsoft Edge",
    "unity": "Unity Hub",
    "file explorer": "explorer",
    "files": "explorer",
    "my files": "explorer",
    "windows settings": "settings",
    "pc settings": "settings",
    "wifi settings": "wi-fi settings",
    "terminal": "wt",
    "command prompt": "cmd",
    "paint": "mspaint",
    "task manager": "taskmgr",
}

# Built-in Windows apps and Store apps have no normal .lnk shortcut, but they
# register a URI or live on PATH. These are checked BEFORE fuzzy shortcut
# matching, so "settings" can never turn into "Printer Data Collection Settings".
APP_URIS: dict[str, str] = {
    "settings": "ms-settings:",
    "bluetooth settings": "ms-settings:bluetooth",
    "sound settings": "ms-settings:sound",
    "display settings": "ms-settings:display",
    "wi-fi settings": "ms-settings:network-wifi",
    "apps settings": "ms-settings:appsfeatures",
    "windows update": "ms-settings:windowsupdate",
    "calculator": "calculator:",
    "microsoft store": "ms-windows-store:",
    "store": "ms-windows-store:",
    "camera": "microsoft.windows.camera:",
    "photos": "ms-photos:",
    "clock": "ms-clock:",
    "alarms": "ms-clock:",
    "snipping tool": "ms-screenclip:",
    "xbox": "xbox:",
    "spotify": "spotify:",
    "discord": "discord:",
    "steam": "steam://open/main",
    "whatsapp": "whatsapp:",
    "explorer": "explorer",
    "notepad": "notepad",
    "cmd": "cmd",
    "mspaint": "mspaint",
    "taskmgr": "taskmgr",
    "wt": "wt",
}


def configure(app_aliases: dict[str, str] | None = None) -> None:
    if app_aliases:
        APP_ALIASES.update({k.lower(): v for k, v in app_aliases.items()})


# --- Time ------------------------------------------------------------------

@tool
def get_datetime() -> str:
    """Get the current local date, time and day of the week."""
    now = dt.datetime.now()
    return now.strftime("%A %d %B %Y, %H:%M")


# --- Apps ------------------------------------------------------------------

_shortcut_cache: tuple[float, dict[str, Path]] | None = None


def _start_menu_shortcuts() -> dict[str, Path]:
    """Every app shortcut on the Start Menu and Desktop, keyed by normalised name."""
    global _shortcut_cache
    if _shortcut_cache and time.time() - _shortcut_cache[0] < 300:
        return _shortcut_cache[1]

    roots = [
        Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
        Path.home() / "Desktop",
        Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop",
    ]
    junk = ("uninstall", "readme", "help", "documentation", "release notes", "license")
    found: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in (".lnk", ".url"):
                continue
            name = _normalise(path.stem)
            if any(j in name for j in junk):
                continue
            found.setdefault(name, path)

    _shortcut_cache = (time.time(), found)
    return found


def _normalise(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())


def _find_shortcut(wanted: str, exact_only: bool = False) -> Path | None:
    shortcuts = _start_menu_shortcuts()
    if not shortcuts:
        return None
    if wanted in shortcuts:
        return shortcuts[wanted]
    if exact_only:
        return None

    # "spotify" -> "Spotify Premium", "unity hub" -> "Unity Hub 3.9"
    starts = sorted((k for k in shortcuts if k.startswith(wanted + " ")), key=len)
    if starts:
        return shortcuts[starts[0]]

    # Every spoken word appears as a whole word, e.g. "blender" -> "Blender 4.2".
    # Only accept short names, so one generic word can't match a long unrelated title.
    words = set(wanted.split())
    whole = sorted((k for k in shortcuts
                    if words <= set(k.split()) and len(k.split()) <= len(words) + 2), key=len)
    if whole:
        return shortcuts[whole[0]]

    close = difflib.get_close_matches(wanted, list(shortcuts), n=1, cutoff=0.8)
    if close:
        return shortcuts[close[0]]

    # Last resort: compare how the names sound ("chwome" -> "chrome").
    keyed = {speech_key(k): k for k in shortcuts}
    hit = keyed.get(speech_key(wanted))
    return shortcuts[hit] if hit else None


def _launch(target: str | Path, label: str) -> str:
    try:
        os.startfile(str(target))  # type: ignore[attr-defined]
    except OSError as e:
        if getattr(e, "winerror", None) == 1223:
            return f"Error: opening {label} was cancelled (probably an admin prompt that was declined)."
        return f"Error: Windows couldn't open {label}: {e.strerror or e}"
    return f"Opened {label}."


@tool
def open_app(name: str) -> str:
    """Open an application or Windows settings page on this PC.

    Args:
        name: The app's name as the user said it, e.g. "Spotify", "Discord", "VS Code", "Settings", "Bluetooth settings".
    """
    if not IS_WINDOWS:
        return "Error: opening apps is only supported on Windows."

    spoken = _normalise(clean_name(name, project=False))
    target = _normalise(APP_ALIASES.get(spoken, spoken))

    # 1. A shortcut with exactly this name (respects what's actually installed).
    shortcut = _find_shortcut(target, exact_only=True)
    if shortcut:
        return _launch(shortcut, shortcut.stem)

    # 2. Built-in / Store apps with a known URI or command.
    uri = APP_URIS.get(target) or APP_URIS.get(spoken)
    if uri:
        return _launch(uri, name)

    # 3. Looser shortcut matching.
    shortcut = _find_shortcut(target)
    if shortcut:
        return _launch(shortcut, shortcut.stem)

    # 4. Anything on PATH (notepad, code, blender...).
    exe = shutil.which(target.replace(" ", ""))
    if exe:
        return _launch(exe, name)

    # 5. Maybe it's a Unity project the model sent to the wrong tool ("open Wacer").
    from . import unity
    project = unity.match_project(name)
    if project:
        return unity.open_unity_project(project)

    return f"Error: couldn't find an app or Unity project called '{name}' on this PC."


# --- Web -------------------------------------------------------------------

@tool
def open_website(url: str) -> str:
    """Open a website in the default browser.

    Args:
        url: A domain or full URL, e.g. "youtube.com" or "https://github.com/HyperVoid01".
    """
    url = url.strip()
    if not re.match(r"^[a-z]+://", url, re.IGNORECASE):
        if "." not in url:
            url += ".com"  # "youtube" -> "youtube.com"
        url = "https://" + url
    webbrowser.open(url)
    return f"Opened {url}."


@tool
def web_search(query: str, site: Literal["google", "youtube"] = "google") -> str:
    """Open a Google or YouTube search in the browser. ONLY when the user explicitly asks to search, look it up online, or show results; never for ordinary questions you can answer.

    Args:
        query: What to search for.
        site: "youtube" to search YouTube videos, otherwise "google".
    """
    if site == "youtube":
        url = "https://www.youtube.com/results?search_query=" + quote_plus(query)
    else:
        url = "https://www.google.com/search?q=" + quote_plus(query)
    webbrowser.open(url)
    return f"Showing {site} results for '{query}' in the browser."


# --- Timers ----------------------------------------------------------------
#
# Unnamed timers are named after their length ("2 minute timer"), so with
# several running you can still say "cancel the 1 minute timer".

@dataclass
class _Timer:
    end: float            # time.time() when it rings
    length: float         # seconds it was set for (used by restart)
    name: str             # "pizza", or "2 minute" for an unnamed timer
    named: bool           # True if the user gave it a name
    thread: threading.Timer

    @property
    def title(self) -> str:
        return f"{self.name} timer"


_timers: list[_Timer] = []  # oldest first
_timers_lock = threading.Lock()

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "forty five": 45, "fifty": 50, "sixty": 60,
    "a": 1, "an": 1,
}


@tool
def set_timer(minutes: float = 0, seconds: float = 0, label: str = "") -> str:
    """Start a NEW countdown timer that alerts the user when it finishes. Not for changing, restarting or renaming a timer; use change_timer for those.

    Args:
        minutes: Minutes to count down.
        seconds: Extra seconds to count down.
        label: Optional short name, e.g. "pizza" or "break".
    """
    total = float(minutes) * 60 + float(seconds)
    if total <= 0:
        return "Error: the timer needs a duration."
    name = _clean_label(label)
    t = _start(total, name or _short_duration(total), named=bool(name))
    if t.named:
        return f"Started the {t.title} for {_format_duration(total)}."
    return f"Started a {t.title}."


@tool
def change_timer(minutes: float = 0, seconds: float = 0, label: str = "", rename_to: str = "") -> str:
    """Change a running timer: give it a new length ("make it ten minutes"), restart it ("restart the timer": leave minutes and seconds at 0), or rename it ("call it pizza").

    Args:
        minutes: New length in minutes, counted from now. 0 to keep the length.
        seconds: Extra seconds for the new length.
        label: Which timer: its name ("pizza") or its length ("2 minute"). Leave empty for the most recent one.
        rename_to: A new name for the timer, if the user gave one.
    """
    old = _pick(label)
    if old is None:
        return _not_found(label)

    new_length = float(minutes) * 60 + float(seconds)
    new_name = _clean_label(rename_to)

    if new_length <= 0 and new_name:
        old.name, old.named = new_name, True  # rename only, keep counting down
        return f"Renamed it to the {old.title}; {_format_duration(old.end - time.time())} left."

    restart = new_length <= 0
    length = old.length if restart else new_length
    if new_name:
        name, named = new_name, True
    elif old.named:
        name, named = old.name, True
    else:
        name, named = _short_duration(length), False
    _cancel(old)
    t = _start(length, name, named)
    if restart:
        return f"Restarted the {t.title}: {_format_duration(length)} from now."
    return f"Changed the {old.title} to {_format_duration(length)}; it's now the {t.title}."


@tool
def cancel_timer(label: str = "", cancel_all: bool = False) -> str:
    """Cancel a running timer.

    Args:
        label: Which timer: its name ("pizza") or its length ("1 minute"). Leave empty for the most recent one.
        cancel_all: True if the user wants every timer cancelled.
    """
    if cancel_all:
        with _timers_lock:
            gone = list(_timers)
            _timers.clear()
        for t in gone:
            t.thread.cancel()
        if not gone:
            return "No timers were running."
        return f"Cancelled {len(gone)} timer{'s' * (len(gone) != 1)}."

    t = _pick(label)
    if t is None:
        return _not_found(label)
    _cancel(t)
    return f"Cancelled the {t.title} ({_format_duration(t.end - time.time())} was left)."


@tool
def list_timers() -> str:
    """List running timers and exactly how long is left on each. Call this EVERY time the user asks how long is left; the answer changes every second."""
    now = time.time()
    with _timers_lock:
        active = [t for t in _timers if t.end > now]
    if not active:
        return "No timers running."
    return "; ".join(f"{t.title}: {_format_duration(t.end - now)} left"
                     for t in sorted(active, key=lambda t: t.end))


def _start(length: float, name: str, named: bool) -> _Timer:
    def ring():
        with _timers_lock:
            if t in _timers:
                _timers.remove(t)
        notify(f"Time's up on your {t.title}.")

    t = _Timer(time.time() + length, length, name, named, threading.Timer(length, ring))
    t.thread.daemon = True
    with _timers_lock:
        _timers.append(t)
    t.thread.start()
    return t


def _cancel(t: _Timer) -> None:
    t.thread.cancel()
    with _timers_lock:
        if t in _timers:
            _timers.remove(t)


def _clean_label(label: str) -> str:
    """"the pizza timer" -> "pizza"."""
    s = re.sub(r"\b(the|my|a|an|timer|timers|for|called|named)\b", " ", label.lower())
    s = re.sub(r"[^\w' ]", " ", s)
    return " ".join(s.split())


def _match_key(s: str) -> str:
    """Spoken label -> comparable form: "the timer for one minute" -> "1 minute"."""
    s = " " + _clean_label(s) + " "
    for word, n in sorted(_NUMBER_WORDS.items(), key=lambda kv: -len(kv[0])):
        s = re.sub(rf"\b{word}\b", str(n), s)
    s = re.sub(r"\b(minutes|mins?)\b", "minute", s)
    s = re.sub(r"\b(seconds|secs?)\b", "second", s)
    s = re.sub(r"\b(hours|hrs?)\b", "hour", s)
    return " ".join(s.split())


def _pick(label: str) -> _Timer | None:
    """The timer the user means: by name or length if given, otherwise the most recent."""
    with _timers_lock:
        timers = list(_timers)
    if not timers:
        return None
    wanted = _match_key(label)
    if not wanted:
        return timers[-1]
    keys = [(t, _match_key(t.name)) for t in reversed(timers)]
    for t, key in keys:
        if key == wanted:
            return t
    for t, key in keys:
        if wanted in key or key in wanted:
            return t
    close = difflib.get_close_matches(wanted, [k for _, k in keys], n=1, cutoff=0.6)
    if close:
        return next(t for t, k in keys if k == close[0])
    return None


def _not_found(label: str) -> str:
    with _timers_lock:
        titles = ", ".join(t.title for t in _timers)
    if not titles:
        return "Error: no timers are running."
    return f"Error: no timer called '{label}'. Running timers: {titles}."


def _short_duration(seconds: float) -> str:
    """Name for an unnamed timer: 120 -> "2 minute", 45 -> "45 second", 5400 -> "90 minute"."""
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} second"
    m, s = divmod(seconds, 60)
    return f"{m} minute" if not s else f"{m} minute {s} second"


def _format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    parts = [f"{h} hour{'s' * (h != 1)}" if h else "",
             f"{m} minute{'s' * (m != 1)}" if m else "",
             f"{s} second{'s' * (s != 1)}" if s else ""]
    return " ".join(p for p in parts if p) or "0 seconds"
