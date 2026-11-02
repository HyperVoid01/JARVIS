"""JARVIS entry point.

    python main.py                         # voice mode: "Hey Jarvis" + command
    python main.py --text                  # type commands instead (fast way to test the brain)
    python main.py --no-brain              # voice -> text only, no LLM (Stage 1 behaviour)
    python main.py --list-devices          # find your mic's index
    python main.py --input-device 3        # use a specific mic
    python main.py --llm-model qwen3:8b    # try a different Ollama model
    python main.py --file test.wav         # run a recording through the pipeline
    python main.py --debug                 # wake-word scores + raw LLM responses

Every setting in jarvis/config.py is available as --kebab-case-flag.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import types
import typing

from jarvis.config import Config


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="JARVIS voice assistant")
    p.add_argument("--list-devices", action="store_true", help="list audio devices and exit")
    p.add_argument("--text", action="store_true", help="type commands instead of speaking them")
    p.add_argument("--no-brain", action="store_true", help="only transcribe, don't send to the LLM")
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


def build_brain(config: Config, debug: bool):
    from jarvis import tools
    from jarvis.brain import Brain, OllamaError
    from jarvis.tools import unity

    unity.configure(project_dirs=config.unity_project_dirs, editor_root=config.unity_editor_root)

    brain = Brain(
        model=config.llm_model,
        url=config.ollama_url,
        num_ctx=config.llm_num_ctx,
        keep_alive=config.llm_keep_alive,
        history_turns=config.history_turns,
        history_timeout_s=config.history_timeout_s,
        user_name=config.user_name,
        debug=debug,
    )
    print(f"Connecting to Ollama ({config.llm_model})...")
    try:
        brain.check()
        brain.warm_up()
    except OllamaError as e:
        print(f"\n[brain] {e}")
        sys.exit(1)
    print(f"Brain ready with {len(tools.all_tools())} tools: "
          + ", ".join(t.name for t in tools.all_tools()))
    return brain


def make_responder(brain):
    """Returns a function that sends text to the brain and shows the reply."""
    from jarvis.brain import OllamaError

    def respond(text: str) -> None:
        try:
            reply = brain.ask(text)
        except OllamaError as e:
            print(f"[brain] {e}")
            return
        # Stage 3 will speak this out loud.
        print(f"JARVIS: {reply}")

    return respond


def run_text_mode(respond) -> None:
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
            respond(text)


def main() -> int:
    args = build_parser().parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        print("\nPass the number of your microphone with --input-device N")
        return 0

    config = Config(**{f.name: getattr(args, f.name) for f in dataclasses.fields(Config)})

    from jarvis import sounds, tools

    # Timers and other delayed tools report back here.
    def notify(message: str) -> None:
        print(f"\nJARVIS: {message}")
        if config.chime:
            sounds.play(sounds.ALERT)

    tools.set_notifier(notify)

    brain = None if (args.no_brain or args.no_stt) else build_brain(config, args.debug)
    respond = make_responder(brain) if brain else None

    if args.text:
        if respond is None:
            print("--text needs the brain; drop --no-brain.")
            return 1
        run_text_mode(respond)
        return 0

    # Imports are deferred so --help, --list-devices and --text start quickly.
    from jarvis.audio import FileStream, MicStream
    from jarvis.pipeline import VoicePipeline
    from jarvis.stt import Transcriber

    transcriber = None
    if not args.no_stt and brain:
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

    pipeline = VoicePipeline(config, source, transcriber, debug=args.debug)
    try:
        pipeline.run(on_command)
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        source.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
