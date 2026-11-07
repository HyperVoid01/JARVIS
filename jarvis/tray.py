"""System tray icon for JARVIS running in the background.

    Pause listening / Resume listening   (the mic is ignored while paused)
    Open log
    Open JARVIS folder
    Restart
    Quit

The icon is a small ring like the HUD's: cyan while listening, grey while paused.
Needs pystray and Pillow (in requirements.txt); without them JARVIS runs fine,
just without the icon.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable


def _icon(active: bool):
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ring = (79, 243, 255, 255) if active else (110, 125, 130, 255)
    glow = (42, 143, 163, 140) if active else (70, 80, 85, 120)
    d.ellipse((4, 4, 60, 60), outline=glow, width=8)
    d.ellipse((10, 10, 54, 54), outline=ring, width=5)
    d.ellipse((26, 26, 38, 38), fill=ring)
    return img


class Tray:
    def __init__(
        self,
        on_pause: Callable[[bool], None],
        on_quit: Callable[[], None],
        on_restart: Callable[[], None],
        log_path: Path | None,
        project_dir: Path,
    ):
        self._on_pause = on_pause
        self._on_quit = on_quit
        self._on_restart = on_restart
        self._log_path = log_path
        self._project_dir = project_dir
        self.paused = False
        self._icon_obj = None

    def start(self) -> "Tray":
        try:
            import pystray
        except ImportError:
            print("[tray] pystray isn't installed, so there's no tray icon (pip install -r requirements.txt)")
            return self
        try:
            menu = pystray.Menu(
                pystray.MenuItem("JARVIS", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(lambda item: "Resume listening" if self.paused else "Pause listening",
                                 self._toggle, default=True),
                pystray.MenuItem("Open log", self._open_log, enabled=lambda item: bool(self._log_path)),
                pystray.MenuItem("Open JARVIS folder", lambda: _open(self._project_dir)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Restart", lambda: self._on_restart()),
                pystray.MenuItem("Quit", lambda: self._on_quit()),
            )
            self._icon_obj = pystray.Icon("JARVIS", _icon(True), "JARVIS: listening", menu)
            self._icon_obj.run_detached()
        except Exception as e:  # no tray on this desktop, missing Pillow...
            print(f"[tray] couldn't show the tray icon: {e}")
            self._icon_obj = None
        return self

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        if self._icon_obj:
            self._icon_obj.icon = _icon(not paused)
            self._icon_obj.title = "JARVIS: paused" if paused else "JARVIS: listening"
            self._icon_obj.update_menu()

    def stop(self) -> None:
        if self._icon_obj:
            try:
                self._icon_obj.stop()
            except Exception:
                pass
            self._icon_obj = None

    def _toggle(self, *_):
        self.set_paused(not self.paused)
        self._on_pause(self.paused)

    def _open_log(self, *_):
        if self._log_path:
            _open(self._log_path)


def _open(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])
