"""The listening loop: wake word -> record -> transcribe -> hand off -> speak.

`on_command` gets the transcribed text. main.py sends it to the brain and
hands the reply to the Speaker, which talks on its own threads while this
loop keeps listening.

While JARVIS is talking, the mic hears its voice. To stop it reacting to
itself:
  - the noise floor isn't updated (its voice would raise it),
  - the wake word only counts above `barge_in_threshold` (so "Hey Jarvis"
    can still interrupt it, but its own speech can't),
  - when it stops talking, any audio queued up meanwhile is thrown away.
"""

from __future__ import annotations

import os
import re
import time
from contextlib import nullcontext
from typing import TYPE_CHECKING, Callable

from . import sounds
from .audio import AudioSource, save_wav
from .config import CHUNK_MS, Config
from .recorder import CommandRecorder
from .stt import Transcriber, Transcript
from .wakeword import WakeWordDetector

if TYPE_CHECKING:
    from .tts import Speaker

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
        speaker: Speaker | None = None,
        debug: bool = False,
    ):
        self.config = config
        self.source = source
        self.transcriber = transcriber
        self.speaker = speaker
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

    def _talking(self) -> bool:
        return self.speaker is not None and self.speaker.recently_speaking(self.config.echo_tail_s)

    def run(self, on_command: Callable[[str, Transcript | None], None]) -> None:
        print(f'Listening for "{self.wake.name}"...  (Ctrl+C to quit)')
        cooldown = 0
        was_talking = False

        while True:
            chunk = self.source.read()
            if chunk is None:
                return  # end of file in test mode

            talking = self._talking()
            if was_talking and not talking:
                # JARVIS just finished speaking: drop the echo of its own voice.
                self.source.clear()
                self.wake.reset()
                was_talking = False
                continue
            was_talking = talking

            if not talking:
                self.recorder.observe_background(chunk)  # learn the room's noise level

            if cooldown > 0:
                cooldown -= 1
                continue

            if talking:
                if not self.config.barge_in:
                    continue
                self.wake.process(chunk)
                if self.wake.last_score < self.config.barge_in_threshold:
                    continue
                self.speaker.stop()
                print(f"\n[wake] interrupted (score {self.wake.last_score:.2f})")
            else:
                detected = self.wake.process(chunk)
                if self.debug and self.wake.last_score > 0.1:
                    print(f"  wake score {self.wake.last_score:.2f}  "
                          f"(noise floor {self.recorder.noise_floor:.0f})")
                if not detected:
                    continue
                print(f"\n[wake] heard \"{self.wake.name}\" (score {self.wake.last_score:.2f})")

            self._listen_and_handle(on_command)

            # Start fresh: drop audio that queued up while we were busy, and
            # clear the wake model's buffer so it can't re-trigger on old audio.
            self.source.clear()
            self.wake.reset()
            cooldown = self._cooldown_chunks
            was_talking = self._talking()
            print(f'\nListening for "{self.wake.name}"...')

    def _listen_and_handle(self, on_command) -> None:
        if self.config.chime:
            sounds.play(sounds.WAKE)

        # Nothing plays while recording, so a timer going off can't end up in the
        # command. The reply queued by on_command starts as soon as this block ends.
        with self.speaker.held() if self.speaker else nullcontext():
            audio = self.recorder.record(
                self.source,
                on_speech_start=lambda: print("[rec] recording..."),
            )

            if audio is None:
                print("[rec] didn't hear a command")
                if self.config.chime:
                    sounds.play(sounds.CANCEL)
                return

            print(f"[rec] got {len(audio) / 16000:.1f}s of audio")
            if self.config.chime:
                sounds.play(sounds.DONE)
            if self.config.save_recordings:
                self._save(audio)
            self._handle(audio, on_command)

    def _handle(self, audio, on_command) -> None:
        if self.transcriber is None:
            on_command("", None)
            return
        print("[stt] transcribing...")
        result = self.transcriber.transcribe(audio)
        text = clean_command(result.text)
        if not text:
            print("[stt] (couldn't make out any words)")
            if self.config.chime:
                sounds.play(sounds.CANCEL)
            return
        on_command(text, result)

    @staticmethod
    def _save(audio) -> None:
        os.makedirs("recordings", exist_ok=True)
        ms = int(time.time() * 1000) % 1000
        path = os.path.join("recordings", f"{time.strftime('%Y%m%d-%H%M%S')}-{ms:03d}.wav")
        save_wav(path, audio)
        print(f"[rec] saved {path}")
