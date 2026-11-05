"""Turn other apps down while JARVIS listens and talks ("ducking").

Uses each app's own volume slider (the ones in Windows' Volume Mixer), not
the master volume, so music gets quieter but JARVIS's own voice doesn't.

    wake word -> duck() -> you talk, JARVIS replies -> restore() (fades back up)

If you move an app's volume yourself while it's ducked, restore() leaves
that app alone instead of overwriting your change.
"""

from __future__ import annotations

import atexit
import os
import sys
import time

_FADE_S = 0.35
_FADE_STEPS = 7


class Ducker:
    def __init__(self, level: float = 0.2):
        self.level = max(0.0, min(1.0, level))
        self._ducked: list[tuple[object, float, float]] = []  # (volume control, original, ducked)
        self.available = sys.platform == "win32"
        if self.available:
            try:
                from pycaw.pycaw import AudioUtilities  # noqa: F401
            except Exception:
                self.available = False
        atexit.register(self.restore, fade=False)  # never leave Spotify stuck quiet

    @property
    def active(self) -> bool:
        return bool(self._ducked)

    def duck(self) -> None:
        """Lower every other app to `level` x its current volume. Safe to call twice."""
        if not self.available or self._ducked:
            return
        for control in _other_app_volumes():
            try:
                original = control.GetMasterVolume()
                if original <= 0.01:
                    continue  # already silent; nothing to duck
                ducked = original * self.level
                control.SetMasterVolume(ducked, None)
                self._ducked.append((control, original, ducked))
            except Exception:
                pass  # the app closed or refused; skip it

    def restore(self, fade: bool = True) -> None:
        """Put every ducked app back where it was."""
        if not self._ducked:
            return
        ducked, self._ducked = self._ducked, []

        # Skip apps whose volume the user changed while ducked.
        targets = []
        for control, original, low in ducked:
            try:
                if abs(control.GetMasterVolume() - low) < 0.02:
                    targets.append((control, original, low))
            except Exception:
                pass

        steps = _FADE_STEPS if fade else 1
        for i in range(1, steps + 1):
            t = i / steps
            for control, original, low in targets:
                try:
                    control.SetMasterVolume(low + (original - low) * t, None)
                except Exception:
                    pass
            if i < steps:
                time.sleep(_FADE_S / steps)


def _other_app_volumes():
    """Volume controls for every audio session except this process (JARVIS itself)."""
    from pycaw.pycaw import AudioUtilities

    me = os.getpid()
    for session in AudioUtilities.GetAllSessions():
        if getattr(session, "ProcessId", None) == me:
            continue
        control = getattr(session, "SimpleAudioVolume", None)
        if control is not None:
            yield control
