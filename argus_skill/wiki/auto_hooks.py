"""Mechanical support for the shared declarative knowledge wiki."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from ..core.event_catalog import EventType

log = logging.getLogger(__name__)
EventSink = Callable[[dict], None] | None


def discover_wikis(workdir: Path) -> list[Path]:
    """Return initialized ``.autors/<project>/wiki`` directories."""
    autors = workdir / ".autors"
    if not autors.exists():
        return []
    return [
        wiki
        for child in sorted(autors.iterdir())
        if child.is_dir()
        for wiki in (child / "wiki",)
        if (wiki / "query_pack.md").exists()
    ]


def _safe(fn: Callable[[], object], *, what: str, emit: EventSink) -> object | None:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - wiki support is fail-open
        log.warning("wiki maintenance %s failed: %s: %s", what, type(exc).__name__, exc)
        if emit is not None:
            try:
                emit({
                    "type": EventType.WIKI_HOOK_WARNING,
                    "operation": what,
                    "error": f"{type(exc).__name__}: {exc}",
                    "text": f"{what}: {type(exc).__name__}: {exc}",
                })
            except Exception:  # noqa: BLE001 - telemetry is fail-open
                log.exception("wiki warning emit failed")
        return None


def _ingest_sources(
    wiki_root: Path,
    *,
    source_root: Path,
    mission_id: str,
    emit: EventSink,
) -> int:
    """Import existing literature artifacts as immutable evidence."""
    from .ingest import ingest_lit_matrix, ingest_refs_bib
    from .store import WikiStore

    store = WikiStore(wiki_root)
    written = 0
    refs_bib = source_root / "paper" / "refs.bib"
    if refs_bib.exists():
        result = _safe(
            lambda: ingest_refs_bib(
                store,
                bib_path=refs_bib,
                ingested_by=f"wiki-source-import@mission-{mission_id}",
            ),
            what="ingest_refs_bib",
            emit=emit,
        )
        if result is not None:
            written += len(getattr(result, "written", []) or [])

    lit_matrix = source_root / "research" / "LIT_MATRIX.tsv"
    if lit_matrix.exists():
        _safe(
            lambda: ingest_lit_matrix(store, tsv_path=lit_matrix),
            what="ingest_lit_matrix",
            emit=emit,
        )
    return written


def rebuild_wiki_indexes(wiki_root: Path, *, emit: EventSink = None) -> None:
    from .index import rebuild_indexes
    from .store import WikiStore

    _safe(
        lambda: rebuild_indexes(WikiStore(wiki_root)),
        what="rebuild_indexes",
        emit=emit,
    )


def prepare_wikis_for_review(
    workdir: Path,
    *,
    mission_id: str,
    emit: EventSink = None,
) -> dict[str, dict[str, int]]:
    """Import available evidence and refresh indexes before Reviewer runs.

    This never creates knowledge pages and never copies round history. Semantic
    synthesis belongs to direct role edits, with Reviewer reconciliation.
    """
    summary: dict[str, dict[str, int]] = {}
    for wiki_root in discover_wikis(workdir):
        sources_written = _ingest_sources(
            wiki_root,
            source_root=workdir,
            mission_id=mission_id,
            emit=emit,
        )
        rebuild_wiki_indexes(wiki_root, emit=emit)
        summary[str(wiki_root)] = {"sources_written": sources_written}
        if emit is not None and sources_written:
            try:
                emit({
                    "type": EventType.WIKI_HOOK_OK,
                    "project": wiki_root.parent.name,
                    "path": str(wiki_root),
                    "sources_written": sources_written,
                    "text": (
                        f"{wiki_root.parent.name}: "
                        f"+{sources_written} knowledge sources"
                    ),
                })
            except Exception:  # noqa: BLE001 - telemetry is fail-open
                log.debug("wiki hook ok emit failed", exc_info=True)
    return summary


__all__ = [
    "discover_wikis",
    "prepare_wikis_for_review",
    "rebuild_wiki_indexes",
]
