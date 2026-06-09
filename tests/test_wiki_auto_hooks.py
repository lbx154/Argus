"""Tests for argus_skill.wiki.auto_hooks — harness-driven wiki maintenance."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from argus_skill.wiki.auto_hooks import discover_wikis, run_post_mission_hooks
from argus_skill.wiki.bootstrap import init_wiki


SAMPLE_BIB = """
@article{smith2025attention,
  title={Visual attention in VLMs},
  author={Smith, A.},
  year={2025},
  url={https://arxiv.org/abs/2501.12345}
}

@misc{lee2026probing,
  title={Probing hallucination},
  author={Lee, B.},
  year={2026},
  url={https://arxiv.org/abs/2603.99999}
}
""".strip()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    init_wiki(project="demo", base=tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_discover_wikis_finds_initialized_wiki(project: Path):
    found = discover_wikis(project)
    assert len(found) == 1
    assert found[0] == project / ".autors" / "demo" / "wiki"


def test_discover_skips_uninitialized_dir(tmp_path: Path):
    (tmp_path / ".autors" / "scaffold-only" / "wiki").mkdir(parents=True)
    assert discover_wikis(tmp_path) == []


def test_discover_returns_empty_when_no_autors(tmp_path: Path):
    assert discover_wikis(tmp_path) == []


def test_run_hooks_backfills_refs_bib(project: Path):
    paper_dir = project / "paper"
    paper_dir.mkdir()
    (paper_dir / "refs.bib").write_text(SAMPLE_BIB, encoding="utf-8")
    summary = run_post_mission_hooks(project, mission_id="t1", success=True)
    [(wiki_str, info)] = summary.items()
    assert info["sources_written"] == 2, info
    # ...and the mechanical lift turned each new source into a scratch card.
    assert info["scratch_written"] == 2, info
    wiki = Path(wiki_str)
    pages = list((wiki / "pages" / "techniques").glob("*.md"))
    assert len(pages) == 2
    sample = pages[0].read_text(encoding="utf-8")
    assert "status: scratch" in sample
    assert "Auto-created by wiki-auto-hook" in sample


def test_run_hooks_idempotent(project: Path):
    (project / "paper").mkdir()
    (project / "paper" / "refs.bib").write_text(SAMPLE_BIB, encoding="utf-8")
    run_post_mission_hooks(project, mission_id="t1", success=True)
    s2 = run_post_mission_hooks(project, mission_id="t2", success=True)
    # Second pass writes nothing new — sources are immutable, scratch
    # cards already exist.
    [(_, info)] = s2.items()
    assert info["sources_written"] == 0
    assert info["scratch_written"] == 0


def test_run_hooks_skips_when_no_refs_bib(project: Path):
    s = run_post_mission_hooks(project, mission_id="t1", success=True)
    [(_, info)] = s.items()
    assert info["sources_written"] == 0
    assert info["scratch_written"] == 0


def test_run_hooks_fails_open_on_broken_bib(project: Path, capsys):
    (project / "paper").mkdir()
    # Garbage bib — should produce a warning event, not raise.
    (project / "paper" / "refs.bib").write_text("@@@ not valid bibtex @@@",
                                                  encoding="utf-8")
    events: list[dict] = []
    s = run_post_mission_hooks(
        project, mission_id="t1", success=True, emit=events.append
    )
    # No raise — and either succeeds with zero sources or emits a warning.
    assert s  # one wiki discovered
    # warnings (if any) are isolated; never blocks
