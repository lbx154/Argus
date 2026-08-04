"""Automatic source ingestion was removed from the minimal Wiki.

Literature files remain ordinary project artifacts. An Agent may read them and
write a semantically named Wiki page when they support durable knowledge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .store import WikiStore


@dataclass
class IngestResult:
    written: list[Path] = field(default_factory=list)
    enriched_count: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)


def ingest_refs_bib(
    store: WikiStore,
    *,
    bib_path: Path,
    ingested_by: str,
    today: object | None = None,
) -> IngestResult:
    _ = (store, bib_path, ingested_by, today)
    return IngestResult(
        warnings=[
            "automatic Wiki source ingestion is disabled; let an Agent read the "
            "bibliography and author semantic pages"
        ]
    )


def ingest_lit_matrix(store: WikiStore, *, tsv_path: Path) -> IngestResult:
    _ = (store, tsv_path)
    return IngestResult(
        warnings=[
            "automatic Wiki source enrichment is disabled; let an Agent update "
            "semantic pages and INDEX.md"
        ]
    )


__all__ = ["IngestResult", "ingest_lit_matrix", "ingest_refs_bib"]
