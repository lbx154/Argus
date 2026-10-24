"""Tests for GlobalMemory / ProjectMemory / MemoryBundle (Phase 2 split)."""
from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from argus.apps.cli import main
from argus.life import (
    BacklogItem,
    JournalEntry,
    MemoryBundle,
    ProjectMemory,
)


def _write_project_event(journal, entry: JournalEntry) -> None:
    row = {
        "type": "user.note" if entry.kind == "note" else "life.mission.completed",
        "id": entry.id,
        "item_id": entry.id,
        "ts": entry.ts,
        "success": entry.kind == "mission_complete",
        "title": entry.title,
        "summary": entry.summary,
        "text": entry.summary,
        "tags": entry.tags,
        "cost_usd": entry.cost_usd,
    }
    journal.path.parent.mkdir(parents=True, exist_ok=True)
    with journal.path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# GlobalMemory
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ProjectMemory
# ---------------------------------------------------------------------------


def test_project_memory_init_idempotent(isolated_home: Path) -> None:
    proj = ProjectMemory.open("abc123abc123")
    proj.init()
    assert proj.init() == {
        "events": False,
        "backlog": False,
    }


def test_project_memory_rejects_empty_fingerprint() -> None:
    with pytest.raises(ValueError):
        ProjectMemory.open("")


# ---------------------------------------------------------------------------
# MemoryBundle
# ---------------------------------------------------------------------------


def test_memory_bundle_render_prelude_excludes_cross_project_journal(
    isolated_home: Path, tmp_path: Path
) -> None:
    bundle = MemoryBundle.for_cwd(tmp_path)
    other = MemoryBundle.for_cwd(tmp_path / "other")
    bundle.init()
    other.init()
    bundle.identity.path.write_text(
        "# Operator identity\n\nVoice: concise.\n",
        encoding="utf-8",
    )
    _write_project_event(
        other.project.memory,
        JournalEntry.new(
            kind="mission_complete",
            title="cross-project postgres tuning",
            summary="brin index, big win",
            tags=["postgres"],
        )
    )
    _write_project_event(
        bundle.project.memory,
        JournalEntry.new(
            kind="mission_complete",
            title="local postgres migration script",
            summary="bumped to 16",
            tags=["postgres", "migration"],
        )
    )
    rendered = bundle.render_prelude(max_project_entries=2)
    assert "Memory context (non-authoritative)" in rendered
    assert "Identity" in rendered
    assert "local postgres migration" in rendered
    assert "cross-project postgres" not in rendered
    assert "other projects" not in rendered


def test_memory_bundle_journal_writes_are_project_only(
    isolated_home: Path, tmp_path: Path
) -> None:
    bundle = MemoryBundle.for_cwd(tmp_path)
    bundle.init()

    local = JournalEntry.new(
        kind="mission_complete",
        title="local new",
        summary="right repo",
        cost_usd=0.25,
    )
    _write_project_event(bundle.journal, local)

    # Reads are strictly project-scoped and derive from project events.
    assert [entry.title for entry in bundle.journal.all()] == ["local new"]
    assert [entry.title for entry in bundle.journal.tail(5)] == ["local new"]
    assert bundle.journal.path == bundle.project.memory.path
    assert bundle.journal.total_cost_since(0) == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# LifeMemory still works (compatibility facade)
# ---------------------------------------------------------------------------


def test_cli_status_and_prelude_are_project_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))

    bundle_a = MemoryBundle.for_cwd(repo_a)
    bundle_b = MemoryBundle.for_cwd(repo_b)
    bundle_a.init()
    bundle_b.init()

    _write_project_event(
        bundle_a.project.memory,
        JournalEntry.new(kind="note", title="alpha memory", summary="alpha only")
    )
    _write_project_event(
        bundle_b.project.memory,
        JournalEntry.new(kind="note", title="beta memory", summary="beta only")
    )
    bundle_a.backlog.add(BacklogItem.new(title="alpha backlog", objective="alpha"))
    bundle_b.backlog.add(BacklogItem.new(title="beta backlog", objective="beta"))

    assert bundle_a.project.root != bundle_b.project.root
    assert bundle_a.project.memory.path != bundle_b.project.memory.path
    assert bundle_a.backlog.path != bundle_b.backlog.path

    prelude_a = bundle_a.render_prelude(max_project_entries=1)
    prelude_b = bundle_b.render_prelude(max_project_entries=1)
    assert "alpha memory" in prelude_a
    assert "beta memory" not in prelude_a
    assert "beta memory" in prelude_b
    assert "alpha memory" not in prelude_b

    monkeypatch.setattr(
        "argus.daemon.life_worker.read_daemon_status",
        lambda life_dir: Namespace(
            alive=False,
            pid=None,
            uptime_seconds=None,
            backend=None,
        ),
    )
    monkeypatch.setattr("argus.apps.cli._core._check_logout_survival", lambda status: None)

    monkeypatch.chdir(repo_a)
    rc_a = main(["--status"])
    out_a = capsys.readouterr().out
    assert rc_a == 0
    assert str(bundle_a.project.root) in out_a
    assert "alpha only" in out_a
    assert "beta only" not in out_a
    assert "wrong workspace" not in out_a

    monkeypatch.chdir(repo_b)
    rc_b = main(["--status"])
    out_b = capsys.readouterr().out
    assert rc_b == 0
    assert str(bundle_b.project.root) in out_b
    assert "beta only" in out_b
    assert "alpha only" not in out_b
    assert "wrong workspace" not in out_b
