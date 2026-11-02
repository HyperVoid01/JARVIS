"""Wake word detection with openWakeWord.

Ships with a pre-trained "hey_jarvis" model. You can also train your own
wake phrase and pass the path to its .onnx file as `wake_model`.
"""

from __future__ import annotations

import os

import numpy as np


class WakeWordDetector:
    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.5, vad_threshold: float = 0.0):
        from openwakeword.model import Model
        from openwakeword.utils import download_models

        is_custom_file = model.endswith((".onnx", ".tflite")) and os.path.exists(model)
        if not is_custom_file:
            # Downloads the model once into the openwakeword package folder; no-op afterwards.
            download_models(model_names=[model])

        # ONNX runs everywhere (tflite has no Windows build), so always use it.
        self._model = Model(
            wakeword_models=[model],
            inference_framework="onnx",
            vad_threshold=vad_threshold,
        )
        self.name = next(iter(self._model.models))
        self.threshold = threshold
        self.last_score = 0.0

    def process(self, chunk: np.ndarray) -> bool:
        """Feed one 80 ms chunk. Returns True when the wake word is detected."""
        scores = self._model.predict(chunk)
        self.last_score = float(scores[self.name])
        return self.last_score >= self.threshold

    def reset(self) -> None:
        """Clear the model's rolling audio buffer so the same 'Hey Jarvis' can't fire twice."""
        self._model.reset()
