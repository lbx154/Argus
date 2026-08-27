from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.prose.stages import stage_completion_issues

_DRAFT = "灶台还在那里。\n\n光从窗格里斜下来。\n\n后来老屋拆了。"
_STATE = {
    "narrative_center": "祖母的厨房",
    "observation_subject": "灶台与光",
    "factual_anchors": ["1998年"],
    "memory_boundary": "气味是回忆，年份是事实",
    "paragraph_movement": "从物到人到时间",
    "ending_strategy": "以动作收束",
    "spec": {"language": "zh", "min_paragraphs": 3},
}


def test_structure_completion_enforces_state_and_constraints(tmp_path: Path) -> None:
    assert stage_completion_issues("structure_check", tmp_path)
    prose = tmp_path / "prose"
    prose.mkdir()
    (prose / "draft.md").write_text(_DRAFT, encoding="utf-8")
    (prose / "prose_state.json").write_text(
        json.dumps(_STATE, ensure_ascii=False), encoding="utf-8"
    )
    assert stage_completion_issues("structure_check", tmp_path) == ()

    bad = dict(_STATE)
    bad.pop("memory_boundary")
    (prose / "prose_state.json").write_text(
        json.dumps(bad, ensure_ascii=False), encoding="utf-8"
    )
    assert stage_completion_issues("structure_check", tmp_path)


def test_revise_completion_rechecks_final_prose(tmp_path: Path) -> None:
    assert stage_completion_issues("revise", tmp_path)
    prose = tmp_path / "prose"
    prose.mkdir()
    path = prose / "final.md"
    path.write_text(_DRAFT, encoding="utf-8")
    (prose / "prose_state.json").write_text(
        json.dumps(_STATE, ensure_ascii=False), encoding="utf-8"
    )
    assert stage_completion_issues("revise", tmp_path) == ()

    path.write_text("只有一段。", encoding="utf-8")
    assert stage_completion_issues("revise", tmp_path)
