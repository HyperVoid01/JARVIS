# JARVIS

A local, offline voice assistant. Built in stages:

- [x] **Stage 1: Ears.** Wake word ("Hey Jarvis") → record command → speech-to-text
- [x] **Stage 2: Brain.** Local LLM (Ollama) with tool calling: opens apps, Unity projects, timers, media keys
- [x] **Stage 3: Voice.** Spoken replies with Piper, and "Hey Jarvis" interrupts it mid-sentence
- [x] **Stage 4a: Memory.** Remembers what you ask it to, across restarts
- [x] **Stage 4b: HUD.** Always-on-top arc-reactor overlay showing what it's doing, heard and said

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
                                    jarvis/tools/*
                                      │
                                      ▼
                    reply ──► Piper (sentence by sentence) ──► speakers
```

Speech plays on its own threads, so JARVIS keeps listening while it talks. Talk over it (or say "Hey Jarvis") and it stops mid-sentence and listens to you.

| File | Job |
|---|---|
| `main.py` | Entry point and CLI flags |
| `jarvis/config.py` | Every tunable setting, with comments |
| `jarvis/audio.py` | Microphone input, plus WAV-file input for testing |
| `jarvis/wakeword.py` | openWakeWord wrapper |
| `jarvis/recorder.py` | Detects when you start and stop talking |
| `jarvis/stt.py` | faster-whisper wrapper |
| `jarvis/pipeline.py` | The listen → record → transcribe loop, and the self-hearing guard |
| `jarvis/brain.py` | Ollama chat + tool-calling loop, short conversation memory |
| `jarvis/tts.py` | Piper text-to-speech: streamed playback, interrupt, hold |
| `jarvis/interrupt.py` | Tells your voice apart from JARVIS's own echo, so you can talk over it |
| `jarvis/ducking.py` | Turns other apps down while JARVIS listens and talks |
| `jarvis/tools/__init__.py` | `@tool` decorator: turns a Python function into something the LLM can call |
| `jarvis/tools/system.py` | Time, apps, websites, web search, timers |
| `jarvis/tools/media.py` | Per-app playback control and system volume |
| `jarvis/tools/windows.py` | Close / quit / switch to / minimize apps; lock, sleep, shut down |
| `jarvis/fastpath.py` | Instant handling of simple commands, without the LLM |
| `jarvis/tools/memory.py` | Long-term memory: remember / update / forget facts (`memory.json`) |
| `jarvis/tools/unity.py` | Find Unity projects and open them in the right editor version |
| `jarvis/hud.py` | The on-screen HUD (tkinter, click-through, always on top) |
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

Check the voice works:

```cmd
python main.py --say "Good evening, sir. All systems are online."
```

## Run

```cmd
python main.py --text             # type commands - quickest way to test the brain
python main.py                    # voice: say "Hey Jarvis, open Spotify"
python main.py --no-tts           # print replies instead of speaking them
python main.py --list-devices     # find your mic's and speakers' numbers
python main.py --input-device 3   # specific mic
python main.py --output-device 5  # specific speakers / headphones
python main.py --no-brain         # voice -> text only (Stage 1 behaviour)
```

The first run downloads the wake word model (~3 MB), Whisper `small.en` (~490 MB) and the Piper voice (~60 MB, into `models/voices`). After that it works offline.

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
| "Set the volume to 50%" / "Turn it up a bit" / "Mute" / "What's the volume?" | `volume` (Windows Core Audio) |
| "Pause Spotify" / "Next song" / "Resume YouTube" / "What's playing?" | `media_control` (per-app, via Windows media sessions) |
| "Set a timer for 10 minutes for pizza" / "How long is left?" | `set_timer`, `list_timers` (finished timers are announced out loud) |
| "Actually make it 15" / "Cancel the pizza timer" / "Cancel all timers" | `change_timer`, `cancel_timer` |
| "Close Discord" / "Quit Spotify" / "Switch to Chrome" / "Minimize VS Code" / "Show the desktop" / "What's open?" | `window` |
| "Lock the PC" / "Go to sleep" / "Shut down" / "Restart" / "Cancel the shutdown" | `power` |
| "Remember that my WIL deadline is Friday" / "It moved to Monday" / "Forget my deadline" / "What do you know about me?" | `remember`, `forget` |
| "What Unity projects do I have?" / "Open Rat Attack" | `list_unity_projects`, `open_unity_project` |
| "Let's get to work" / "Let's work on Rat Attack" / "Add Discord to the Rat Attack workspace" | `workspace` (asks what to open the first time, then remembers) |
| "Switch to my headset" / "Which mic are you using?" | `microphone` |
| "Open my Downloads" / "Open the RatController script" / "Where's my WIL report?" / "What's in my Desktop?" | `files` (Desktop, Documents, Downloads, `D:\UNITY Projects`; change with `--file-dirs`) |
| "Read me my ideas note" / "What does my WIL report say?" | `files` read (text, code and Word files) |
| "Make a note called ideas: rat boss" / "Add a combo meter to my ideas note" | `files` note (in `Documents\JARVIS Notes`, only ever added to) |
| "Ask me something" / "Stop asking me questions" | One question a day about you, saved to memory (`--no-daily-question` to turn off) |
| "I've got a demo on Friday" | "Want me to remember that?" (nothing is saved without a yes) |
| (automatic) | Remembered deadlines mentioned two days ahead (`--no-deadline-reminders` to turn off); "Rat Attack again?" when you usually open it |
| "What's my student number?" | Answered from files you put in `Documents\JARVIS Notes\About Me` |

After each reply, a soft tick plays and JARVIS listens for 4 seconds without the wake word, so you can keep talking:

```
You:    Hey Jarvis, set a timer for 5 minutes.
JARVIS: Timer set for 5 minutes.        *tick*
You:    Actually, make it ten.
JARVIS: Done, ten minutes it is.        *tick*
You:    Thanks.                         (conversation ends)
```

Silence, or "thanks", "that's all", "no", "never mind", ends the conversation. Music stays ducked until it does. It also remembers the last few commands, so "now make it 5 minutes" works later too. That memory resets after 5 minutes of silence.

### HUD

A small overlay sits in the bottom-right corner, above the taskbar. The ring shows what JARVIS is doing at a glance:

| Ring | Means |
|---|---|
| Dim, turning slowly | Waiting for "Hey Jarvis" (the text panel tucks away after a few seconds) |
| Bright cyan, pulsing | Listening to you |
| Soft cyan, "GO AHEAD" | Follow-up window: talk without the wake word |
| Pale blue | Transcribing |
| Amber, spinning fast | Thinking |
| Cyan, glowing with its voice | Speaking |

Next to it, the panel shows what it heard and its reply. Clicks go straight through it, so it never blocks anything, and it stays out of Alt+Tab and the taskbar.

```cmd
python main.py --hud-position top-right    # or bottom-left / top-left
python main.py --hud-scale 1.25            # bigger
python main.py --no-hud                    # turn it off
```

### Memory

Tell JARVIS to remember something and it keeps it for good, across restarts:

```
You:    Hey Jarvis, remember that my WIL deadline is this Friday.
JARVIS: Noted, sir. Friday the 14th.
        ...next week, after a restart...
You:    Hey Jarvis, when's my WIL deadline?
JARVIS: Friday 14 November.
```

- It only saves what you explicitly ask it to ("remember", "note that", "don't forget"). It never quietly collects things.
- "Remember…" is instant (no LLM): it's rewritten in code ("my deadline is Friday" becomes "Sooriyan's deadline is on Friday 13 November 2026") and read back to you, so you can catch a misheard word or a wrong date.
- "Friday" or "tomorrow" is saved as the real date, so it still makes sense later. A bare weekday always means an upcoming one; "next Monday" is the one after the coming Monday.
- Saving a new value for the same thing ("my favourite food is sushi") replaces the old one.
- "It moved to Monday" updates the old fact; "forget my deadline" removes it; "forget everything" asks first.
- Everything is in `memory.json` in the project folder, which you can open and edit by hand. It's in `.gitignore`, so it never gets pushed to GitHub. Use `--memory-file` to keep it somewhere else.

### Closing apps, and things that ask first

"Close" closes an app's window the normal way, so it can still ask you to save. Apps that hide in the system tray (Discord, Spotify, Steam) keep running; say "quit" to make them exit. Quitting an app that might have unsaved work (VS Code, Unity, Word), shutting down, restarting and signing out all ask "are you sure?" first. Say yes within the follow-up window. This is enforced in code: the model can't answer its own question, and a yes only counts from your next command. Shutdowns and restarts wait 30 seconds, so "cancel the shutdown" (instant, no LLM) or just "cancel" can still stop them.

Sleep uses Windows' sleep call, which hibernates instead if you've turned hibernation on.

### Instant commands

Simple commands skip the LLM entirely and happen straight away: pause, play, next, previous and "what's playing" (optionally for one app, e.g. "pause Spotify"), volume ("volume 40", "turn it up a bit", "louder", "mute"), and the time or date. You'll see `(fast path)` in the log. Anything with more to it, like "play some lofi on YouTube", still goes to the LLM. Turn this off with `--no-fast-path`.

### Start with Windows

```cmd
python main.py --install-startup      # run this once, from the activated venv
python main.py --uninstall-startup    # to undo it
```

This puts `JARVIS.cmd` in your Startup folder, which opens JARVIS in a minimised window every time you log in, using your settings from `jarvis/config.py`. If Ollama isn't running yet, JARVIS starts it and waits for it. To stop JARVIS, close that window or press Ctrl+C in it.

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

## Voice

The default voice is `en_US-danny-low`, run through a JARVIS-style effect (`jarvis/voice_fx.py`): the boomy low end is cut, the top end is made crisper, and it adds a faint doubled edge and a hint of room. Any [Piper voice](https://rhasspy.github.io/piper-samples/) works by name and downloads on first use:

```cmd
python main.py --tts-voice en_US-ryan-high            # same voice family, higher quality
python main.py --tts-voice models/my_voice.onnx       # a voice file you downloaded or trained
```

Shaping the sound:

```cmd
python main.py --say "Good evening, sir." --tts-effect 1.5           # more processed
python main.py --say "Good evening, sir." --tts-effect 0             # raw voice
python main.py --say "Good evening, sir." --tts-expressiveness 1.4   # livelier intonation
python main.py --say "Good evening, sir." --tts-speed 1.2            # quicker
```

Piper runs on the CPU, so it doesn't compete with the LLM for VRAM. Replies are spoken sentence by sentence: the first sentence starts while the rest is still being generated.

**Interrupting it.** Just talk over it. The mic hears JARVIS's own voice too, so it can't simply listen for "someone talking". Instead it knows how loud it's playing at every moment and learns how much of that leaks back into the mic. Sound only counts as you when it's `--interrupt-margin` (2.0) times louder than that expected echo, sounds like a voice, and lasts `--interrupt-min-s` (0.3 s). The start of what you said is kept. On laptop speakers you may need to speak up a little; with headphones there's no echo and any speech works. "Hey Jarvis" also still interrupts it.

**How it avoids hearing itself.** While it's talking:

- the room noise level isn't updated, so its voice doesn't raise the bar for detecting yours,
- speech must be well above its own predicted echo to count as an interruption,
- the wake word only counts above `--barge-in-threshold` (0.7), higher than the normal 0.5,
- when it stops, the audio buffered meanwhile is thrown away (plus `--echo-tail-s` for the last bit leaving the speakers).

While you're giving a command nothing is played, so a timer going off can't end up in your recording; it's announced right after.

Headphones remove the echo problem completely.

## Tuning

Every setting in `jarvis/config.py` is also a flag, e.g. `--wake-threshold 0.6`. Run `python main.py --help` for the list.

| Problem | Fix |
|---|---|
| Triggers when nobody said "Hey Jarvis" | `--wake-threshold 0.6` or higher, or `--wake-vad-threshold 0.5` |
| Doesn't respond to you | `--debug` to watch scores, then lower `--wake-threshold` (try 0.35) |
| Interrupts itself while talking | `--interrupt-margin 2.5` (or 3). `--debug` shows `[interrupt] mic … predicted echo …` lines so you can see why |
| Ignores you when you talk over it | `--interrupt-margin 1.7`, speak up a little, turn the speakers down, or use headphones |
| Stops on a cough or a word from someone else | `--interrupt-min-s 0.5` |
| Don't want talking over it to interrupt | `--no-interrupt-on-speech` ("Hey Jarvis" still works; `--no-barge-in` turns that off too) |
| Reacts to the end of its own reply | `--echo-tail-s 0.8` |
| Follow-up window too short / too long | `--follow-up-s 6` or `2`; `--no-follow-up` turns it off |
| Reacts to people talking nearby after a reply | `--follow-up-s 2`, or `--no-follow-up` |
| Music too loud / too quiet while it listens | `--duck-level 0.1` (quieter) or `0.4` (louder); `--no-duck` turns it off |
| An app stayed quiet after closing JARVIS | It was force-closed mid-command; raise the app again in Windows' Volume Mixer |
| Talks too fast / slow | `--tts-speed 0.9` (slower) or `1.2` (faster) |
| No sound from the voice | `--list-devices`, then `--output-device N`; test with `--say "test"` |
| Cuts you off mid-sentence | `--end-silence-s 1.8`, or lower `--min-speech-rms` (try 30) |
| Keeps recording after you stop (noisy room) | `--speech-energy-ratio 3` or `--vad-aggressiveness 3` |
| Doesn't notice you started talking (quiet mic) | `--min-speech-rms 30` and/or `--speech-energy-ratio 1.5` |
| Transcription too slow | `--whisper-model base.en` |
| Transcription inaccurate | `--whisper-model medium.en`; add names to `initial_prompt` in config |
| LLM replies slow | Check `ollama ps` shows `100% GPU`. If not, close GPU-heavy apps or lower `--llm-num-ctx 2048` |
| LLM picks the wrong tool | `--debug` shows its raw replies; tighten that tool's docstring, or try a bigger model |

### Testing without talking

```cmd
python main.py --say "Testing, one two three."   # just the voice
python main.py --text                     # type commands straight into the brain (replies are spoken)
python main.py --file test.wav            # replay a recording (make one with --save-recordings)
python main.py --no-stt --debug           # tune wake word / VAD without loading Whisper
```

### GPU usage

On a 4 GB card the GPU goes to the LLM, and Whisper and Piper run on the CPU (`whisper_device = "cpu"`). Whisper takes about 1.5 s per command with `small.en`. With 8 GB+ VRAM you can move Whisper to the GPU with `--whisper-device cuda`. That needs the CUDA 12 cuBLAS and cuDNN 9 DLLs on your PATH; see the "GPU" section of the [faster-whisper README](https://github.com/SYSTRAN/faster-whisper#gpu).

## Custom wake word

openWakeWord can train a model for any phrase (e.g. just "Jarvis", or "Hey Sierra") using its Colab notebook, with no recordings needed. Drop the resulting `.onnx` file in the project and run:

```cmd
python main.py --wake-model models/my_wake_word.onnx
```

Note: openWakeWord's pre-trained models (including `hey_jarvis`) are licensed for non-commercial use. Piper voices have their own licences, listed on each voice's model card.
