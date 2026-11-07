"""Running without a console window: log file, and only one JARVIS at a time.

Started by Windows at login (see main.py --install-startup), JARVIS runs under
pythonw.exe, which has no console: print() would have nowhere to go. Output is
written to jarvis.log in the project folder instead, with a time on each line.

Two copies listening at once would both answer "Hey Jarvis", so a second copy
waits briefly for the first to exit (a restart) and otherwise quits.
"""

from __future__ import annotations

import datetime as dt
import io
import sys
import time
from pathlib import Path

LOG_MAX_BYTES = 2_000_000  # past this, jarvis.log becomes jarvis.old.log and a new one starts


class _TimestampedLog(io.TextIOBase):
    """A text stream that puts "HH:MM:SS " at the start of every line."""

    def __init__(self, f):
        self._f = f
        self._line_start = True

    def write(self, s: str) -> int:
        out = []
        for part in s.splitlines(keepends=True):
            if self._line_start and part.strip():
                out.append(dt.datetime.now().strftime("%H:%M:%S "))
            out.append(part)
            self._line_start = part.endswith("\n")
        self._f.write("".join(out))
        self._f.flush()
        return len(s)

    def flush(self) -> None:
        self._f.flush()

    @property
    def encoding(self):
        return "utf-8"

    def isatty(self) -> bool:
        return False


def log_to_file(path: Path) -> Path:
    """Send print() and errors to `path` (rotated at LOG_MAX_BYTES). Returns the path."""
    try:
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            old = path.with_name(path.stem + ".old" + path.suffix)
            old.unlink(missing_ok=True)
            path.rename(old)
    except OSError:
        pass
    f = open(path, "a", encoding="utf-8", buffering=1, errors="replace")
    stream = _TimestampedLog(f)
    sys.stdout = sys.stderr = stream
    print(f"\n===== JARVIS started {dt.datetime.now():%A %d %B %Y, %H:%M} =====")
    return path


def has_console() -> bool:
    """False under pythonw.exe, where print() has nowhere to go. (Output redirected to a file is fine.)"""
    return sys.stdout is not None


# --- One JARVIS at a time ------------------------------------------------------

_lock = None  # kept for the life of the process; the OS releases it when we exit


def single_instance(wait_s: float = 0) -> bool:
    """True if this is the only JARVIS running (waiting up to `wait_s` for another to exit)."""
    global _lock
    deadline = time.time() + wait_s
    while True:
        if _try_lock():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


def _try_lock() -> bool:
    global _lock
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, "Local\\JARVIS-voice-assistant")
        if not handle:
            return True  # can't tell; don't block startup over it
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            return False
        _lock = handle
        return True
    import fcntl
    import tempfile
    f = open(Path(tempfile.gettempdir()) / "jarvis.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    _lock = f
    return True


def release_instance() -> None:
    """Let a restarted JARVIS take over right away (otherwise it's released on exit)."""
    global _lock
    if _lock is None:
        return
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(_lock))
    else:
        _lock.close()
    _lock = None
