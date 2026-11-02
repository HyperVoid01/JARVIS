"""Everyday PC tools: time, apps, websites, media keys, timers."""

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

# Spoken name -> Start Menu shortcut name. Extend this in config.py (app_aliases).
APP_ALIASES: dict[str, str] = {
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "code": "Visual Studio Code",
    "visual studio": "Visual Studio 2022",
    "chrome": "Google Chrome",
    "edge": "Microsoft Edge",
    "unity": "Unity Hub",
    "file explorer": "explorer",
    "explorer": "explorer",
    "files": "explorer",
    "calculator": "calc",
    "terminal": "wt",
}

# Apps that register a URI scheme (works for Microsoft Store installs that have no .lnk).
APP_URIS: dict[str, str] = {
    "spotify": "spotify:",
    "discord": "discord:",
    "settings": "ms-settings:",
    "steam": "steam://open/main",
    "whatsapp": "whatsapp:",
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
    """Every app shortcut on the Start Menu and Desktop, keyed by lowercase name."""
    global _shortcut_cache
    if _shortcut_cache and time.time() - _shortcut_cache[0] < 300:
        return _shortcut_cache[1]

    roots = [
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
        Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
        Path.home() / "Desktop",
        Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop",
    ]
    found: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in (".lnk", ".url"):
                continue
            name = path.stem.lower()
            if "uninstall" in name or "readme" in name:
                continue
            found.setdefault(name, path)

    _shortcut_cache = (time.time(), found)
    return found


def _normalise(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def _find_shortcut(name: str) -> Path | None:
    shortcuts = _start_menu_shortcuts()
    if not shortcuts:
        return None
    wanted = _normalise(name)
    names = {_normalise(k): v for k, v in shortcuts.items()}

    if wanted in names:
        return names[wanted]
    starts = sorted((k for k in names if k.startswith(wanted)), key=len)
    if starts:
        return names[starts[0]]
    contains = sorted((k for k in names if wanted in k), key=len)
    if contains:
        return names[contains[0]]
    close = difflib.get_close_matches(wanted, list(names), n=1, cutoff=0.7)
    return names[close[0]] if close else None


@tool
def open_app(name: str) -> str:
    """Open an application installed on this PC.

    Args:
        name: The app's name as the user said it, e.g. "Spotify", "Discord", "VS Code", "Unity Hub".
    """
    if not IS_WINDOWS:
        return "Error: opening apps is only supported on Windows."

    key = name.lower().strip()
    target = APP_ALIASES.get(key, name)

    shortcut = _find_shortcut(target)
    if shortcut:
        os.startfile(shortcut)  # type: ignore[attr-defined]
        return f"Opened {shortcut.stem}."

    uri = APP_URIS.get(key) or APP_URIS.get(target.lower())
    if uri:
        os.startfile(uri)  # type: ignore[attr-defined]
        return f"Opened {name}."

    exe = shutil.which(target)
    if exe:
        os.startfile(exe)  # type: ignore[attr-defined]
        return f"Opened {name}."

    return f"Error: couldn't find an app called '{name}' in the Start Menu."


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


# --- Media keys ------------------------------------------------------------

_MEDIA_KEYS = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "volume_up": 0xAF,
    "volume_down": 0xAE,
    "mute": 0xAD,
}


@tool
def media_control(
    action: Literal["play_pause", "next", "previous", "volume_up", "volume_down", "mute"],
    times: int = 1,
) -> str:
    """Control music/video playback and system volume, like pressing the keyboard media keys.

    Args:
        action: play_pause, next, previous, volume_up, volume_down or mute.
        times: How many presses. Each volume press is about 2%, so "turn it up a lot" is around 10.
    """
    if not IS_WINDOWS:
        return "Error: media keys are only supported on Windows."
    import ctypes

    vk = _MEDIA_KEYS[action]
    times = max(1, min(int(times), 50))
    KEYUP = 0x0002
    for _ in range(times):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)  # type: ignore[attr-defined]
        ctypes.windll.user32.keybd_event(vk, 0, KEYUP, 0)  # type: ignore[attr-defined]
        time.sleep(0.02)
    return f"Pressed {action.replace('_', ' ')}" + (f" {times} times." if times > 1 else ".")


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
