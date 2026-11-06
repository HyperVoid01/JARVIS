"""Notice the user talking over JARVIS, without being fooled by JARVIS's own voice.

The mic hears JARVIS through the speakers, so "is someone speaking?" alone
would make it interrupt itself. But we know exactly how loud JARVIS is
playing at every moment (Speaker.playback_rms), so:

    predicted echo = echo_gain x playback loudness
    user speech    = mic is `margin` times louder than the predicted echo,
                     sounds like a voice (WebRTC VAD), and lasts `min_s`

`echo_gain` (how much of the speaker output reaches the mic) is learned
while JARVIS talks and you don't. It's measured from the first half-second of
the first reply (you can't interrupt during that), then follows the loudest
echo it hears: it rises quickly and falls slowly, so it never under-estimates
its own voice. It's kept between replies. With headphones there's no echo at
all and any speech works.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from .config import CHUNK_MS
from .recorder import CommandRecorder, rms


class InterruptDetector:
    def __init__(self, recorder: CommandRecorder, margin: float = 2.0, min_s: float = 0.3,
                 debug: bool = False):
        self.recorder = recorder   # for its noise floor and voice check
        self.margin = margin
        self.min_chunks = max(1, round(min_s * 1000 / CHUNK_MS))
        self.debug = debug
        self.echo_gain = 0.0       # 0 until measured from the first reply
        self._bootstrap: list[float] = []
        self._speech: list[np.ndarray] = []
        self._misses = 0
        self._voiced = 0           # chunks in _speech that were actually the user's voice
        self._recent: deque[np.ndarray] = deque(maxlen=3)  # audio just before you started

    def reset(self) -> None:
        self._speech = []
        self._misses = 0
        self._voiced = 0
        self._recent.clear()

    def update(self, chunk: np.ndarray, playback_rms: float) -> bool:
        """Feed one mic chunk while JARVIS is talking. True once the user has clearly spoken over it."""
        mic = rms(chunk)
        if not self.echo_gain:
            self._measure(mic, playback_rms)
            return False
        predicted = self.echo_gain * playback_rms
        user = mic > self.margin * predicted and self.recorder.is_speech(chunk)

        if self.debug and (user or mic > self.recorder.speech_threshold):
            print(f"  [interrupt] mic {mic:5.0f}  predicted echo {predicted:5.0f}  "
                  f"gain {self.echo_gain:.2f}  {'YOU' if user else 'echo'}")

        if user:
            if not self._speech:
                self._speech = list(self._recent)  # keep the start of the first word
            self._speech.append(chunk)
            self._voiced += 1
            self._misses = 0
        else:
            self._learn(mic, playback_rms)
            if self._speech:
                self._misses += 1
                if self._misses > 1:  # allow one quiet chunk between syllables
                    self._speech = []
                    self._misses = 0
                    self._voiced = 0
                else:
                    self._speech.append(chunk)  # keep the audio continuous
        self._recent.append(chunk)

        return self._voiced >= self.min_chunks

    def take(self) -> list[np.ndarray]:
        """The user's speech so far, to start the recording with."""
        audio, self._speech = self._speech, []
        self._misses = 0
        self._voiced = 0
        return audio

    def _measure(self, mic: float, playback_rms: float) -> None:
        """First reply: take the loudest echo over ~0.5 s of JARVIS talking as the starting gain."""
        if playback_rms < 200:
            return
        self._bootstrap.append(mic / playback_rms)
        if len(self._bootstrap) >= 6:
            self.echo_gain = min(max(max(self._bootstrap), 0.005), 4.0)
            if self.debug:
                print(f"  [interrupt] measured echo gain {self.echo_gain:.2f}")

    def _learn(self, mic: float, playback_rms: float) -> None:
        if playback_rms < 200:
            return  # JARVIS is between words; nothing to learn from
        ratio = mic / playback_rms
        # Follow the loudest echo: rise quickly towards echoes just above the
        # estimate, so its own voice is never under-estimated, and fall slowly so
        # one quiet moment doesn't lower the bar. Anything far above the estimate
        # is probably you starting to talk, so it's learned slowly or not at all
        # (otherwise your voice would teach it to ignore you).
        if ratio > 2.0 * self.echo_gain:
            return
        if ratio > 1.1 * self.echo_gain:
            rate = 0.01   # e.g. the volume was turned up: adapt, but carefully
        elif ratio > self.echo_gain:
            rate = 0.3
        else:
            rate = 0.005
        self.echo_gain += (ratio - self.echo_gain) * rate
        self.echo_gain = min(max(self.echo_gain, 0.005), 4.0)
