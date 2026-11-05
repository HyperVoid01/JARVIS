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
    end_silence_s: float = 1.3       # this much silence after speech = you're done
    max_command_s: float = 15.0      # hard cap on command length
    min_speech_s: float = 0.25       # ignore blips shorter than this
    speech_energy_ratio: float = 2.0 # speech must be this many times louder than room noise
    min_speech_rms: float = 50.0     # absolute loudness floor for speech (int16 RMS)

    # --- Speech-to-text ---------------------------------------------------
    whisper_model: str = "small.en"  # tiny.en / base.en / small.en / medium.en / large-v3 / turbo
    # cpu keeps the GPU free for the LLM (a 4 GB card can't hold both). Use auto/cuda on bigger GPUs.
    whisper_device: str = "cpu"      # auto / cpu / cuda
    whisper_compute_type: str = "auto"  # auto / int8 / float16 / int8_float16
    language: str | None = "en"      # None = auto-detect (needs a non-.en model)
    beam_size: int = 5
    # Biases Whisper toward words it should expect. Add names you use a lot.
    initial_prompt: str | None = "Jarvis, open, launch, Unity, Visual Studio, Spotify, Discord."

    # --- Brain (LLM via Ollama) -------------------------------------------
    # Instruct (non-reasoning) model: answers instantly. Plain "qwen3:4b" is a slow thinking model.
    llm_model: str = "qwen3:4b-instruct-2507-q4_K_M"
    ollama_url: str = "http://localhost:11434"
    llm_num_ctx: int = 4096          # context window; bigger uses more VRAM
    llm_keep_alive: str = "30m"      # keep the model loaded in VRAM between commands
    history_turns: int = 6           # how many past commands JARVIS remembers for follow-ups
    history_timeout_s: float = 300.0 # forget the conversation after this long without a command
    user_name: str = "Sooriyan"

    # --- Voice (text-to-speech via Piper) ---------------------------------
    tts: bool = True                 # speak replies out loud (--no-tts to only print them)
    # Any voice from https://rhasspy.github.io/piper-samples/ (downloaded on first use),
    # or a path to a .onnx voice file. en_US-ryan-high is a crisper version of the same voice.
    tts_voice: str = "en_US-danny-low"
    tts_voice_dir: str = "models/voices"
    tts_speed: float = 1.1           # 1.2 = 20% faster
    tts_volume: float = 1.0          # loudness of the voice relative to system volume
    tts_expressiveness: float = 1.2  # intonation variety: 1 = voice default, higher = livelier (wobbly past ~1.6)
    tts_effect: float = 2.5          # JARVIS processing (EQ, doubling, room): 0 = off, 1.5 = stronger
    output_device: int | str | None = None  # None = system default speakers
    # Say "Hey Jarvis" while it's talking to cut it off. The higher threshold stops
    # its own voice (picked up by the mic) from triggering it.
    barge_in: bool = True
    barge_in_threshold: float = 0.7
    echo_tail_s: float = 0.4         # keep ignoring the mic this long after it stops talking

    # --- Ducking ----------------------------------------------------------
    # Turn other apps (Spotify, YouTube...) down from the wake word until the reply ends.
    duck: bool = True
    duck_level: float = 0.2          # fraction of each app's own volume to keep (0 = silent)

    # --- Tools ------------------------------------------------------------
    # Folders to search for Unity projects. None = Documents, Desktop, D:\ and a few common spots.
    unity_project_dirs: list[str] | None = None
    unity_editor_root: str = "C:/Program Files/Unity/Hub/Editor"

    # --- Feedback ---------------------------------------------------------
    chime: bool = True               # play a sound when the wake word is heard
    save_recordings: bool = False    # write each command to recordings/*.wav
