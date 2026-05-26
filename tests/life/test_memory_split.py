"""Tests for GlobalMemory / ProjectMemory / MemoryBundle (Phase 2 split)."""
from __future__ import annotations

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
    assert created == {"identity": True, "journal": True}
    assert (isolated_home / "identity.md").read_text(encoding="utf-8")
    # Idempotent.
    again = mem.init()
    assert again == {"identity": False, "journal": False}


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
    assert len(hits) == 1
    assert hits[0].title.startswith("rewrite postgres")


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
    assert proj.project_card.path == expected_root / "project.md"
    assert proj.memory.path == expected_root / "memory.jsonl"
    assert proj.backlog.path == expected_root / "backlog.jsonl"
    assert proj.label == "my-project"
    assert proj.fingerprint == "abc123abc123"


def test_project_memory_lazy_creation(isolated_home: Path) -> None:
    ProjectMemory.open("deadbeefcafe")
    expected_root = isolated_home / "projects" / "deadbeefcafe"
    assert not expected_root.exists()


def test_project_memory_init_seeds_project_card(isolated_home: Path) -> None:
    proj = ProjectMemory.open("abc123abc123", label="my-project")
    created = proj.init()
    assert created == {
        "project_card": True,
        "memory": True,
        "backlog": True,
    }
    card_text = proj.project_card.path.read_text(encoding="utf-8")
    assert "# my-project" in card_text
    assert "## Project label" in card_text
    assert "## Conventions" in card_text
    assert "## Red lines" in card_text
    assert card_text.strip()


def test_project_memory_init_upgrades_legacy_blank_card(isolated_home: Path) -> None:
    proj = ProjectMemory.open("abc123abc123", label="my-project")
    legacy = """\
# my-project

(Edit me — this is the per-project identity card. Capture conventions,
folder layout, "always do X / never touch Y" rules, contact points for
the team, etc. The agent reads this before every mission targeting
this project.)

## Conventions

## Red lines
"""
    proj.project_card.path.parent.mkdir(parents=True, exist_ok=True)
    proj.project_card.path.write_text(legacy, encoding="utf-8")

    created = proj.init()
    assert created["project_card"] is True
    card_text = proj.project_card.path.read_text(encoding="utf-8")
    assert "## Project label" in card_text
    assert "## Conventions" in card_text
    assert "## Red lines" in card_text
    assert card_text.startswith("# my-project\n")


def test_project_memory_init_idempotent(isolated_home: Path) -> None:
    proj = ProjectMemory.open("abc123abc123")
    proj.init()
    assert proj.init() == {
        "project_card": False,
        "memory": False,
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
    a.memory.append(
        JournalEntry.new(kind="note", title="a-only", summary="exclusive to a")
    )
    assert [e.title for e in a.memory.all()] == ["a-only"]
    assert b.memory.all() == []


def test_project_memory_relevant_memory_for(isolated_home: Path) -> None:
    proj = ProjectMemory.open("aaaaaaaaaaaa")
    proj.memory.append(
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
    assert created["project"]["project_card"] is True
    card_text = bundle.project.project_card.path.read_text(encoding="utf-8")
    assert f"# {bundle.project.label}" in card_text
    assert "## Conventions" in card_text
    assert "## Red lines" in card_text
    # idempotent
    assert bundle.init()["global"] == {"identity": False, "journal": False}


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
    bundle.project.memory.append(
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
    assert "Project card" in rendered
    assert "this project" in rendered
    assert "local postgres migration" in rendered
    assert "cross-project postgres" not in rendered
    assert "other projects" not in rendered


def test_memory_bundle_journal_reads_are_project_scoped(
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
    bundle.journal.append(local)

    assert [entry.title for entry in bundle.journal.all()] == ["local new"]
    assert [entry.title for entry in bundle.journal.tail(5)] == ["local new"]
    assert bundle.journal.path == bundle.project.memory.path
    assert bundle.journal.total_cost_since(0) == pytest.approx(0.25)

    global_titles = [entry.title for entry in bundle.global_mem.journal.tail(5)]
    assert global_titles == ["global old", "local new"]


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
    """The legacy LifeMemory facade must keep its old behaviour."""
    from argus_skill.life import LifeMemory

    mem = LifeMemory.open(tmp_path)
    mem.init()
    assert (tmp_path / "identity.md").exists()
    assert (tmp_path / "journal.jsonl").exists()
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

    bundle_a.project.project_card.path.write_text(
        "# alpha\nAlpha project card\n", encoding="utf-8"
    )
    bundle_b.project.project_card.path.write_text(
        "# beta\nBeta project card\n", encoding="utf-8"
    )
    bundle_a.project.memory.append(
        JournalEntry.new(kind="note", title="alpha memory", summary="alpha only")
    )
    bundle_b.project.memory.append(
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
    assert "Alpha project card" in prelude_a
    assert "alpha memory" in prelude_a
    assert "beta memory" not in prelude_a
    assert "Beta project card" in prelude_b
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
    monkeypatch.setattr("argus_skill.apps.cli._check_logout_survival", lambda status: None)

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
