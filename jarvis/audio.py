"""Audio input sources.

Both sources hand out the same thing: 80 ms chunks of 16 kHz mono int16
audio as numpy arrays. The rest of the pipeline never needs to know whether
it is hearing a live microphone or a test file.
"""

from __future__ import annotations

import queue
import time
import wave
from typing import Protocol

import numpy as np

from .config import CHUNK_SAMPLES, SAMPLE_RATE


class AudioSource(Protocol):
    def read(self) -> np.ndarray | None:
        """Block until the next chunk is ready. Returns None when the source ends."""

    def clear(self) -> None:
        """Throw away any audio buffered while we were busy (e.g. transcribing)."""

    def close(self) -> None: ...


class MicStream:
    """Live microphone input via sounddevice/PortAudio.

    PortAudio calls `_callback` on its own thread every 80 ms; we push the
    chunk into a queue and the main loop pulls from it with `read()`.
    """

    def __init__(self, device: int | str | None = None):
        import sounddevice as sd  # imported here so file mode works without a sound card

        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=500)  # ~40 s buffer
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=CHUNK_SAMPLES,
            device=device,
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata, frames, time_info, status):  # runs on the audio thread
        if status:
            print(f"[audio] {status}")
        try:
            self._queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass  # main loop has stalled; drop audio rather than block the audio thread

    def read(self) -> np.ndarray | None:
        # Wait in short slices: a plain blocking get() swallows Ctrl+C on Windows.
        while True:
            try:
                return self._queue.get(timeout=0.25)
            except queue.Empty:
                continue

    def clear(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()


class FileStream:
    """Plays a WAV file through the pipeline as if it were a microphone.

    Handy for testing tweaks without talking to your PC every time. Any
    sample rate / channel count is accepted and converted to 16 kHz mono.
    Set `realtime=True` to pace the chunks at the real 80 ms interval.
    """

    def __init__(self, path: str, realtime: bool = False, pad_silence_s: float = 2.0):
        self._audio = _load_wav_16k_mono(path)
        # Trailing silence so the recorder can detect the end of the last command.
        self._audio = np.concatenate([self._audio, np.zeros(int(pad_silence_s * SAMPLE_RATE), np.int16)])
        self._pos = 0
        self._realtime = realtime

    def read(self) -> np.ndarray | None:
        if self._pos + CHUNK_SAMPLES > len(self._audio):
            return None
        chunk = self._audio[self._pos:self._pos + CHUNK_SAMPLES]
        self._pos += CHUNK_SAMPLES
        if self._realtime:
            time.sleep(CHUNK_SAMPLES / SAMPLE_RATE)
        return chunk

    def clear(self) -> None:
        pass  # a file never builds up a backlog

    def close(self) -> None:
        pass

    @property
    def position_s(self) -> float:
        return self._pos / SAMPLE_RATE


def _load_wav_16k_mono(path: str) -> np.ndarray:
    with wave.open(path, "rb") as wf:
        if wf.getsampwidth() != 2:
            raise ValueError(f"{path}: only 16-bit PCM WAV files are supported")
        rate = wf.getframerate()
        channels = wf.getnchannels()
        data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)

    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1).astype(np.int16)

    if rate != SAMPLE_RATE:
        # Linear-interpolation resample. Fine for speech testing.
        duration = len(data) / rate
        new_len = int(duration * SAMPLE_RATE)
        old_t = np.linspace(0, duration, num=len(data), endpoint=False)
        new_t = np.linspace(0, duration, num=new_len, endpoint=False)
        data = np.interp(new_t, old_t, data).astype(np.int16)

    return data


def save_wav(path: str, audio: np.ndarray) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.astype(np.int16).tobytes())
