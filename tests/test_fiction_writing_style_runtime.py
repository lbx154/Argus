from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.fiction_writing.stages import stage_completion_issues


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")


def test_review_completion_runs_deterministic_checks(tmp_path: Path) -> None:
    assert stage_completion_issues("review", tmp_path)

    fiction = tmp_path / "fiction"
    _write(fiction / "draft.md", "风从窗外吹进来。")
    _write(fiction / "style_profile.json", {})
    _write(fiction / "creative_brief.json", {"language": "zh"})
    _write(fiction / "story_state.json", {})
    assert stage_completion_issues("review", tmp_path) == ()

    _write(fiction / "style_profile.json", {"forbidden_lexicon": ["窗外"]})
    assert stage_completion_issues("review", tmp_path)

    _write(fiction / "style_profile.json", {})
    _write(
        fiction / "story_state.json",
        {
            "meta": {"world_clock": {"current_year": 2042}},
            "characters": {"c": {"name": "林默", "birth_year": 2008, "age": 20}},
        },
    )
    assert stage_completion_issues("review", tmp_path)

    copied = "天地玄黄宇宙洪荒日月盈昃辰宿列张寒来暑往秋收冬藏闰余成岁律吕调阳"
    _write(fiction / "draft.md", copied)
    _write(fiction / "story_state.json", {})
    _write(fiction / "reference_text.md", copied)
    assert stage_completion_issues("review", tmp_path)


def test_revise_completion_requires_deliverables(tmp_path: Path) -> None:
    assert stage_completion_issues("revise", tmp_path)
    fiction = tmp_path / "fiction"
    _write(fiction / "final.md", "终稿")
    _write(fiction / "updated_story_state.json", {})
    _write(fiction / "style_profile.json", {})
    _write(fiction / "creative_brief.json", {"language": "zh"})
    assert stage_completion_issues("revise", tmp_path) == ()

    _write(fiction / "style_profile.json", {"forbidden_lexicon": ["禁词"]})
    _write(fiction / "final.md", "终稿含有禁词")
    assert stage_completion_issues("revise", tmp_path)
