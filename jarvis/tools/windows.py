"""Window and power control: close, quit, switch to, minimize apps; lock, sleep, shut down.

Uses the Windows API directly through ctypes (no extra installs). psutil, which
pycaw already pulls in, is used to find apps that only live in the system tray.

Anything that can lose work asks first (shutdown, restart, sign out, force-quit).
The confirmation is enforced in code: see tools.confirmation().
"""

from __future__ import annotations

import difflib
import os
import re
import subprocess
import sys
import threading
import time
from typing import Literal

from . import confirmation, tool
from .speech_match import plain, speech_key

IS_WINDOWS = sys.platform == "win32"

# What people call an app -> its .exe name (without .exe).
EXE_ALIASES: dict[str, str] = {
    "vs code": "code", "vscode": "code", "visual studio code": "code", "code": "code",
    "visual studio": "devenv",
    "chrome": "chrome", "google chrome": "chrome", "google": "chrome",
    "edge": "msedge", "microsoft edge": "msedge",
    "firefox": "firefox", "brave": "brave", "opera": "opera",
    "file explorer": "explorer", "explorer": "explorer", "files": "explorer", "my files": "explorer",
    "terminal": "windowsterminal", "command prompt": "cmd", "cmd": "cmd", "powershell": "powershell",
    "task manager": "taskmgr", "settings": "systemsettings", "paint": "mspaint",
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook",
    "unity": "unity", "unity hub": "unity hub", "blender": "blender",
    "whatsapp": "whatsapp", "teams": "ms-teams", "steam": "steam", "obs": "obs64",
    "notepad": "notepad", "calculator": "calculatorapp", "spotify": "spotify", "discord": "discord",
}

# .exe name -> how to say it.
FRIENDLY: dict[str, str] = {
    "code": "VS Code", "devenv": "Visual Studio", "chrome": "Chrome", "msedge": "Edge",
    "explorer": "File Explorer", "windowsterminal": "Terminal", "cmd": "Command Prompt",
    "taskmgr": "Task Manager", "systemsettings": "Settings", "mspaint": "Paint",
    "winword": "Word", "powerpnt": "PowerPoint", "ms-teams": "Teams", "obs64": "OBS",
    "calculatorapp": "Calculator", "applicationframehost": "an app",
    "githubdesktop": "GitHub Desktop", "unity hub": "Unity Hub", "obsidian": "Obsidian",
    "epicgameslauncher": "Epic Games", "steamwebhelper": "Steam", "notepad": "Notepad",
}

# Never force-quit these: Windows itself, or JARVIS's own helpers.
PROTECTED = {
    "explorer", "dwm", "csrss", "winlogon", "wininit", "lsass", "services", "svchost",
    "smss", "system", "registry", "fontdrvhost", "sihost", "ctfmon", "taskhostw",
    "searchhost", "startmenuexperiencehost", "shellexperiencehost", "runtimebroker",
    "conhost", "python", "pythonw", "ollama", "ollama app", "audiodg",
}

# Quitting these can't lose anything, so they don't need a "are you sure?".
SAFE_TO_QUIT = {
    "spotify", "discord", "steam", "whatsapp", "ms-teams", "calculatorapp", "vlc",
    "epicgameslauncher", "battle.net", "riotclientservices", "obs64", "taskmgr",
}

# Windows that exist but aren't apps the user means.
_IGNORE_TITLES = {"program manager", "windows input experience", "microsoft text input application"}


# --- Windows API ----------------------------------------------------------

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _dwmapi = ctypes.windll.dwmapi
    _ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    class _RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

_WM_CLOSE = 0x0010
_SW_MAXIMIZE, _SW_MINIMIZE, _SW_RESTORE = 3, 6, 9
_GW_OWNER = 4
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_APPWINDOW = 0x00040000
_DWMWA_CLOAKED = 14
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_VK_MENU, _VK_LWIN, _VK_D = 0x12, 0x5B, 0x44
_KEYUP = 0x0002


def _exe_name(pid: int) -> str:
    """'discord' for Discord.exe, or '' if Windows won't say."""
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return os.path.splitext(os.path.basename(buf.value))[0].lower()
    finally:
        _kernel32.CloseHandle(handle)


def _is_cloaked(hwnd) -> bool:
    """Hidden UWP windows (e.g. a suspended Settings app) report as visible but aren't."""
    cloaked = ctypes.c_int(0)
    _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return bool(cloaked.value)


def _is_app_window(hwnd) -> bool:
    """Roughly "would this show up in Alt+Tab": skips tool windows and invisible helper windows
    that vendor utilities (graphics, audio, RGB...) keep open."""
    ex = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    if ex & _WS_EX_TOOLWINDOW and not ex & _WS_EX_APPWINDOW:
        return False
    rect = _RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if not _user32.IsIconic(hwnd) and (width < 50 or height < 50):
        return False  # tiny or zero-size: a hidden helper, not something you look at
    return True


def _open_windows() -> list[tuple[int, str, int, str]]:
    """[(hwnd, title, pid, exe)] for every real, visible app window."""
    found: list[tuple[int, str, int, str]] = []
    own_console = _kernel32.GetConsoleWindow()

    def visit(hwnd, _):
        if not _user32.IsWindowVisible(hwnd) or _user32.GetWindow(hwnd, _GW_OWNER):
            return True
        if hwnd == own_console or _is_cloaked(hwnd) or not _is_app_window(hwnd):
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if title.lower() in _IGNORE_TITLES:
            return True
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append((hwnd, title, pid.value, _exe_name(pid.value)))
        return True

    _user32.EnumWindows(_ENUM_PROC(visit), 0)
    return found


def _press(*keys: int) -> None:
    for k in keys:
        _user32.keybd_event(k, 0, 0, 0)
    for k in reversed(keys):
        _user32.keybd_event(k, 0, _KEYUP, 0)


def _bring_to_front(hwnd) -> None:
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, _SW_RESTORE)
    # Windows only lets the app that last had input steal focus; tapping Alt counts as input.
    _press(_VK_MENU)
    _user32.SetForegroundWindow(hwnd)


# --- Matching what was said to an open app ---------------------------------

def _friendly(exe: str) -> str:
    return FRIENDLY.get(exe, exe.replace("_", " ").title() if exe else "that app")


def _app_names(windows) -> str:
    """'Chrome, Discord, WhatsApp' for a list of windows (Store apps by their title)."""
    names = {w[1] if w[3] == "applicationframehost" else _friendly(w[3]) for w in windows if w[3]}
    return ", ".join(sorted(names)) or "none"


def _match(app: str, windows: list[tuple[int, str, int, str]]):
    """(exe, [matching windows]) for the app the user named, or (None, [])."""
    spoken = re.sub(r"\b(the|my|app|application|window|program)\b", " ", app.lower())
    spoken = " ".join(spoken.split())
    if not spoken:
        return None, []
    target = EXE_ALIASES.get(spoken, spoken)
    exes = sorted({w[3] for w in windows if w[3]})

    def by_exe(exe):
        return exe, [w for w in windows if w[3] == exe]

    want = plain(target)
    for exe in exes:                                   # exact: "discord" == discord.exe
        if plain(exe) == want:
            return by_exe(exe)
    for exe in exes:                                   # partial: "unity" ~ unity.exe, "obs" ~ obs64
        if len(want) >= 3 and (want in plain(exe) or plain(exe) in want):
            return by_exe(exe)
    for exe in exes:                                   # by sound: "disgord" ~ discord
        if speech_key(exe) == speech_key(target):
            return by_exe(exe)
    titled = [w for w in windows if len(want) >= 3 and want in plain(w[1])]
    if titled:                                         # in the title: "Rat Attack" in a Unity window
        return titled[0][3], titled
    close = difflib.get_close_matches(want, [plain(e) for e in exes], n=1, cutoff=0.75)
    if close:
        return by_exe(next(e for e in exes if plain(e) == close[0]))
    return None, []


def _not_open(app: str, windows) -> str:
    return f"Error: {app} doesn't seem to be open. Open apps: {_app_names(windows)}."


def _processes_named(exe: str) -> list[int]:
    """Every process with this .exe name, including ones hiding in the tray."""
    try:
        import psutil
    except ImportError:
        return []
    pids = []
    for p in psutil.process_iter(["pid", "name"]):
        name = os.path.splitext(p.info.get("name") or "")[0].lower()
        if name == exe and p.info["pid"] != os.getpid():
            pids.append(p.info["pid"])
    return pids


def _force_quit(exe: str) -> int:
    import psutil
    killed = 0
    for pid in _processes_named(exe):
        try:
            psutil.Process(pid).kill()
            killed += 1
        except Exception:
            pass
    return killed


# --- Tools -----------------------------------------------------------------

@tool
def window(
    action: Literal["close", "quit", "focus", "minimize", "maximize", "show_desktop", "list"],
    app: str = "",
    confirmed: bool = False,
) -> str:
    """Control open apps: close one, force-quit it, switch to it, minimize or maximize it, show the desktop, or list what's open.

    Args:
        action: close (normal close; the app may ask to save), quit (force it to exit, even from the system tray), focus (switch to it), minimize, maximize, show_desktop, or list.
        app: The app as the user said it, e.g. "Discord", "Chrome", "VS Code". Not needed for show_desktop or list.
        confirmed: Only true after the user said yes to a confirmation question from this tool.
    """
    if not IS_WINDOWS:
        return "Error: window control is only supported on Windows."

    if action == "show_desktop":
        _press(_VK_LWIN, _VK_D)
        return "Showing the desktop."

    windows = _open_windows()
    if action == "list":
        return f"Open apps: {_app_names(windows)}."

    if not app.strip():
        return f"Error: which app should I {action}?"

    exe, matches = _match(app, windows)

    if action == "quit":
        if exe is None:  # maybe it's only in the tray, with no window
            target = EXE_ALIASES.get(app.lower().strip(), plain(app))
            exe = target if _processes_named(target) else None
        if exe is None:
            return _not_open(app, windows)
        if exe in PROTECTED:
            return f"Error: I won't force-quit {_friendly(exe)}; Windows needs it."
        if exe not in SAFE_TO_QUIT:
            ask = confirmation(f"quit:{exe}", f"Force-quit {_friendly(exe)}? Unsaved work will be lost.", confirmed)
            if ask:
                return ask
        killed = _force_quit(exe)
        return f"Quit {_friendly(exe)}." if killed else f"Error: Windows wouldn't let me quit {_friendly(exe)}."

    if not matches:
        return _not_open(app, windows)
    # Store apps (WhatsApp, Settings...) all run under one host process; use their title instead.
    name = matches[0][1] if exe == "applicationframehost" else _friendly(exe)

    if action == "close":
        for hwnd, *_ in matches:
            _user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
        if exe in SAFE_TO_QUIT:
            # Some apps exit on close, others hide in the tray. Check which this one did.
            time.sleep(1.0)
            if _processes_named(exe):
                return f"Closed {name}'s window, but it's still running in the tray. Say quit {name} to exit it fully."
        return f"Closed {name}."
    if action == "focus":
        _bring_to_front(matches[0][0])
        return f"Switched to {name}."
    if action == "minimize":
        for hwnd, *_ in matches:
            _user32.ShowWindow(hwnd, _SW_MINIMIZE)
        return f"Minimized {name}."
    if action == "maximize":
        _bring_to_front(matches[0][0])
        _user32.ShowWindow(matches[0][0], _SW_MAXIMIZE)
        return f"Maximized {name}."
    return f"Error: unknown action {action}."


_SHUTDOWN_DELAY_S = 30


@tool
def power(
    action: Literal["lock", "sleep", "shutdown", "restart", "sign_out", "cancel_shutdown"],
    confirmed: bool = False,
) -> str:
    """Lock, sleep, shut down, restart or sign out of the PC, or cancel a pending shutdown.

    Args:
        action: lock, sleep, shutdown, restart, sign_out, or cancel_shutdown.
        confirmed: Only true after the user said yes to a confirmation question from this tool.
    """
    if not IS_WINDOWS:
        return "Error: power control is only supported on Windows."

    if action == "cancel_shutdown":
        r = subprocess.run(["shutdown", "/a"], capture_output=True, text=True)
        return "Cancelled the shutdown." if r.returncode == 0 else "There was no shutdown to cancel."

    if action in ("shutdown", "restart", "sign_out"):
        question = {"shutdown": "Shut down the PC?", "restart": "Restart the PC?",
                    "sign_out": "Sign out of Windows? Open apps will close."}[action]
        ask = confirmation(f"power:{action}", question, confirmed)
        if ask:
            return ask
        if action == "sign_out":
            _later(3, lambda: subprocess.run(["shutdown", "/l"]))
            return "Signing out in a few seconds."
        flag = "/s" if action == "shutdown" else "/r"
        subprocess.run(["shutdown", flag, "/t", str(_SHUTDOWN_DELAY_S)])
        verb = "Shutting down" if action == "shutdown" else "Restarting"
        return f"{verb} in {_SHUTDOWN_DELAY_S} seconds. Say cancel the shutdown to stop it."

    # Lock and sleep happen a moment later, so the spoken reply isn't cut off.
    if action == "lock":
        _later(2, _user32.LockWorkStation)
        return "Locking the PC."
    if action == "sleep":
        _later(3, lambda: ctypes.windll.powrprof.SetSuspendState(0, 1, 0))
        return "Putting the PC to sleep."
    return f"Error: unknown action {action}."


def _later(seconds: float, fn) -> None:
    t = threading.Timer(seconds, fn)
    t.daemon = True
    t.start()
