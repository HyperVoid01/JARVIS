"""Media playback and volume.

Playback uses Windows' media session API (the same thing behind the media
controls in the volume flyout). It can see every app that's playing media
separately, so "pause Spotify" pauses Spotify, not the YouTube tab that
happens to be the "current" session. Keyboard media keys are only used as a
fallback if the winrt packages aren't installed.

Volume uses Windows Core Audio via pycaw.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from . import tool

IS_WINDOWS = sys.platform == "win32"

# What the user might call an app -> fragments of its media session ID (AppUserModelId).
APP_MATCHERS: dict[str, tuple[str, ...]] = {
    "spotify": ("spotify",),
    "browser": ("chrome", "msedge", "firefox", "308046b0af4a39cb", "brave", "opera", "vivaldi"),
    "chrome": ("chrome",),
    "edge": ("msedge",),
    "firefox": ("firefox", "308046b0af4a39cb"),  # Firefox's session ID is a hash
    "vlc": ("vlc",),
    "apple music": ("applemusic", "itunes"),
    "media player": ("zunemusic", "media player"),
}
# Things that play inside a browser.
BROWSER_ALIASES = {"youtube", "youtube music", "netflix", "twitch", "soundcloud", "video", "browser"}

FRIENDLY_NAMES = {
    "spotify": "Spotify", "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox",
    "308046b0af4a39cb": "Firefox", "brave": "Brave", "opera": "Opera", "vlc": "VLC",
}


# --- Windows media sessions ----------------------------------------------

_pool: ThreadPoolExecutor | None = None


def _run(coro_fn):
    """Run WinRT async code on its own thread.

    WinRT wants a multi-threaded COM apartment, while pycaw has already set
    the main thread up as single-threaded, so the two can't share a thread.
    """
    global _pool
    if _pool is None:
        def init():
            try:
                from winrt.runtime import ApartmentType, init_apartment
                init_apartment(ApartmentType.MULTI_THREADED)
            except Exception:
                pass  # already initialised

        _pool = ThreadPoolExecutor(max_workers=1, initializer=init, thread_name_prefix="media")
    return _pool.submit(lambda: asyncio.run(coro_fn())).result(timeout=10)


def _app_name(session) -> str:
    aumid = session.source_app_user_model_id.lower()
    for key, name in FRIENDLY_NAMES.items():
        if key in aumid:
            return name
    # "SomeCompany.SomeApp_abc123!App" or "something.exe" -> readable-ish
    base = re.split(r"[!_]", aumid)[0].removesuffix(".exe").split(".")[-1]
    return base.capitalize() or "Unknown app"


def _matches(session, app: str) -> bool:
    aumid = session.source_app_user_model_id.lower()
    key = "browser" if app in BROWSER_ALIASES else app
    fragments = APP_MATCHERS.get(key, (re.sub(r"[^a-z0-9]", "", key),))
    return any(f in aumid for f in fragments)


async def _describe(session) -> str:
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status

    status = session.get_playback_info().playback_status
    state = {Status.PLAYING: "playing", Status.PAUSED: "paused", Status.STOPPED: "stopped"}.get(status, "idle")
    try:
        props = await session.try_get_media_properties_async()
        title, artist = props.title, props.artist
    except Exception:
        title = artist = ""
    track = f"'{title}'" + (f" by {artist}" if artist else "") if title else "something"
    return f"{_app_name(session)} is {state}: {track}."


async def _media(action: str, app: str) -> str:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
    )

    manager = await Manager.request_async()
    sessions = list(manager.get_sessions())
    if not sessions:
        return "Error: nothing is playing media right now. Open an app like Spotify first."

    app = app.lower().strip()
    if app:
        targets = [s for s in sessions if _matches(s, app)]
        if not targets:
            open_apps = ", ".join(sorted({_app_name(s) for s in sessions}))
            return f"Error: {app} isn't playing anything. Media apps open: {open_apps}."
    else:
        playing = [s for s in sessions if s.get_playback_info().playback_status == Status.PLAYING]
        current = manager.get_current_session()
        if action == "pause":
            targets = playing  # "pause" with no app pauses everything that's playing
        elif action in ("next", "previous", "play_pause", "now_playing") and playing:
            targets = playing[:1]
        else:
            targets = [current] if current else sessions[:1]

    if action == "now_playing":
        return " ".join([await _describe(s) for s in targets])
    if not targets:
        return "Nothing is playing." if action == "pause" else "There's nothing to play."

    results = []
    for s in targets:
        op = {
            "play": s.try_play_async,
            "pause": s.try_pause_async,
            "play_pause": s.try_toggle_play_pause_async,
            "next": s.try_skip_next_async,
            "previous": s.try_skip_previous_async,
        }[action]
        ok = await op()
        if not ok:
            results.append(f"{_app_name(s)} didn't accept {action.replace('_', '/')}.")
            continue
        if action == "pause":
            # Apps take a moment to report the new state, so reading it back straight
            # away still says "playing". The command was accepted; say what was done.
            results.append(f"Paused {_app_name(s)}.")
            continue
        await asyncio.sleep(0.6)  # let the app load the new track / state before reading it
        results.append(await _describe(s))
    return " ".join(results)


def _media_keys(action: str) -> str:
    """Fallback: press a keyboard media key (goes to whatever Windows picks)."""
    import ctypes

    vk = {"play": 0xB3, "pause": 0xB3, "play_pause": 0xB3, "next": 0xB0, "previous": 0xB1}[action]
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)  # type: ignore[attr-defined]
    ctypes.windll.user32.keybd_event(vk, 0, 0x0002, 0)  # type: ignore[attr-defined]
    return f"Pressed the {action.replace('_', '/')} media key."


@tool
def media_control(
    action: Literal["play", "pause", "play_pause", "next", "previous", "now_playing"],
    app: str = "",
) -> str:
    """Control music or video playback: play, pause, skip, or say what's playing. Always call this to find out what's playing; never answer from earlier messages.

    Args:
        action: play, pause, play_pause (toggle), next, previous, or now_playing (what song/video is on).
        app: Which app, if the user named one, e.g. "spotify" or "youtube". Leave empty otherwise.
    """
    if not IS_WINDOWS:
        return "Error: media control is only supported on Windows."
    try:
        import winrt.windows.media.control  # noqa: F401
    except ImportError:
        if action == "now_playing":
            return "Error: install the winrt packages (pip install -r requirements.txt) to see what's playing."
        return _media_keys(action)
    try:
        return _run(lambda: _media(action, app))
    except Exception as e:
        return f"Error: media control failed: {e}"


# --- Volume ----------------------------------------------------------------

def _speakers():
    """The default output device's volume control (Windows Core Audio via pycaw)."""
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetSpeakers().EndpointVolume


@tool
def volume(
    action: Literal["set", "up", "down", "mute", "unmute", "check"],
    amount: int = 10,
) -> str:
    """Control the system volume. Always call this to change or report the volume; never work it out from earlier messages.

    Args:
        action: set (exact level), up, down, mute, unmute, or check (say the current level).
        amount: For set: the target percent (0-100). For up/down: how many percent to change by ("a bit" is about 10, "a lot" about 30).
    """
    if not IS_WINDOWS:
        return "Error: volume control is only supported on Windows."
    try:
        vol = _speakers()
    except Exception as e:
        return f"Error: couldn't access the speakers: {e}"

    current = round(vol.GetMasterVolumeLevelScalar() * 100)

    if action == "check":
        return f"Volume is at {current}%" + (" (muted)." if vol.GetMute() else ".")
    if action == "mute":
        vol.SetMute(1, None)
        return "Muted."
    if action == "unmute":
        vol.SetMute(0, None)
        return f"Unmuted. Volume is at {current}%."

    target = int(amount) if action == "set" else current + (int(amount) if action == "up" else -int(amount))
    target = max(0, min(100, target))
    vol.SetMasterVolumeLevelScalar(target / 100, None)
    if target > 0:
        vol.SetMute(0, None)
    time.sleep(0.02)
    return f"Volume set to {target}%."
