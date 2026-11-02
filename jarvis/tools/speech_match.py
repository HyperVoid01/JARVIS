"""Name matching that tolerates how a name was *spoken*.

Spoken names reach the tools via speech-to-text, so "Racer" may arrive as
"Wacer" or "Waser". `speech_key` folds sounds that are commonly swapped
(r/w, soft c/s, z/s, doubled letters) onto one spelling, so both sides
compare equal.
"""

from __future__ import annotations

import difflib
import re


def plain(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def speech_key(s: str) -> str:
    s = plain(s)
    s = re.sub(r"c(?=[eiy])", "s", s)             # soft c sounds like s: "racer" ~ "raser"
    s = s.replace("wr", "w").replace("r", "w")  # rhotacism: r is said/heard as w
    s = s.replace("z", "s")
    s = re.sub(r"(.)\1+", r"\1", s)               # "rattack" -> "ratack"
    return s


def clean_name(spoken: str, project: bool = True) -> str:
    """Strip descriptions and filler: "Reaser as in Formula 1 in Unity" -> "Reaser".

    With project=False (app names) words like "Unity" are kept, so "Unity Hub" survives.
    """
    s = re.sub(r"\b(as in|like the|like|which is|that's|the one)\b.*$", "", spoken, flags=re.IGNORECASE)
    if project:
        s = re.sub(r"\b(in|on|with|using) unity\b", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\b(my|the|unity|project|game|please)\b", "", s, flags=re.IGNORECASE)
    s = " ".join(s.strip(" .,!?").split())
    return s or spoken.strip()


def best_match(name: str, options: list[str], cutoff: float = 0.6) -> str | None:
    """Pick the option the user most likely meant, or None.

    Tries exact and partial matches on spelling, then on sound, and only
    then falls back to fuzzy matching on sound.
    """
    name = clean_name(name)
    for key in (plain, speech_key):
        wanted = key(name)
        if not wanted:
            continue
        by_key = {key(o): o for o in options}
        if wanted in by_key:
            return by_key[wanted]
        contains = sorted((k for k in by_key if wanted in k or k in wanted), key=len)
        if contains:
            return by_key[contains[0]]

    wanted = speech_key(name)
    by_key = {speech_key(o): o for o in options}
    close = difflib.get_close_matches(wanted, list(by_key), n=1, cutoff=cutoff)
    return by_key[close[0]] if close else None
