"""Tests for GlobalMemory / ProjectMemory / MemoryBundle (Phase 2 split)."""
from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from argus_skill.apps.cli import main
from argus_skill.core import project
from argus_skill.life import (
    BacklogItem,
    GlobalMemory,
    JournalEntry,
    MemoryBundle,
    ProjectMemory,
)


def _write_project_event(journal, entry: JournalEntry) -> None:
    row = {
        "type": "user.note" if entry.kind == "note" else "life.mission.completed",
        "journal_kind": entry.kind,
        **entry.to_jsonable(),
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
    monkeypatch.delenv("ARGUS_SKILL_LIFE_DIR", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# GlobalMemory
# ---------------------------------------------------------------------------

def test_global_memory_open_uses_core_paths(isolated_home: Path) -> None:
    mem = GlobalMemory.open()
    assert mem.root == isolated_home
    assert mem.identity.path == isolated_home / "identity.md"
    assert mem.journal.path == isolated_home / "journal.jsonl"


def test_global_memory_lazy_creation(isolated_home: Path) -> None:
    """Just calling open() must not write anything to disk."""
    GlobalMemory.open()
    assert not (isolated_home / "identity.md").exists()
    assert not (isolated_home / "journal.jsonl").exists()


def test_global_memory_init_seeds_identity(isolated_home: Path) -> None:
    mem = GlobalMemory.open()
    created = mem.init()
    # Global root seeds only identity; logs are per-project, so init must not
    # create a global journal file.
    assert created == {"identity": True}
    assert (isolated_home / "identity.md").read_text(encoding="utf-8")
    assert not (isolated_home / "journal.jsonl").exists()
    # Idempotent.
    again = mem.init()
    assert again == {"identity": False}


def test_global_memory_journal_round_trip(isolated_home: Path) -> None:
    mem = GlobalMemory.open()
    mem.journal.append(
        JournalEntry.new(
            kind="mission_complete",
            title="hello world",
            summary="said hi",
            tags=["greeting"],
        )
    )
    rows = mem.journal.all()
    assert len(rows) == 1
    assert rows[0].title == "hello world"


def test_global_memory_relevant_journal(isolated_home: Path) -> None:
    # Recency-only: both entries are returned, newest first; the harness does
    # not rank them by keyword overlap with the objective.
    mem = GlobalMemory.open()
    mem.journal.append(
        JournalEntry.new(
            kind="mission_complete",
            title="rewrite postgres index",
            summary="moved hot table to brin",
            tags=["postgres", "perf"],
        )
    )
    mem.journal.append(
        JournalEntry.new(
            kind="mission_complete",
            title="add docker compose",
            summary="orchestrated services",
            tags=["docker"],
        )
    )
    hits = mem.relevant_journal_for("optimise postgres index again")
    assert [h.title for h in hits] == ["add docker compose", "rewrite postgres index"]


def test_global_memory_explicit_root_overrides_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "env-home"))
    other = tmp_path / "explicit"
    mem = GlobalMemory.open(other)
    assert mem.root == other


# ---------------------------------------------------------------------------
# ProjectMemory
# ---------------------------------------------------------------------------

def test_project_memory_paths_under_projects_root(isolated_home: Path) -> None:
    proj = ProjectMemory.open("abc123abc123", label="my-project")
    expected_root = isolated_home / "projects" / "abc123abc123"
    assert proj.root == expected_root
    assert proj.memory.path == expected_root / "events.jsonl"
    assert proj.backlog.path == expected_root / "backlog.jsonl"
    assert proj.label == "my-project"
    assert proj.fingerprint == "abc123abc123"


def test_project_memory_lazy_creation(isolated_home: Path) -> None:
    ProjectMemory.open("deadbeefcafe")
    expected_root = isolated_home / "projects" / "deadbeefcafe"
    assert not expected_root.exists()


def test_project_memory_init_seeds_events_and_backlog(isolated_home: Path) -> None:
    proj = ProjectMemory.open("abc123abc123", label="my-project")
    created = proj.init()
    assert created == {
        "events": True,
        "backlog": True,
    }
    assert not (proj.root / "project.md").exists()


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


def test_project_memory_rejects_path_separator(isolated_home: Path) -> None:
    with pytest.raises(ValueError):
        ProjectMemory.open("../escape")


def test_project_memory_journal_isolated_per_fingerprint(
    isolated_home: Path,
) -> None:
    a = ProjectMemory.open("aaaaaaaaaaaa")
    b = ProjectMemory.open("bbbbbbbbbbbb")
    _write_project_event(
        a.memory,
        JournalEntry.new(kind="note", title="a-only", summary="exclusive to a")
    )
    assert [e.title for e in a.memory.all()] == ["a-only"]
    assert b.memory.all() == []


def test_project_memory_relevant_memory_for(isolated_home: Path) -> None:
    proj = ProjectMemory.open("aaaaaaaaaaaa")
    _write_project_event(
        proj.memory,
        JournalEntry.new(
            kind="mission_complete",
            title="refactor sqlite store",
            summary="moved store onto WAL",
            tags=["sqlite"],
        )
    )
    hits = proj.relevant_memory_for("sqlite tuning followup")
    assert len(hits) == 1
    assert hits[0].title.startswith("refactor sqlite")


# ---------------------------------------------------------------------------
# MemoryBundle
# ---------------------------------------------------------------------------

def _init_git_repo(path: Path, remote_url: str) -> None:
    """Helper to make project_fingerprint() resolve via git remote."""
    subprocess.run(
        ["git", "init", "-q"],
        cwd=path,
        env=project._git_env(),
        check=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", remote_url],
        cwd=path,
        env=project._git_env(),
        check=True,
    )


def test_memory_bundle_for_cwd_uses_git_remote(
    isolated_home: Path, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo, "https://github.com/lbx154/argus-skill.git")

    bundle = MemoryBundle.for_cwd(repo)
    assert bundle.global_mem.root == isolated_home
    # fingerprint deterministic (sha1[:12] of normalised remote)
    assert bundle.project.fingerprint
    assert bundle.project.root.parent == isolated_home / "projects"


def test_memory_bundle_for_cwd_falls_back_to_cwd_hash(
    isolated_home: Path, tmp_path: Path
) -> None:
    no_repo = tmp_path / "nogit"
    no_repo.mkdir()
    bundle = MemoryBundle.for_cwd(no_repo)
    assert bundle.project.fingerprint
    assert bundle.project.root == (
        isolated_home / "projects" / bundle.project.fingerprint
    )


def test_memory_bundle_init_creates_both(
    isolated_home: Path, tmp_path: Path
) -> None:
    bundle = MemoryBundle.for_cwd(tmp_path)
    created = bundle.init()
    assert created["global"]["identity"] is True
    assert created["project"]["events"] is True
    assert created["project"]["backlog"] is True
    assert not (bundle.project.root / "project.md").exists()
    # idempotent
    assert bundle.init()["global"] == {"identity": False}
    # Per-project logs only: bundle init must never create a global journal.
    assert not (bundle.global_mem.root / "journal.jsonl").exists()


def test_memory_bundle_render_prelude_excludes_cross_project_journal(
    isolated_home: Path, tmp_path: Path
) -> None:
    bundle = MemoryBundle.for_cwd(tmp_path)
    bundle.init()
    bundle.global_mem.journal.append(
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
    rendered = bundle.render_prelude(objective="upgrade postgres again")
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
    bundle.global_mem.journal.append(
        JournalEntry.new(
            kind="mission_complete", title="global old", summary="wrong repo"
        )
    )

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

    # The project journal write must NOT be mirrored into the global journal.
    global_titles = [entry.title for entry in bundle.global_mem.journal.tail(5)]
    assert global_titles == ["global old"]


def test_memory_bundle_render_prelude_empty_when_nothing_relevant(
    isolated_home: Path, tmp_path: Path
) -> None:
    """Empty memory + un-initialised cards → empty string."""
    bundle = MemoryBundle.for_cwd(tmp_path)
    rendered = bundle.render_prelude(objective="anything")
    assert rendered == ""


def test_memory_bundle_uses_core_paths_project_root(
    isolated_home: Path, tmp_path: Path
) -> None:
    """ProjectMemory under bundle should sit inside ARGUS_SKILL_HOME/projects/."""
    bundle = MemoryBundle.for_cwd(tmp_path)
    assert bundle.project.root.parent == isolated_home / "projects"


# ---------------------------------------------------------------------------
# LifeMemory still works (compatibility facade)
# ---------------------------------------------------------------------------

def test_life_memory_still_works(tmp_path: Path) -> None:
    """The LifeMemory facade uses the canonical event timeline."""
    from argus_skill.life import LifeMemory

    mem = LifeMemory.open(tmp_path)
    mem.init()
    assert (tmp_path / "identity.md").exists()
    assert (tmp_path / "events.jsonl").exists()
    assert (tmp_path / "backlog.jsonl").exists()


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
    bundle_a.global_mem.journal.append(
        JournalEntry.new(kind="note", title="global memory", summary="wrong workspace")
    )
    bundle_a.backlog.add(BacklogItem.new(title="alpha backlog", objective="alpha"))
    bundle_b.backlog.add(BacklogItem.new(title="beta backlog", objective="beta"))

    assert bundle_a.project.root != bundle_b.project.root
    assert bundle_a.project.memory.path != bundle_b.project.memory.path
    assert bundle_a.backlog.path != bundle_b.backlog.path

    prelude_a = bundle_a.render_prelude(objective="alpha objective")
    prelude_b = bundle_b.render_prelude(objective="beta objective")
    assert "alpha memory" in prelude_a
    assert "beta memory" not in prelude_a
    assert "beta memory" in prelude_b
    assert "alpha memory" not in prelude_b

    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.read_daemon_status",
        lambda life_dir: Namespace(
            alive=False,
            pid=None,
            uptime_seconds=None,
            backend=None,
        ),
    )
    monkeypatch.setattr("argus_skill.apps.cli._core._check_logout_survival", lambda status: None)

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
