"""One-shot migrations for wiki trees."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from .schema import SourceNote
from .store import WikiStore


def migrate_orphan_sources(store: WikiStore) -> list[Path]:
    """Move root-level `sources/*.md` orphans into `sources/notes/`.

    These files were produced by early operational missions before the wiki
    had a note source bucket. The migration preserves the original markdown as
    the note body and removes the root orphan **only after** the note is
    successfully written. If a note with the same stem already exists (notes
    are immutable), the orphan is left in place untouched rather than deleted,
    so its content is never silently lost on a stem collision.
    """
    sources_root = store.root / "sources"
    if not sources_root.exists():
        return []
    moved: list[Path] = []
    for orphan in sorted(sources_root.glob("*.md")):
        body = orphan.read_text(encoding="utf-8", errors="ignore")
        stem = orphan.stem
        note = SourceNote(
            id=f"notes/{stem}",
            title=_title_from_stem(stem),
            mission_id="",
            created_at=date.today(),
            tags=["migrated-orphan"],
            body=body,
        )
        try:
            target = store.write_source(note)
        except FileExistsError:
            # A different note already occupies this stem. Do not delete the
            # orphan — that would discard its body. Leave it for manual review.
            continue
        orphan.unlink()
        moved.append(target)
    return moved


def _title_from_stem(stem: str) -> str:
    return stem.replace("_", " ").replace("-", " ").strip().title() or stem
