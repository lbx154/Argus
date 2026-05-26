"""Subprocess lifecycle coverage for the ``argus-skill`` daemon CLI."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from argus_skill.core import project
from argus_skill.daemon.life_worker import write_continuous_config

_ENV_VARS_TO_CLEAR = (
    "ARGUS_SKILL_DAILY_CAP_USD",
    "ARGUS_SKILL_DAEMON_AUTO_RESTART",
    "ARGUS_SKILL_DAEMON_HANDOFF_CONFIG",
    "ARGUS_SKILL_DAEMON_HANDOFF_GEN",
    "ARGUS_SKILL_DAEMON_HANDOFF_MAX_GEN",
    "ARGUS_SKILL_DAEMON_HANDOFF_MIN_S",
    "ARGUS_SKILL_DAEMON_HANDOFF_READY",
    "ARGUS_SKILL_DAEMON_HANDOFF_TOKEN",
    "ARGUS_SKILL_DAEMON_POLL_S",
    "ARGUS_SKILL_DAEMON_SOURCE_SIGNATURE",
    "ARGUS_SKILL_DAEMON_TEST_SOURCE_SIGNATURE_FILE",
    "ARGUS_SKILL_ENGINEER_MODEL",
    "ARGUS_SKILL_HOME",
    "ARGUS_SKILL_LIFE_BACKEND",
    "ARGUS_SKILL_PER_MISSION_CAP_USD",
    "ARGUS_SKILL_RESEARCH_PROFILE",
    "ARGUS_SKILL_RESEARCH_PROFILE_PATH",
    "ARGUS_SKILL_REVIEWER_MODEL",
    "ARGUS_SKILL_SCIENTIST_MODEL",
    "ARGUS_SKILL_SKILLS_DIR",
    "ARGUS_SKILL_WORKDIR",
)


def _clean_env(**updates: str) -> dict[str, str]:
    env = os.environ.copy()
    for name in _ENV_VARS_TO_CLEAR:
        env.pop(name, None)
    env.update(updates)
    return env


def _run_cli(
    *args: str,
    env: dict[str, str],
    cwd: Path,
    stdin: int | None = subprocess.DEVNULL,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "argus_skill", *args],
        cwd=cwd,
        env=env,
        stdin=stdin,
        text=True,
        capture_output=True,
        timeout=30,
    )


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_until(predicate, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting for daemon lifecycle condition")


def _read_pid(pid_path: Path) -> int | None:
    try:
        return int(pid_path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict[str, object]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def test_daemon_lifecycle_via_subprocess(tmp_path: Path) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "README.md").write_text("daemon lifecycle test\n", encoding="utf-8")
    skills_dir = tmp_path / "skills"
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    project_root = global_root / "projects" / fingerprint
    pid_path = project_root / "daemon.pid"
    status_path = project_root / "daemon.status.json"
    env = _clean_env(
        ARGUS_SKILL_LIFE_BACKEND="memory",
        ARGUS_SKILL_SKILLS_DIR=str(skills_dir),
        ARGUS_SKILL_PER_MISSION_CAP_USD="12.5",
        ARGUS_SKILL_DAILY_CAP_USD="42.25",
    )

    pid: int | None = None
    try:
        start = _run_cli("--daemon", "--life-dir", str(global_root), env=env, cwd=repo_dir)
        assert start.returncode == 0, start

        _wait_until(lambda: pid_path.exists() and status_path.exists())
        pid = _read_pid(pid_path)
        assert pid is not None and pid > 0
        assert _pid_is_alive(pid)

        status = json.loads(status_path.read_text(encoding="utf-8"))
        assert status["pid"] == pid
        assert status["backend"] == "memory"
        assert Path(status["life_dir"]) == project_root
        assert status["per_mission_cap_usd"] == 12.5
        assert status["daily_cap_usd"] == 42.25

        live_status = _run_cli("--status", "--life-dir", str(global_root), env=env, cwd=repo_dir)
        assert live_status.returncode == 0, live_status
        assert "daemon   : alive" in live_status.stdout
        assert f"pid {pid}" in live_status.stdout
        assert "budget   : per-mission $12.50 · daily $42.25 · remaining $42.25" in live_status.stdout

        second = _run_cli("--daemon", "--life-dir", str(global_root), env=env, cwd=repo_dir)
        assert second.returncode == 2, second
        assert "already running" in second.stderr
        assert _read_pid(pid_path) == pid

        stop = _run_cli("--daemon-stop", "--life-dir", str(global_root), env=env, cwd=repo_dir)
        assert stop.returncode == 0, stop

        _wait_until(lambda: not pid_path.exists() and not status_path.exists() and not _pid_is_alive(pid))

        final_status = _run_cli("--status", "--life-dir", str(global_root), env=env, cwd=repo_dir)
        assert final_status.returncode == 0, final_status
        assert "daemon   : not running" in final_status.stdout
        assert "budget   : per-mission $12.50 · daily $42.25 · remaining $42.25" in final_status.stdout
    finally:
        if pid is not None:
            _run_cli("--daemon-stop", "--life-dir", str(global_root), env=env, cwd=repo_dir)
            try:
                _wait_until(lambda: not _pid_is_alive(pid), timeout=10.0)
            except AssertionError:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass


def test_daemon_bootstrap_preflight_records_empty_repo_objective(
    tmp_path: Path,
) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "empty-repo"
    repo_dir.mkdir()
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    project_root = global_root / "projects" / fingerprint
    pid_path = project_root / "daemon.pid"
    backlog_path = project_root / "backlog.jsonl"
    events_path = project_root / "events.jsonl"
    env = _clean_env(
        ARGUS_SKILL_LIFE_BACKEND="memory",
        PYTHONUNBUFFERED="1",
    )

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "argus_skill",
            "--daemon-fg",
            "--life-dir",
            str(global_root),
        ],
        cwd=repo_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        bootstrap_row: dict[str, object] | None = None
        bootstrap_event: dict[str, object] | None = None

        def _bootstrap_ready() -> bool:
            nonlocal bootstrap_row, bootstrap_event
            if backlog_path.exists():
                for row in _read_jsonl(backlog_path):
                    if row.get("title") == "bootstrap empty project root":
                        bootstrap_row = row
                        break
            if events_path.exists():
                for event in _read_jsonl(events_path):
                    if event.get("type") == "life.project.bootstrap_required":
                        bootstrap_event = event
                        break
            return (
                pid_path.exists()
                and bootstrap_row is not None
                and bootstrap_event is not None
            )

        _wait_until(_bootstrap_ready, timeout=20.0)
        assert bootstrap_row is not None
        assert bootstrap_event is not None

        assert "pyproject.toml" in str(bootstrap_row.get("objective", ""))
        assert "README.md" in str(bootstrap_row.get("objective", ""))
        assert f"src/{repo_dir.name.replace('-', '_')}/__init__.py" in str(bootstrap_row.get("objective", ""))
        assert bootstrap_event["type"] == "life.project.bootstrap_required"
        assert "uninitialized project root" in str(bootstrap_event.get("event_text", ""))
        assert "pyproject.toml" in str(bootstrap_event.get("objective", ""))

        proc.terminate()
        _wait_until(lambda: proc.poll() is not None, timeout=10.0)
    finally:
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            proc.wait(timeout=10)


def test_daemon_bootstrap_materializes_repo_files_in_workdir(
    tmp_path: Path,
) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    project_root = global_root / "projects" / fingerprint
    pid_path = project_root / "daemon.pid"
    status_path = project_root / "daemon.status.json"
    project_card_path = project_root / "project.md"
    memory_path = project_root / "memory.jsonl"
    backlog_path = project_root / "backlog.jsonl"
    package_init = repo_dir / "src" / "repo" / "__init__.py"
    env = _clean_env(
        ARGUS_SKILL_LIFE_BACKEND="memory",
        PYTHONUNBUFFERED="1",
    )

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "argus_skill",
            "--daemon-fg",
            "--life-dir",
            str(global_root),
        ],
        cwd=repo_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_until(
            lambda: (
                pid_path.exists()
                and status_path.exists()
                and (repo_dir / ".git").exists()
                and (repo_dir / "pyproject.toml").exists()
                and (repo_dir / "README.md").exists()
                and package_init.exists()
                and (repo_dir / "tests" / "test_smoke.py").exists()
                and project_card_path.exists()
                and memory_path.exists()
                and backlog_path.exists()
            ),
            timeout=30.0,
        )

        assert project_card_path.parent == project_root
        assert memory_path.parent == project_root
        assert backlog_path.parent == project_root
        assert not (project_root / "pyproject.toml").exists()
        assert not (project_root / "README.md").exists()
        assert not (project_root / "src").exists()
        assert not (project_root / "tests").exists()
        assert (repo_dir / ".git").exists()
        assert (repo_dir / "pyproject.toml").exists()
        assert (repo_dir / "README.md").exists()
        assert package_init.exists()
        assert (repo_dir / "tests" / "test_smoke.py").exists()
    finally:
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            proc.wait(timeout=10)


def test_daemon_bootstrap_materializes_research_files_in_workdir(
    tmp_path: Path,
) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "empty-repo"
    repo_dir.mkdir()
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    project_root = global_root / "projects" / fingerprint
    pid_path = project_root / "daemon.pid"
    status_path = project_root / "daemon.status.json"
    research_state = repo_dir / "research" / "PIPELINE_STATE.json"
    research_brief = repo_dir / "research" / "RESEARCH_BRIEF.md"
    research_plan = repo_dir / "research" / "EXPERIMENT_PLAN.md"
    research_claims = repo_dir / "research" / "CLAIMS_TO_TEST.md"
    research_go_no_go = repo_dir / "research" / "GO_NO_GO.md"
    benchmark_provenance = repo_dir / "experiments" / "BENCHMARK_PROVENANCE.md"
    env = _clean_env(
        ARGUS_SKILL_LIFE_BACKEND="memory",
        ARGUS_SKILL_RESEARCH_PROFILE="emnlp2026-tierharness",
        PYTHONUNBUFFERED="1",
    )

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "argus_skill",
            "--daemon-fg",
            "--life-dir",
            str(global_root),
        ],
        cwd=repo_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_until(
            lambda: (
                pid_path.exists()
                and status_path.exists()
                and research_state.exists()
                and research_brief.exists()
                and research_plan.exists()
                and research_claims.exists()
                and research_go_no_go.exists()
                and benchmark_provenance.exists()
            ),
            timeout=30.0,
        )

        assert (repo_dir / ".git").exists()
        assert not (repo_dir / "pyproject.toml").exists()
        assert not (repo_dir / "README.md").exists()
        assert not (repo_dir / "src").exists()
        assert not (repo_dir / "tests").exists()
        assert "research bootstrap mission" in research_state.read_text(encoding="utf-8").lower()
        assert "research seed" in research_brief.read_text(encoding="utf-8").lower()
        assert "benchmark source: to be selected" in benchmark_provenance.read_text(encoding="utf-8").lower()
    finally:
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            proc.wait(timeout=10)


def test_daemon_bootstrap_heals_partial_research_seed_in_workdir(
    tmp_path: Path,
) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "empty-repo"
    repo_dir.mkdir()
    (repo_dir / "research").mkdir(parents=True, exist_ok=True)
    pipeline_state = repo_dir / "research" / "PIPELINE_STATE.json"
    original_state = "{\n  \"current_stage\": \"plan\"\n}\n"
    pipeline_state.write_text(original_state, encoding="utf-8")
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    project_root = global_root / "projects" / fingerprint
    pid_path = project_root / "daemon.pid"
    status_path = project_root / "daemon.status.json"
    research_brief = repo_dir / "research" / "RESEARCH_BRIEF.md"
    research_plan = repo_dir / "research" / "EXPERIMENT_PLAN.md"
    research_claims = repo_dir / "research" / "CLAIMS_TO_TEST.md"
    research_go_no_go = repo_dir / "research" / "GO_NO_GO.md"
    benchmark_provenance = repo_dir / "experiments" / "BENCHMARK_PROVENANCE.md"
    env = _clean_env(
        ARGUS_SKILL_LIFE_BACKEND="memory",
        ARGUS_SKILL_RESEARCH_PROFILE="emnlp2026-tierharness",
        PYTHONUNBUFFERED="1",
    )

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "argus_skill",
            "--daemon-fg",
            "--life-dir",
            str(global_root),
        ],
        cwd=repo_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_until(
            lambda: (
                pid_path.exists()
                and status_path.exists()
                and research_brief.exists()
                and research_plan.exists()
                and research_claims.exists()
                and research_go_no_go.exists()
                and benchmark_provenance.exists()
            ),
            timeout=30.0,
        )

        assert pipeline_state.read_text(encoding="utf-8") == original_state
        assert (repo_dir / ".git").exists()
        assert not (repo_dir / "pyproject.toml").exists()
        assert not (repo_dir / "README.md").exists()
        assert not (repo_dir / "src").exists()
        assert not (repo_dir / "tests").exists()
        assert "testable research plan" in research_plan.read_text(encoding="utf-8").lower()
    finally:
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            proc.wait(timeout=10)


def test_daemon_foreground_handoff_replaces_pid_and_cleans_sidecars(
    tmp_path: Path,
) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "README.md").write_text("handoff lifecycle test\n", encoding="utf-8")
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    project_root = global_root / "projects" / fingerprint
    pid_path = project_root / "daemon.pid"
    status_path = project_root / "daemon.status.json"
    handoff_path = project_root / "daemon.handoff.json"
    signature_path = tmp_path / "daemon-source-signature.txt"
    signature_path.write_text("sig-1\n", encoding="utf-8")
    env = _clean_env(
        ARGUS_SKILL_LIFE_BACKEND="memory",
        ARGUS_SKILL_DAEMON_AUTO_RESTART="1",
        ARGUS_SKILL_DAEMON_HANDOFF_MIN_S="0",
        ARGUS_SKILL_DAEMON_HANDOFF_MAX_GEN="2",
        ARGUS_SKILL_DAEMON_POLL_S="0.1",
        ARGUS_SKILL_DAEMON_TEST_SOURCE_SIGNATURE_FILE=str(signature_path),
        PYTHONUNBUFFERED="1",
    )

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "argus_skill",
            "--daemon-fg",
            "--life-dir",
            str(global_root),
        ],
        cwd=repo_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    replacement_pid: int | None = None
    try:
        _wait_until(lambda: pid_path.exists() and status_path.exists(), timeout=15.0)
        incumbent_pid = _read_pid(pid_path)
        assert incumbent_pid is not None and incumbent_pid == proc.pid

        status = json.loads(status_path.read_text(encoding="utf-8"))
        assert status["pid"] == incumbent_pid
        assert status["backend"] == "memory"
        assert Path(status["life_dir"]) == project_root
        assert not handoff_path.exists()
        assert not any(project_root.glob("daemon.handoff.*.json"))

        signature_path.write_text("sig-2\n", encoding="utf-8")

        def _current_pid() -> int | None:
            return _read_pid(pid_path)

        _wait_until(
            lambda: (
                (pid := _current_pid()) is not None
                and pid != incumbent_pid
                and _pid_is_alive(pid)
            ),
            timeout=20.0,
        )
        replacement_pid = _current_pid()
        assert replacement_pid is not None and replacement_pid != incumbent_pid
        assert _pid_is_alive(replacement_pid)

        def _replacement_status_pid() -> int | None:
            try:
                return int(
                    json.loads(status_path.read_text(encoding="utf-8"))["pid"]
                )
            except (OSError, ValueError, KeyError, TypeError):
                return None

        _wait_until(
            lambda: _replacement_status_pid() == replacement_pid,
            timeout=20.0,
        )
        _wait_until(lambda: proc.poll() is not None, timeout=20.0)

        replacement_status = json.loads(status_path.read_text(encoding="utf-8"))
        assert replacement_status["pid"] == replacement_pid
        assert replacement_status["backend"] == "memory"
        assert Path(replacement_status["life_dir"]) == project_root
        assert not handoff_path.exists()
        assert not any(project_root.glob("daemon.handoff.*.json"))

        assert proc.wait(timeout=20) == 0
        stdout, stderr = proc.communicate(timeout=5)
        output = stdout + stderr
        assert "Traceback" not in output
        assert "daemon handoff candidate ready" in output
        assert "test-controlled source signature changed" in output

        live_status = _run_cli("--status", "--life-dir", str(global_root), env=env, cwd=repo_dir)
        assert live_status.returncode == 0, live_status
        assert "daemon   : alive" in live_status.stdout
        assert f"pid {replacement_pid}" in live_status.stdout
        assert "backend memory" in live_status.stdout

        stop = _run_cli("--daemon-stop", "--life-dir", str(global_root), env=env, cwd=repo_dir)
        assert stop.returncode == 0, stop

        _wait_until(lambda: not pid_path.exists() and not status_path.exists(), timeout=20.0)

        final_status = _run_cli("--status", "--life-dir", str(global_root), env=env, cwd=repo_dir)
        assert final_status.returncode == 0, final_status
        assert "daemon   : not running" in final_status.stdout
    finally:
        _run_cli("--daemon-stop", "--life-dir", str(global_root), env=env, cwd=repo_dir)
        if replacement_pid is not None:
            try:
                _wait_until(lambda: not _pid_is_alive(replacement_pid), timeout=10.0)
            except AssertionError:
                try:
                    os.kill(replacement_pid, signal.SIGKILL)
                except OSError:
                    pass
                _wait_until(lambda: not _pid_is_alive(replacement_pid), timeout=10.0)
        elif pid_path.exists():
            pid = _read_pid(pid_path)
            if pid is not None:
                try:
                    _wait_until(lambda: not _pid_is_alive(pid), timeout=10.0)
                except AssertionError:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass


def test_daemon_continuous_planner_followup_work_and_project_done(
    tmp_path: Path,
) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "README.md").write_text("planner lifecycle test\n", encoding="utf-8")
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    project_root = global_root / "projects" / fingerprint
    pid_path = project_root / "daemon.pid"
    status_path = project_root / "daemon.status.json"
    events_path = project_root / "events.jsonl"
    log_path = project_root / "daemon.log"
    continuous_path = project_root / "continuous.json"
    planner_script_path = tmp_path / "planner-script.json"
    planner_script_path.write_text(
        json.dumps(
            {
                "planner": [
                    {
                        "project_done": False,
                        "reason": "needs follow-up work",
                        "delay_seconds": 0.25,
                        "new_tasks": [
                            {
                                "title": "follow-up task",
                                "impact_score": 4,
                                "impact_area": "integration",
                                "evidence": "daemon continuous planner must enqueue follow-up work",
                                "objective": "write the follow-up deliverable",
                            }
                        ],
                    },
                    {
                        "project_done": True,
                        "reason": "follow-up complete",
                        "delay_seconds": 0.25,
                        "new_tasks": [],
                    },
                ],
                "critic": [
                    {
                        "stop": True,
                        "reason": "follow-up good enough",
                        "delay_seconds": 0.15,
                        "improvements": [],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    env = _clean_env(
        ARGUS_SKILL_LIFE_BACKEND="memory",
        ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS="1",
        ARGUS_SKILL_DAEMON_POLL_S="0.1",
        ARGUS_SKILL_DAEMON_TEST_PLANNER_SCRIPT=str(planner_script_path),
        PYTHONUNBUFFERED="1",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "argus_skill",
            "--daemon",
            "--continuous",
            "--objective",
            "keep following up",
            "--life-dir",
            str(global_root),
        ],
        cwd=repo_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc
    assert "Traceback" not in (proc.stdout + proc.stderr)

    _wait_until(lambda: pid_path.exists() and status_path.exists(), timeout=15.0)

    pid = _read_pid(pid_path)
    assert pid is not None and _pid_is_alive(pid)

    events: list[dict[str, object]] = []

    def _planner_flow_ready() -> bool:
        nonlocal events
        events = _read_jsonl(events_path)
        task_added = any(event.get("type") == "life.planner.task_added" for event in events)
        mission_completed = any(event.get("type") == "life.mission.completed" for event in events)
        project_done = any(
            event.get("type") == "life.planner.verdict" and event.get("project_done") is True
            for event in events
        )
        return task_added and mission_completed and project_done

    _wait_until(_planner_flow_ready, timeout=20.0)

    task_added = next(event for event in events if event.get("type") == "life.planner.task_added")
    task_id = str(task_added["item_id"])
    mission_completed = next(
        event
        for event in events
        if event.get("type") == "life.mission.completed" and event.get("item_id") == task_id
    )
    verdicts = [event for event in events if event.get("type") == "life.planner.verdict"]
    assert any(event.get("project_done") is False for event in verdicts)
    assert any(event.get("project_done") is True for event in verdicts)
    assert mission_completed["success"] is True

    added_index = next(i for i, event in enumerate(events) if event.get("type") == "life.planner.task_added")
    completed_index = next(
        i
        for i, event in enumerate(events)
        if event.get("type") == "life.mission.completed" and event.get("item_id") == task_id
    )
    done_index = next(
        i
        for i, event in enumerate(events)
        if event.get("type") == "life.planner.verdict" and event.get("project_done") is True
    )
    assert added_index < completed_index < done_index

    _wait_until(
        lambda: (
            continuous_path.exists()
            and "done_at" in json.loads(continuous_path.read_text(encoding="utf-8"))
        ),
        timeout=10.0,
    )
    continuous = json.loads(continuous_path.read_text(encoding="utf-8"))
    assert continuous == {
        "enabled": False,
        "objective": "keep following up",
        "done_reason": "planner declared project done",
        "done_at": continuous["done_at"],
    }
    assert "Traceback" not in log_path.read_text(encoding="utf-8")

    live_status = _run_cli("--status", "--life-dir", str(global_root), env=env, cwd=repo_dir)
    assert live_status.returncode == 0, live_status
    assert "daemon   : alive" in live_status.stdout
    assert "continuous: off" in live_status.stdout
    assert "done_reason: planner declared project done" in live_status.stdout

    stop = _run_cli("--daemon-stop", "--life-dir", str(global_root), env=env, cwd=repo_dir)
    assert stop.returncode == 0, stop

    _wait_until(lambda: not pid_path.exists() and not status_path.exists(), timeout=20.0)
    if pid is not None:
        _wait_until(lambda: not _pid_is_alive(pid), timeout=20.0)

    final_status = _run_cli("--status", "--life-dir", str(global_root), env=env, cwd=repo_dir)
    assert final_status.returncode == 0, final_status
    assert "daemon   : not running" in final_status.stdout
    assert "continuous: off" in final_status.stdout


def test_daemon_continuous_open_ended_project_done_stays_on(
    tmp_path: Path,
) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "README.md").write_text("open-ended planner lifecycle test\n", encoding="utf-8")
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    project_root = global_root / "projects" / fingerprint
    pid_path = project_root / "daemon.pid"
    status_path = project_root / "daemon.status.json"
    events_path = project_root / "events.jsonl"
    continuous_path = project_root / "continuous.json"
    planner_script_path = tmp_path / "planner-script-open-ended.json"
    planner_script_path.write_text(
        json.dumps(
            {
                "planner": [
                    {
                        "project_done": True,
                        "reason": "open-ended objective can continue later",
                        "delay_seconds": 0.1,
                        "new_tasks": [],
                    },
                    {
                        "project_done": True,
                        "reason": "open-ended objective can continue later",
                        "delay_seconds": 0.1,
                        "new_tasks": [],
                    },
                    {
                        "project_done": True,
                        "reason": "open-ended objective can continue later",
                        "delay_seconds": 0.1,
                        "new_tasks": [],
                    },
                ],
                "critic": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    env = _clean_env(
        ARGUS_SKILL_LIFE_BACKEND="memory",
        ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS="1",
        ARGUS_SKILL_DAEMON_POLL_S="0.1",
        ARGUS_SKILL_DAEMON_TEST_PLANNER_SCRIPT=str(planner_script_path),
        PYTHONUNBUFFERED="1",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "argus_skill",
            "--daemon",
            "--continuous",
            "--objective",
            "open-ended self-improvement goal",
            "--life-dir",
            str(global_root),
        ],
        cwd=repo_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc
    assert "Traceback" not in (proc.stdout + proc.stderr)

    _wait_until(lambda: pid_path.exists() and status_path.exists(), timeout=15.0)
    pid = _read_pid(pid_path)
    assert pid is not None and _pid_is_alive(pid)

    def _open_ended_retry_ready() -> bool:
        events = _read_jsonl(events_path)
        return any(
            event.get("type") == "life.planner.verdict"
            and event.get("open_ended_objective") is True
            for event in events
        )

    _wait_until(_open_ended_retry_ready, timeout=20.0)

    continuous = json.loads(continuous_path.read_text(encoding="utf-8"))
    assert continuous["enabled"] is True
    assert continuous["objective"] == "open-ended self-improvement goal"
    assert "done_reason" not in continuous

    live_status = _run_cli("--status", "--life-dir", str(global_root), env=env, cwd=repo_dir)
    assert live_status.returncode == 0, live_status
    assert "daemon   : alive" in live_status.stdout
    assert "continuous: on" in live_status.stdout
    assert "done_reason:" not in live_status.stdout

    stop = _run_cli("--daemon-stop", "--life-dir", str(global_root), env=env, cwd=repo_dir)
    assert stop.returncode == 0, stop

    _wait_until(lambda: not pid_path.exists() and not status_path.exists(), timeout=20.0)
    _wait_until(lambda: not _pid_is_alive(pid), timeout=20.0)

    final_status = _run_cli("--status", "--life-dir", str(global_root), env=env, cwd=repo_dir)
    assert final_status.returncode == 0, final_status
    assert "daemon   : not running" in final_status.stdout
    assert "continuous: on" in final_status.stdout


def test_repl_autospawn_pid_matches_status_sidecar(tmp_path: Path) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    env = _clean_env(
        ARGUS_SKILL_LIFE_BACKEND="memory",
        ARGUS_SKILL_SAFE_MODE="1",
    )

    pid: int | None = None
    try:
        run = _run_cli("--life-dir", str(global_root), env=env, cwd=repo_dir)
        output = run.stdout + run.stderr
        assert run.returncode == 0, run
        assert "daemon auto-spawned (pid 0)" not in output
        assert "daemon auto-spawned (pid " in output

        status = json.loads(
            (global_root / "projects" / project.project_fingerprint(repo_dir).fingerprint / "daemon.status.json").read_text(encoding="utf-8")
        )
        pid = int(status["pid"])
        assert pid > 0
        assert f"daemon auto-spawned (pid {pid})" in output
        assert f"pid {pid}" in output
    finally:
        if pid is not None:
            _run_cli("--daemon-stop", "--life-dir", str(global_root), env=env, cwd=repo_dir)
            try:
                _wait_until(lambda: not _pid_is_alive(pid), timeout=10.0)
            except AssertionError:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass


def test_repl_inherited_continuous_downgrades_on_memory_backend(tmp_path: Path) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    project_root = global_root / "projects" / fingerprint
    write_continuous_config(
        project_root,
        enabled=True,
        objective="keep going",
        done_reason="planner declared project done",
    )
    before = (project_root / "continuous.json").read_text(encoding="utf-8")

    env = _clean_env(ARGUS_SKILL_LIFE_BACKEND="memory")
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "argus_skill",
            "--no-daemon",
            "--life-dir",
            str(global_root),
        ],
        cwd=repo_dir,
        env=env,
        input="/status\n/exit\n",
        text=True,
        capture_output=True,
        timeout=30,
    )
    after = (project_root / "continuous.json").read_text(encoding="utf-8")
    output = run.stdout + run.stderr

    assert run.returncode == 0, run
    assert "continuous: off" in output
    assert "objective: keep going" in output
    assert "done_reason: planner declared project done" in output
    assert after == before


def test_follow_waits_for_fresh_events_file(tmp_path: Path) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    project_root = global_root / "projects" / fingerprint
    backlog_path = project_root / "backlog.jsonl"
    events_path = global_root / "projects" / fingerprint / "events.jsonl"
    project_root.mkdir(parents=True, exist_ok=True)
    backlog_path.write_text(
        json.dumps(
            {
                "id": "mission-1",
                "title": "Ship follow output",
                "objective": "show the operator the task objective",
                "status": "running",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    env = _clean_env(
        ARGUS_SKILL_LIFE_BACKEND="memory",
        PYTHONUNBUFFERED="1",
    )

    proc = subprocess.Popen(
        [sys.executable, "-m", "argus_skill", "--follow", "--life-dir", str(global_root)],
        cwd=repo_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.6)
        assert proc.poll() is None
        events_path.write_text("", encoding="utf-8")
        time.sleep(0.4)
        assert proc.poll() is None

        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "type": "life.mission.started",
                        "item_id": "mission-1",
                        "title": "Ship follow output",
                    }
                )
                + "\n"
            )
            fh.write(
                json.dumps(
                    {
                        "type": "life.mission.completed",
                        "item_id": "mission-1",
                        "status": "done",
                        "success": True,
                    }
                )
                + "\n"
            )
        time.sleep(0.8)
        assert proc.poll() is None

        proc.send_signal(signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=10)
        output = stdout + stderr
        assert proc.returncode == 0, proc
        assert "not found" not in output
        assert "following" in output
        assert "waiting for" in output
        assert "started · item_id=mission-1 · title=Ship follow output · objective=show the operator the task objective" in output
        assert "mission complete · status=done · success=True · item_id=mission-1 · title=Ship follow output · objective=show the operator the task objective" in output
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate(timeout=10)


def test_follow_accepts_project_life_dir(tmp_path: Path) -> None:
    """Operators often paste the exact ``projects/<fingerprint>`` path."""
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    project_root = global_root / "projects" / fingerprint
    events_path = project_root / "events.jsonl"
    project_root.mkdir(parents=True)
    events_path.write_text("", encoding="utf-8")
    env = _clean_env(
        ARGUS_SKILL_LIFE_BACKEND="memory",
        PYTHONUNBUFFERED="1",
    )

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "argus_skill",
            "--follow",
            "--life-dir",
            str(project_root),
        ],
        cwd=repo_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.6)
        assert proc.poll() is None
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "life.mission.started", "item_id": "mission-2"}) + "\n")
        time.sleep(0.6)
        proc.send_signal(signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=10)
        output = stdout + stderr
        assert proc.returncode == 0, proc
        assert f"following {events_path}" in output
        assert "projects" + os.sep + fingerprint + os.sep + "projects" not in output
        assert "started · item_id=mission-2" in output
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate(timeout=10)


def test_follow_renderer_is_layered_and_compact() -> None:
    from argus_skill.apps import cli

    assert cli._format_follow_event(
        {"type": "life.phase.started", "agent_layer": "critic", "iteration_cycle": 1, "iteration_max": 6},
        "engineer",
    ) == "🔄 进入 [L3 评审员] · iteration=1/6"

    verification = cli._format_follow_event(
        {
            "type": "engineer.progress",
            "kind": "agent_message",
            "agent_layer": "engineer",
            "text": "## Verification (verbatim)\n```text\n.... [100%]\n```\n```text\nAll checks passed!\n```",
        },
        "engineer",
    )
    assert verification == "  [L1 工程师] ✅ 验证：tests passed · ruff passed"

    mypy = cli._format_follow_event(
        {
            "type": "engineer.progress",
            "kind": "command_execution",
            "agent_layer": "engineer",
            "text": "/bin/bash -lc 'python -m mypy argus_skill tests'",
            "status": "completed",
            "exit_code": 0,
            "output_excerpt": "tests/foo.py:1: note: noisy note",
        },
        "engineer",
    )
    assert mypy == (
        "  [L1 工程师] ✅ 🐍 python -m mypy argus_skill tests — "
        "mypy completed (notes omitted)"
    )

    added = cli._format_follow_event(
        {
            "type": "life.planner.task_added",
            "item_id": "task-123",
            "title": "Follow task",
            "objective": "show the operator what was enqueued",
        },
        "planner",
    )
    assert added == (
        "📋 [L4 规划师] added · item_id=task-123 · title=Follow task · "
        "objective=show the operator what was enqueued"
    )

    skipped = cli._format_follow_event(
        {
            "type": "life.planner.task_skipped",
            "title": "Follow task",
            "objective": "show the operator what was skipped",
            "matched_item_id": "task-123",
            "matched_status": "running",
            "reason": "duplicate pending/running task",
        },
        "planner",
    )
    assert skipped == (
        "⏭️ [L4 规划师] skipped duplicate · title=Follow task · "
        "objective=show the operator what was skipped · matched_item_id=task-123 · "
        "matched_status=running · reason=duplicate pending/running task"
    )

    started = cli._format_follow_event(
        {
            "type": "life.mission.started",
            "item_id": "mission-1",
            "title": "Ship follow output",
        },
        "engineer",
        mission_context={
            "item_id": "mission-1",
            "title": "Ship follow output",
            "objective": "show the operator the task objective",
        },
    )
    assert started == (
        "\n🚀 [L1 工程师] started · item_id=mission-1 · title=Ship follow output · "
        "objective=show the operator the task objective"
    )

    completed = cli._format_follow_event(
        {
            "type": "life.mission.completed",
            "item_id": "mission-1",
            "status": "done",
            "success": True,
        },
        "engineer",
        mission_context={
            "item_id": "mission-1",
            "title": "Ship follow output",
            "objective": "show the operator the task objective",
        },
    )
    assert completed == (
        "✅ mission complete · status=done · success=True · item_id=mission-1 · "
        "title=Ship follow output · objective=show the operator the task objective"
    )

    completed_duplicate = cli._format_follow_event(
        {
            "type": "life.planner.task_skipped",
            "title": "Follow task",
            "objective": "show the operator what was skipped",
            "matched_item_id": "task-456",
            "matched_status": "done",
            "reason": "duplicate completed task",
        },
        "planner",
    )
    assert completed_duplicate == (
        "⏭️ [L4 规划师] skipped duplicate · title=Follow task · "
        "objective=show the operator what was skipped · matched_item_id=task-456 · "
        "matched_status=done · reason=duplicate completed task"
    )

    quarantined = cli._format_follow_event(
        {
            "type": "life.planner.task_skipped",
            "title": "Follow task",
            "objective": "show the operator what was quarantined",
            "matched_item_id": "task-789",
            "matched_title": "Follow task",
            "matched_status": "no_progress",
            "matched_stop_reason": "stalled",
            "skip_category": "recent_no_progress_failure",
            "reason": "recent no_progress failure",
        },
        "planner",
    )
    assert quarantined == (
        "⏭️ [L4 规划师] quarantined recent no-progress failure · title=Follow task · "
        "objective=show the operator what was quarantined · matched_item_id=task-789 · "
        "matched_title=Follow task · matched_status=no_progress · "
        "matched_stop_reason=stalled · skip_category=recent_no_progress_failure · "
        "reason=recent no_progress failure"
    )


def test_follow_renders_planner_task_added_and_skipped(tmp_path: Path) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    events_path = global_root / "projects" / fingerprint / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("", encoding="utf-8")
    env = _clean_env(
        ARGUS_SKILL_LIFE_BACKEND="memory",
        PYTHONUNBUFFERED="1",
    )

    proc = subprocess.Popen(
        [sys.executable, "-m", "argus_skill", "--follow", "--life-dir", str(global_root)],
        cwd=repo_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.6)
        assert proc.poll() is None

        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "type": "life.planner.task_added",
                        "item_id": "task-123",
                        "title": "Follow task",
                        "objective": "show the operator what was enqueued",
                    }
                )
                + "\n"
            )
            fh.write(
                json.dumps(
                    {
                        "type": "life.planner.task_skipped",
                        "title": "Follow task",
                        "objective": "show the operator what was skipped",
                        "matched_item_id": "task-123",
                        "matched_status": "running",
                        "reason": "duplicate pending/running task",
                    }
                )
                + "\n"
            )
            fh.write(
                json.dumps(
                    {
                        "type": "life.planner.task_skipped",
                        "title": "Follow task",
                        "objective": "show the operator what was skipped",
                        "matched_item_id": "task-456",
                        "matched_status": "done",
                        "reason": "duplicate completed task",
                    }
                )
                + "\n"
            )

        time.sleep(0.8)
        proc.send_signal(signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=10)
        output = stdout + stderr
        assert proc.returncode == 0, proc
        assert "📋 [L4 规划师] added" in output
        assert "item_id=task-123" in output
        assert "objective=show the operator what was enqueued" in output
        assert "⏭️ [L4 规划师] skipped duplicate" in output
        assert "matched_item_id=task-123" in output
        assert "matched_status=running" in output
        assert "duplicate pending/running task" in output
        assert "matched_item_id=task-456" in output
        assert "matched_status=done" in output
        assert "duplicate completed task" in output
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate(timeout=10)


def test_follow_renders_planner_task_skipped_quarantine_reason(
    tmp_path: Path,
) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    events_path = global_root / "projects" / fingerprint / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("", encoding="utf-8")
    env = _clean_env(
        ARGUS_SKILL_LIFE_BACKEND="memory",
        PYTHONUNBUFFERED="1",
    )

    proc = subprocess.Popen(
        [sys.executable, "-m", "argus_skill", "--follow", "--life-dir", str(global_root)],
        cwd=repo_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.6)
        assert proc.poll() is None

        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "type": "life.planner.task_skipped",
                        "title": "Follow task",
                        "objective": "show the operator what was quarantined",
                        "matched_item_id": "task-789",
                        "matched_title": "Follow task",
                        "matched_status": "no_progress",
                        "matched_stop_reason": "stalled",
                        "skip_category": "recent_no_progress_failure",
                        "reason": "recent no_progress failure",
                    }
                )
                + "\n"
            )

        time.sleep(0.8)
        proc.send_signal(signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=10)
        output = stdout + stderr
        assert proc.returncode == 0, proc
        assert "⏭️ [L4 规划师] quarantined recent no-progress failure" in output
        assert "skip_category=recent_no_progress_failure" in output
        assert "matched_item_id=task-789" in output
        assert "matched_status=no_progress" in output
        assert "matched_stop_reason=stalled" in output
        assert "skipped duplicate" not in output
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate(timeout=10)


def test_notify_and_follow_render_inbox_guidance(tmp_path: Path) -> None:
    global_root = tmp_path / "life"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    fingerprint = project.project_fingerprint(repo_dir).fingerprint
    project_root = global_root / "projects" / fingerprint
    env = _clean_env(
        ARGUS_SKILL_LIFE_BACKEND="memory",
        PYTHONUNBUFFERED="1",
    )

    proc = subprocess.Popen(
        [sys.executable, "-m", "argus_skill", "--follow", "--life-dir", str(global_root)],
        cwd=repo_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.6)
        assert proc.poll() is None

        notify = _run_cli(
            "--notify",
            "hello operator",
            "--life-dir",
            str(global_root),
            env=env,
            cwd=repo_dir,
        )
        assert notify.returncode == 0, notify
        events = [
            json.loads(line)
            for line in (project_root / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert any(
            event.get("type") == "life.inbox.queued" and event.get("text") == "hello operator"
            for event in events
        )

        repl = subprocess.run(
            [sys.executable, "-m", "argus_skill", "--no-daemon", "--life-dir", str(global_root)],
            cwd=repo_dir,
            env=env,
            input="work on the inbox task\n/exit\n",
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert repl.returncode == 0, repl

        time.sleep(0.8)
        proc.send_signal(signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=10)
        output = stdout + stderr
        assert "life.inbox.queued" in output
        assert "hello operator" in output
        assert "life.inbox.drained" in output
        assert "1 message" in output
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate(timeout=10)
