from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.modern_poetry.stages import stage_completion_issues

_POEM = "夜把城市折起来\n只留一盏灯\n和灯下没说完的话"


def test_form_completion_enforces_declared_constraints(tmp_path: Path) -> None:
    assert stage_completion_issues("form_check", tmp_path)
    poetry = tmp_path / "poetry"
    poetry.mkdir()
    (poetry / "draft_poem.txt").write_text(_POEM, encoding="utf-8")
    (poetry / "form_spec.json").write_text(
        json.dumps({"language": "zh", "line_count": 3}), encoding="utf-8"
    )
    assert stage_completion_issues("form_check", tmp_path) == ()

    (poetry / "form_spec.json").write_text(
        json.dumps({"language": "zh", "banned_words": ["灯下"]}), encoding="utf-8"
    )
    assert stage_completion_issues("form_check", tmp_path)


def test_revise_completion_rechecks_final_poem(tmp_path: Path) -> None:
    assert stage_completion_issues("revise", tmp_path)
    poetry = tmp_path / "poetry"
    poetry.mkdir()
    path = poetry / "final_poem.txt"
    path.write_text(_POEM, encoding="utf-8")
    (poetry / "form_spec.json").write_text(
        json.dumps({"language": "zh", "line_count": 3}), encoding="utf-8"
    )
    assert stage_completion_issues("revise", tmp_path) == ()

    path.write_text("只有一行", encoding="utf-8")
    assert stage_completion_issues("revise", tmp_path)
