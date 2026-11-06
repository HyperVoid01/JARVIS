"""Tool registry - the things JARVIS can actually *do*.

Write a normal Python function, put @tool on it, and it becomes available to
the LLM. The JSON schema the model sees is generated from the function's type
hints and docstring, so the docstring IS the prompt for that tool: keep it
short and say exactly when to use it.

    @tool
    def get_weather(city: str) -> str:
        '''Get the current weather for a city.

        Args:
            city: City name, e.g. "Durban".
        '''
        ...

Supported parameter types: str, int, float, bool, and Literal["a", "b"]
(which becomes an enum the model must pick from). Parameters with a default
value are optional.
"""

from __future__ import annotations

import inspect
import json
import re
import traceback
import typing
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    func: Callable[..., Any]
    schema: dict


_REGISTRY: dict[str, Tool] = {}

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}

# Tools that finish later (timers) report back through this. main.py points it
# at the Speaker so they're announced out loud.
_notifier: Callable[[str], None] = print


def set_notifier(fn: Callable[[str], None]) -> None:
    global _notifier
    _notifier = fn


def notify(message: str) -> None:
    _notifier(message)


# --- Turns -----------------------------------------------------------------
# Every user command is one "turn". Tools that need a yes from the user
# (shutdown, force-quit) remember the turn they asked in, and only accept
# confirmed=true from a LATER turn, so the model can't ask and answer itself.

_turn = 0


def begin_turn() -> None:
    global _turn
    _turn += 1


def current_turn() -> int:
    return _turn


_pending: dict[str, int] = {}  # confirmation key -> turn it was asked in


def confirmation(key: str, question: str, confirmed: bool) -> str | None:
    """None if the user has confirmed `key`, otherwise the message telling the model to ask.

    Confirmation counts only if this tool asked in the previous turn or the one before.
    """
    asked = _pending.get(key)
    if confirmed and asked is not None and 0 < _turn - asked <= 2:
        del _pending[key]
        return None
    _pending[key] = _turn
    return (f"Not done yet: this needs the user's OK. Ask them exactly: \"{question}\" "
            f"If they say yes, call this tool again with confirmed=true. "
            f"Do not set confirmed=true until they have answered.")


# --- Registry --------------------------------------------------------------

def tool(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator: register `func` as a tool the LLM can call."""
    _REGISTRY[func.__name__] = Tool(func.__name__, func, _build_schema(func))
    return func


def all_tools() -> list[Tool]:
    return list(_REGISTRY.values())


def schemas(names: list[str] | None = None) -> list[dict]:
    return [t.schema for t in _REGISTRY.values() if names is None or t.name in names]


def call(name: str, arguments: dict | str | None) -> str:
    """Run a tool by name and return its result as text for the LLM.

    Never raises: errors are returned as text so the model can tell the user
    what went wrong instead of the whole assistant crashing.
    """
    t = _REGISTRY.get(name)
    if t is None:
        return f"Error: there is no tool called '{name}'."

    if isinstance(arguments, str):  # some models send arguments as a JSON string
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return f"Error: could not read the arguments for {name}."
    arguments = arguments or {}

    # Drop arguments the function doesn't accept (small models sometimes invent extras).
    params = inspect.signature(t.func).parameters
    arguments = {k: v for k, v in arguments.items() if k in params}

    try:
        result = t.func(**arguments)
    except TypeError as e:
        return f"Error: bad arguments for {name}: {e}"
    except Exception as e:
        traceback.print_exc()
        return f"Error: {name} failed: {e}"
    return result if isinstance(result, str) else json.dumps(result)


# --- Schema generation -----------------------------------------------------

def _build_schema(func: Callable[..., Any]) -> dict:
    doc = inspect.getdoc(func) or ""
    summary, arg_docs = _parse_docstring(doc)
    hints = typing.get_type_hints(func)

    properties: dict[str, dict] = {}
    required: list[str] = []
    for name, param in inspect.signature(func).parameters.items():
        prop = _json_type(hints.get(name, str))
        if name in arg_docs:
            prop["description"] = arg_docs[name]
        properties[name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(name)

    return {
        "type": "function",
        "function": {
            "name": func.__name__,
            "description": summary,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


def _json_type(hint) -> dict:
    origin = typing.get_origin(hint)
    if origin is typing.Literal:
        values = list(typing.get_args(hint))
        return {"type": _JSON_TYPES.get(type(values[0]), "string"), "enum": values}
    if origin in (typing.Union, getattr(__import__("types"), "UnionType", None)):
        inner = [a for a in typing.get_args(hint) if a is not type(None)]
        return _json_type(inner[0]) if inner else {"type": "string"}
    return {"type": _JSON_TYPES.get(hint, "string")}


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Split a Google-style docstring into (summary, {arg: description})."""
    parts = re.split(r"^\s*Args:\s*$", doc, maxsplit=1, flags=re.MULTILINE)
    summary = " ".join(parts[0].split())
    arg_docs: dict[str, str] = {}
    if len(parts) == 2:
        current = None
        for line in parts[1].splitlines():
            m = re.match(r"^\s*(\w+)\s*:\s*(.*)$", line)
            if m:
                current = m.group(1)
                arg_docs[current] = m.group(2).strip()
            elif current and line.strip():
                arg_docs[current] += " " + line.strip()
    return summary, arg_docs


# Importing the modules registers their tools.
from . import media, system, unity, windows  # noqa: E402,F401
