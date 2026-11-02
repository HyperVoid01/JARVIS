"""Speech-to-text with faster-whisper (local Whisper, no API key).

Model size trade-off (rough, English):
    tiny.en   ~75 MB   very fast, makes mistakes
    base.en   ~145 MB  fast, decent
    small.en  ~490 MB  good balance on CPU   <- default
    medium.en ~1.5 GB  better, wants a GPU
    turbo     ~1.6 GB  near large-v3 quality, fast on a GPU
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np


@dataclass
class Transcript:
    text: str
    seconds_of_audio: float
    seconds_to_transcribe: float


class Transcriber:
    def __init__(
        self,
        model: str = "small.en",
        device: str = "auto",
        compute_type: str = "auto",
        language: str | None = "en",
        beam_size: int = 5,
        initial_prompt: str | None = None,
    ):
        self.language = language
        self.beam_size = beam_size
        self.initial_prompt = initial_prompt
        self._model, self.device = _load_model(model, device, compute_type)

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16_000) -> Transcript:
        start = time.perf_counter()
        samples = audio.astype(np.float32) / 32768.0  # int16 -> float32 in [-1, 1]

        segments, _info = self._model.transcribe(
            samples,
            language=self.language,
            beam_size=self.beam_size,
            initial_prompt=self.initial_prompt,
            vad_filter=True,                  # strips silence; reduces "Thank you." hallucinations
            condition_on_previous_text=False,
        )
        # `segments` is a lazy generator - the actual work happens here.
        parts = [s.text.strip() for s in segments if s.no_speech_prob < 0.6]
        text = " ".join(p for p in parts if p).strip()

        return Transcript(
            text=text,
            seconds_of_audio=len(audio) / sample_rate,
            seconds_to_transcribe=time.perf_counter() - start,
        )


def _load_model(model: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    attempts: list[tuple[str, str]] = []
    if device in ("auto", "cuda"):
        attempts.append(("cuda", "float16" if compute_type == "auto" else compute_type))
    if device in ("auto", "cpu"):
        attempts.append(("cpu", "int8" if compute_type == "auto" else compute_type))

    last_error: Exception | None = None
    for dev, ctype in attempts:
        try:
            m = WhisperModel(model, device=dev, compute_type=ctype)
            if dev == "cuda":
                # CUDA libraries can load fine but fail on first use; run a tiny warm-up to be sure.
                list(m.transcribe(np.zeros(16_000, dtype=np.float32), beam_size=1)[0])
            return m, f"{dev}/{ctype}"
        except Exception as e:  # missing CUDA/cuDNN, no GPU, unsupported compute type...
            last_error = e
            if device == "auto":
                print(f"[stt] {dev} unavailable ({type(e).__name__}: {e}); trying next option")
    raise RuntimeError(f"Could not load Whisper model '{model}'") from last_error
