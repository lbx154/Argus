"""Tests for `argus_skill.tools.trajectory_index` and `query_unified`."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from argus_skill.tools import query_unified, trajectory_index


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch) -> Path:
    """Point ARGUS_SKILL_HOME and CODEX_HOME at fresh dirs."""
    home = tmp_path / "argus_home"
    codex = tmp_path / "codex_home"
    home.mkdir()
    codex.mkdir()
    (codex / "sessions" / "2026" / "06" / "11").mkdir(parents=True)
    (home / "projects" / "abc123").mkdir(parents=True)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    return tmp_path


def _write_codex_jsonl(path: Path, entries: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def test_index_empty_returns_zero_rows(isolated_home: Path) -> None:
    r = trajectory_index.index_all()
    assert r["rows_total"] == 0
    assert r["files_total"] == 0


def test_index_codex_jsonl(isolated_home: Path) -> None:
    codex_root = Path(os.environ["CODEX_HOME"]) / "sessions"
    rollout = codex_root / "2026" / "06" / "11" / "rollout-test.jsonl"
    _write_codex_jsonl(rollout, [
        {"timestamp": "2026-06-11T00:00:00Z", "type": "response_item",
         "payload": {"type": "function_call_output", "output": "Overfull \\hbox (5pt) at line 42"}},
        {"timestamp": "2026-06-11T00:01:00Z", "type": "response_item",
         "payload": {"type": "message", "content": [{"type": "input_text", "text": "fix the layout"}]}},
    ])
    r = trajectory_index.index_all()
    assert r["files_scanned"] == 1
    assert r["rows_total"] == 2

    hits = trajectory_index.search_trajectories("overfull")
    assert len(hits) == 1
    assert "Overfull" in hits[0].text
    assert hits[0].source == "codex"
    assert hits[0].session_id == "rollout-test"

    hits2 = trajectory_index.search_trajectories("layout")
    assert any("fix the layout" in h.text for h in hits2)


def test_index_argus_inbox_and_decisions(isolated_home: Path) -> None:
    proj = Path(os.environ["ARGUS_SKILL_HOME"]) / "projects" / "abc123"
    (proj / "inbox.jsonl").write_text(
        json.dumps({"ts": "2026-06-11T00:00Z", "kind": "operator_directive",
                    "text": "STOP layout polish loop"}) + "\n",
        encoding="utf-8",
    )
    (proj / "decisions.jsonl").write_text(
        json.dumps({"ts": "2026-06-11T00:01Z", "role": "reviewer",
                    "verdict": "fail", "reason": "overfull hbox 8.879pt"}) + "\n",
        encoding="utf-8",
    )
    r = trajectory_index.index_all()
    assert r["files_scanned"] == 2

    hits = trajectory_index.search_trajectories("layout")
    assert any(h.source == "argus_inbox" for h in hits)
    hits2 = trajectory_index.search_trajectories("overfull")
    assert any(h.source == "argus_decisions" for h in hits2)


def test_index_is_incremental(isolated_home: Path) -> None:
    rollout = Path(os.environ["CODEX_HOME"]) / "sessions" / "rollout-1.jsonl"
    _write_codex_jsonl(rollout, [
        {"timestamp": "2026-06-11T00:00Z", "type": "response_item",
         "payload": {"type": "message", "content": [{"text": "hello"}]}}
    ])
    r1 = trajectory_index.index_all()
    assert r1["files_scanned"] == 1
    r2 = trajectory_index.index_all()
    assert r2["files_scanned"] == 0  # nothing changed; skipped
    assert r2["rows_total"] == r1["rows_total"]


def test_search_with_no_db_returns_empty(tmp_path: Path) -> None:
    out = trajectory_index.search_trajectories("anything", db_path=tmp_path / "missing.sqlite")
    assert out == []


def test_unified_query_filters_zero_score_skills(isolated_home: Path, tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    # 1 matching skill, 1 unrelated — query must keep only the match
    (skills_dir / "match.md").write_text(
        "---\nname: latex-overflow-fix\ndescription: fix overfull hbox in tex\n---\nbody\n",
        encoding="utf-8",
    )
    (skills_dir / "noise.md").write_text(
        "---\nname: hello-world\ndescription: greet user\n---\nbody\n",
        encoding="utf-8",
    )
    r = query_unified.unified_query(
        "overfull hbox",
        skills_dir=skills_dir,
        wiki_search_roots=[tmp_path],
        auto_index=False,
    )
    assert len(r["skills"]) == 1
    assert r["skills"][0]["name"] == "latex-overflow-fix"


def test_unified_query_finds_wiki_pages(isolated_home: Path, tmp_path: Path) -> None:
    project_dir = tmp_path / "myproj"
    pages_dir = project_dir / ".autors" / "mp" / "wiki" / "pages" / "techniques"
    pages_dir.mkdir(parents=True)
    (pages_dir / "latex.md").write_text(
        "# Latex Overflow\nWhen overfull hbox shows up, shrink the caption.\n",
        encoding="utf-8",
    )
    r = query_unified.unified_query(
        "overfull hbox",
        skills_dir=None,
        wiki_search_roots=[tmp_path],
        auto_index=False,
    )
    assert len(r["wiki"]) == 1
    assert r["wiki"][0]["project"] == "mp"
    assert "techniques/latex.md" in r["wiki"][0]["page"]
    assert "overfull" in r["wiki"][0]["snippet"].lower()


def test_unified_query_render_text_smoke(isolated_home: Path) -> None:
    r = query_unified.unified_query(
        "nonexistent",
        skills_dir=None,
        wiki_search_roots=[],
        auto_index=False,
    )
    txt = query_unified.render_text(r)
    assert "[trajectory]" in txt
    assert "[skills]" in txt
    assert "[wiki]" in txt


def test_fts_escape_drops_punctuation() -> None:
    # FTS5 syntax operators (AND, OR, NOT, NEAR, parens) must not slip through
    out = trajectory_index._fts_escape("foo (bar) AND baz")
    assert "(" not in out
    assert "\"foo\"" in out
    assert "\"bar\"" in out
    assert "\"baz\"" in out
    # the bare AND token gets quoted, neutralizing FTS5 operator semantics
    assert "AND" in out and "\"AND\"" in out


def test_empty_query_returns_no_hits(isolated_home: Path) -> None:
    rollout = Path(os.environ["CODEX_HOME"]) / "sessions" / "rollout.jsonl"
    _write_codex_jsonl(rollout, [
        {"timestamp": "x", "type": "response_item",
         "payload": {"type": "message", "content": [{"text": "hello"}]}}
    ])
    trajectory_index.index_all()
    assert trajectory_index.search_trajectories("") == []
    assert trajectory_index.search_trajectories("!!!") == []
