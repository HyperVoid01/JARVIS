"""Microphone switching: list input devices, pick one by spoken name, remember the choice.

Mics are matched and saved by NAME, not index, because Windows renumbers
devices whenever a USB headset or webcam is plugged in or out. The choice is
saved to user_settings.json so JARVIS starts on the same mic next time.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from . import tool
from .speech_match import speech_key

SETTINGS_PATH = Path(__file__).resolve().parents[2] / "user_settings.json"

# Windows lists every mic once per audio API. Use one API, best first.
# DirectSound gives full names (MME cuts them at 31 characters), and both
# resample to 16 kHz for us. WASAPI is skipped: in shared mode it refuses
# any sample rate except the device's own.
_PREFERRED_APIS = ("Windows DirectSound", "MME")
_SKIP_NAMES = ("primary sound capture", "sound mapper",
               # Loopback "mics" record what the speakers play, so JARVIS would hear itself.
               "stereo mix", "what u hear", "wave out mix", "loopback", "mixage stéréo")

# What people call a mic -> words that appear in its Windows name.
_SYNONYMS = {
    "handset": "headset", "headsets": "headset",  # Whisper often hears "handset"
    "headphones": "headset", "headphone": "headset", "earbuds": "headset", "earphones": "headset",
    "airpods": "headset", "buds": "headset", "bluetooth": "headset", "wireless": "headset",
    # Bluetooth headsets often show only their model number ("WH-1000XM5"), not the brand.
    "sony": "headset", "bose": "headset", "jbl": "headset", "beats": "headset", "jabra": "headset",
}

# Words that say nothing about WHICH mic the user means.
_FILLER = {"mic", "mics", "microphone", "microphones", "the", "my", "to", "input", "device",
           "use", "using", "switch", "change", "please", "a", "an", "audio", "number", "no"}
_DEFAULT_WORDS = {"default", "system", "windows", "normal", "usual"}
_BUILTIN_WORDS = {"laptop", "builtin", "built", "internal", "computer", "pc", "onboard"}
_BUILTIN_HINTS = ("array", "realtek", "internal", "built-in", "intel", "amd")
_NUMBER_WORDS = {"one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3,
                 "four": 4, "fourth": 4, "five": 5, "fifth": 5, "six": 6, "seven": 7,
                 "eight": 8, "nine": 9, "ten": 10}

# The live MicStream, registered by main.py in voice mode. None in --text / --file mode.
_mic = None


def set_stream(stream) -> None:
    global _mic
    _mic = stream


# --- Saved settings --------------------------------------------------------

def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_setting(key: str, value) -> None:
    data = load_settings()
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    try:
        SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"[settings] couldn't save {SETTINGS_PATH}: {e}")


# --- Devices ---------------------------------------------------------------

def input_devices() -> list[dict]:
    """[{"index": sounddevice index, "name": ...}], each microphone listed once."""
    import sounddevice as sd

    apis = sd.query_hostapis()
    inputs = [(i, d) for i, d in enumerate(sd.query_devices())
              if d["max_input_channels"] > 0
              and not any(s in d["name"].lower() for s in _SKIP_NAMES)]

    chosen = inputs
    for api_name in _PREFERRED_APIS:
        api_ids = {i for i, a in enumerate(apis) if a["name"] == api_name}
        in_api = [(i, d) for i, d in inputs if d["hostapi"] in api_ids]
        if in_api:
            chosen = in_api
            break

    seen: set[str] = set()
    out: list[dict] = []
    for i, d in chosen:
        name = d["name"].strip()
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append({"index": i, "name": name})
    return out


def device_name(index: int | str | None) -> str:
    """Readable name of a device index (None = the Windows default mic)."""
    import sounddevice as sd
    try:
        if index is None:
            index = sd.default.device[0]
        name = sd.query_devices(index)["name"].strip()
    except Exception:
        return "the default microphone"
    # The default is an MME device, whose name is cut at 31 characters. Use the full one.
    try:
        for d in input_devices():
            if _same(d["name"], name):
                return d["name"]
    except Exception:
        pass
    return name


def _same(a: str, b: str) -> bool:
    """Names match, allowing for MME's 31-character truncation."""
    a, b = a.lower().strip(), b.lower().strip()
    return a == b or a.startswith(b) or b.startswith(a)


def find_device(saved_name: str) -> int | None:
    """Index of a mic saved by name, or None if it isn't connected."""
    for d in input_devices():
        if _same(d["name"], saved_name):
            return d["index"]
    return None


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def _word_match(spoken: str, word: str) -> bool:
    if spoken == word:
        return True
    if len(spoken) >= 3 and word.startswith(spoken):   # "head" -> "headset"
        return True
    if len(word) >= 3 and spoken.startswith(word):     # "hyperx2" -> "hyperx"
        return True
    return speech_key(spoken) == speech_key(word)      # misheard: "weal tek" ~ "realtek"


def match_device(spoken: str, devices: list[dict]) -> tuple[dict | None, str]:
    """(device, "") for what the user said, or (None, error message)."""
    query = [_SYNONYMS.get(w, w) for w in _words(spoken) if w not in _FILLER]
    if not query:
        return None, "Error: which microphone? " + _list_text(devices)

    # "number 2", "the second one", "mic three", or just "3": pick from the list order.
    numbers = [w for w in query if w.isdigit() or w in _NUMBER_WORDS]
    if len(numbers) == len(query):
        word = next((w for w in numbers if w != "one"), numbers[0])  # "second one" -> second
        n = int(word) if word.isdigit() else _NUMBER_WORDS[word]
        if 1 <= n <= len(devices):
            return devices[n - 1], ""
        return None, f"Error: there's no microphone number {n}. " + _list_text(devices)

    # "laptop mic" / "built-in mic": Windows names those after the chip or "Microphone Array".
    if set(query) & _BUILTIN_WORDS:
        builtin = [d for d in devices if any(h in d["name"].lower() for h in _BUILTIN_HINTS)]
        if len(builtin) == 1:
            return builtin[0], ""

    joined = speech_key("".join(query))  # "weal tek" -> one word, compared by sound
    scored = []
    for d in devices:
        dwords = _words(d["name"])
        if len(joined) >= 4 and joined in speech_key(d["name"]):
            scored.append((1.0, d))
            continue
        hits = sum(1 for w in query if any(_word_match(w, dw) for dw in dwords))
        scored.append((hits / len(query), d))

    best = max(score for score, _ in scored)
    if best < 0.5:
        return None, f"Error: no microphone matches '{spoken}'. " + _list_text(devices)
    top = [d for score, d in scored if score == best]
    if len(top) > 1:
        names = " or ".join(d["name"] for d in top)
        return None, f"Error: that could be {names}. Which one?"
    return top[0], ""


def _list_text(devices: list[dict], current: str | None = None) -> str:
    items = [f"{n}. {d['name']}" + (" (in use)" if current and _same(d["name"], current) else "")
             for n, d in enumerate(devices, 1)]
    return f"{len(devices)} microphones: " + "; ".join(items) + "."


# --- Tool ------------------------------------------------------------------

@tool
def microphone(action: Literal["list", "check", "switch"], name: str = "") -> str:
    """List the microphones, say which one is in use, or switch which mic JARVIS listens with. Use for any request about a mic, microphone, headset, headphones or webcam mic (not for switching between apps).

    Args:
        action: list (all mics), check (which one is in use), or switch.
        name: For switch: the mic as the user described it, e.g. "headset", "webcam", "Blue Yeti", "default", or its number from the list.
    """
    try:
        import sounddevice  # noqa: F401
    except ImportError:
        return "Error: the sounddevice package isn't installed."
    try:
        devices = input_devices()
    except Exception as e:
        return f"Error: couldn't read the audio devices: {e}"
    if not devices:
        return "Error: no microphones found."

    if _mic is not None:
        current = device_name(_mic.device)
    else:
        current = load_settings().get("microphone") or device_name(None)

    if action == "list":
        return _list_text(devices, current)

    if action == "check":
        if _mic is None:
            return f"No microphone is open right now. Voice mode will use {current}."
        return f"Using {current}."

    # --- switch ---
    if set(_words(name)) & _DEFAULT_WORDS:
        target, label = None, "the default microphone"
    else:
        dev, error = match_device(name, devices)
        if dev is None:
            return error
        target, label = dev["index"], dev["name"]

    saved_value = None if target is None else label

    if _mic is None:
        save_setting("microphone", saved_value)
        return f"Saved. Voice mode will use {label} next time it starts."

    if target is not None and _same(current, label):
        return f"Already using {label}."

    try:
        _mic.switch(target)
    except Exception as e:
        return f"Error: couldn't open {label} ({e}). Still using {current}."

    save_setting("microphone", saved_value)
    print(f"[audio] now listening on {device_name(target)}")
    return f"Switched to {label}."
