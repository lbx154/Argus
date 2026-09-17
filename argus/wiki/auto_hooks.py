"""Mechanical discovery for minimal Agent-maintained Wikis."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..core.event_catalog import EventType

EventSink = Callable[[dict], None] | None


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


def rebuild_wiki_indexes(wiki_root: Path, *, emit: EventSink = None) -> None:
    """Explicit compatibility command; normal missions maintain INDEX.md."""
    from .index import rebuild_indexes
    from .store import WikiStore

    try:
        rebuild_indexes(WikiStore(wiki_root))
    except Exception as exc:  # noqa: BLE001
        if emit is not None:
            emit(
                {
                    "type": EventType.WIKI_HOOK_WARNING,
                    "path": str(wiki_root),
                    "error": f"{type(exc).__name__}: {exc}",
                    "text": "explicit Wiki index rebuild failed",
                }
            )


def prepare_wikis_for_review(
    workdir: Path,
    *,
    mission_id: str,
    emit: EventSink = None,
) -> dict[str, dict[str, int]]:
    """Expose existing Wikis; do not ingest, parse, or rewrite them."""
    _ = (mission_id, emit)
    return {str(root): {} for root in discover_wikis(workdir)}


__all__ = ["discover_wikis", "prepare_wikis_for_review", "rebuild_wiki_indexes"]
