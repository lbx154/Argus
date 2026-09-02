from __future__ import annotations

import json
from pathlib import Path

from argus_skill.skills.checklist_store import store_items_for_stage
from argus_skill.skills.vertical_select import persist_vertical


def _store(root: Path, vertical: str, stage: str) -> Path:
    path = root / "research" / "CHECKLISTS.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "vertical": vertical,
            "revision": 1,
            "stages": {
                stage: [{
                    "id": f"{stage}.custom",
                    "statement": "Use the official evaluator.",
                    "evidence_hint": "direct output",
                }]
            },
        }),
        encoding="utf-8",
    )
    return path


def test_research_ignores_but_preserves_legacy_checklists(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "research")
    path = _store(tmp_path, "research", "idea")

    assert store_items_for_stage(tmp_path, "idea") is None
    assert path.is_file()


def test_other_vertical_custom_checklists_still_load(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "speedrun")
    _store(tmp_path, "speedrun", "setup")

    items = store_items_for_stage(tmp_path, "setup")

    assert items is not None
    assert any(item.id == "setup.custom" for item in items)
