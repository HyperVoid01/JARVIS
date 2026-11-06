"""Text-to-speech with Piper (local neural TTS, runs on the CPU).

    say(text) -> synth thread (Piper, one chunk per sentence, + voice effect) -> audio queue
              -> player thread -> speakers

Synthesis and playback run on separate threads, so the first sentence starts
playing while the rest of the reply is still being generated. `say()` returns
immediately. `stop()` cuts speech off within ~50 ms (used for barge-in).

Voices: https://rhasspy.github.io/piper-samples/
A voice is downloaded into models/voices the first time it's used; after
that it works offline. voice_fx.py adds the JARVIS-style processing.
"""

from __future__ import annotations

import queue
from collections import deque
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from .voice_fx import jarvis_fx

_END = object()         # marks the end of one utterance in the audio queue
_BLOCK_S = 0.05         # playback block length; stop() takes effect within this
_SENTENCE_GAP_S = 0.12  # short pause between sentences so they don't run together


class Speaker:
    def __init__(
        self,
        voice: str = "en_US-danny-low",
        voice_dir: str = "models/voices",
        speed: float = 1.0,
        volume: float = 1.0,
        expressiveness: float = 1.0,
        effect: float = 1.0,
        device: int | str | None = None,
    ):
        from piper import PiperVoice, SynthesisConfig

        model = _ensure_voice(voice, Path(voice_dir))
        self._voice = PiperVoice.load(str(model))
        cfg = self._voice.config
        self._syn = SynthesisConfig(
            # Piper's length_scale is "how long each sound lasts", so speed 1.2 -> 0.83.
            length_scale=cfg.length_scale / max(speed, 0.1),
            # noise_scale is how much the intonation varies. Higher = livelier, too high = wobbly.
            noise_scale=cfg.noise_scale * max(expressiveness, 0.0),
            volume=volume,
        )
        self._effect = effect
        self.voice_name = model.stem

        self._voice_rate = cfg.sample_rate
        self._stream, self._rate = _open_output(device, self._voice_rate)
        # The effect's reverb tail already leaves a pause after each sentence.
        gap_s = _SENTENCE_GAP_S if effect <= 0 else 0.0
        self._gap = np.zeros(int(gap_s * self._rate), np.int16)

        self._texts: queue.Queue = queue.Queue()
        self._audio: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._pending = 0       # utterances queued or playing
        self._gen = 0           # bumped by stop(); anything from an older generation is dropped
        self._released = threading.Event()
        self._released.set()
        self.last_spoke = 0.0   # time.monotonic() when the speakers last went quiet
        self._played: deque[tuple[float, float]] = deque(maxlen=64)  # (time, loudness) per block

        threading.Thread(target=self._synth_loop, daemon=True, name="tts-synth").start()
        threading.Thread(target=self._play_loop, daemon=True, name="tts-play").start()

    # --- Public API ----------------------------------------------------

    def say(self, text: str, sound: np.ndarray | None = None) -> None:
        """Queue text to be spoken. Non-blocking.

        `sound` is an optional chime from sounds.py, played just before the text.
        """
        text = speakable(text)
        if not text and sound is None:
            return
        with self._lock:
            self._pending += 1
            gen = self._gen
        self._texts.put((gen, text, sound))

    @property
    def speaking(self) -> bool:
        return self._pending > 0

    def playback_rms(self, window_s: float = 0.35) -> float:
        """How loud JARVIS has been over the last `window_s` (loudest block, int16 RMS).

        The window covers the speaker buffer and the trip back into the mic, so the
        echo the mic hears now is matched against what was actually playing.
        """
        cutoff = time.monotonic() - window_s
        return max((level for t, level in list(self._played) if t >= cutoff), default=0.0)

    def recently_speaking(self, tail_s: float = 0.3) -> bool:
        """True while speaking and for `tail_s` after, while the last audio leaves the speakers."""
        return self.speaking or time.monotonic() - self.last_spoke < tail_s

    def stop(self) -> None:
        """Stop talking now and forget anything queued."""
        with self._lock:
            self._gen += 1
            self._pending = 0
            self.last_spoke = time.monotonic()
        for q in (self._texts, self._audio):
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

    @contextmanager
    def held(self):
        """Don't play anything inside this block (e.g. while recording a command).

        Speech queued meanwhile (a reply, a timer alert) plays as soon as the block ends.
        """
        self._released.clear()
        try:
            yield
        finally:
            self._released.set()

    def wait(self, timeout: float | None = None) -> None:
        """Block until everything queued has been spoken."""
        end = None if timeout is None else time.monotonic() + timeout
        while self.speaking and (end is None or time.monotonic() < end):
            time.sleep(0.05)

    def close(self) -> None:
        self.stop()
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass

    # --- Worker threads ------------------------------------------------

    def _synth_loop(self) -> None:
        while True:
            gen, text, sound = self._texts.get()
            if gen != self._gen:
                continue
            try:
                if sound is not None:
                    self._audio.put((gen, self._from_chime(sound)))
                if text:
                    for chunk in self._voice.synthesize(text, self._syn):
                        if gen != self._gen:
                            break
                        audio = jarvis_fx(chunk.audio_int16_array, chunk.sample_rate, self._effect)
                        audio = _resample(audio, chunk.sample_rate, self._rate)
                        self._audio.put((gen, np.concatenate([audio, self._gap])))
            except Exception as e:
                print(f"[tts] couldn't speak {text!r}: {e}")
            self._audio.put((gen, _END))

    def _play_loop(self) -> None:
        block = max(1, int(self._rate * _BLOCK_S))
        while True:
            gen, item = self._audio.get()
            if gen != self._gen:
                continue
            if item is _END:
                with self._lock:
                    if gen == self._gen:
                        self._pending = max(0, self._pending - 1)
                        if self._pending == 0:
                            self.last_spoke = time.monotonic()
                continue
            for i in range(0, len(item), block):
                self._released.wait()
                if gen != self._gen:
                    break
                try:
                    piece = item[i:i + block]
                    self._stream.write(piece.reshape(-1, 1))
                    level = float(np.sqrt(np.mean(piece.astype(np.float32) ** 2)))
                    self._played.append((time.monotonic(), level))
                except Exception as e:
                    print(f"[tts] playback failed: {e}")
                    break

    def _from_chime(self, sound: np.ndarray) -> np.ndarray:
        from .sounds import _RATE as chime_rate
        audio = (np.clip(sound, -1, 1) * 32767).astype(np.int16)
        return np.concatenate([_resample(audio, chime_rate, self._rate),
                               np.zeros(int(_SENTENCE_GAP_S * self._rate), np.int16)])


# --- Helpers ---------------------------------------------------------------

def speakable(text: str) -> str:
    """Rewrite the bits of a reply that Piper would read badly."""
    text = re.sub(r"https?://", "", text)
    text = re.sub(r"\bwww\.", "", text)
    text = re.sub(r"(\d)\s*°\s*C\b", r"\1 degrees", text)
    text = text.replace("%", " percent")
    text = text.replace("&", " and ")
    return " ".join(text.split())


def _ensure_voice(voice: str, voice_dir: Path) -> Path:
    """Path to the voice's .onnx file, downloading it the first time."""
    path = Path(voice)
    if path.suffix == ".onnx":
        if not path.exists():
            raise FileNotFoundError(f"Piper voice not found: {path}")
        return path

    model = voice_dir / f"{voice}.onnx"
    if model.exists() and model.with_suffix(".onnx.json").exists():
        return model

    voice_dir.mkdir(parents=True, exist_ok=True)
    print(f"[tts] downloading voice '{voice}' into {voice_dir} (first run only)...")
    from piper.download_voices import download_voice
    try:
        download_voice(voice, voice_dir)
    except Exception as e:
        for p in (model, model.with_suffix(".onnx.json")):
            p.unlink(missing_ok=True)  # don't leave a half-downloaded voice behind
        raise RuntimeError(f"Couldn't download Piper voice '{voice}': {e}") from e
    return model


def _open_output(device, rate: int):
    """Open the speakers at the voice's sample rate, or the device's own rate if it refuses."""
    import sounddevice as sd

    try:
        stream = sd.OutputStream(samplerate=rate, channels=1, dtype="int16",
                                 device=device, latency="low")
        stream.start()
        return stream, rate
    except Exception:
        native = int(sd.query_devices(device, "output")["default_samplerate"])
        stream = sd.OutputStream(samplerate=native, channels=1, dtype="int16",
                                 device=device, latency="low")
        stream.start()
        return stream, native


def _resample(audio: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or len(audio) == 0:
        return audio
    n = int(len(audio) * dst / src)
    x = np.linspace(0, len(audio), n, endpoint=False)
    return np.interp(x, np.arange(len(audio)), audio).astype(np.int16)
