"""The listening loop: wake word -> record -> transcribe -> hand off.

`on_command` is the hook for the next stage. Right now main.py just prints
the text; later it will go to the LLM "brain".
"""

from __future__ import annotations

import os
import re
import time
from typing import Callable

from . import sounds
from .audio import AudioSource, save_wav
from .config import CHUNK_MS, Config
from .recorder import CommandRecorder
from .stt import Transcriber, Transcript
from .wakeword import WakeWordDetector

# The tail of "Hey Jarvis" sometimes leaks into the recording. Strip it off.
_WAKE_PREFIX = re.compile(r"^\s*(hey[\s,]+)?jarvis[\s,.!?]*", re.IGNORECASE)


def clean_command(text: str) -> str:
    return _WAKE_PREFIX.sub("", text).strip()


class VoicePipeline:
    def __init__(
        self,
        config: Config,
        source: AudioSource,
        transcriber: Transcriber | None,
        debug: bool = False,
    ):
        self.config = config
        self.source = source
        self.transcriber = transcriber
        self.debug = debug

        self.wake = WakeWordDetector(
            model=config.wake_model,
            threshold=config.wake_threshold,
            vad_threshold=config.wake_vad_threshold,
        )
        self.recorder = CommandRecorder(
            aggressiveness=config.vad_aggressiveness,
            start_timeout_s=config.start_timeout_s,
            end_silence_s=config.end_silence_s,
            max_command_s=config.max_command_s,
            min_speech_s=config.min_speech_s,
            speech_energy_ratio=config.speech_energy_ratio,
            min_speech_rms=config.min_speech_rms,
        )
        self._cooldown_chunks = int(config.wake_cooldown_s * 1000 / CHUNK_MS)

    def run(self, on_command: Callable[[str, Transcript | None], None]) -> None:
        print(f'Listening for "{self.wake.name}"...  (Ctrl+C to quit)')
        cooldown = 0

        while True:
            chunk = self.source.read()
            if chunk is None:
                return  # end of file in test mode

            self.recorder.observe_background(chunk)  # learn the room's noise level

            if cooldown > 0:
                cooldown -= 1
                continue

            detected = self.wake.process(chunk)
            if self.debug and self.wake.last_score > 0.1:
                print(f"  wake score {self.wake.last_score:.2f}  "
                      f"(noise floor {self.recorder.noise_floor:.0f})")
            if not detected:
                continue

            print(f"\n[wake] heard \"{self.wake.name}\" (score {self.wake.last_score:.2f})")
            if self.config.chime:
                sounds.play(sounds.WAKE)

            audio = self.recorder.record(
                self.source,
                on_speech_start=lambda: print("[rec] recording..."),
            )

            if audio is None:
                print("[rec] didn't hear a command")
                if self.config.chime:
                    sounds.play(sounds.CANCEL)
            else:
                print(f"[rec] got {len(audio) / 16000:.1f}s of audio")
                if self.config.chime:
                    sounds.play(sounds.DONE)
                if self.config.save_recordings:
                    self._save(audio)
                self._handle(audio, on_command)

            # Start fresh: drop audio that queued up while we were busy, and
            # clear the wake model's buffer so it can't re-trigger on old audio.
            self.source.clear()
            self.wake.reset()
            cooldown = self._cooldown_chunks
            print(f'\nListening for "{self.wake.name}"...')

    def _handle(self, audio, on_command) -> None:
        if self.transcriber is None:
            on_command("", None)
            return
        print("[stt] transcribing...")
        result = self.transcriber.transcribe(audio)
        text = clean_command(result.text)
        if not text:
            print("[stt] (couldn't make out any words)")
            return
        on_command(text, result)

    @staticmethod
    def _save(audio) -> None:
        os.makedirs("recordings", exist_ok=True)
        ms = int(time.time() * 1000) % 1000
        path = os.path.join("recordings", f"{time.strftime('%Y%m%d-%H%M%S')}-{ms:03d}.wav")
        save_wav(path, audio)
        print(f"[rec] saved {path}")
