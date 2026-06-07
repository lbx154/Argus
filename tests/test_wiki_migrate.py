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
