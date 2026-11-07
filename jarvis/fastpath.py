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
_FILLER_START = re.compile(r"^((hey )?jarvis |please |(can|could|would) you |just |ok(ay)? |now |no |so |and |well |"
                           r"actually |um+ |uh+ |sorry |i said )+")
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


# --- Getting to know you -------------------------------------------------------

@_on(r"^(stop|quit|don'?t keep|no more) (asking|ask)( me)?( (questions|things|stuff|about me))?$"
     r"|^don'?t ask me (questions|things)( any ?more)?$")
def _stop_asking(m):
    from . import learning
    return learning.stop_asking()


@_on(r"^ask me (something|a question|anything|a random question)( about (me|myself))?$"
     r"|^get to know me( better)?$")
def _ask_me(m):
    from . import learning
    return learning.ask_now()


# --- Files -----------------------------------------------------------------

@_on(r"^(open|show|show me|go to) (up )?(my |the )?(?P<root>downloads?|desktop|documents?|unity projects?)( folder)?$")
def _open_root(m):
    return _call("files", {"action": "open", "name": m["root"]})


@_on(r"^what('?s| is) in (my |the )?(?P<root>downloads?|desktop|documents?|unity projects?)( folder)?$")
def _list_root(m):
    return _call("files", {"action": "list", "name": m["root"]})


@_on(r"^(where('?s| is| are| did i put)|find) (my |the )(?!(note|file|summary|document|doc|folder)( |$))"
     r"(?P<name>.+?)(?P<kind> file| folder)?( (located|saved|stored|kept))?$")
def _find_file(m):
    kind = (m["kind"] or " any").strip()
    return _call("files", {"action": "find", "name": m["name"], "kind": kind})


# "Where is it?", "where did you save that?": the file or note just mentioned.
# Must be a tool call: left to itself the model guesses ("it's on your Desktop").
@_on(r"^where('?s| is| was| did) (it|that|this|(my|the|that) (note|file|summary|document|doc))"
     r"( (go|located|saved|stored|kept|now|end up))?$"
     r"|^where (did|have) you (put|save|saved|store|stored|write|written|keep|kept) (it|that|this|them|those)$")
def _where_is_it(m):
    return _call("files", {"action": "find", "name": "it"})


_TYPE = (r"file|document|doc|docx|pdf|folder|script|note|notes|report|spreadsheet|presentation|slides|"
         r"scene|essay|assignment|brief|planner|image|picture|video")
_WHERE = r"(from|in|on) (my |the )?(desktop|documents?|downloads?|unity projects?)( folder)?"


# "Open my game trailer document from documents": named files, with a type word or a place,
# so "open my Spotify" still goes to open_app.
@_on(rf"^open (up )?(my |the )(?P<name>.+? ({_TYPE})( {_WHERE})?)$"
     rf"|^open (up )?(my |the )?(?P<name2>.+? {_WHERE})$")
def _open_file(m):
    return _call("files", {"action": "open", "name": m["name"] or m["name2"]})


@_on(r"^(open|show me) (it|that|this)( up)?$")
def _open_it(m):
    return _call("files", {"action": "open", "name": "it"})


@_on(r"^(open|show me|go to) (the|that|this|its) folder( it'?s in)?$")
def _open_its_folder(m):
    return _call("files", {"action": "open", "name": "the folder"})


# Notes are matched on the original text, to keep what's written as it was said.
_LEAD = r"^\W*((hey )?jarvis\W+)?(please\W+)?(can you |could you |would you )?"
_NOTE_NEW = re.compile(
    _LEAD + r"(make|create|start|write|take|open)( me)?( a| a new)? note (called|named|titled) "
    r"(?P<title>[^,:.;]+?)(\s*[,:.;]\s*|\s+(saying|that says|with|and (write|put|add)|that)\s+)(?P<body>.+?)\W*$",
    re.IGNORECASE)
# "note" is often heard as "node" or "knot".
_NOTE_WORD = r"(notes?|nodes?|knots?)"
_NOTE_ADD = re.compile(
    _LEAD + r"(add|put|write|jot down|note down)\s+(?P<body>.+?)\s+(to|in|on|into) (my|the) "
    rf"(?P<title>[\w' -]+?) {_NOTE_WORD}\W*$", re.IGNORECASE)
# "Make a note called ideas." with nothing to put in it yet: JARVIS asks.
_NOTE_EMPTY = re.compile(
    _LEAD + rf"(make|create|start|open)( me)?( a| a new)? {_NOTE_WORD} (called|named|titled) (?P<title>.+?)\W*$",
    re.IGNORECASE)
# "Remove that", "undo that and add a rat boss".
_NOTE_UNDO = re.compile(
    _LEAD + r"(remove|delete|undo|scratch|erase|take out|take back|get rid of) (that|it|the last (one|line|bit|thing))"
    rf"( out| back)?( from (my |the )?[\w' ]+ {_NOTE_WORD})?"
    r"(,?\s+(and|then|and then) (add|put|write)( in)? (?P<body>.+?))?\W*$", re.IGNORECASE)


def _note(text: str) -> str | None:
    from .tools import files
    m = _NOTE_NEW.match(text) or _NOTE_ADD.match(text)
    if m:
        return _spoken(_call("files", {"action": "note", "name": m["title"], "text": m["body"]}))
    m = _NOTE_EMPTY.match(text)
    if m:
        return _spoken(_call("files", {"action": "note", "name": m["title"], "text": ""}))
    m = _NOTE_UNDO.match(text)
    if m and files.last_note_title():
        title = files.last_note_title()
        reply = _call("files", {"action": "undo_note"})
        if m["body"] and not reply.startswith("Error"):
            added = _call("files", {"action": "note", "name": title, "text": m["body"]})
            if not added.startswith("Error"):
                old_text = re.search(r'"(.+?)"', reply)
                new_text = re.search(r'"(.+?)"', added)
                if old_text and new_text:
                    return f'Swapped "{old_text[1]}" for "{new_text[1]}" in your {title} note.'
            reply = f"{reply} {added}"
        return reply
    return None


def _note_answer(text: str) -> str | None:
    """The answer to "what should I put in the X note?"."""
    from .tools import files
    hit = files.answer_pending_note(text)
    if hit is None:
        return None
    name, args = hit
    return _call(name, args)


# --- Reading (the model sums it up) -------------------------------------------
# These need the LLM for the answer, but not to pick the tool: prefetch() runs
# the read and hands the result to the brain, so it can't answer "I can't read files".

_READ_PATTERNS = [
    re.compile(r"^(what|so what) (does|did|do) (it|that|this|the file|that file|the (report|document|doc|note|script)) "
               r"(say|see|said|says|contain|have in it)$"),
    re.compile(r"^what('?s| is) (in|inside) (it|that|this|the file|that file)$"),
    re.compile(r"^(read|summari[sz]e|sum up)( me| out| for me)? (it|that|this)( out| to me| for me)?$"),
    re.compile(r"^(read|summari[sz]e|sum up)( me| out| for me)? (my |the )(?P<name>.+?)( out| to me| for me)?$"),
    re.compile(r"^what (does|did) (my |the )(?P<name>.+?) (say|see|said|says)$"),
    # "What's in my Dream Planner on desktop?" (folders get listed instead; see files read)
    re.compile(r"^what('?s| is) (in|inside|on) (my |the )?(?P<name>.+?)$"),
    re.compile(r"^what('?s| is) (in|inside) (it|that|this)( file| document| folder)?$"),
]


def prefetch(text: str) -> list[tuple[str, dict]]:
    """Tool calls the brain should run before it answers `text` (see Brain.ask)."""
    from .tools import files
    closing = files.answer_pending_read(text)  # "yes" to "shall I close Word and read it?"
    if closing:
        return closing
    s = normalise(text)
    for pattern in _READ_PATTERNS:
        m = pattern.match(s)
        if m:
            name = m.groupdict().get("name") or "it"
            return [("files", {"action": "read", "name": name})]
    # "What's my student number?": look it up in the About Me folder first.
    from . import learning
    if learning.is_question_about_me(text) and learning.about_me_hits(text):
        return [("about_me", {"question": text})]
    return []


# --- Workspaces ------------------------------------------------------------

_ASK = re.compile(r'Ask them exactly: "(.+?)"')


def _spoken(result: str) -> str:
    """A tool result that asks the user something -> just the question."""
    m = _ASK.search(result)
    return m.group(1) if m else result


@_on(r"^((ok(ay)?|right|alright|so) )?(let's|lets|let us|time to|ready to) "
     r"(get (back )?to work|get working|get started|start working|start work|do some work|"
     r"get busy|crack on|work|begin)( on (?P<name>.+))?$"
     r"|^(i'm|i am|we're|we are) (going to |gonna )?(be )?working on (?P<name2>.+)$"
     r"|^(start|open|load|launch) (up )?(my |the )?(?P<name3>.+?) (workspace|setup|work setup)$")
def _start_work(m):
    name = m["name"] or m["name2"] or m["name3"] or ""
    return _spoken(_call("workspace", {"action": "start", "name": name}))


def _workspace_answer(text: str) -> str | None:
    """The answer to "what are we working on?" or "what should I open for X?"."""
    from .tools import workspaces
    hit = workspaces.answer_pending(text)
    if hit is None:
        return None
    name, args = hit
    return _spoken(_call(name, args))


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
    if not m or _NOT_A_FACT.match(m["fact"]) or len(m["fact"].split()) < 2 \
            or re.match(r"(called|named|titled|in|to|on|into)\b", m["fact"], re.IGNORECASE):
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
    # Answers to JARVIS's own questions come first: they must see every command to expire.
    from . import learning
    reply = learning.answer_pending(text)
    if reply is not None:
        return reply, list(_calls)
    reply = _note_answer(text)
    if reply is not None:
        return reply, list(_calls)
    reply = _workspace_answer(text)
    if reply is not None:
        return reply, list(_calls)
    reply = _note(text)  # before _remember: "make a note called X" is a file, not a memory
    if reply is not None:
        return reply, list(_calls)
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
