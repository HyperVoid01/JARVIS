"""Unity tools: find your projects and open them in the right editor version.

A folder counts as a Unity project if it has Assets/ and
ProjectSettings/ProjectVersion.txt. The editor version is read from that file
and launched from Unity Hub's install folder, so each project opens in the
version it was made with.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from . import tool
from .speech_match import best_match

# Where to look for projects. Override with --unity-project-dirs.
PROJECT_DIRS: list[Path] = [
    Path.home() / "Documents",
    Path.home() / "Desktop",
    Path.home() / "Unity Projects",
    Path.home() / "Documents" / "Unity Projects",
    Path.home() / "Documents" / "GitHub",
    Path("D:/"),
]
EDITOR_ROOT = Path("C:/Program Files/Unity/Hub/Editor")
SEARCH_DEPTH = 3  # how many folders deep to look under each PROJECT_DIRS entry

_SKIP = {"node_modules", "library", "appdata", "$recycle.bin", "windows", "program files",
         "program files (x86)", "system volume information", ".git", ".venv", "venv"}


def configure(project_dirs: list[str] | None = None, editor_root: str | None = None) -> None:
    global PROJECT_DIRS, EDITOR_ROOT, _cache
    if project_dirs:
        PROJECT_DIRS = [Path(os.path.expanduser(p)) for p in project_dirs]
    if editor_root:
        EDITOR_ROOT = Path(editor_root)
    _cache = None


_cache: tuple[float, dict[str, Path]] | None = None


def find_projects() -> dict[str, Path]:
    """{project folder name: path}, cached for a minute."""
    global _cache
    if _cache and time.time() - _cache[0] < 60:
        return _cache[1]

    projects: dict[str, Path] = {}

    def walk(folder: Path, depth: int) -> None:
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        names = {e.name for e in entries}
        if "Assets" in names and (folder / "ProjectSettings" / "ProjectVersion.txt").exists():
            projects.setdefault(folder.name, folder)
            return  # don't look for projects inside a project
        if depth == 0:
            return
        for e in entries:
            if e.is_dir(follow_symlinks=False) and not e.name.startswith(".") \
                    and e.name.lower() not in _SKIP:
                walk(Path(e.path), depth - 1)

    for root in PROJECT_DIRS:
        if root.is_dir():
            walk(root, SEARCH_DEPTH)

    _cache = (time.time(), projects)
    return projects


def editor_version(project: Path) -> str | None:
    text = (project / "ProjectSettings" / "ProjectVersion.txt").read_text(errors="ignore")
    m = re.search(r"m_EditorVersion:\s*(\S+)", text)
    return m.group(1) if m else None


def _match(name: str, options: list[str]) -> str | None:
    return best_match(name, options, cutoff=0.6)


@tool
def list_unity_projects() -> str:
    """List the Unity projects on this PC with their editor versions."""
    projects = find_projects()
    if not projects:
        return "No Unity projects found. The folders searched can be changed with --unity-project-dirs."
    return "; ".join(f"{name} (Unity {editor_version(p) or '?'})" for name, p in sorted(projects.items()))


@tool
def open_unity_project(name: str) -> str:
    """Open one of the user's Unity projects in the Unity editor.

    Args:
        name: The project name exactly as transcribed, even if it looks misspelled (e.g. "Wacer" for "Racer"). Fuzzy matching handles it.
    """
    projects = find_projects()
    match = _match(name, list(projects))
    if not match:
        known = ", ".join(sorted(projects)) or "none found"
        return f"Error: no Unity project called '{name}'. Projects I know: {known}."

    path = projects[match]
    version = editor_version(path)
    exe = EDITOR_ROOT / (version or "") / "Editor" / "Unity.exe"
    if not version or not exe.exists():
        return (f"Error: {match} needs Unity {version or 'unknown version'}, "
                f"which isn't installed in {EDITOR_ROOT}.")

    subprocess.Popen([str(exe), "-projectPath", str(path)])
    return f"Opening {match} in Unity {version}. It may take a minute to load."
