from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.engineer.reviewer import _load_wiki_curator_skill_if_present


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
