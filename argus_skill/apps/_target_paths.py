"""Shared CLI target-path normalization helpers."""
from __future__ import annotations

import re
from pathlib import Path

from ..core import paths as core_paths

_PROJECT_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{12}$")


def looks_like_project_life_dir(path: Path) -> bool:
    """Return True when ``path`` already points at ``.../projects/<fingerprint>``."""
    path = core_paths.resolve_runtime_path(path, context="--life-dir")
    return (
        path.parent.name == "projects"
        and bool(path.name)
        and bool(_PROJECT_FINGERPRINT_RE.fullmatch(path.name))
    )


def resolve_life_root(life_dir: str | Path | None) -> Path:
    """Resolve a CLI ``--life-dir`` to the canonical global root."""
    if life_dir is None:
        return core_paths.global_root()
    explicit = core_paths.resolve_runtime_path(life_dir, context="--life-dir")
    if looks_like_project_life_dir(explicit):
        return explicit.parent.parent
    return explicit
