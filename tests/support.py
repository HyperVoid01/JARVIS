"""A fake PC for tests: real folders in a temp directory, nothing actually launched.

    class MyTest(JarvisTest):
        def test_something(self):
            self.add_file("Documents/WIL/REFLECTIVE REPORT.docx", docx="This term we built...")
            r = self.say("Where's my world effective report?")
            self.assertIn("WIL folder in Documents", r.reply)

`say()` runs a command the way main.py's responder does: the fast path first,
then (where the LLM would answer) the prefetched tool calls, then whatever
learning.py adds to the reply. The LLM itself is never called: `r.fast` says
whether the fast path handled it, and `r.prefetched` holds the tool results the
LLM would have been given.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from jarvis import fastpath, learning, tools
from jarvis.tools import devices, files, memory, system, unity, workspaces


@dataclass
class Said:
    reply: str
    fast: bool
    prefetched: list[tuple[str, dict, str]] = field(default_factory=list)
    extra: str = ""   # what learning.py added (a question, reminder or offer)

    def __str__(self) -> str:
        return self.reply


class JarvisTest(unittest.TestCase):
    user = "Sooriyan"
    daily_questions = False   # tests about other things shouldn't get "By the way, sir..." on replies

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jarvis-test-"))
        for d in ("Desktop", "Documents", "Downloads", "UNITY Projects"):
            (self.tmp / d).mkdir()
        self.launched: list[str] = []   # every file, folder, app or URL "opened"
        self.unity_projects: dict[str, Path] = {}
        self._saved = []

        # Where JARVIS keeps its state: all inside the temp folder.
        memory.configure(self.tmp / "memory.json", user=self.user)
        workspaces.configure(self.tmp / "workspaces.json")
        learning.configure(self.tmp / "learning.json", self.user, questions=self.daily_questions)
        files.ROOTS = {name: self.tmp / name for name in ("Desktop", "Documents", "Downloads", "UNITY Projects")}

        # Nothing real gets launched.
        self._patch(os, "startfile", lambda p: self.launched.append(Path(p).name), create=True)
        self._patch(system, "open_app", lambda name: self._launch(name))
        self._patch(system, "open_website", lambda url: self._launch(url))
        self._patch(unity, "find_projects", lambda: dict(self.unity_projects))
        self._patch(unity, "open_unity_project", lambda name: self._launch(f"Unity:{name}", f"Opening {name} in Unity."))
        self._patch(tools._REGISTRY["window"], "func", lambda action, app="", confirmed=False: self._window(action, app))
        self._patch(learning, "_quiet", lambda: False)  # tests run at any hour
        self.installed_apps = {"discord", "spotify", "chrome", "google chrome", "visual studio code", "vs code",
                               "notion", "unity hub", "blender"}
        self.word_has_open: set[str] = set()   # file names Word is "holding open" (locked)
        real_read = files._read_text
        self._patch(files, "_read_text", lambda p: self._read(p, real_read))

        self._reset_state()
        files.refresh(wait=True)

    def tearDown(self):
        for obj, name, old, existed in reversed(self._saved):
            if existed:
                setattr(obj, name, old)
            else:
                delattr(obj, name)
        self._reset_state()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- helpers -------------------------------------------------------------

    def say(self, text: str) -> Said:
        """One command, as main.py's responder handles it (minus the LLM)."""
        fast = fastpath.handle(text)
        tools.begin_turn()
        if fast is not None:
            reply, prefetched, is_fast = fast[0], [], True
        else:
            prefetched = [(n, a, tools.call(n, a)) for n, a in fastpath.prefetch(text)]
            reply, is_fast = "(LLM reply)", False
        extra = learning.after_reply(text, reply)
        return Said(f"{reply} {extra}".strip() if extra else reply, is_fast, prefetched, extra)

    def add_file(self, rel: str, text: str | None = None, docx: str | None = None) -> Path:
        path = self.tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if docx is not None:
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("word/document.xml", f"<w:document><w:body><w:p><w:r><w:t>{docx}</w:t></w:r></w:p>"
                                                "</w:body></w:document>")
        else:
            path.write_text(text or "", encoding="utf-8")
        files.refresh(wait=True)
        return path

    def add_folder(self, rel: str) -> Path:
        path = self.tmp / rel
        path.mkdir(parents=True, exist_ok=True)
        files.refresh(wait=True)
        return path

    def add_unity_project(self, name: str, git_remote: str | None = None) -> Path:
        path = self.add_folder(f"UNITY Projects/{name}")
        (path / "Assets").mkdir(exist_ok=True)
        if git_remote:
            (path / ".git").mkdir()
            (path / ".git" / "config").write_text(f'[remote "origin"]\n\turl = {git_remote}\n')
        self.unity_projects[name] = path
        return path

    def note(self, name: str) -> str:
        return (files.notes_dir() / f"{name}.txt").read_text(encoding="utf-8")

    def facts(self) -> list[str]:
        return [f["text"] for f in memory.load()]

    # --- internals -----------------------------------------------------------

    def _patch(self, obj, name, value, create=False):
        existed = hasattr(obj, name)
        if not existed and not create:
            raise AttributeError(name)
        self._saved.append((obj, name, getattr(obj, name, None), existed))
        setattr(obj, name, value)

    def _launch(self, what: str, reply: str | None = None) -> str:
        if not what.startswith(("Unity:", "http")) and "." not in what and what.lower() not in self.installed_apps:
            return f"Error: couldn't find an app called '{what}'."
        self.launched.append(what)
        return reply or f"Opened {what}."

    def _window(self, action: str, app: str) -> str:
        if action == "close" and app.lower() == "word":
            self.word_has_open.clear()
        self.launched.append(f"window:{action}:{app}")
        return f"Closed {app}."

    def _read(self, path: Path, real_read):
        if path.name in self.word_has_open:
            raise PermissionError(13, "Permission denied", str(path))
        return real_read(path)

    @staticmethod
    def _reset_state():
        """Forget anything a previous test left in module globals."""
        workspaces._pending = None
        files._index, files._index_at, files._building = [], 0.0, False
        files._last_path = files._last_note = files._pending_note = files._pending_read = None
        files._app_closing_until = 0.0
        learning._pending, learning._just_answered, learning._busy_turn = None, False, False
        learning._about_cache, learning._last_activity = None, 0.0
        tools._pending.clear()
        devices.set_stream(None)
        fastpath._calls.clear()
