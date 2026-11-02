"""All tunable settings in one place.

Every field can be overridden from the command line (see main.py), so you
can experiment without editing code.
"""

from dataclasses import dataclass

# openWakeWord and webrtcvad both expect 16 kHz, 16-bit, mono audio.
SAMPLE_RATE = 16_000

# openWakeWord works on 80 ms chunks (1280 samples). Everything in the
# pipeline uses this chunk size so audio only has to be read once.
CHUNK_SAMPLES = 1280
CHUNK_MS = CHUNK_SAMPLES * 1000 // SAMPLE_RATE  # 80


@dataclass
class Config:
    # --- Microphone -------------------------------------------------------
    input_device: int | str | None = None  # None = system default mic

    # --- Wake word --------------------------------------------------------
    wake_model: str = "hey_jarvis"   # built-in model name, or path to a custom .onnx
    wake_threshold: float = 0.5      # 0..1, raise if it triggers by accident
    wake_vad_threshold: float = 0.0  # >0 enables Silero VAD gating inside openWakeWord
    wake_cooldown_s: float = 1.5     # ignore re-triggers for this long after a command

    # --- Command recording (VAD endpointing) ------------------------------
    vad_aggressiveness: int = 2      # 0 (lenient) .. 3 (strict) speech detection
    start_timeout_s: float = 5.0     # give up if you don't start talking in time
    end_silence_s: float = 0.9       # this much silence after speech = you're done
    max_command_s: float = 15.0      # hard cap on command length
    min_speech_s: float = 0.25       # ignore blips shorter than this
    speech_energy_ratio: float = 3.0 # speech must be this many times louder than room noise
    min_speech_rms: float = 100.0    # absolute loudness floor for speech (int16 RMS)

    # --- Speech-to-text ---------------------------------------------------
    whisper_model: str = "small.en"  # tiny.en / base.en / small.en / medium.en / large-v3 / turbo
    whisper_device: str = "auto"     # auto / cpu / cuda
    whisper_compute_type: str = "auto"  # auto / int8 / float16 / int8_float16
    language: str | None = "en"      # None = auto-detect (needs a non-.en model)
    beam_size: int = 5
    # Biases Whisper toward words it should expect. Add names you use a lot.
    initial_prompt: str | None = "Jarvis, open, launch, Unity, Visual Studio, Spotify, Discord."

    # --- Feedback ---------------------------------------------------------
    chime: bool = True               # play a sound when the wake word is heard
    save_recordings: bool = False    # write each command to recordings/*.wav
