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
    the note body and removes the root orphan after a successful write.
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
            target = store.root / "sources" / "notes" / f"{stem}.md"
        orphan.unlink()
        moved.append(target)
    return moved


def _title_from_stem(stem: str) -> str:
    return stem.replace("_", " ").replace("-", " ").strip().title() or stem
