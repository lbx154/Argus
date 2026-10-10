"""The task's own input directories outside the project workdir.

A task often hands its inputs over in a directory beside the workdir: a
benchmark container mounts the packet at ``/app/packet`` while the run works
in ``/workspace``. Those files are the task's statements as much as its text
is, so a read-only Reviewer must be able to open them, and a quote from one
must be groundable while it is unchanged.

They are found from what the task already says, never guessed:

- absolute paths the objective (or the campaign objective it narrows) names;
- the mission packet's ``context_refs``.

An existing directory counts as itself, an existing file as its directory.
Anything inside the workdir is skipped, because the workdir is already
readable, and so is any directory that contains the workdir, because it
would count the Engineer's own files as task inputs. Roots too broad to
hand over are skipped as well: the filesystem root, and any directory that
contains the user's home or Argus's own state.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

#: How many input directories one task may add.
MAX_INPUT_ROOTS = 8

# An absolute POSIX path not glued to a preceding word, path or URL character
# (so ``https://host/x`` and ``./a/b`` do not match).
_ABSOLUTE_PATH = re.compile(r"(?<![\w/.:~$-])/[\w.@+-]+(?:/[\w.@+-]+)*/?")
_TRAILING = ".,;:!?)]}'\"`"


def _absolute_paths(text: str) -> list[str]:
    return [match.group(0).rstrip(_TRAILING) for match in _ABSOLUTE_PATH.finditer(str(text or ""))]


def _too_broad(path: Path) -> bool:
    from .paths import global_root

    if path == Path(path.anchor):
        return True
    protected: list[Path] = []
    for candidate in (Path.home, global_root):
        try:
            protected.append(candidate().resolve())
        except (OSError, RuntimeError):
            continue
    return any(root == path or root.is_relative_to(path) for root in protected)


def task_input_roots(
    *,
    texts: Iterable[str] = (),
    context_refs: Iterable[Any] = (),
    workdir: str | Path | None = None,
) -> tuple[str, ...]:
    """Resolved input directories of the task, outside ``workdir``."""
    base: Path | None = None
    if workdir:
        try:
            base = Path(workdir).expanduser().resolve()
        except (OSError, RuntimeError):
            base = None
    named: list[Path] = []
    for text in texts:
        named.extend(Path(value) for value in _absolute_paths(text) if value)
    for ref in context_refs:
        raw = str(ref.get("ref") if isinstance(ref, dict) else ref or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute():
            if base is None:
                continue
            path = base / path
        named.append(path)
    roots: list[str] = []
    for path in named:
        try:
            resolved = path.resolve()
            if resolved.is_dir():
                root = resolved
            elif resolved.is_file():
                root = resolved.parent
            else:
                continue
        except (OSError, RuntimeError, ValueError):
            continue
        if base is not None and (
            root.is_relative_to(base) or base.is_relative_to(root)
        ):
            # Inside the workdir it is readable already; a directory holding
            # the workdir would hand over the Engineer's own files as inputs.
            continue
        if _too_broad(root) or any(char in str(root) for char in (",", "\n", "\r", "\0")):
            continue
        text = str(root)
        if text not in roots:
            roots.append(text)
        if len(roots) >= MAX_INPUT_ROOTS:
            break
    return tuple(roots)


__all__ = ["MAX_INPUT_ROOTS", "task_input_roots"]
