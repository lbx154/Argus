"""Regression tests for the ``argus-skill --status`` command."""
from __future__ import annotations

import getpass
import json
import subprocess
import time
from argparse import Namespace
from pathlib import Path

import pytest

from argus_skill.apps import cli as cli_mod
from argus_skill.apps.cli import _check_logout_survival, _cmd_status
from argus_skill.life import BacklogItem, MemoryBundle


@pytest.fixture()
def project_with_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    monkeypatch.chdir(repo)
    mem = MemoryBundle.for_cwd(repo, global_root=home)
    mem.init()
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
            global_daily_cap_usd=0.0,
        ),
    )
    monkeypatch.setattr("argus_skill.daemon.life_worker.global_daily_spend", lambda *a, **k: 0.0)
    monkeypatch.setattr("argus_skill.apps.cli._core._check_logout_survival", lambda status: None)

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
    assert (
        "budget   : global daily disabled (spent $0.00)"
    ) in out


def test_status_projects_latest_persisted_mission_outcome(
    monkeypatch: pytest.MonkeyPatch,
    project_with_history: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    life_root, repo = project_with_history
    bundle = MemoryBundle.for_cwd(repo, global_root=life_root)
    done = next(item for item in bundle.backlog.all() if item.status == "done")
    bundle.backlog.update(
        done.id,
        finished_ts=100.0,
        outcome={
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "not_certified",
            "scientific_decision": "no_go",
            "failure_source": "",
            "interruption_kind": "none",
            "resumable": False,
        },
    )
    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.read_daemon_status",
        lambda life_dir: Namespace(
            alive=False,
            pid=None,
            uptime_seconds=None,
            backend=None,
            per_mission_cap_usd=9.0,
            daily_cap_usd=50.0,
            global_daily_cap_usd=0.0,
        ),
    )
    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.global_daily_spend",
        lambda *args, **kwargs: 0.0,
    )
    monkeypatch.setattr(
        "argus_skill.apps.cli._core._check_logout_survival",
        lambda status: None,
    )

    rc = _cmd_status(Namespace(life_dir=str(life_root)))
    out = capsys.readouterr().out

    assert rc == 0
    assert (
        "outcome  : execution=completed · review=done · "
        "stage=not_certified · science=no_go"
    ) in out


def test_status_reads_lifecycle_from_canonical_project_state(
    monkeypatch: pytest.MonkeyPatch,
    project_with_history: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    from datetime import datetime, timezone

    from argus_skill.life.project_lifecycle import ProjectState, ProjectStatus
    from argus_skill.life.project_lifecycle_io import write_persisted

    life_root, repo = project_with_history
    monkeypatch.delenv("ARGUS_SKILL_WORKDIR", raising=False)
    bundle = MemoryBundle.for_cwd(repo, global_root=life_root)
    worktree = bundle.project.root / "code"
    worktree.mkdir()
    write_persisted(
        bundle.project.root,
        status=ProjectStatus(
            project_id=bundle.project.root.name,
            state=ProjectState.QUARANTINED,
            created_at=datetime.now(timezone.utc),
        ),
        history=[],
    )
    assert not (worktree / "lifecycle.json").exists()

    rc = _cmd_status(Namespace(life_dir=str(life_root)))
    out = capsys.readouterr().out

    assert rc == 0
    assert "state         : quarantined  (persisted)" in out
    assert "allocatable   : False" in out


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
            global_daily_cap_usd=0.0,
        ),
    )
    monkeypatch.setattr("argus_skill.daemon.life_worker.global_daily_spend", lambda *a, **k: 0.0)
    monkeypatch.setattr("argus_skill.apps.cli._core._check_logout_survival", lambda status: None)

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
    assert (
        "budget   : global daily disabled (spent $0.00)"
    ) in out


def test_status_shows_latest_mission_telemetry(
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
        ),
    )
    monkeypatch.setattr("argus_skill.apps.cli._core._check_logout_survival", lambda status: None)
    project_root = MemoryBundle.for_cwd(repo, global_root=life_root).project.root
    from argus_skill.life.telemetry import TelemetryRecorder

    TelemetryRecorder(project_root).record({
        "running": True,
        "seq": 7,
        "item_id": "task-telemetry",
        "running_seconds": 125.0,
        "processes": [{"cmd": "python run_eval.py", "pid": 1234}],
        "process_count": 1,
        "files": [{
            "path": "results/run.jsonl",
            "new_lines": 3,
            "line_count": 24,
            "size": 1200,
        }],
        "files_changed": 1,
        "scanned_files": 5,
        "scan_ms": 12,
    })

    rc = _cmd_status(Namespace(life_dir=str(life_root)))
    out = capsys.readouterr().out

    assert rc == 0
    assert "telemetry:" in out
    assert "state    : running · mission 2m 5s" in out
    assert "proc     : python run_eval.py" in out
    assert "artifacts: results/run.jsonl +3 lines" in out


def test_status_shows_planner_activity_when_backlog_and_telemetry_are_idle(
    monkeypatch: pytest.MonkeyPatch,
    project_with_history: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    life_root, repo = project_with_history
    project_root = MemoryBundle.for_cwd(repo, global_root=life_root).project.root
    (project_root / "events.jsonl").write_text(
        json.dumps({
            "type": "engineer.progress",
            "kind": "command_execution",
            "actor": "planner.cycle0",
            "agent_layer": "planner",
            "status": "completed",
            "text": "pytest -q",
            "ts": time.time() - 3,
        })
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.read_daemon_status",
        lambda life_dir: Namespace(
            alive=True,
            pid=4321,
            uptime_seconds=12.0,
            backend="memory",
        ),
    )
    monkeypatch.setattr("argus_skill.apps.cli._core._check_logout_survival", lambda status: None)
    monkeypatch.setattr(
        "argus_skill.life.telemetry.collect_descendant_processes",
        lambda pid, limit=12: {
            "processes": [
                {"pid": 5001, "cmd": "node /usr/bin/codex exec --json -m gpt-5.4 -"},
                {"pid": 5002, "cmd": "codex exec --json -m gpt-5.4 -"},
            ],
            "process_count": 2,
            "processes_truncated": 0,
        },
    )

    rc = _cmd_status(Namespace(life_dir=str(life_root)))
    out = capsys.readouterr().out

    assert rc == 0
    assert "active   : 0 pending · 0 running" in out
    assert "activity :" in out
    assert "state    : planner active · 2 agent process(es)" in out
    assert "last event" in out
    assert "last     : planner.cycle0 command_execution completed · pytest -q" in out


def test_follow_heartbeat_includes_latest_telemetry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus_skill.life.telemetry import TelemetryRecorder

    life_dir = tmp_path / "life"
    life_dir.mkdir()
    events_path = life_dir / "events.jsonl"
    events_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli_mod._follow, "_daemon_alive_for_events_path", lambda path: True)
    TelemetryRecorder(life_dir).record({
        "running": True,
        "seq": 3,
        "running_seconds": 61.0,
        "processes": [{"cmd": "python run_eval.py"}],
        "process_count": 1,
        "files": [{"path": "results/run.jsonl", "new_lines": 2}],
        "files_changed": 1,
        "scanned_files": 4,
        "scan_ms": 8,
    })

    line = cli_mod._format_follow_heartbeat(events_path, "engineer", 22.0)

    assert "daemon alive" in line
    assert "telemetry running" in line
    assert "proc: python run_eval.py" in line
    assert "artifacts: results/run.jsonl +2 lines" in line


def test_status_uses_env_caps_and_pauses_when_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
    project_with_history: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    life_root, repo = project_with_history
    bundle = MemoryBundle.for_cwd(repo, global_root=life_root)
    (bundle.project.root / "events.jsonl").write_text(
        json.dumps({
            "type": "life.mission.completed",
            "ts": time.time(),
            "cost_usd": 5.0,
            "success": True,
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "30.0")
    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.read_daemon_status",
        lambda life_dir: Namespace(
            alive=False,
            pid=None,
            uptime_seconds=None,
            backend=None,
            global_daily_cap_usd=0.0,
        ),
    )
    monkeypatch.setattr("argus_skill.daemon.life_worker.global_daily_spend", lambda *a, **k: 5.0)
    monkeypatch.setattr("argus_skill.apps.cli._core._check_logout_survival", lambda status: None)

    rc = _cmd_status(Namespace(life_dir=str(life_root)))
    out = capsys.readouterr().out

    assert rc == 0
    assert (
        "budget   : global daily $30.00 (spent $5.00) · remaining $25.00"
    ) in out


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
        ),
    )
    monkeypatch.setattr("argus_skill.apps.cli._core._check_logout_survival", lambda status: None)

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
    monkeypatch.setattr(cli_mod._core.sys, "platform", platform)
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
        ),
    )
    monkeypatch.setattr(
        "argus_skill.apps.cli._core._check_logout_survival",
        lambda status: survival_msg,
    )

    rc = _cmd_status(Namespace(life_dir=str(life_root)))
    out = capsys.readouterr().out

    assert rc == 0
    if expected_line is None:
        assert "  survival : " not in out
    else:
        assert expected_line in out
