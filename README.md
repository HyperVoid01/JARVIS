# JARVIS

A local, offline voice assistant. Built in stages:

- [x] **Stage 1: Ears.** Wake word ("Hey Jarvis") → record command → speech-to-text
- [x] **Stage 2: Brain.** Local LLM (Ollama) with tool calling: opens apps, Unity projects, timers, media keys
- [ ] Stage 3: Voice. Text-to-speech replies (Piper)
- [ ] Stage 4: Memory + HUD

## How it works

```
 mic ──► 80 ms chunks ──► openWakeWord ("hey_jarvis") ──► chime
                                                           │
                    ┌──────────────────────────────────────┘
                    ▼
        record until you stop talking  (WebRTC VAD + noise-floor gate)
                    │
                    ▼
        faster-whisper ──► text ──► Brain (Ollama, Qwen3 4B Instruct)
                                      │   ▲
                              tool call   tool result
                                      ▼   │
                                    jarvis/tools/*  ──► reply  ◄── Stage 3 speaks this
```

| File | Job |
|---|---|
| `main.py` | Entry point and CLI flags |
| `jarvis/config.py` | Every tunable setting, with comments |
| `jarvis/audio.py` | Microphone input, plus WAV-file input for testing |
| `jarvis/wakeword.py` | openWakeWord wrapper |
| `jarvis/recorder.py` | Detects when you start and stop talking |
| `jarvis/stt.py` | faster-whisper wrapper |
| `jarvis/pipeline.py` | The listen → record → transcribe loop |
| `jarvis/brain.py` | Ollama chat + tool-calling loop, short conversation memory |
| `jarvis/tools/__init__.py` | `@tool` decorator: turns a Python function into something the LLM can call |
| `jarvis/tools/system.py` | Time, apps, websites, web search, media keys, timers |
| `jarvis/tools/unity.py` | Find Unity projects and open them in the right editor version |
| `jarvis/sounds.py` | Chimes generated in code |

Everything runs locally. No API keys and no audio leaves your PC.

## Setup (Windows)

Use Python **3.10–3.13**. On Linux use 3.10 or 3.11, because openWakeWord pulls in `tflite-runtime` there.

```cmd
cd JARVIS
py -3.10 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Install [Ollama](https://ollama.com), then pull the model:

```cmd
ollama pull qwen3:4b-instruct-2507-q4_K_M
```

This is the **instruct** version of Qwen3 4B (~2.5 GB), which fits fully on a 4 GB GPU and answers without a slow "thinking" phase. Avoid plain `qwen3:4b`: it points to the reasoning-only version, which adds seconds to every reply. Any Ollama model with tool support works via `--llm-model`.

## Run

```cmd
python main.py --text             # type commands - quickest way to test the brain
python main.py                    # voice: say "Hey Jarvis, open Spotify"
python main.py --list-devices     # find your mic's number
python main.py --input-device 3   # specific mic
python main.py --no-brain         # voice -> text only (Stage 1 behaviour)
```

The first run downloads the wake word model (~3 MB) and Whisper `small.en` (~490 MB). After that it works offline.

```
[wake] heard "hey_jarvis" (score 0.93)
[rec] recording...
[rec] got 2.1s of audio
[stt] transcribing...
>> "Open Spotify."   (2.1s audio, transcribed in 1.40s)
[tool] open_app(name='Spotify') -> Opened Spotify.
JARVIS: Spotify is open, sir.
```

## What it can do

| Say | Tool |
|---|---|
| "What time is it?" / "What's the date?" | `get_datetime` |
| "Open Spotify / Discord / VS Code / Settings / Bluetooth settings" | `open_app` (Start Menu + Desktop shortcuts, built-in Windows apps) |
| "Open YouTube" / "Go to github.com" | `open_website` |
| "Search for Unity NavMesh tutorials" / "Find lofi on YouTube" | `web_search` |
| "Set the volume to 50%" / "What's the volume?" | `set_volume` (exact level via Windows Core Audio) |
| "Pause the music" / "Next song" / "Turn it up a bit" | `media_control` (keyboard media keys) |
| "Set a timer for 10 minutes for pizza" / "How long is left?" | `set_timer`, `list_timers` |
| "What Unity projects do I have?" / "Open Rat Attack" | `list_unity_projects`, `open_unity_project` |

It remembers the last few commands, so follow-ups like "now make it 5 minutes" work. The memory resets after 5 minutes of silence.

### Unity projects

JARVIS looks for folders containing `Assets/` and `ProjectSettings/ProjectVersion.txt` in Documents, Desktop, `~/Unity Projects`, `Documents/GitHub` and `D:\`, up to 3 folders deep. It opens each project with the editor version it was made in, from `C:\Program Files\Unity\Hub\Editor`. If your projects live elsewhere:

```cmd
python main.py --unity-project-dirs "D:\Unity" "E:\School\Projects"
```

### Adding your own tool

Write a function with type hints and a docstring in any file under `jarvis/tools/`, and add `@tool`:

```python
@tool
def lock_pc() -> str:
    """Lock the Windows session."""
    import ctypes
    ctypes.windll.user32.LockWorkStation()
    return "Locked."
```

The docstring is what the LLM reads to decide when to use it, so keep it short and specific. For a new file, import it at the bottom of `jarvis/tools/__init__.py`. Small models get less reliable as the tool list grows, so prefer a few general tools over many narrow ones.

App names that don't match their Start Menu shortcut can be added to `APP_ALIASES` in `jarvis/tools/system.py`.

## Tuning

Every setting in `jarvis/config.py` is also a flag, e.g. `--wake-threshold 0.6`. Run `python main.py --help` for the list.

| Problem | Fix |
|---|---|
| Triggers when nobody said "Hey Jarvis" | `--wake-threshold 0.6` or higher, or `--wake-vad-threshold 0.5` |
| Doesn't respond to you | `--debug` to watch scores, then lower `--wake-threshold` (try 0.35) |
| Cuts you off mid-sentence | `--end-silence-s 1.8`, or lower `--min-speech-rms` (try 30) |
| Keeps recording after you stop (noisy room) | `--speech-energy-ratio 3` or `--vad-aggressiveness 3` |
| Doesn't notice you started talking (quiet mic) | `--min-speech-rms 30` and/or `--speech-energy-ratio 1.5` |
| Transcription too slow | `--whisper-model base.en` |
| Transcription inaccurate | `--whisper-model medium.en`; add names to `initial_prompt` in config |
| LLM replies slow | Check `ollama ps` shows `100% GPU`. If not, close GPU-heavy apps or lower `--llm-num-ctx 2048` |
| LLM picks the wrong tool | `--debug` shows its raw replies; tighten that tool's docstring, or try a bigger model |

### Testing without talking

```cmd
python main.py --text                     # type commands straight into the brain
python main.py --file test.wav            # replay a recording (make one with --save-recordings)
python main.py --no-stt --debug           # tune wake word / VAD without loading Whisper
```

### GPU usage

On a 4 GB card the GPU goes to the LLM and Whisper runs on the CPU (`whisper_device = "cpu"`), which takes about 1.5 s per command with `small.en`. With 8 GB+ VRAM you can move Whisper to the GPU with `--whisper-device cuda`. That needs the CUDA 12 cuBLAS and cuDNN 9 DLLs on your PATH; see the "GPU" section of the [faster-whisper README](https://github.com/SYSTRAN/faster-whisper#gpu).

## Custom wake word

openWakeWord can train a model for any phrase (e.g. just "Jarvis", or "Hey Sierra") using its Colab notebook, with no recordings needed. Drop the resulting `.onnx` file in the project and run:

```cmd
python main.py --wake-model models/my_wake_word.onnx
```

Note: openWakeWord's pre-trained models (including `hey_jarvis`) are licensed for non-commercial use.
