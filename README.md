# JARVIS

A local, offline voice assistant. Built in stages:

- [x] **Stage 1: Ears.** Wake word ("Hey Jarvis") → record command → speech-to-text
- [ ] Stage 2: Brain. LLM (Ollama) with tool calling
- [ ] Stage 3: Voice. Text-to-speech replies (Piper)
- [ ] Stage 4: Memory + HUD

## How Stage 1 works

```
 mic ──► 80 ms chunks ──► openWakeWord ("hey_jarvis") ──► chime
                                                           │
                    ┌──────────────────────────────────────┘
                    ▼
        record until you stop talking  (WebRTC VAD + noise-floor gate)
                    │
                    ▼
        faster-whisper ──► text ──► on_command()  ◄── Stage 2 plugs in here
```

| File | Job |
|---|---|
| `main.py` | Entry point and CLI flags |
| `jarvis/config.py` | Every tunable setting, with comments |
| `jarvis/audio.py` | Microphone input, plus WAV-file input for testing |
| `jarvis/wakeword.py` | openWakeWord wrapper |
| `jarvis/recorder.py` | Detects when you start and stop talking |
| `jarvis/stt.py` | faster-whisper wrapper (auto GPU → CPU fallback) |
| `jarvis/pipeline.py` | The listen → record → transcribe loop |
| `jarvis/sounds.py` | Chimes generated in code |

Everything runs locally. No API keys and no audio leaves your PC.

## Setup (Windows)

Use **Python 3.12** (3.10–3.13 work). On Linux use 3.11, because openWakeWord pulls in `tflite-runtime` there.

```powershell
cd JARVIS
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Or with uv: `uv venv -p 3.12` then `uv pip install -r requirements.txt`.

## Run

```powershell
python main.py --list-devices     # find your mic's number
python main.py                    # default mic
python main.py --input-device 3   # specific mic
```

The first run downloads the wake word model (~3 MB) and Whisper `small.en` (~490 MB). After that it works offline.

Say **"Hey Jarvis"**, wait for the chime, then speak. You can also say it in one breath: *"Hey Jarvis, open Spotify."*

```
[wake] heard "hey_jarvis" (score 0.91)
[rec] recording...
[rec] got 2.1s of audio
[stt] transcribing...
>> "Open Spotify."   (2.1s audio, transcribed in 0.84s)
```

## Tuning

Every setting in `jarvis/config.py` is also a flag, e.g. `--wake-threshold 0.6`. Run `python main.py --help` for the list.

| Problem | Fix |
|---|---|
| Triggers when nobody said "Hey Jarvis" | `--wake-threshold 0.6` or higher, or `--wake-vad-threshold 0.5` |
| Doesn't respond to you | `--debug` to watch scores, then lower `--wake-threshold` (try 0.35) |
| Cuts you off mid-sentence | `--end-silence-s 1.3` |
| Keeps recording after you stop (noisy room) | `--speech-energy-ratio 4` or `--vad-aggressiveness 3` |
| Doesn't notice you started talking (quiet mic) | `--min-speech-rms 50` and/or `--speech-energy-ratio 2` |
| Transcription too slow | `--whisper-model base.en`, or use an NVIDIA GPU (below) |
| Transcription inaccurate | `--whisper-model medium.en` or `turbo`; add names to `initial_prompt` in config |

### Testing without talking

Record yourself once with `--save-recordings`, or use any 16-bit WAV, then replay it:

```powershell
python main.py --file test.wav            # as fast as possible
python main.py --file test.wav --realtime # real speed, with chimes
python main.py --no-stt --debug           # tune wake word / VAD without loading Whisper
```

### NVIDIA GPU (optional)

`whisper_device` defaults to `auto`: it tries CUDA and falls back to CPU if that fails, so it is safe on any PC. To actually use the GPU, faster-whisper needs the CUDA 12 cuBLAS and cuDNN 9 DLLs on your PATH. See the "GPU" section of the [faster-whisper README](https://github.com/SYSTRAN/faster-whisper#gpu) for the Windows download. The startup line `Whisper ready on cuda/float16` confirms it worked.

On a GPU, `turbo` or `medium.en` is fast enough for real-time use.

## Custom wake word

openWakeWord can train a model for any phrase (e.g. just "Jarvis", or "Hey Sierra") using its Colab notebook, with no recordings needed. Drop the resulting `.onnx` file in the project and run:

```powershell
python main.py --wake-model models/my_wake_word.onnx
```
