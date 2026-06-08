from __future__ import annotations

from pathlib import Path

from argus_skill.wiki.bootstrap import init_wiki
from argus_skill.wiki.migrate import migrate_orphan_sources
from argus_skill.wiki.store import WikiStore


def test_migrate_moves_root_orphans_to_notes(tmp_path: Path):
    root = init_wiki("demo", base=tmp_path)
    orphan = root / "sources" / "stage_check_terminal_external_blocker.md"
    orphan.write_text("# Stage Check\n\noperator wait state\n", encoding="utf-8")
    store = WikiStore(root)

    moved = migrate_orphan_sources(store)

    assert len(moved) == 1
    assert not orphan.exists()
    notes = store.iter_note_sources()
    assert len(notes) == 1
    assert notes[0].id == "notes/stage_check_terminal_external_blocker"
    assert "operator wait state" in notes[0].body


def test_migrate_preserves_orphan_on_note_stem_collision(tmp_path: Path):
    root = init_wiki("demo", base=tmp_path)
    store = WikiStore(root)

    # A note already occupies the target stem (notes are immutable).
    from datetime import date

    from argus_skill.wiki.schema import SourceNote

    existing = SourceNote(
        id="notes/dup",
        title="Existing",
        mission_id="",
        created_at=date.today(),
        tags=[],
        body="canonical existing note body",
    )
    store.write_source(existing)

    orphan = root / "sources" / "dup.md"
    orphan.write_text("# Dup\n\norphan body that must not be lost\n", encoding="utf-8")

    moved = migrate_orphan_sources(store)

    # The colliding orphan is neither migrated nor deleted.
    assert moved == []
    assert orphan.exists()
    assert "orphan body that must not be lost" in orphan.read_text(encoding="utf-8")
    # The pre-existing immutable note is untouched.
    notes = {n.id: n for n in store.iter_note_sources()}
    assert notes["notes/dup"].body.strip() == "canonical existing note body"
