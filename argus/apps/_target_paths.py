"""Shared CLI target-path normalization helpers."""
from __future__ import annotations

from pathlib import Path

from ..core import paths as core_paths
from ..core.project import is_state_directory_name


def looks_like_project_life_dir(path: Path) -> bool:
    """Return True when ``path`` already points at ``.../projects/<state directory>``."""
    path = core_paths.resolve_runtime_path(path, context="--life-dir")
    return path.parent.name == "projects" and is_state_directory_name(path.name)


def resolve_life_root(life_dir: str | Path | None) -> Path:
    """Resolve a CLI ``--life-dir`` to the canonical global root."""
    explicit = (
        core_paths.global_root()
        if life_dir is None
        else core_paths.resolve_runtime_path(life_dir, context="--life-dir")
    ).resolve()
    if looks_like_project_life_dir(explicit):
        return explicit.parent.parent
    return explicit
