from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.engineer.reviewer import _load_wiki_curator_skill_if_present
from argus_skill.engineer.reviewer import Reviewer


def test_returns_skill_text_when_wiki_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".autors" / "demo" / "wiki" / "queries").mkdir(parents=True)
    (tmp_path / ".autors" / "demo" / "wiki" / "query_pack.md").write_text("# pack")
    text = _load_wiki_curator_skill_if_present()
    assert text is not None
    assert "wiki-curator" in text.lower() or "Wiki Curator" in text


def test_returns_none_when_no_wiki(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    text = _load_wiki_curator_skill_if_present()
    assert text is None


def test_reviewer_prompt_includes_fixed_wiki_curator_when_wiki_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".autors" / "demo" / "wiki" / "queries").mkdir(parents=True)
    (tmp_path / ".autors" / "demo" / "wiki" / "query_pack.md").write_text("# pack")
    reviewer = Reviewer(runner=object())

    prompt = reviewer._build_prompt(
        objective="diagnose a training failure",
        operator_messages=[],
        planner_review_instruction="",
        round_index=0,
        session_id=None,
        main_summary="summary",
        main_error=None,
        checks=[],
    )

    assert "Wiki curator (fixed when a wiki exists" in prompt
    assert "wiki-curator" in prompt.lower() or "Wiki Curator" in prompt
