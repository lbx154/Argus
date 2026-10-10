"""The task's own input directories outside the project workdir.

A task often hands its inputs over in a directory beside the workdir: a
benchmark container mounts the packet at ``/app/packet`` while the run works
in ``/workspace``. Those files are the task's statements as much as its text
is, so a read-only Reviewer must be able to open them, and a quote from one
must be groundable while it is unchanged.

Only sources the operator or the host wrote are read, never Planner prose:

- absolute paths in the operator's original objective;
- the mission packet's ``context_refs`` (host-hydrated, hash-pinned refs).

An existing directory counts as itself, an existing file as its directory.
Everything is judged on the path as written, normalized but never resolved,
so a symlink cannot launder a location:

- a path with a symlink anywhere along it is refused;
- anything inside the workdir is skipped (already readable), and so is any
  directory that holds the workdir (it would hand over the Engineer's files);
- system, credential and per-user locations are refused (``DENIED_PREFIXES``,
  the user's home, Argus's own state, and on Windows the system and profile
  directories), as is any directory that contains one of them.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path, PurePath
from typing import Any, Iterable

#: How many input directories one task may add.
MAX_INPUT_ROOTS = 8

#: System, runtime and per-user trees a task input never lives in.
DENIED_PREFIXES: tuple[str, ...] = (
    "/proc", "/sys", "/dev", "/run", "/var/run", "/var/tmp", "/etc", "/root",
    "/boot", "/home", "/tmp", "/usr", "/bin", "/sbin", "/lib", "/lib32",
    "/lib64", "/libx32",
    # macOS
    "/System", "/Library", "/Users", "/private/etc", "/private/var", "/private/tmp",
)

# An absolute POSIX path not glued to a preceding word, path or URL character
# (so ``https://host/x`` and ``./a/b`` do not match).
_ABSOLUTE_PATH = re.compile(r"(?<![\w/.:~$-])/[\w.@+-]+(?:/[\w.@+-]+)*/?")
_TRAILING = ".,;:!?)]}'\"`"


def _absolute_paths(text: str) -> list[str]:
    return [match.group(0).rstrip(_TRAILING) for match in _ABSOLUTE_PATH.finditer(str(text or ""))]


def _lexical(path: Path) -> Path:
    return Path(os.path.normpath(str(path)))


def _protected_roots() -> list[Path]:
    """Per-user and Argus-owned trees, as written (not resolved)."""
    from .paths import global_root

    roots: list[Path] = []
    for candidate in (Path.home, global_root):
        try:
            roots.append(_lexical(Path(candidate()).absolute()))
        except (OSError, RuntimeError, KeyError):
            continue
    if sys.platform.startswith("win"):
        for name in ("SystemRoot", "WINDIR", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
            value = os.environ.get(name, "").strip()
            if value:
                roots.append(_lexical(Path(value)))
    return roots


def denied_reason(path: Path) -> str:
    """Why ``path`` (absolute, normalized) may not be a task input, or ""."""
    if path == Path(path.anchor):
        return "filesystem root"
    for prefix in DENIED_PREFIXES:
        base = PurePath(prefix)
        if path == base or path.is_relative_to(base):
            return f"system or per-user location {prefix}"
    for root in _protected_roots():
        if path == root or path.is_relative_to(root) or root.is_relative_to(path):
            return f"user home or Argus state ({root})"
    if any(char in str(path) for char in (",", "\n", "\r", "\0")):
        return "path has characters a read-only bind cannot carry"
    return ""


def _through_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            return True
    return False


def task_input_roots(
    *,
    texts: Iterable[str] = (),
    context_refs: Iterable[Any] = (),
    workdir: str | Path | None = None,
) -> tuple[str, ...]:
    """Input directories of the task outside ``workdir``, as normalized absolute paths.

    ``texts`` must be operator-written (the original objective), never a
    Planner-rewritten mission objective.
    """
    base: Path | None = None
    resolved_base: Path | None = None
    if workdir:
        base = _lexical(Path(workdir).expanduser().absolute())
        try:
            resolved_base = base.resolve()
        except (OSError, RuntimeError):
            resolved_base = base
    named: list[Path] = []
    for text in texts:
        named.extend(Path(value) for value in _absolute_paths(text) if value)
    for ref in context_refs:
        raw = str(ref.get("ref") if isinstance(ref, dict) else ref or "").strip()
        if not raw or raw.startswith(("~", "$", "%")):
            continue
        path = Path(raw)
        if not path.is_absolute():
            if base is None:
                continue
            path = base / path
        named.append(path)
    roots: list[str] = []
    for raw in named:
        path = _lexical(raw)
        inside = [
            work for work in (base, resolved_base)
            if work is not None and (path == work or path.is_relative_to(work))
        ]
        if inside:
            continue  # the workdir is readable already; never follow out of it
        try:
            if _through_symlink(path):
                continue
            if path.is_dir():
                root = path
            elif path.is_file():
                root = path.parent
            else:
                continue
        except (OSError, RuntimeError, ValueError):
            continue
        if any(
            work is not None and work.is_relative_to(root) for work in (base, resolved_base)
        ):
            continue
        if denied_reason(root):
            continue
        text = str(root)
        if text not in roots:
            roots.append(text)
        if len(roots) >= MAX_INPUT_ROOTS:
            break
    return tuple(roots)


__all__ = ["DENIED_PREFIXES", "MAX_INPUT_ROOTS", "denied_reason", "task_input_roots"]
