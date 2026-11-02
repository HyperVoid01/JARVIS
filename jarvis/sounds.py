"""Short UI sounds, generated in code so there are no audio files to ship."""

from __future__ import annotations

import numpy as np

_RATE = 44_100


def _tone(freqs: list[float], note_s: float = 0.07, volume: float = 0.25) -> np.ndarray:
    out = []
    for f in freqs:
        t = np.linspace(0, note_s, int(_RATE * note_s), endpoint=False)
        note = np.sin(2 * np.pi * f * t)
        fade = np.minimum(1, np.minimum(t, note_s - t) / 0.01)  # 10 ms fade in/out, no clicks
        out.append(note * fade)
    return (np.concatenate(out) * volume).astype(np.float32)


WAKE = _tone([660, 990])        # rising: "I'm listening"
DONE = _tone([990, 660])        # falling: "got it"
CANCEL = _tone([440], 0.12)     # low: "didn't hear anything"
ALERT = _tone([880, 0, 880, 0, 880], 0.12)  # beep-beep-beep: timer finished


def play(sound: np.ndarray) -> None:
    """Non-blocking playback. Silently does nothing if there's no output device."""
    try:
        import sounddevice as sd
        sd.play(sound, _RATE)
    except Exception:
        pass
