"""Regression tests for the ``argus-skill --status`` command."""
from __future__ import annotations

import getpass
import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from argus_skill.apps import cli as cli_mod
from argus_skill.apps.cli import _check_logout_survival, _cmd_status
from argus_skill.life import BacklogItem, MemoryBundle
from argus_skill.life.memory import JournalEntry


@pytest.fixture()
def project_with_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    monkeypatch.chdir(repo)
    mem = MemoryBundle.for_cwd(repo, global_root=home)
    mem.init()
    mem.global_mem.journal.append(
        JournalEntry.new(kind="mission_failed", title="old failure", summary="boom")
    )
    done = mem.backlog.add(BacklogItem.new(title="done", objective="finished work"))
    mem.backlog.mark_done(done.id)
    failed = mem.backlog.add(BacklogItem.new(title="failed", objective="bad work"))
    mem.backlog.mark_failed(failed.id, error="boom")
    skipped = mem.backlog.add(BacklogItem.new(title="skipped", objective="later work"))
    mem.backlog.update(skipped.id, status="skipped")
    project_root = mem.project.root
    inbox = project_root / "inbox.jsonl"
    first = json.dumps({"text": "old guidance"}) + "\n"
    second = json.dumps({"text": "fresh guidance"}) + "\n"
    inbox.write_text(first + second, encoding="utf-8")
    (project_root / "inbox.offset").write_text(str(len(first.encode("utf-8"))), encoding="utf-8")
    return home, repo


@pytest.fixture()
def project_with_active_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    monkeypatch.chdir(repo)
    mem = MemoryBundle.for_cwd(repo, global_root=home)
    mem.init()
    mem.global_mem.journal.append(
        JournalEntry.new(kind="mission_failed", title="old failure", summary="boom")
    )
    pending = mem.backlog.add(BacklogItem.new(title="pending", objective="queued work"))
    running = mem.backlog.add(BacklogItem.new(title="running", objective="in flight"))
    mem.backlog.mark_running(running.id)
    done = mem.backlog.add(BacklogItem.new(title="done", objective="finished work"))
    mem.backlog.mark_done(done.id)
    failed = mem.backlog.add(BacklogItem.new(title="failed", objective="bad work"))
    mem.backlog.mark_failed(failed.id, error="boom")
    skipped = mem.backlog.add(BacklogItem.new(title="skipped", objective="later work"))
    mem.backlog.update(skipped.id, status="skipped")
    assert pending.id
    return home, repo


def test_status_separates_active_queue_from_history(
    monkeypatch: pytest.MonkeyPatch,
    project_with_history: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    life_root, repo = project_with_history
    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.read_daemon_status",
        lambda life_dir: Namespace(
            alive=True,
            pid=4321,
            uptime_seconds=12.0,
            backend="memory",
            per_mission_cap_usd=9.0,
            daily_cap_usd=50.0,
        ),
    )
    monkeypatch.setattr("argus_skill.apps.cli._check_logout_survival", lambda status: None)

    rc = _cmd_status(Namespace(life_dir=str(life_root)))
    out = capsys.readouterr().out
    project_root = MemoryBundle.for_cwd(repo, global_root=life_root).project.root

    assert rc == 0
    assert "active   : 0 pending · 0 running" in out
    assert "history  : 1 done · 1 failed · 1 skipped" in out
    assert str(project_root) in out
    assert "done   :" not in out
    assert "failed" in out
    assert "inbox    : 1 pending" in out
    assert "continuous: off" in out
    assert "current  :" not in out
    assert "budget   : per-mission $9.00 · daily $50.00 · remaining $50.00" in out


def test_status_shows_active_work_when_present(
    monkeypatch: pytest.MonkeyPatch,
    project_with_active_and_history: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    life_root, repo = project_with_active_and_history
    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.read_daemon_status",
        lambda life_dir: Namespace(
            alive=True,
            pid=4321,
            uptime_seconds=12.0,
            backend="memory",
            per_mission_cap_usd=9.0,
            daily_cap_usd=50.0,
        ),
    )
    monkeypatch.setattr("argus_skill.apps.cli._check_logout_survival", lambda status: None)

    rc = _cmd_status(Namespace(life_dir=str(life_root)))
    out = capsys.readouterr().out
    project_root = MemoryBundle.for_cwd(repo, global_root=life_root).project.root
    running_item = next(item for item in MemoryBundle.for_cwd(repo, global_root=life_root).backlog.all() if item.status == "running")

    assert rc == 0
    assert "active   : 1 pending · 1 running" in out
    assert "current  :" in out
    assert f"id       : {running_item.id}" in out
    assert "title    : running" in out
    assert "objective: in flight" in out
    assert "history  : 1 done · 1 failed · 1 skipped" in out
    assert str(project_root) in out
    assert "pending" in out
    assert "running" in out
    assert "budget   : per-mission $9.00 · daily $50.00 · remaining $50.00" in out


def test_status_uses_env_caps_and_pauses_when_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
    project_with_history: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    life_root, repo = project_with_history
    bundle = MemoryBundle.for_cwd(repo, global_root=life_root)
    bundle.global_mem.journal.append(
        JournalEntry.new(kind="budget", title="spent", summary="daily spend", cost_usd=5.0)
    )
    monkeypatch.setenv("ARGUS_SKILL_PER_MISSION_CAP_USD", "2.5")
    monkeypatch.setenv("ARGUS_SKILL_DAILY_CAP_USD", "5.0")
    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.read_daemon_status",
        lambda life_dir: Namespace(
            alive=False,
            pid=None,
            uptime_seconds=None,
            backend=None,
            per_mission_cap_usd=99.0,
            daily_cap_usd=99.0,
        ),
    )
    monkeypatch.setattr("argus_skill.apps.cli._check_logout_survival", lambda status: None)

    rc = _cmd_status(Namespace(life_dir=str(life_root)))
    out = capsys.readouterr().out

    assert rc == 0
    assert "budget   : per-mission $2.50 · daily $5.00 · remaining $0.00 (paused)" in out


def test_status_prefers_latest_running_item_and_works_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    monkeypatch.chdir(repo)
    mem = MemoryBundle.for_cwd(repo, global_root=home)
    mem.init()
    older = mem.backlog.add(BacklogItem.new(title="older", objective="first stale row"))
    newer = mem.backlog.add(BacklogItem.new(title="newer", objective="current task row"))
    mem.backlog.update(older.id, status="running", started_ts=10.0)
    mem.backlog.update(newer.id, status="running", started_ts=20.0)

    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.read_daemon_status",
        lambda life_dir: Namespace(
            alive=False,
            pid=None,
            uptime_seconds=None,
            backend=None,
            per_mission_cap_usd=9.0,
            daily_cap_usd=50.0,
        ),
    )
    monkeypatch.setattr("argus_skill.apps.cli._check_logout_survival", lambda status: None)

    rc = _cmd_status(Namespace(life_dir=str(home)))
    out = capsys.readouterr().out

    assert rc == 0
    assert "active   : 0 pending · 2 running" in out
    assert "current  :" in out
    assert f"id       : {newer.id}" in out
    assert "title    : newer" in out
    assert "objective: current task row" in out


@pytest.mark.parametrize(
    (
        "platform",
        "probe_result",
        "expected",
    ),
    [
        (
            "linux",
            subprocess.CompletedProcess(
                ["loginctl", "show-user", "codex", "--property=Linger"],
                0,
                stdout="Linger=yes\n",
                stderr="",
            ),
            "linger=on  (daemon will survive logout / SSH disconnect)",
        ),
        (
            "linux",
            subprocess.CompletedProcess(
                ["loginctl", "show-user", "codex", "--property=Linger"],
                0,
                stdout="Linger=no\n",
                stderr="",
            ),
            (
                "linger=off ⚠  daemon may be killed at logout. "
                "Run `loginctl enable-linger codex` to make 7×24 honest."
            ),
        ),
        ("linux", FileNotFoundError("loginctl"), None),
        ("linux", subprocess.CompletedProcess(["loginctl"], 1, stdout="", stderr=""), None),
        ("linux", subprocess.TimeoutExpired(["loginctl"], 2.0), None),
        ("linux", OSError("probe failed"), None),
        ("darwin", None, None),
    ],
)
def test_check_logout_survival_handles_probe_matrix(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    probe_result: object | None,
    expected: str | None,
) -> None:
    status = Namespace(alive=True, pid=4321)
    monkeypatch.setattr(cli_mod.sys, "platform", platform)
    if platform == "linux":
        monkeypatch.setattr(getpass, "getuser", lambda: "codex")

        def _run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            if isinstance(probe_result, BaseException):
                raise probe_result
            assert isinstance(probe_result, subprocess.CompletedProcess)
            return probe_result

        monkeypatch.setattr(subprocess, "run", _run)

    assert _check_logout_survival(status) == expected


@pytest.mark.parametrize(
    ("survival_msg", "expected_line"),
    [
        (
            "linger=on  (daemon will survive logout / SSH disconnect)",
            "  survival : linger=on  (daemon will survive logout / SSH disconnect)",
        ),
        (
            (
                "linger=off ⚠  daemon may be killed at logout. "
                "Run `loginctl enable-linger codex` to make 7×24 honest."
            ),
            (
                "  survival : linger=off ⚠  daemon may be killed at logout. "
                "Run `loginctl enable-linger codex` to make 7×24 honest."
            ),
        ),
        (None, None),
    ],
)
def test_status_survival_line_follows_probe_result(
    monkeypatch: pytest.MonkeyPatch,
    project_with_history: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    survival_msg: str | None,
    expected_line: str | None,
) -> None:
    life_root, _repo = project_with_history
    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.read_daemon_status",
        lambda life_dir: Namespace(
            alive=True,
            pid=4321,
            uptime_seconds=12.0,
            backend="memory",
            per_mission_cap_usd=9.0,
            daily_cap_usd=50.0,
        ),
    )
    monkeypatch.setattr(
        "argus_skill.apps.cli._check_logout_survival",
        lambda status: survival_msg,
    )

    rc = _cmd_status(Namespace(life_dir=str(life_root)))
    out = capsys.readouterr().out

    assert rc == 0
    if expected_line is None:
        assert "  survival : " not in out
    else:
        assert expected_line in out
