"""Instant handling for simple commands, skipping the LLM.

"Pause", "next song", "volume 40", "what time is it" don't need a language
model to decide anything, and going through it costs a second or more. These
patterns only match short, unambiguous commands; anything else ("play some
lofi", "pause and then open Discord") still goes to the brain.

The tool result is the spoken reply. The exchange is added to the brain's
memory *as a tool call* (user -> tool call -> result -> reply), exactly as if
the LLM had done it. That matters: if memory showed "pause" answered without a
tool, the model would copy that and start making answers up.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Callable

from . import tools

_APPS = (r"spotify|youtube music|youtube|chrome|edge|firefox|netflix|twitch|vlc|"
         r"soundcloud|apple music|the browser|browser")
_THING = r"(the |my |this )?(music|song|track|video|playback|audio|it|that)"

# Filler around a command: "Hey Jarvis, could you pause the music please?" -> "pause the music"
_FILLER_START = re.compile(r"^((hey )?jarvis |please |(can|could|would) you |just |ok(ay)? |now )+")
_FILLER_END = re.compile(r"( (please|for me|jarvis|now|thanks|thank you))+$")

_PATTERNS: list[tuple[re.Pattern, Callable[[re.Match], str]]] = []
_calls: list[tuple[str, dict, str]] = []  # (tool, arguments, result) for the command being handled


def _call(name: str, args: dict) -> str:
    result = tools.call(name, args)
    _calls.append((name, args, result))
    return result


def _on(pattern: str):
    def register(fn):
        _PATTERNS.append((re.compile(pattern), fn))
        return fn
    return register


def _app(m: re.Match) -> str:
    app = (m.groupdict().get("app") or "").removeprefix("the ")
    return app


# --- Media -----------------------------------------------------------------

@_on(rf"^(pause|stop {_THING})( {_THING})?( on| in)?( (?P<app>{_APPS}))?$")
def _pause(m):
    return _call("media_control", {"action": "pause", "app": _app(m)})


@_on(rf"^(resume|unpause|continue|play)( {_THING})?( on| in)?( (?P<app>{_APPS}))?$")
def _play(m):
    return _call("media_control", {"action": "play", "app": _app(m)})


@_on(rf"^(next|skip)( (this|the|to the next))?( (song|track|video|one|it))?( on (?P<app>{_APPS}))?$")
def _next(m):
    return _call("media_control", {"action": "next", "app": _app(m)})


@_on(r"^((play|go to) )?(the )?(previous|last)( (song|track|video|one))?$|^go back( a (song|track))?$")
def _previous(m):
    return _call("media_control", {"action": "previous", "app": ""})


@_on(r"^what('?s| is) (playing|(this|the) (song|track))( called)?$|^what song is (this|playing)$")
def _now_playing(m):
    return _call("media_control", {"action": "now_playing", "app": ""})


# --- Volume ----------------------------------------------------------------

@_on(r"^((set|turn|change|put) )?(the )?(volume|sound)( level)?( (to|at))? (?P<n>\d{1,3})( ?(percent|%))?$")
def _volume_set(m):
    return _call("volume", {"action": "set", "amount": int(m["n"])})


@_on(r"^(turn )?(it |the volume |the sound |volume )?(?P<dir>up|down)( a (?P<amt>bit|little|lot)( more)?)?$")
def _volume_step(m):
    amount = 30 if m["amt"] == "lot" else 10
    return _call("volume", {"action": m["dir"], "amount": amount})


@_on(r"^turn (?P<dir>up|down) (the )?(volume|sound|music)( a (?P<amt>bit|little|lot)( more)?)?$")
def _volume_turn(m):
    return _call("volume", {"action": m["dir"], "amount": 30 if m["amt"] == "lot" else 10})


@_on(r"^((a )?(bit|little) )?(?P<dir>louder|quieter|softer)( please)?$")
def _volume_word(m):
    return _call("volume", {"action": "up" if m["dir"] == "louder" else "down", "amount": 10})


@_on(r"^(?P<what>mute|unmute)( (it|the sound|the volume|audio|the audio))?$")
def _mute(m):
    return _call("volume", {"action": m["what"]})


@_on(r"^what('?s| is) the volume( at)?$|^how loud is it$")
def _volume_check(m):
    return _call("volume", {"action": "check"})


# --- Power ------------------------------------------------------------------

@_on(r"^(cancel|abort|stop)( the)? (shutdown|shut down|restart|reboot)$|^(don'?t|do not) (shut down|restart)$")
def _cancel_shutdown(m):
    # Instant, so a shutdown can always be stopped even if the LLM is slow or confused.
    return _call("power", {"action": "cancel_shutdown"})


# --- Time ------------------------------------------------------------------

@_on(r"^what('?s| is) the time( now)?$|^what time is it( now)?$|^(the )?time$")
def _time(m):
    _call("get_datetime", {})
    now = dt.datetime.now()
    hour = now.hour % 12 or 12
    return f"It's {hour}:{now.minute:02d} {'AM' if now.hour < 12 else 'PM'}."


@_on(r"^what('?s| is) (the date|today'?s date)( today)?$|^what day is it( today)?$")
def _date(m):
    _call("get_datetime", {})
    now = dt.datetime.now()
    return f"It's {now:%A}, {now.day} {now:%B}."


# --- Entry point -----------------------------------------------------------

def normalise(text: str) -> str:
    s = text.lower().replace("%", " percent")
    s = re.sub(r"[^\w' ]", " ", s)
    s = " ".join(s.split())
    s = _FILLER_START.sub("", s)
    s = _FILLER_END.sub("", s)
    return s.strip()


# "Remember that my favourite food is pizza" is matched on the original text, to keep its capitals.
_REMEMBER = re.compile(
    r"^\W*((hey )?jarvis\W+)?(please\W+)?(can you |could you )?"
    r"(remember|note|make a note|don't forget|do not forget|keep in mind)(\s+that)?[\s,:]+(?P<fact>.+?)\W*$",
    re.IGNORECASE,
)
_NOT_A_FACT = re.compile(r"^(what|when|where|who|which|how|if|whether|me|it|this|that)\b", re.IGNORECASE)


def _remember(text: str) -> str | None:
    m = _REMEMBER.match(text)
    if not m or _NOT_A_FACT.match(m["fact"]) or len(m["fact"].split()) < 2:
        return None  # "remember what I said?", "remember this" -> let the LLM ask what
    from .tools import memory
    result = _call("remember", {"fact": memory.to_fact(m["fact"])})
    if result.startswith("Error"):
        return result
    # Read it back, so a misheard name or a wrong date gets noticed straight away.
    return f"Noted: {memory.said_back(m['fact'])}."


def handle(text: str) -> tuple[str, list[tuple[str, dict, str]]] | None:
    """If `text` is a simple command, carry it out and return (spoken reply, tool calls made).

    Returns None for anything that needs the LLM.
    """
    _calls.clear()
    reply = _remember(text)
    if reply is not None:
        return reply, list(_calls)
    s = normalise(text)
    for pattern, fn in _PATTERNS:
        m = pattern.match(s)
        if m:
            _calls.clear()
            reply = fn(m)
            return reply, list(_calls)
    return None
