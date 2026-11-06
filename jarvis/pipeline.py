"""The listening loop: wake word -> record -> transcribe -> hand off -> speak.

`on_command` gets the transcribed text. main.py sends it to the brain and
hands the reply to the Speaker, which talks on its own threads while this
loop keeps listening.

Follow-ups: after JARVIS replies, a soft tick plays and it listens for
`follow_up_s` without needing the wake word. Each answer opens another
window, so you can keep talking. Silence, or "thanks" / "that's all",
closes it.

Interrupting: while JARVIS talks you can speak over it (interrupt.py tells
your voice apart from its own echo), or say "Hey Jarvis". Either way it
stops mid-sentence and listens.

While JARVIS is talking, the mic hears its voice. To stop it reacting to
itself:
  - the noise floor isn't updated (its voice would raise it),
  - speech only counts as an interruption when it's well above the echo
    JARVIS expects from its own playback,
  - the wake word only counts above `barge_in_threshold`,
  - when it stops talking, any audio queued up meanwhile is thrown away.

Other apps (music, videos) are turned down from the wake word until the
conversation ends, so Whisper hears you and you hear the answer.
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
from .interrupt import InterruptDetector
from .recorder import CommandRecorder
from .stt import Transcriber, Transcript
from .wakeword import WakeWordDetector

if TYPE_CHECKING:
    import numpy as np

    from .ducking import Ducker
    from .tts import Speaker

# The tail of "Hey Jarvis" sometimes leaks into the recording. Strip it off.
_WAKE_PREFIX = re.compile(r"^\s*(hey[\s,]+)?jarvis[\s,.!?]*", re.IGNORECASE)

# Said in a follow-up window, these end the conversation instead of going to the brain.
# Also catches Whisper's habit of "hearing" "Thank you." in near-silence.
# "Cancel" and "stop" are deliberately NOT here: after "shutting down in 30 seconds"
# they have to reach the brain so the shutdown actually gets cancelled.
_DISMISS_PHRASE = (
    r"(no|nope|nah|no thanks?|no thank you|thanks?|thank you|thanks a lot|cheers|"
    r"that'?s (all|it|everything)|that is all|nothing|nothing else|never ?mind|"
    r"ok(ay)?|alright|right|cool|great|perfect|got it|good|all good|bye|goodbye|"
    r"for now|jarvis|sir|mate)"
)
# One or more of those in a row: "okay, that's all", "no thanks Jarvis", "great, thank you".
_DISMISS = re.compile(rf"^{_DISMISS_PHRASE}( {_DISMISS_PHRASE})*$", re.IGNORECASE)


def clean_command(text: str) -> str:
    return _WAKE_PREFIX.sub("", text).strip()


def is_dismissal(text: str) -> bool:
    words = re.sub(r"[^\w' ]", " ", text.lower())
    return bool(_DISMISS.match(" ".join(words.split())))


class VoicePipeline:
    def __init__(
        self,
        config: Config,
        source: AudioSource,
        transcriber: Transcriber | None,
        speaker: Speaker | None = None,
        ducker: Ducker | None = None,
        hud=None,
        debug: bool = False,
    ):
        self.config = config
        self.source = source
        self.transcriber = transcriber
        self.speaker = speaker
        self.ducker = ducker
        if hud is None:
            from .hud import NullHud
            hud = NullHud()
        self.hud = hud
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
        self.interrupter = None
        if speaker is not None and config.interrupt_on_speech:
            self.interrupter = InterruptDetector(
                self.recorder,
                margin=config.interrupt_margin,
                min_s=config.interrupt_min_s,
                debug=debug,
            )
        self._cooldown_chunks = int(config.wake_cooldown_s * 1000 / CHUNK_MS)
        # MicStream bumps this when the mic is switched; FileStream doesn't have it.
        self._source_generation = getattr(source, "generation", 0)

    def _talking(self) -> bool:
        return self.speaker is not None and self.speaker.recently_speaking(self.config.echo_tail_s)

    def run(self, on_command: Callable[[str, Transcript | None], None]) -> None:
        print(f'Listening for "{self.wake.name}"...  (Ctrl+C to quit)')
        self.hud.state("idle")
        cooldown = 0
        was_talking = False
        follow_up = False  # a command was just handled; listen again once the reply ends

        try:
            while True:
                chunk = self.source.read()
                if chunk is None:
                    return  # end of file in test mode

                talking = self._talking()

                # Reply finished: listen for a follow-up without the wake word.
                if follow_up and not talking:
                    follow_up = self._listen_and_handle(on_command, follow_up=True)
                    cooldown = self._reset()
                    was_talking = self._talking()
                    continue

                # Conversation over: bring the music back up.
                if self.ducker and self.ducker.active and not talking:
                    self.ducker.restore()

                if was_talking and not talking:
                    # JARVIS just finished speaking: drop the echo of its own voice.
                    self.source.clear()
                    self.wake.reset()
                    if self.interrupter:
                        self.interrupter.reset()
                    was_talking = False
                    continue
                was_talking = talking

                # Talked over: stop mid-sentence and record what the user is saying.
                if talking and self.interrupter and self.speaker.speaking:
                    if self.interrupter.update(chunk, self.speaker.playback_rms()):
                        speech = self.interrupter.take()
                        self.speaker.stop()
                        self.hud.state("listening")
                        print("\n[interrupt] you spoke over JARVIS; listening")
                        follow_up = self._listen_and_handle(on_command, preroll=speech)
                        self.interrupter.reset()
                        cooldown = self._reset()
                        was_talking = self._talking()
                        continue

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
                    if self.interrupter:
                        self.interrupter.reset()
                    print(f"\n[wake] interrupted (score {self.wake.last_score:.2f})")
                    self.hud.state("listening")
                else:
                    detected = self.wake.process(chunk)
                    if self.debug and self.wake.last_score > 0.1:
                        print(f"  wake score {self.wake.last_score:.2f}  "
                              f"(noise floor {self.recorder.noise_floor:.0f})")
                    if not detected:
                        continue
                    print(f"\n[wake] heard \"{self.wake.name}\" (score {self.wake.last_score:.2f})")
                    self.hud.state("listening")

                if self.ducker:
                    self.ducker.duck()
                follow_up = self._listen_and_handle(on_command)
                cooldown = self._reset()
                was_talking = self._talking()
        finally:
            if self.ducker:
                self.ducker.restore(fade=False)

    def _reset(self) -> int:
        """Start fresh after a command. Returns the cooldown in chunks."""
        # The command may have switched microphones: the new one hears the room
        # and the speakers at different levels, so learn both again.
        generation = getattr(self.source, "generation", 0)
        if generation != self._source_generation:
            self._source_generation = generation
            self.recorder.reset_noise_floor()
            if self.interrupter:
                self.interrupter.relearn()
        # Drop audio that queued up while we were busy, and clear the wake
        # model's buffer so it can't re-trigger on old audio.
        self.source.clear()
        self.wake.reset()
        self.hud.state("idle")  # shows "speaking" by itself while a reply plays
        if not self._talking():
            print(f'\nListening for "{self.wake.name}"...')
        return self._cooldown_chunks

    def _listen_and_handle(
        self,
        on_command,
        follow_up: bool = False,
        preroll: list[np.ndarray] | None = None,
    ) -> bool:
        """Record and handle one command. Returns True if it was sent to `on_command`
        (and so a follow-up window should open when the reply ends).

        `preroll` is speech already captured when the user talked over JARVIS.
        """
        if preroll:
            pass  # you're mid-sentence: no chime (the mic would record it) and keep the audio
        elif follow_up:
            if not self.config.follow_up:
                return False
            if self.config.chime:
                sounds.play(sounds.FOLLOW)
                time.sleep(0.12)      # let the tick finish so the mic doesn't record it
            self.source.clear()
            self.wake.reset()
            print(f"[follow-up] listening for {self.config.follow_up_s:.0f}s (no wake word needed)")
            self.hud.state("follow_up")
        elif self.config.chime:
            sounds.play(sounds.WAKE)

        # Nothing plays while recording, so a timer going off can't end up in the
        # command. The reply queued by on_command starts as soon as this block ends.
        with self.speaker.held() if self.speaker else nullcontext():
            audio = self.recorder.record(
                self.source,
                on_speech_start=lambda: print("[rec] recording..."),
                start_timeout_s=self.config.follow_up_s if follow_up else None,
                preroll=preroll,
            )

            if audio is None:
                if follow_up:
                    print("[follow-up] nothing more; back to waiting for the wake word")
                    return False
                print("[rec] didn't hear a command")
                if self.config.chime:
                    sounds.play(sounds.CANCEL)
                return False

            print(f"[rec] got {len(audio) / 16000:.1f}s of audio")
            if self.config.chime:
                sounds.play(sounds.DONE)
            if self.config.save_recordings:
                self._save(audio)
            # Talking over JARVIS is a follow-up too: "thanks" / "stop" just end it.
            return self._handle(audio, on_command, follow_up or bool(preroll))

    def _handle(self, audio, on_command, follow_up: bool) -> bool:
        if self.transcriber is None:
            on_command("", None)
            return False
        print("[stt] transcribing...")
        self.hud.state("transcribing")
        result = self.transcriber.transcribe(audio)
        text = clean_command(result.text)
        if not text:
            print("[stt] (couldn't make out any words)")
            if self.config.chime and not follow_up:
                sounds.play(sounds.CANCEL)
            return False
        if follow_up and is_dismissal(text):
            print(f'>> "{text}"   (ending the conversation)')
            return False
        on_command(text, result)
        return True

    @staticmethod
    def _save(audio) -> None:
        os.makedirs("recordings", exist_ok=True)
        ms = int(time.time() * 1000) % 1000
        path = os.path.join("recordings", f"{time.strftime('%Y%m%d-%H%M%S')}-{ms:03d}.wav")
        save_wav(path, audio)
        print(f"[rec] saved {path}")
