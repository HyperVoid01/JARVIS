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
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from . import notify, tool

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
    return shortcuts[close[0]] if close else None


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

    spoken = _normalise(name)
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

    return f"Error: couldn't find an app called '{name}' on this PC."


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
    """Search the web and show the results in the browser. Use when the user asks to look something up.

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

_timers: list[tuple[float, str, threading.Timer]] = []


@tool
def set_timer(minutes: float = 0, seconds: float = 0, label: str = "") -> str:
    """Start a countdown timer that alerts the user when it finishes.

    Args:
        minutes: Minutes to count down.
        seconds: Extra seconds to count down.
        label: Optional short name, e.g. "pizza" or "break".
    """
    total = float(minutes) * 60 + float(seconds)
    if total <= 0:
        return "Error: the timer needs a duration."

    name = label.strip() or "timer"

    def ring():
        _timers[:] = [t for t in _timers if t[2] is not timer]
        notify(f"Your {name} is done." if label else "Time's up.")

    timer = threading.Timer(total, ring)
    timer.daemon = True
    timer.start()
    _timers.append((time.time() + total, name, timer))
    return f"Timer set for {_format_duration(total)}" + (f" ({name})." if label else ".")


@tool
def list_timers() -> str:
    """List running timers and how long is left on each."""
    now = time.time()
    active = [(end - now, name) for end, name, _ in _timers if end > now]
    if not active:
        return "No timers running."
    return "; ".join(f"{name}: {_format_duration(left)} left" for left, name in sorted(active))


def _format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    parts = [f"{h} hour{'s' * (h != 1)}" if h else "",
             f"{m} minute{'s' * (m != 1)}" if m else "",
             f"{s} second{'s' * (s != 1)}" if s else ""]
    return " ".join(p for p in parts if p) or "0 seconds"
