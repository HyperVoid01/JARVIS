"""JARVIS entry point.

    python main.py                         # voice mode: "Hey Jarvis" + command, spoken replies
    python main.py --text                  # type commands instead (fast way to test the brain)
    python main.py --no-tts                # print replies instead of speaking them
    python main.py --no-brain              # voice -> text only, no LLM (Stage 1 behaviour)
    python main.py --say "Hello, sir."     # test the voice and exit
    python main.py --install-startup       # start JARVIS automatically when you log in
    python main.py --uninstall-startup     # stop doing that
    python main.py --list-devices          # find your mic's / speakers' index
    python main.py --input-device 3        # use a specific mic
    python main.py --llm-model qwen3:8b    # try a different Ollama model
    python main.py --file test.wav         # run a recording through the pipeline
    python main.py --debug                 # wake-word scores + raw LLM responses

Every setting in jarvis/config.py is available as --kebab-case-flag.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import shutil
import subprocess
import sys
import time
import types
import typing
from pathlib import Path

from jarvis.config import Config

PROJECT_DIR = Path(__file__).resolve().parent
STARTUP_SCRIPT = "JARVIS.cmd"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="JARVIS voice assistant")
    p.add_argument("--list-devices", action="store_true", help="list audio devices and exit")
    p.add_argument("--text", action="store_true", help="type commands instead of speaking them")
    p.add_argument("--no-brain", action="store_true", help="only transcribe, don't send to the LLM")
    p.add_argument("--say", metavar="TEXT", help="speak TEXT with the current voice settings and exit")
    p.add_argument("--install-startup", action="store_true", help="start JARVIS when you log in to Windows")
    p.add_argument("--uninstall-startup", action="store_true", help="stop starting JARVIS at login")
    p.add_argument("--file", help="feed a WAV file instead of the microphone (for testing)")
    p.add_argument("--realtime", action="store_true", help="with --file, play at real speed")
    p.add_argument("--debug", action="store_true", help="print wake word scores and raw LLM replies")
    p.add_argument("--no-stt", action="store_true", help="skip transcription (tune wake word / VAD only)")

    # Generate a flag for every Config field so the two never drift apart.
    hints = typing.get_type_hints(Config)
    for field in dataclasses.fields(Config):
        flag = "--" + field.name.replace("_", "-")
        hint = hints[field.name]
        if hint is bool:
            p.add_argument(flag, action=argparse.BooleanOptionalAction, default=field.default)
        elif hint in (int, float):
            p.add_argument(flag, type=hint, default=field.default)
        elif _is_list(hint):
            p.add_argument(flag, nargs="+", default=field.default, metavar="PATH")
        else:
            p.add_argument(flag, type=_parse_optional_str, default=field.default,
                           help="use 'none' to unset" if _allows_none(hint) else None)
    return p


def _allows_none(hint) -> bool:
    return isinstance(hint, types.UnionType) and type(None) in hint.__args__


def _is_list(hint) -> bool:
    args = hint.__args__ if isinstance(hint, types.UnionType) else (hint,)
    return any(typing.get_origin(a) is list for a in args)


def _parse_optional_str(value: str):
    if value.lower() == "none":
        return None
    if value.isdigit():  # device indexes
        return int(value)
    return value


# --- Start with Windows ----------------------------------------------------

def _startup_folder() -> Path:
    return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) \
        / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def install_startup() -> int:
    """Put a small script in the Startup folder that runs JARVIS (minimised) at login."""
    if sys.platform != "win32":
        print("--install-startup only works on Windows.")
        return 1
    script = _startup_folder() / STARTUP_SCRIPT
    script.write_text(
        "@echo off\r\n"
        "rem Starts JARVIS when you log in. Remove with: python main.py --uninstall-startup\r\n"
        f'cd /d "{PROJECT_DIR}"\r\n'
        f'start "JARVIS" /min "{sys.executable}" "{PROJECT_DIR / "main.py"}"\r\n',
        encoding="utf-8",
    )
    print(f"JARVIS will start (minimised) every time you log in.\n  Script: {script}\n"
          f"  Python: {sys.executable}\n"
          "It waits for Ollama, and starts it if needed. Settings come from jarvis\\config.py.\n"
          "To undo: python main.py --uninstall-startup")
    return 0


def uninstall_startup() -> int:
    script = _startup_folder() / STARTUP_SCRIPT
    if script.exists():
        script.unlink()
        print("JARVIS won't start at login any more.")
    else:
        print("JARVIS wasn't set to start at login.")
    return 0


# --- Ollama ----------------------------------------------------------------

def _start_ollama() -> bool:
    """Try to launch Ollama in the background. True if something was started."""
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama app.exe",
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Ollama" / "ollama app.exe",
    ]
    for exe in candidates:
        if exe.exists():
            subprocess.Popen([str(exe)])
            return True
    exe = shutil.which("ollama")
    if exe:
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags)
        return True
    return False


def connect_ollama(brain, wait_s: float = 90) -> None:
    """Check Ollama is up; if it isn't, start it and wait (e.g. right after logging in)."""
    from jarvis.brain import OllamaError

    started = False
    deadline = time.monotonic() + wait_s
    while True:
        try:
            brain.check()
            return
        except OllamaError as e:
            unreachable = str(e).startswith("Can't reach")
            if not unreachable or time.monotonic() > deadline:
                raise
            if not started:
                started = _start_ollama()
                print("Ollama isn't running; " + ("starting it..." if started else "waiting for it..."))
            time.sleep(2)


# --- Building the parts ----------------------------------------------------

def build_speaker(config: Config):
    """Load the Piper voice, or return None (replies are then only printed)."""
    if not config.tts:
        return None
    from jarvis.tts import Speaker

    print(f"Loading voice '{config.tts_voice}'...")
    try:
        speaker = Speaker(
            voice=config.tts_voice,
            voice_dir=config.tts_voice_dir,
            speed=config.tts_speed,
            volume=config.tts_volume,
            expressiveness=config.tts_expressiveness,
            effect=config.tts_effect,
            device=config.output_device,
        )
    except ImportError:
        print("[tts] Piper isn't installed (pip install -r requirements.txt). Replies will only be printed.")
        return None
    except Exception as e:
        print(f"[tts] couldn't start the voice: {e}\n      Replies will only be printed. (--no-tts hides this)")
        return None
    print(f"Voice ready: {speaker.voice_name}")
    return speaker


def build_brain(config: Config, debug: bool):
    from jarvis import tools
    from jarvis.brain import Brain, OllamaError
    from jarvis.tools import memory, unity

    unity.configure(project_dirs=config.unity_project_dirs, editor_root=config.unity_editor_root)
    memory_path = Path(config.memory_file)
    memory.configure(memory_path if memory_path.is_absolute() else PROJECT_DIR / memory_path,
                     user=config.user_name)

    def extra_context() -> str:
        projects = ", ".join(sorted(unity.find_projects())) or "none found"
        return f"The user's Unity projects: {projects}.\n\n{memory.prompt_block(config.user_name)}"

    brain = Brain(
        model=config.llm_model,
        url=config.ollama_url,
        num_ctx=config.llm_num_ctx,
        keep_alive=config.llm_keep_alive,
        history_turns=config.history_turns,
        history_timeout_s=config.history_timeout_s,
        user_name=config.user_name,
        extra_context=extra_context,
        debug=debug,
    )
    print(f"Connecting to Ollama ({config.llm_model})...")
    try:
        connect_ollama(brain)
        brain.warm_up()
    except OllamaError as e:
        print(f"\n[brain] {e}")
        sys.exit(1)
    print(f"Brain ready with {len(tools.all_tools())} tools: "
          + ", ".join(t.name for t in tools.all_tools()))
    facts = len(memory.load())
    print(f"Memory: {facts} saved fact{'s' * (facts != 1)} ({memory_path.name})")
    return brain


def make_responder(brain, speaker, fast_path: bool = True):
    """Returns a function that handles one command, then prints and speaks the reply."""
    from jarvis import fastpath, tools
    from jarvis.brain import OllamaError

    def respond(text: str) -> None:
        # Simple commands ("pause", "volume 40", "what time is it") skip the LLM.
        fast = fastpath.handle(text) if fast_path else None
        if fast is not None:
            reply, calls = fast
            tools.begin_turn()
            brain.remember(text, reply, calls)
            print(f"JARVIS: {reply}   (fast path)")
        else:
            try:
                reply = brain.ask(text)
            except OllamaError as e:
                print(f"[brain] {e}")
                if speaker:
                    speaker.say("Sorry, I can't reach my language model right now.")
                return
            print(f"JARVIS: {reply}")
        if speaker:
            speaker.say(reply)

    return respond


def run_text_mode(respond, speaker) -> None:
    print('\nType a command (or "quit"). Example: open spotify\n')
    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if text.lower() in ("quit", "exit"):
            return
        if text:
            if speaker:
                speaker.stop()  # a new command cuts off the previous reply
            respond(text)


def main() -> int:
    args = build_parser().parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        print("\n'>' marks the default mic, '<' the default speakers.")
        print("Pass a number with --input-device N (mic) or --output-device N (speakers).")
        return 0
    if args.install_startup:
        return install_startup()
    if args.uninstall_startup:
        return uninstall_startup()

    config = Config(**{f.name: getattr(args, f.name) for f in dataclasses.fields(Config)})

    if args.say:
        config.tts = True
        speaker = build_speaker(config)
        if speaker is None:
            return 1
        speaker.say(args.say)
        speaker.wait()
        speaker.close()
        return 0

    from jarvis import sounds, tools

    speaker = None if (args.no_brain or args.no_stt) else build_speaker(config)

    # Timers and other delayed tools report back here.
    def notify(message: str) -> None:
        print(f"\nJARVIS: {message}")
        if speaker:
            speaker.say(message, sound=sounds.ALERT if config.chime else None)
        elif config.chime:
            sounds.play(sounds.ALERT)

    tools.set_notifier(notify)

    brain = None if (args.no_brain or args.no_stt) else build_brain(config, args.debug)
    respond = make_responder(brain, speaker, config.fast_path) if brain else None

    try:
        if args.text:
            if respond is None:
                print("--text needs the brain; drop --no-brain.")
                return 1
            run_text_mode(respond, speaker)
            return 0
        return run_voice_mode(config, args, respond, speaker)
    finally:
        if speaker:
            speaker.close()


def run_voice_mode(config: Config, args, respond, speaker) -> int:
    # Imports are deferred so --help, --list-devices and --text start quickly.
    from jarvis.audio import FileStream, MicStream
    from jarvis.pipeline import VoicePipeline
    from jarvis.stt import Transcriber

    transcriber = None
    if not args.no_stt and respond:
        # Tell Whisper the names it should expect, so "Racer" isn't heard as "Laser".
        from jarvis.tools import unity
        names = sorted(unity.find_projects())
        if names:
            config.initial_prompt = f"{config.initial_prompt or ''} Unity projects: {', '.join(names)}.".strip()
            print(f"Whisper primed with {len(names)} Unity project names")
    if not args.no_stt:
        print(f"Loading Whisper '{config.whisper_model}' (first run downloads it)...")
        transcriber = Transcriber(
            model=config.whisper_model,
            device=config.whisper_device,
            compute_type=config.whisper_compute_type,
            language=config.language,
            beam_size=config.beam_size,
            initial_prompt=config.initial_prompt,
        )
        print(f"Whisper ready on {transcriber.device}")

    if args.file:
        source = FileStream(args.file, realtime=args.realtime)
        if not args.realtime:
            config.chime = False  # chimes would overlap when running faster than real time
    else:
        source = MicStream(device=config.input_device)

    def on_command(text: str, transcript) -> None:
        if transcript is None:
            print(">> (command recorded, STT disabled)")
            return
        print(f'>> "{text}"   ({transcript.seconds_of_audio:.1f}s audio, '
              f"transcribed in {transcript.seconds_to_transcribe:.2f}s)")
        if respond:
            respond(text)

    ducker = None
    if config.duck:
        from jarvis.ducking import Ducker
        ducker = Ducker(level=config.duck_level)
        if not ducker.available:
            ducker = None

    pipeline = VoicePipeline(config, source, transcriber, speaker=speaker, ducker=ducker,
                             debug=args.debug)
    try:
        pipeline.run(on_command)
        if speaker:
            speaker.wait(timeout=30)  # --file mode: let the last reply finish
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        source.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
