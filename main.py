"""JARVIS entry point.

    python main.py                         # voice mode: "Hey Jarvis" + command, spoken replies
    python main.py --text                  # type commands instead (fast way to test the brain)
    python main.py --no-tts                # print replies instead of speaking them
    python main.py --no-brain              # voice -> text only, no LLM (Stage 1 behaviour)
    python main.py --say "Hello, sir."     # test the voice and exit
    python main.py --install-startup       # start JARVIS (in the tray, no window) when you log in
    python main.py --uninstall-startup     # stop doing that
    python main.py --background            # run now the way login does: tray icon, log in jarvis.log
    python main.py --list-devices          # list your microphones and speakers
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
STARTUP_SCRIPT = "JARVIS.cmd"   # the old way of starting at login; removed by --install-startup
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "JARVIS"
LOG_PATH = PROJECT_DIR / "jarvis.log"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="JARVIS voice assistant")
    p.add_argument("--list-devices", action="store_true", help="list audio devices and exit")
    p.add_argument("--text", action="store_true", help="type commands instead of speaking them")
    p.add_argument("--no-brain", action="store_true", help="only transcribe, don't send to the LLM")
    p.add_argument("--say", metavar="TEXT", help="speak TEXT with the current voice settings and exit")
    p.add_argument("--install-startup", action="store_true", help="start JARVIS when you log in to Windows")
    p.add_argument("--uninstall-startup", action="store_true", help="stop starting JARVIS at login")
    p.add_argument("--background", action="store_true",
                   help="no console: tray icon, output to jarvis.log, one copy at a time (used at login)")
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


def _startup_command() -> str:
    """pythonw.exe (no console window) running main.py in background mode."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return f'"{pythonw if pythonw.exists() else exe}" "{PROJECT_DIR / "main.py"}" --background'


def relaunch_detached() -> int:
    """Start this same command under pythonw.exe with no window, and return to the prompt."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(pythonw if pythonw.exists() else exe), str(PROJECT_DIR / "main.py"), *sys.argv[1:]],
        cwd=PROJECT_DIR, env={**os.environ, "JARVIS_DETACHED": "1"}, creationflags=flags,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
    )
    print("JARVIS is running in the background: look for the ring in the system tray.\n"
          f"You can close this window. Log: {LOG_PATH}")
    return 0


def install_startup() -> int:
    """Start JARVIS in the background at login, through Windows' per-user Run list.

    That's the same list Task Manager's Startup tab shows, so JARVIS can also be
    switched off there. No console window: it lives in the tray and logs to jarvis.log.
    """
    if sys.platform != "win32":
        print("--install-startup only works on Windows.")
        return 1
    import winreg

    command = _startup_command()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
    old = _startup_folder() / STARTUP_SCRIPT
    if old.exists():
        old.unlink()  # the old minimised-console script: two would start two JARVISes
        print(f"Removed the old startup script ({old.name}).")
    print("JARVIS will start in the background every time you log in.\n"
          f"  Command: {command}\n"
          f"  Log:     {LOG_PATH}\n"
          "It shows up as a ring in the system tray (click ^ if it's hidden): right-click it to\n"
          "pause, open the log, restart or quit. It also appears in Task Manager > Startup apps.\n"
          "To undo: python main.py --uninstall-startup\n"
          "To start it now without logging out: python main.py --background")
    return 0


def uninstall_startup() -> int:
    removed = False
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, RUN_VALUE)
                removed = True
        except FileNotFoundError:
            pass
    old = _startup_folder() / STARTUP_SCRIPT
    if old.exists():
        old.unlink()
        removed = True
    print("JARVIS won't start at login any more." if removed else "JARVIS wasn't set to start at login.")
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
    from jarvis.tools import files, memory, unity, workspaces

    unity.configure(project_dirs=config.unity_project_dirs, editor_root=config.unity_editor_root)
    memory_path = Path(config.memory_file)
    memory.configure(memory_path if memory_path.is_absolute() else PROJECT_DIR / memory_path,
                     user=config.user_name)
    workspaces.configure(PROJECT_DIR / "workspaces.json")
    files.configure(config.file_dirs)
    files.refresh()  # index the folders in the background so the first search is instant
    from jarvis import learning
    learning.configure(PROJECT_DIR / "learning.json", config.user_name,
                       questions=config.daily_question, reminders=config.deadline_reminders)
    learning.ensure_about_dir()
    print("Files: " + ", ".join(f"{name} ({path})" for name, path in files.ROOTS.items()))

    def extra_context() -> str:
        projects = ", ".join(sorted(unity.find_projects())) or "none found"
        habits = learning.habits_line()
        return (f"The user's Unity projects: {projects}.\n{workspaces.prompt_line()}\n"
                + (habits + "\n" if habits else "") +
                f"\n{memory.prompt_block(config.user_name)}")

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


def build_hud(config: Config):
    """Start the on-screen HUD, or a do-nothing stand-in if it's off or can't open."""
    from jarvis.hud import Hud, NullHud

    if not config.hud:
        return NullHud()
    try:
        return Hud(position=config.hud_position, scale=config.hud_scale).start()
    except Exception as e:
        print(f"[hud] couldn't open the HUD ({type(e).__name__}: {e}); carrying on without it. (--no-hud hides this)")
        return NullHud()


def make_responder(brain, speaker, fast_path: bool = True, hud=None):
    """Returns a function that handles one command, then prints and speaks the reply."""
    from jarvis import fastpath, learning, tools
    from jarvis.brain import OllamaError

    from jarvis.hud import NullHud
    hud = hud or NullHud()

    def respond(text: str) -> None:
        hud.heard(text)
        # Simple commands ("pause", "volume 40", "what time is it") skip the LLM.
        fast = fastpath.handle(text) if fast_path else None
        if fast is not None:
            reply, calls = fast
            tools.begin_turn()
            brain.remember(text, reply, calls)
            print(f"JARVIS: {reply}   (fast path)")
        else:
            hud.state("thinking")
            try:
                reply = brain.ask(text, prefetch=fastpath.prefetch(text) if fast_path else [])
            except OllamaError as e:
                print(f"[brain] {e}")
                reply = "Sorry, I can't reach my language model right now."
            else:
                print(f"JARVIS: {reply}")
        # A deadline reminder, an offer to remember something, or today's question.
        extra = learning.after_reply(text, reply)
        if extra:
            reply = f"{reply} {extra}"
            brain.amend_last_reply(reply)
            print(f"        + {extra}")
        hud.reply(reply)
        hud.state("idle")  # the HUD switches to "speaking" by itself while the reply plays
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


def list_devices() -> None:
    import sounddevice as sd

    from jarvis.tools import devices

    mics = devices.input_devices()
    saved = devices.load_settings().get("microphone")
    print("Microphones:")
    for d in mics:
        mark = "  <- saved choice" if saved and devices._same(d["name"], saved) else ""
        print(f"  {d['index']:>3}  {d['name']}{mark}")
    print(f"Windows default mic: {devices.device_name(None)}")

    print("\nSpeakers:")
    seen = set()
    for i, d in enumerate(sd.query_devices()):
        name = d["name"].strip()
        if d["max_output_channels"] > 0 and name.lower() not in seen:
            seen.add(name.lower())
            print(f"  {i:>3}  {name}")

    print("\nSay \"Hey Jarvis, switch to my headset\" to change mic (it's remembered),")
    print("or pass --input-device N (mic) / --output-device N (speakers) for one run.")


def pick_microphone(config: Config) -> int | str | None:
    """--input-device wins; otherwise the mic saved last time; otherwise the Windows default."""
    if config.input_device is not None:
        return config.input_device

    from jarvis.tools import devices

    saved = devices.load_settings().get("microphone")
    if not saved:
        return None
    index = devices.find_device(saved)
    if index is None:
        print(f"[audio] saved mic '{saved}' isn't connected; using the Windows default")
    return index


def main() -> int:
    args = build_parser().parse_args()

    if args.list_devices:
        list_devices()
        return 0
    if args.install_startup:
        return install_startup()
    if args.uninstall_startup:
        return uninstall_startup()

    # Started at login (or with pythonw): no console, so log to a file and allow one copy only.
    from jarvis import background
    if args.background and background.has_console() and not os.environ.get("JARVIS_DETACHED"):
        # Typed into cmd: run it detached instead, or closing the window would close JARVIS too.
        return relaunch_detached()
    log_path = None
    if args.background or not background.has_console():
        args.background = True
        os.chdir(PROJECT_DIR)  # login starts us somewhere else; relative paths must still work
        log_path = background.log_to_file(LOG_PATH)
    if not background.single_instance(wait_s=15 if args.background else 0):
        print("JARVIS is already running" + (" (look for the ring in the system tray)." if sys.platform == "win32" else "."))
        return 1

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

    hud = build_hud(config)
    hud.state("thinking", "starting up")
    speaker = None if (args.no_brain or args.no_stt) else build_speaker(config)
    if speaker:
        hud.attach_speaker(speaker)

    # Timers and other delayed tools report back here.
    def notify(message: str) -> None:
        print(f"\nJARVIS: {message}")
        hud.reply(message)
        if speaker:
            speaker.say(message, sound=sounds.ALERT if config.chime else None)
        elif config.chime:
            sounds.play(sounds.ALERT)

    tools.set_notifier(notify)
    if not (args.no_brain or args.no_stt):
        from jarvis import learning
        # Say a deadline that's today or tomorrow out loud, if you're around but quiet.
        learning.start_reminder_thread(notify, busy=lambda: bool(speaker and speaker.speaking))

    brain = None if (args.no_brain or args.no_stt) else build_brain(config, args.debug)
    respond = make_responder(brain, speaker, config.fast_path, hud) if brain else None

    try:
        if args.text:
            if respond is None:
                print("--text needs the brain; drop --no-brain.")
                return 1
            hud.state("idle")
            run_text_mode(respond, speaker)
            return 0
        return run_voice_mode(config, args, respond, speaker, hud, log_path)
    finally:
        if speaker:
            speaker.close()
        hud.close()


def run_voice_mode(config: Config, args, respond, speaker, hud=None, log_path=None) -> int:
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
        from jarvis.tools import devices
        source = open_microphone(config, wait=args.background)
        devices.set_stream(source)  # lets the microphone tool switch it live
        print(f"Microphone: {devices.device_name(source.device)}")

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
                             hud=hud, debug=args.debug)

    tray = None
    if args.background and not args.file:
        from jarvis.tray import Tray

        def pause(paused: bool) -> None:
            (pipeline.paused.set if paused else pipeline.paused.clear)()
            if paused and speaker:
                speaker.stop()

        def quit_jarvis() -> None:
            print("[tray] quitting")
            if speaker:
                speaker.stop()
            pipeline.stopped.set()

        def restart() -> None:
            print("[tray] restarting")
            from jarvis import background
            subprocess.Popen([sys.executable, str(PROJECT_DIR / "main.py"), *sys.argv[1:]], cwd=PROJECT_DIR,
                             env={**os.environ, "JARVIS_DETACHED": "1"},
                             creationflags=0x00000008 if sys.platform == "win32" else 0)
            background.release_instance()  # the new copy waits for this
            quit_jarvis()

        tray = Tray(pause, quit_jarvis, restart, log_path, PROJECT_DIR).start()

    try:
        pipeline.run(on_command)
        if speaker:
            speaker.wait(timeout=30)  # --file mode: let the last reply finish
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        source.close()
        if tray:
            tray.stop()
    return 0


def open_microphone(config: Config, wait: bool = False):
    """Open the saved mic (or --input-device, or the default).

    Right after logging in, a Bluetooth headset can take a few seconds to connect
    and audio devices can still be starting, so with `wait` it keeps trying for a while.
    """
    from jarvis.audio import MicStream
    from jarvis.tools import devices

    deadline = time.monotonic() + (60 if wait else 0)
    saved = devices.load_settings().get("microphone") if config.input_device is None else None
    while True:
        mic = pick_microphone(config)
        if saved and mic is None and wait and time.monotonic() < deadline - 40:
            # The saved mic isn't connected yet: give it up to 20 seconds before using the default.
            _refresh_audio_devices()
            time.sleep(2)
            continue
        try:
            return MicStream(device=mic)
        except Exception as e:
            if mic is not None:
                print(f"[audio] couldn't open {devices.device_name(mic)} ({e}); using the Windows default")
                try:
                    return MicStream(device=None)
                except Exception as e2:
                    e = e2
            if time.monotonic() > deadline:
                raise
            print(f"[audio] no microphone yet ({e}); trying again")
            _refresh_audio_devices()
            time.sleep(3)


def _refresh_audio_devices() -> None:
    """PortAudio lists devices once at start; make it look again (a headset may have connected)."""
    try:
        import sounddevice as sd
        sd._terminate()
        sd._initialize()
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
