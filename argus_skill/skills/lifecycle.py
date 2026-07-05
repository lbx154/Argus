"""Skill archival — move a retired skill out of the active library.

Historically this module also held a mission-completion → skill-lifecycle
dispatcher (``decide_action`` / ``apply_action``). That dispatcher is gone:
skill memory is now reviewer-proposed and applied by ``SkillRouter``
(mechanical structure + independence checks — no Manager gate, the reviewer
is sole authority), so the only thing that survives here is the low-level
``archive_skill`` move, reused by ``SkillStore.archive`` /
``SkillStore.discard_provisional`` and by ``compaction``.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

EventSink = Callable[[dict[str, Any]], None]

__all__ = ["archive_skill"]


def archive_skill(
    skill_path: str | os.PathLike[str],
    *,
    archive_root: Path | None = None,
) -> Path | None:
    """Move a skill markdown into ``skills/_archive/``. Returns the
    archived path, or ``None`` if the source didn't exist.

    Names collide on the day-of-archive prefix — we add a short uuid
    suffix to keep the archive append-only.
    """
    src = Path(skill_path)
    if not src.exists():
        return None
    if archive_root is None:
        from ..core import paths as core_paths
        archive_root = core_paths.skills_archive_root()
    archive_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    target = archive_root / f"{src.stem}.{stamp}.{uuid.uuid4().hex[:6]}{src.suffix}"
    # Use shutil.move so atomic-rename inside one filesystem still
    # works, but we also handle cross-fs by falling back to copy+unlink.
    try:
        shutil.move(str(src), target)
    except OSError:
        shutil.copy2(str(src), target)
        try:
            src.unlink()
        except OSError:  # pragma: no cover — best-effort
            pass
    return target
