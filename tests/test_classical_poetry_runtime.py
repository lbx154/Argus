from __future__ import annotations

from pathlib import Path

from argus_skill.verticals.classical_poetry.stages import stage_completion_issues

_DENG = "白日依山尽，黄河入海流。欲穷千里目，更上一层楼。"


def test_prosody_completion_blocks_invalid_poem(tmp_path: Path) -> None:
    assert stage_completion_issues("prosody_check", tmp_path)
    path = tmp_path / "poetry" / "draft_poem.txt"
    path.parent.mkdir()
    path.write_text(_DENG, encoding="utf-8")
    assert stage_completion_issues("prosody_check", tmp_path) == ()

    path.write_text("白日依山高，黄河入海天。欲穷千里目，更上一层云。", encoding="utf-8")
    assert stage_completion_issues("prosody_check", tmp_path)


def test_revise_completion_rechecks_final_poem(tmp_path: Path) -> None:
    assert stage_completion_issues("revise", tmp_path)
    path = tmp_path / "poetry" / "final_poem.txt"
    path.parent.mkdir()
    path.write_text(_DENG, encoding="utf-8")
    assert stage_completion_issues("revise", tmp_path) == ()

    path.write_text("白日依山高，黄河入海天。欲穷千里目，更上一层云。", encoding="utf-8")
    assert stage_completion_issues("revise", tmp_path)
