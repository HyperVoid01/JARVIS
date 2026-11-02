"""JARVIS entry point.

    python main.py                         # listen on the default mic
    python main.py --list-devices          # find your mic's index
    python main.py --input-device 3        # use a specific mic
    python main.py --whisper-model base.en # faster, less accurate STT
    python main.py --file test.wav         # run a recording through the pipeline
    python main.py --debug                 # print live wake-word scores

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
    p = argparse.ArgumentParser(description="JARVIS voice assistant - wake word + speech-to-text")
    p.add_argument("--list-devices", action="store_true", help="list audio devices and exit")
    p.add_argument("--file", help="feed a WAV file instead of the microphone (for testing)")
    p.add_argument("--realtime", action="store_true", help="with --file, play at real speed")
    p.add_argument("--debug", action="store_true", help="print wake word scores as they come in")
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
        else:
            p.add_argument(flag, type=_parse_optional_str, default=field.default,
                           help="use 'none' to unset" if _allows_none(hint) else None)
    return p


def _allows_none(hint) -> bool:
    return isinstance(hint, types.UnionType) and type(None) in hint.__args__


def _parse_optional_str(value: str):
    if value.lower() == "none":
        return None
    if value.isdigit():  # device indexes
        return int(value)
    return value


def main() -> int:
    args = build_parser().parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        print("\nPass the number of your microphone with --input-device N")
        return 0

    config = Config(**{f.name: getattr(args, f.name) for f in dataclasses.fields(Config)})

    # Imports are deferred so --help and --list-devices start instantly.
    from jarvis.audio import FileStream, MicStream
    from jarvis.pipeline import VoicePipeline
    from jarvis.stt import Transcriber

    transcriber = None
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
        # ---------------------------------------------------------------
        # This is where the "brain" plugs in next (LLM + tool calling).
        # ---------------------------------------------------------------
        if transcript is None:
            print(">> (command recorded, STT disabled)")
            return
        print(f'>> "{text}"   ({transcript.seconds_of_audio:.1f}s audio, '
              f"transcribed in {transcript.seconds_to_transcribe:.2f}s)")

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
