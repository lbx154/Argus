"""Shared source-tree write and commit primitives for Manager promotions."""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import uuid
from pathlib import Path

log = logging.getLogger(__name__)


def source_root() -> Path:
    """Return the ``argus_skill`` source directory inside the repository."""
    from ..skills.builtins import builtin_skill_source_path

    return builtin_skill_source_path().resolve().parent


def atomic_write(path: Path, text: str) -> None:
    """Atomically replace a UTF-8 source file without sharing temp paths."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f"{path.name}.tmp.{os.getpid()}.{threading.get_ident():x}.{uuid.uuid4().hex[:8]}"
    )
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _autocommit_enabled() -> bool:
    return os.environ.get("ARGUS_SKILL_AUTOCOMMIT_SKILLS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def commit_to_source(paths: list[Path], message: str) -> bool:
    """Best-effort commit of only ``paths`` in the Argus source repository.

    Auto-commit is disabled by default. When enabled, ``git commit --only`` keeps
    the operator's ambient staged index out of the autonomous commit.
    """
    if not paths:
        return False
    if not _autocommit_enabled():
        log.info(
            "commit_to_source: source auto-commit disabled (default); %d file(s) "
            "written, not committed. Set ARGUS_SKILL_AUTOCOMMIT_SKILLS=1 to opt in.",
            len(paths),
        )
        return False
    root = source_root()
    rendered_paths = [str(path) for path in paths]
    try:
        subprocess.run(
            ["git", "-C", str(root), "add", "--", *rendered_paths],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "commit",
                "--only",
                "-m",
                message,
                "--",
                *rendered_paths,
            ],
            check=True,
            capture_output=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - source commit is best-effort
        log.warning("commit_to_source failed (%s)", type(exc).__name__)
        return False


__all__ = ["atomic_write", "commit_to_source", "source_root"]
