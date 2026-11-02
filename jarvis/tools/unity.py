"""Unity tools: find your projects and open them in the right editor version.

A folder counts as a Unity project if it has Assets/ and
ProjectSettings/ProjectVersion.txt. The editor version is read from that file
and launched directly, so each project opens in the version it was made with.

Installed editors are found the same way Unity Hub finds them:
  - Hub's default install folder (C:/Program Files/Unity/Hub/Editor)
  - Hub's custom install folder, if you changed it (Preferences -> Installs)
  - editors you added with "Locate" in Hub (stored in Hub's editors JSON files)
  - --unity-editor-root, if you pass it
"""

from __future__ import annotations

import json
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
DEFAULT_EDITOR_ROOT = Path("C:/Program Files/Unity/Hub/Editor")
EDITOR_ROOTS: list[Path] = [DEFAULT_EDITOR_ROOT]
SEARCH_DEPTH = 3  # how many folders deep to look under each PROJECT_DIRS entry

_SKIP = {"node_modules", "library", "appdata", "$recycle.bin", "windows", "program files",
         "program files (x86)", "system volume information", ".git", ".venv", "venv"}

_VERSION_RE = re.compile(r"\d{4,}\.\d+\.\d+[abfpx]\d+")


def configure(project_dirs: list[str] | None = None, editor_root: str | None = None) -> None:
    global PROJECT_DIRS, EDITOR_ROOTS, _cache, _editor_cache
    if project_dirs:
        PROJECT_DIRS = [Path(os.path.expanduser(p)) for p in project_dirs]
    if editor_root:
        root = Path(editor_root)
        EDITOR_ROOTS = [root] + ([DEFAULT_EDITOR_ROOT] if root != DEFAULT_EDITOR_ROOT else [])
    _cache = None
    _editor_cache = None


# --- Projects --------------------------------------------------------------

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


def match_project(name: str) -> str | None:
    """Best-matching project name for what the user said, or None."""
    return _match(name, list(find_projects()))


# --- Editors ---------------------------------------------------------------

_editor_cache: tuple[float, dict[str, Path], list[Path]] | None = None


def _hub_config_dir() -> Path:
    return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "UnityHub"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _editors_from_hub_json(data, found: dict[str, Path]) -> None:
    """Pull every Unity.exe path out of Hub's editors.json / editors-v2.json, whatever their layout."""
    if isinstance(data, dict):
        version = data.get("version") if isinstance(data.get("version"), str) else None
        for value in data.values():
            if isinstance(value, str):
                _add_exe(value, version, found)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        _add_exe(item, version, found)
                    else:
                        _editors_from_hub_json(item, found)
            else:
                _editors_from_hub_json(value, found)
    elif isinstance(data, list):
        for item in data:
            _editors_from_hub_json(item, found)


def _add_exe(path_str: str, version: str | None, found: dict[str, Path]) -> None:
    if not path_str.lower().endswith("unity.exe"):
        return
    exe = Path(path_str)
    if not version:
        m = _VERSION_RE.search(path_str)
        version = m.group(0) if m else None
    if version and exe.exists():
        found.setdefault(version, exe)


def find_editors() -> tuple[dict[str, Path], list[Path]]:
    """({version: Unity.exe}, [folders searched]), cached for a minute."""
    global _editor_cache
    if _editor_cache and time.time() - _editor_cache[0] < 60:
        return _editor_cache[1], _editor_cache[2]

    roots = list(EDITOR_ROOTS)
    hub = _hub_config_dir()

    # Custom install folder set in Hub -> Preferences -> Installs.
    secondary = _read_json(hub / "secondaryInstallPath.json")
    if isinstance(secondary, str) and secondary.strip():
        roots.append(Path(secondary.strip()))

    found: dict[str, Path] = {}
    for root in roots:
        try:
            children = list(os.scandir(root))
        except OSError:
            continue
        for child in children:
            exe = Path(child.path) / "Editor" / "Unity.exe"
            if child.is_dir() and exe.exists():
                m = _VERSION_RE.search(child.name)
                found.setdefault(m.group(0) if m else child.name, exe)

    # Editors added with "Locate" in Hub.
    for name in ("editors-v2.json", "editors.json"):
        data = _read_json(hub / name)
        if data is not None:
            _editors_from_hub_json(data, found)

    _editor_cache = (time.time(), found, roots)
    return found, roots


def _open_unity_hub() -> bool:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Unity Hub" / "Unity Hub.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Unity Hub" / "Unity Hub.exe",
    ]
    for exe in candidates:
        if exe.exists():
            subprocess.Popen([str(exe)])
            return True
    return False


# --- Tools -----------------------------------------------------------------

@tool
def list_unity_projects() -> str:
    """List the Unity projects on this PC with their editor versions."""
    projects = find_projects()
    if not projects:
        return "No Unity projects found. The folders searched can be changed with --unity-project-dirs."
    return f"{len(projects)} projects: " + "; ".join(
        f"{name} (Unity {editor_version(p) or '?'})" for name, p in sorted(projects.items()))


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
    editors, searched = find_editors()
    exe = editors.get(version or "")

    if exe is None:
        print(f"[unity] editor {version} not found. Installed: {', '.join(sorted(editors)) or 'none'}. "
              f"Searched: {', '.join(str(r) for r in searched)}")
        hub = _open_unity_hub()
        return (f"Error: couldn't find the Unity {version} editor that {match} uses"
                + (", so I opened Unity Hub instead." if hub else ".")
                + " Pass its install folder with --unity-editor-root.")

    subprocess.Popen([str(exe), "-projectPath", str(path)])
    return f"Opening {match} in Unity {version}. It may take a minute to load."
