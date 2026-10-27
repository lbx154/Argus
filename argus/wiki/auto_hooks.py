"""Mechanical discovery for minimal Agent-maintained Wikis."""
from __future__ import annotations

from pathlib import Path


def discover_wikis(workdir: Path) -> list[Path]:
    autors = workdir / ".autors"
    if not autors.exists():
        return []
    return [
        wiki
        for child in sorted(autors.iterdir())
        if child.is_dir()
        for wiki in (child / "wiki",)
        if (wiki / "INDEX.md").is_file() and (wiki / "pages").is_dir()
    ]


__all__ = ["discover_wikis"]
