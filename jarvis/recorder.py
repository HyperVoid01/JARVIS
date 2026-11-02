"""Records the spoken command after the wake word.

Decides "speech" vs "silence" with two checks that must both pass:
  1. WebRTC VAD says the chunk sounds like a voice, and
  2. the chunk is clearly louder than the room's background noise.

WebRTC VAD on its own happily calls fan hum or hiss "speech", which keeps
the recording going until the max length. The energy check fixes that. The
background noise level is learned continuously while JARVIS waits for the
wake word, so it adapts to a quiet bedroom or a noisy lab.

    waiting for speech --(speech)--> in speech --(silence > end_silence_s)--> done
          |                              |
     start_timeout_s                max_command_s
          v                              v
      give up (None)                   done
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import webrtcvad

from .audio import AudioSource
from .config import CHUNK_MS, CHUNK_SAMPLES, SAMPLE_RATE

# webrtcvad only accepts 10/20/30 ms frames. 80 ms chunk = 4 x 20 ms frames.
VAD_FRAME_MS = 20
VAD_FRAME_SAMPLES = SAMPLE_RATE * VAD_FRAME_MS // 1000
VAD_FRAMES_PER_CHUNK = CHUNK_SAMPLES // VAD_FRAME_SAMPLES


def rms(chunk: np.ndarray) -> float:
    return float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))


class CommandRecorder:
    def __init__(
        self,
        aggressiveness: int = 2,
        start_timeout_s: float = 5.0,
        end_silence_s: float = 0.9,
        max_command_s: float = 15.0,
        min_speech_s: float = 0.25,
        speech_energy_ratio: float = 3.0,
        min_speech_rms: float = 100.0,
    ):
        self._vad = webrtcvad.Vad(aggressiveness)
        self._start_timeout_chunks = int(start_timeout_s * 1000 / CHUNK_MS)
        self._end_silence_chunks = int(end_silence_s * 1000 / CHUNK_MS)
        self._max_chunks = int(max_command_s * 1000 / CHUNK_MS)
        self._min_speech_chunks = max(1, int(min_speech_s * 1000 / CHUNK_MS))
        self._energy_ratio = speech_energy_ratio
        self._min_rms = min_speech_rms
        self.noise_floor = min_speech_rms / speech_energy_ratio  # sensible start, adapts quickly

    # --- Background noise tracking -------------------------------------
    def observe_background(self, chunk: np.ndarray) -> None:
        """Call with every chunk while idle. Tracks the quiet level of the room.

        Falls quickly toward quieter chunks and rises slowly, so it follows
        the noise floor without being dragged up by people talking nearby.
        """
        level = rms(chunk)
        rate = 0.1 if level < self.noise_floor else 0.005
        self.noise_floor += (level - self.noise_floor) * rate
        self.noise_floor = max(self.noise_floor, 1.0)

    @property
    def speech_threshold(self) -> float:
        return max(self._min_rms, self.noise_floor * self._energy_ratio)

    # --- Speech detection ----------------------------------------------
    def is_speech(self, chunk: np.ndarray) -> bool:
        if rms(chunk) < self.speech_threshold:
            return False
        voiced = 0
        for i in range(VAD_FRAMES_PER_CHUNK):
            frame = chunk[i * VAD_FRAME_SAMPLES:(i + 1) * VAD_FRAME_SAMPLES]
            if self._vad.is_speech(frame.tobytes(), SAMPLE_RATE):
                voiced += 1
        return voiced * 2 >= VAD_FRAMES_PER_CHUNK  # at least half the 20 ms frames voiced

    # --- Recording -----------------------------------------------------
    def record(
        self,
        source: AudioSource,
        on_speech_start: Callable[[], None] | None = None,
    ) -> np.ndarray | None:
        """Pull chunks from `source` until the command ends.

        Returns the recorded int16 audio, or None if nothing was said.
        """
        chunks: list[np.ndarray] = []
        speech_chunks = 0
        silence_run = 0
        started = False
        pre_roll: list[np.ndarray] = []  # keeps the start of a word that VAD caught late

        for n in range(self._start_timeout_chunks + self._max_chunks):
            chunk = source.read()
            if chunk is None:  # source ended (file mode)
                break

            speech = self.is_speech(chunk)

            if not started:
                pre_roll = (pre_roll + [chunk])[-3:]  # last ~240 ms
                if speech:
                    started = True
                    chunks.extend(pre_roll)
                    speech_chunks = 1
                    if on_speech_start:
                        on_speech_start()
                elif n >= self._start_timeout_chunks:
                    return None
                continue

            chunks.append(chunk)
            if speech:
                speech_chunks += 1
                silence_run = 0
            else:
                silence_run += 1
                if silence_run >= self._end_silence_chunks:
                    break
            if len(chunks) >= self._max_chunks:
                break

        if not started or speech_chunks < self._min_speech_chunks:
            return None

        # Trim most of the trailing silence, keep a little so the last word isn't clipped.
        keep_tail = 3
        if silence_run > keep_tail:
            chunks = chunks[: len(chunks) - (silence_run - keep_tail)]
        return np.concatenate(chunks)
