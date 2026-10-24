"""M1 tests for the web/TUI backend API command surface (POST endpoints + auth).

Daemon start/stop are monkeypatched so no real subprocess is spawned.
"""

from __future__ import annotations

import dataclasses
import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from argus.core import runtime_identity as runtime_identity_module
from argus.core.session import SessionMeta, touch_session, write_session_meta
from argus.daemon import commands as daemon_commands
from argus.daemon import life_worker as daemon_worker
from argus.daemon import protocol as daemon_protocol
from argus.life.memory import BacklogItem, LifeMemory
from argus.manager import front_door
from argus.manager.front_door import (
    ManagerHandoffError,
)
from argus.webapi import (
    daemon_lifecycle,
    daemon_upgrade,
    manager_dispatch,
    manager_state,
    mission_items,
    project_crud,
    project_state,
    server,
)
from argus.webapi.daemon_services import DaemonServices
from tests.hand_off import manager_runner, ticket

pytest.importorskip("fastapi")
import threading

from fastapi import WebSocketDisconnect  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _daemon_services(*, alive: bool = False) -> DaemonServices:
    return DaemonServices(
        read_status=lambda path: daemon_worker.DaemonStatus(
            alive=alive,
            pid=123 if alive else None,
            started_at_iso=None,
            uptime_seconds=5.0 if alive else None,
            life_dir=Path(path),
            pid_path=Path(path) / "daemon.pid",
        ),
        start=daemon_lifecycle.start_project_daemon,
    )


def _make_project(root: Path, sid: str = "s-cmd00001") -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").touch()
    (life / "backlog.jsonl").touch()
    write_session_meta(
        root,
        SessionMeta(
            id=sid,
            cwd=str(life),
            workdir=str(life),
            launch_cwd=str(life),
        ),
    )
    return life


@pytest.fixture()
def ctx(tmp_path: Path):
    life = _make_project(tmp_path)
    return tmp_path, "s-cmd00001", life


@pytest.fixture(autouse=True)
def _identity_manager_handoff(monkeypatch) -> None:
    _install_manager(monkeypatch, lambda text: text)


def _install_manager(monkeypatch, execution_for) -> None:
    manager_state._STATES.clear()

    class _Manager:
        def decide_vertical(self, text, **kwargs):
            return SimpleNamespace(execution_task=execution_for(text))

        def commit_vertical_decision(self, text, decision, **kwargs):
            return SimpleNamespace(execution_task=decision.execution_task)

    monkeypatch.setattr(front_door, "_ensure_manager_runner",
                        lambda chat_state, mem: manager_runner(_Manager(), hand=lambda text: ticket(text, lifetime="standing")))


# ── tasks ─────────────────────────────────────────────────────────────────


def test_post_task_honours_once_flag(ctx) -> None:
    root, sid, life = ctx
    client = TestClient(server.create_app(global_root=root))
    r = client.post(
        f"/api/projects/{sid}/tasks", json={"text": "tune it --once", "autostart_daemon": False}
    )
    item = r.json()["item"]
    assert item["objective"] == "tune it"  # flags stripped
    assert item["iterate"] is False  # --once


def test_post_task_returns_503_instead_of_enqueuing_raw_on_handoff_failure(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx

    def fail_handoff(*args, **kwargs):
        raise ManagerHandoffError("safe handoff unavailable")

    monkeypatch.setattr(manager_dispatch, "manager_bounded_handoff", fail_handoff)
    client = TestClient(server.create_app(global_root=root))
    response = client.post(
        f"/api/projects/{sid}/tasks",
        json={
            "text": "write paper; Manager owns the sidebar",
            "autostart_daemon": False,
        },
    )

    assert response.status_code == 503
    assert LifeMemory.open(life).backlog.all() == []


def test_post_task_empty_400(ctx) -> None:
    root, sid, _ = ctx
    client = TestClient(server.create_app(global_root=root))
    assert client.post(f"/api/projects/{sid}/tasks", json={"text": "   "}).status_code == 400


def test_post_task_lazy_spawns_daemon(ctx, monkeypatch) -> None:
    # Default autostart_daemon=True: queueing a task lazily starts the executor
    # if none is alive (the Python cockpit's _autospawn_daemon_for_task behaviour).
    root, sid, life = ctx
    spawned = {}

    def fake_spawn(config, *, quiet=False):
        spawned["life_dir"] = config.life_dir
        return 0

    monkeypatch.setattr(daemon_worker, 'spawn_detached_daemon_clean', fake_spawn)
    client = TestClient(server.create_app(global_root=root))
    r = client.post(f"/api/projects/{sid}/tasks", json={"text": "run it"})  # autostart default
    assert r.status_code == 200
    assert r.json()["item"]["objective"] == "run it"
    assert "daemon" in r.json()  # daemon-ensure result returned
    assert spawned.get("life_dir") == life.resolve()  # lazy spawn fired (no daemon was alive)


def test_start_project_daemon_returns_replacement_candidates_at_cap(
    tmp_path,
    monkeypatch,
) -> None:
    target = _make_project(tmp_path, "s-target001")
    running = _make_project(tmp_path, "s-running01")
    (running / "session.json").write_text(
        json.dumps(
            {
                "id": "s-running01",
                "display_name": "Existing work",
                "last_active": 1,
            }
        ),
        encoding="utf-8",
    )
    spawned = []

    def fake_status(path):
        path = Path(path)
        alive = path == running
        return daemon_worker.DaemonStatus(
            alive=alive,
            pid=123 if alive else None,
            started_at_iso=None,
            uptime_seconds=None,
            life_dir=path,
            pid_path=path / "daemon.pid",
        )

    monkeypatch.setattr(daemon_worker, 'read_daemon_status', fake_status)
    monkeypatch.setattr(project_state, "read_daemon_status", fake_status)
    monkeypatch.setattr(daemon_lifecycle, '_max_active_daemons', lambda config: 1)
    monkeypatch.setattr(daemon_lifecycle, '_active_daemon_count', lambda config: 1)
    monkeypatch.setattr(
        daemon_worker,
        'spawn_detached_daemon_clean',
        lambda config, quiet=True: spawned.append(config.life_dir) or 0,
    )

    result = daemon_lifecycle.start_project_daemon("s-target001", global_root=tmp_path)
    assert result is not None and result["rc"] == 2
    assert result["admission_required"] is True
    assert result["limit"] == 1
    assert result["active_count"] == 1
    assert result["running_daemons"][0]["id"] == "s-running01"
    assert result["running_daemons"][0]["label"] == "Existing work"
    assert spawned == []
    assert target.exists()
    persisted = json.loads((target / "daemon.admission.json").read_text())
    assert persisted["running_daemons"][0]["id"] == "s-running01"
    snapshot = project_state.build_snapshot("s-target001", global_root=tmp_path)
    assert snapshot is not None
    assert snapshot["daemon_admission"]["requested_at"] == persisted["requested_at"]


def test_lazy_task_start_reclaims_oldest_safe_idle_daemon(
    tmp_path,
    monkeypatch,
) -> None:
    target = _make_project(tmp_path, "s-target001")
    victim = _make_project(tmp_path, "s-idle0001")
    replaced = {}

    def fake_status(path):
        path = Path(path)
        alive = path == victim
        return daemon_worker.DaemonStatus(
            alive=alive,
            pid=44 if alive else None,
            started_at_iso=None,
            uptime_seconds=None,
            life_dir=path,
            pid_path=path / "daemon.pid",
        )

    monkeypatch.setattr(daemon_worker, 'read_daemon_status', fake_status)
    monkeypatch.setattr(daemon_lifecycle, '_max_active_daemons', lambda config: 1)
    monkeypatch.setattr(daemon_lifecycle, '_active_daemon_count', lambda config: 1)
    monkeypatch.setattr(
        daemon_lifecycle,
        'list_running_daemons',
        lambda **kwargs: [
            {
                "id": "s-idle0001",
                "last_active": 1,
                "unfinished_tasks": 0,
                "active_role": "",
                "continuous_enabled": False,
            }
        ],
    )
    monkeypatch.setattr(
        daemon_lifecycle,
        'replace_project_daemon',
        lambda sid, victim_sid, **kwargs: (
            replaced.update(
                sid=sid,
                victim_sid=victim_sid,
            )
            or {"rc": 0}
        ),
    )

    result = daemon_lifecycle.start_project_daemon(
        "s-target001",
        global_root=tmp_path,
        reclaim_idle=True,
    )

    assert result is not None and result["rc"] == 0
    assert result["auto_parked_idle"] == "s-idle0001"
    assert replaced == {"sid": "s-target001", "victim_sid": "s-idle0001"}
    assert target.exists()


def test_replace_project_daemon_parks_state_then_starts_target(
    tmp_path,
    monkeypatch,
) -> None:
    target = _make_project(tmp_path, "s-target001")
    victim = _make_project(tmp_path, "s-victim001")
    mission_items.enqueue_task("s-victim001", "unfinished work", global_root=tmp_path)
    running = {"s-victim001"}
    spawned = []

    def fake_status(path):
        path = Path(path)
        alive = path.name in running
        return daemon_worker.DaemonStatus(
            alive=alive,
            pid=321 if alive else None,
            started_at_iso=None,
            uptime_seconds=None,
            life_dir=path,
            pid_path=path / "daemon.pid",
        )

    def fake_stop(path, *, timeout=10.0, drain=False, drain_timeout=1800.0, force=False):
        assert force is True
        running.discard(Path(path).name)
        return 0

    def fake_spawn(config, *, quiet=False):
        running.add(config.life_dir.name)
        spawned.append(config.life_dir)
        return 0

    monkeypatch.setattr(daemon_worker, 'read_daemon_status', fake_status)
    monkeypatch.setattr(project_state, "read_daemon_status", fake_status)
    monkeypatch.setattr(daemon_lifecycle, '_max_active_daemons', lambda config: 1)
    monkeypatch.setattr(daemon_lifecycle, '_active_daemon_count', lambda config: len(running))
    monkeypatch.setattr(daemon_worker, 'stop_daemon', fake_stop)
    monkeypatch.setattr(daemon_worker, 'spawn_detached_daemon_clean', fake_spawn)
    (target / "daemon.admission.json").write_text(
        json.dumps(
            {
                "admission_required": True,
                "requested_at": 1,
                "target_sid": "s-target001",
                "resume_continuous": False,
                "limit": 1,
                "active_count": 1,
                "error": "choose",
                "running_daemons": [],
            }
        )
    )

    result = daemon_lifecycle.replace_project_daemon(
        "s-target001",
        "s-victim001",
        global_root=tmp_path,
    )
    assert result is not None and result["rc"] == 0
    assert result["parked_session"] == "s-victim001"
    assert spawned == [target.resolve()]
    assert not (target / "daemon.admission.json").exists()
    parked = json.loads((victim / "daemon.parked.json").read_text())
    assert parked["state_preserved"] is True
    assert parked["replaced_by"] == "s-target001"
    assert parked["unfinished_tasks"][0]["title"] == "unfinished work"
    events = [json.loads(line) for line in (victim / "events.jsonl").read_text().splitlines()]
    assert events[-1]["type"] == "daemon.parked"


# ── nudge ─────────────────────────────────────────────────────────────────


def test_post_nudge_is_a_turn_of_the_manager_session(ctx, monkeypatch) -> None:
    from argus.apps._inbox import count_pending_inbox_messages

    root, sid, life = ctx
    runner = manager_runner(hand=None, reply="Noted: I will fix the framework.")
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: runner)
    client = TestClient(server.create_app(global_root=root))
    r = client.post(f"/api/projects/{sid}/nudge", json={"text": "don't nudge, fix the framework"})
    assert r.status_code == 200
    assert r.json()["kind"] == "chat" and r.json()["reply"] == "Noted: I will fix the framework."
    assert runner.turns == ["don't nudge, fix the framework"]
    assert count_pending_inbox_messages(life) == 0


# ── continuous ────────────────────────────────────────────────────────────


def test_post_continuous_writes_config_and_starts_matching_executor(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx
    spawned = {}

    def fake_spawn(config, *, quiet=False):
        spawned["continuous"] = config.continuous
        spawned["objective"] = config.continuous_objective
        spawned["resume_continuous"] = config.resume_continuous
        return 0

    monkeypatch.setattr(daemon_worker, 'spawn_detached_daemon_clean', fake_spawn)
    client = TestClient(server.create_app(global_root=root))
    r = client.post(
        f"/api/projects/{sid}/continuous", json={"enabled": True, "objective": "keep improving X"}
    )
    assert r.status_code == 200
    cfg = json.loads((life / "continuous.json").read_text())
    assert cfg["enabled"] is True and cfg["objective"] == "keep improving X"
    assert spawned == {
        "continuous": False,
        "objective": "keep improving X",
        "resume_continuous": True,
    }


def test_disable_continuous_surfaces_persistence_failure(
    ctx,
    monkeypatch,
) -> None:
    root, sid, _life = ctx
    bridge_state = manager_state._chat_state_for(sid)
    bridge_state["config"]["continuous"] = True
    bridge_state["continuous_objective"] = "still active"
    monkeypatch.setattr(
        "argus.daemon.state.disable_continuous_config",
        lambda life_dir: SimpleNamespace(enabled=True),
    )

    with pytest.raises(ManagerHandoffError, match="could not be persisted"):
        project_crud.set_continuous(
            sid,
            enabled=False,
            global_root=root,
        )

    assert bridge_state["config"]["continuous"] is True
    assert bridge_state["continuous_objective"] == "still active"


def test_post_continuous_rejects_enable_without_any_objective(ctx) -> None:
    root, sid, _life = ctx
    client = TestClient(server.create_app(global_root=root))

    response = client.post(
        f"/api/projects/{sid}/continuous",
        json={"enabled": True, "objective": ""},
    )

    assert response.status_code == 400


# ── daemon start/stop (monkeypatched — no real subprocess) ─────────────────


@pytest.fixture()
def fenced_backlog(ctx):
    _root, _sid, life = ctx
    memory = LifeMemory.open(life)
    item = BacklogItem.new(
        title="provider quota hold",
        objective="resume after restoring quota",
        manager_decision={"routed": True, "vertical": "software"},
    )
    item.status = "paused_provider_fence"
    memory.backlog.add(item)
    return memory, item


@pytest.mark.parametrize("operation", ["start", "replace"])
def test_explicit_daemon_resume_rearms_provider_fence_once_per_command(
    ctx, fenced_backlog, monkeypatch, operation,
) -> None:
    root, sid, _life = ctx
    memory, item = fenced_backlog
    starts = []

    def start(*args, **_kwargs):
        starts.append(args)
        assert memory.backlog.all()[0].status == "parked"
        return {"rc": 0, "already_alive": True}

    monkeypatch.setattr(daemon_lifecycle, f"{operation}_project_daemon", start)
    client = TestClient(server.create_app(global_root=root))
    url = f"/api/projects/{sid}/daemon/{operation}"
    body = {"command_id": "resume-fence", "expected_revision": 0}
    if operation == "replace":
        body.update(victim_sid="s-victim001", resume_continuous=True)

    first = client.post(url, json=body).json()
    assert first["command_status"] == "applied"
    assert memory.backlog.all()[0].status == "pending"
    assert memory.backlog.all()[0].attempt == 2
    memory.backlog.update(item.id, park_reason="provider_fence")

    duplicate = client.post(url, json=body).json()
    stale = client.post(url, json={**body, "command_id": "stale-resume"}).json()
    assert duplicate["command_status"] == "applied"
    assert stale["command_status"] == "rejected"
    assert len(starts) == 1
    assert memory.backlog.all()[0].status == "parked"
    assert memory.backlog.all()[0].attempt == 2

    fresh = client.post(url, json={
        **body,
        "command_id": "recover-again",
        "expected_revision": stale["command_revision"],
    }).json()
    assert fresh["command_status"] == "applied"
    assert len(starts) == 2
    assert memory.backlog.all()[0].status == "pending"
    assert memory.backlog.all()[0].attempt == 3


def test_daemon_start_surfaces_clean_launcher_failure(ctx, monkeypatch) -> None:
    root, sid, _life = ctx

    def fail_spawn(_config, *, quiet=False):
        assert quiet is True
        raise RuntimeError("ModuleNotFoundError: No module named 'uvicorn'")

    monkeypatch.setattr(daemon_worker, 'spawn_detached_daemon_clean', fail_spawn)
    client = TestClient(server.create_app(global_root=root))
    response = client.post(f"/api/projects/{sid}/daemon/start")

    assert response.status_code == 200
    body = response.json()
    assert body["rc"] == 2
    assert body["error"] == (
        "The background worker could not start. "
        "Check the startup diagnostic and try again."
    )
    assert body["startup_diagnostic"] == (
        "RuntimeError: ModuleNotFoundError: No module named 'uvicorn'"
    )
    assert "ModuleNotFoundError" not in body["error"]


def test_daemon_start_retries_one_transient_windows_sharing_failure(
    ctx,
    monkeypatch,
) -> None:
    root, sid, _life = ctx
    attempts = 0

    def fake_spawn(config, *, quiet=False):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            config.last_spawn_error = (
                "PermissionError: [WinError 32] The process cannot access the file"
            )
            return 1
        config.last_spawn_error = ""
        return 0

    monkeypatch.setattr(daemon_worker, 'spawn_detached_daemon_clean', fake_spawn)
    monkeypatch.setattr(daemon_lifecycle, "_running_on_windows", lambda: True)

    result = daemon_lifecycle.start_project_daemon(sid, global_root=root)

    assert result is not None and result["rc"] == 0
    assert result["startup_retried"] is True
    assert "startup_diagnostic" not in result
    assert attempts == 2


def test_daemon_start_does_not_retry_deterministic_rc1(
    ctx,
    monkeypatch,
) -> None:
    root, sid, _life = ctx
    attempts = 0

    def fake_spawn(config, *, quiet=False):
        nonlocal attempts
        attempts += 1
        config.last_spawn_error = "ModuleNotFoundError: No module named argus"
        return 1

    monkeypatch.setattr(daemon_worker, 'spawn_detached_daemon_clean', fake_spawn)
    monkeypatch.setattr(daemon_lifecycle, "_running_on_windows", lambda: True)

    result = daemon_lifecycle.start_project_daemon(sid, global_root=root)

    assert result is not None and result["rc"] == 1
    assert attempts == 1


def test_daemon_start_accepts_runtime_published_after_transient_launcher_failure(
    ctx,
    monkeypatch,
) -> None:
    root, sid, _life = ctx
    attempts = 0
    original_status = daemon_worker.read_daemon_status

    def fake_spawn(config, *, quiet=False):
        nonlocal attempts
        attempts += 1
        config.last_spawn_error = "OSError: [WinError 33] lock violation"
        return 1

    status_reads = 0

    def fake_status(path):
        nonlocal status_reads
        status_reads += 1
        status = original_status(path)
        if status_reads < 2:
            return status
        return dataclasses.replace(status, alive=True, pid=4242)

    monkeypatch.setattr(daemon_worker, 'spawn_detached_daemon_clean', fake_spawn)
    monkeypatch.setattr(daemon_worker, 'read_daemon_status', fake_status)
    monkeypatch.setattr(daemon_lifecycle, "_running_on_windows", lambda: True)

    result = daemon_lifecycle.start_project_daemon(sid, global_root=root)

    assert result is not None and result["rc"] == 0
    assert result["startup_retried"] is True
    assert "startup_diagnostic" not in result
    assert attempts == 1


def test_daemon_upgrade_restarts_from_current_web_release(ctx, monkeypatch) -> None:
    root, sid, _life = ctx
    calls = []
    monkeypatch.setattr(
        daemon_upgrade,
        'upgrade_project_daemon',
        lambda project_id, **kwargs: calls.append(project_id) or {"rc": 0, "upgraded": True},
    )
    client = TestClient(server.create_app(global_root=root))

    response = client.post(f"/api/projects/{sid}/daemon/upgrade")

    assert response.status_code == 200
    assert response.json()["upgraded"] is True
    assert calls == [sid]


def test_daemon_upgrade_schedule_returns_before_boundary_drain(
    ctx,
    monkeypatch,
) -> None:
    root, sid, _life = ctx
    calls = []
    monkeypatch.setattr(
        daemon_upgrade,
        'schedule_project_daemon_upgrade',
        lambda project_id, **kwargs: (
            calls.append((project_id, kwargs))
            or {"rc": 0, "scheduled": True, "reason": "release mismatch"}
        ),
    )
    client = TestClient(server.create_app(global_root=root))

    response = client.post(f"/api/projects/{sid}/daemon/upgrade-schedule")

    assert response.status_code == 200
    assert response.json()["scheduled"] is True
    assert calls == [(sid, {"global_root": root})]


def test_schedule_daemon_upgrade_requests_nonblocking_boundary_drain(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx
    status = daemon_worker.DaemonStatus(
        alive=True,
        pid=321,
        started_at_iso=None,
        uptime_seconds=5.0,
        life_dir=life,
        pid_path=life / "daemon.pid",
    )
    monkeypatch.setattr(daemon_worker, 'read_daemon_status', lambda path: status)
    monkeypatch.setattr(
        daemon_protocol,
        'daemon_protocol_compatibility',
        lambda value: (False, "release mismatch"),
    )
    monkeypatch.setattr(
        daemon_protocol,
        'daemon_runtime_owned_by_current_source',
        lambda value: True,
    )
    source = root / "checkout"
    source.mkdir()
    monkeypatch.setattr(
        runtime_identity_module,
        'runtime_identity',
        lambda: {"source_root": str(source)},
    )
    monkeypatch.setattr(
        daemon_worker,
        'read_continuous_state',
        lambda path: SimpleNamespace(enabled=True, objective="keep going"),
    )
    stops = []
    monkeypatch.setattr(
        daemon_worker,
        'stop_daemon',
        lambda path, **kwargs: stops.append((path, kwargs)) or 2,
    )
    starts = []
    monkeypatch.setattr(
        daemon_lifecycle,
        'start_project_daemon',
        lambda project_id, **kwargs: starts.append((project_id, kwargs)) or {"rc": 0},
    )
    locks = []

    @contextmanager
    def execution_lock(path, *, blocking=True):
        locks.append((path, blocking))
        yield True

    monkeypatch.setattr(daemon_commands, 'daemon_command_execution_lock', execution_lock)

    class ImmediateThread:
        def __init__(self, *, target, name, daemon):
            self.target = target

        def start(self):
            self.target()

    timers = []

    class DeferredTimer:
        def __init__(self, delay, target):
            self.delay = delay
            self.target = target
            self.daemon = False

        def start(self):
            timers.append((self.delay, self.daemon))

    monkeypatch.setattr(threading, "Thread", ImmediateThread)
    monkeypatch.setattr(threading, "Timer", DeferredTimer)
    daemon_upgrade._SCHEDULED_DAEMON_UPGRADES.clear()

    result = daemon_upgrade.schedule_project_daemon_upgrade(sid, global_root=root)

    assert result == {"rc": 0, "scheduled": True, "reason": "release mismatch"}
    assert stops == [
        (
            life,
            {
                "drain": True,
                "drain_timeout": 0.0,
                "force": False,
                "preserve_upgrade_request": True,
            },
        )
    ]
    assert starts == []
    assert locks == []
    assert timers == [(5.0, True)]
    assert (life / server.project_state.DAEMON_UPGRADE_REQUEST_FILE).exists()


def test_pending_daemon_upgrade_survives_webapi_restart(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx
    source = root / "checkout"
    source.mkdir()
    monkeypatch.setattr(runtime_identity_module, 'runtime_identity', lambda: {"source_root": str(source)})
    daemon_upgrade._write_daemon_upgrade_request(
        life,
        {
            "schema_version": 1,
            "sid": sid,
            "expected_pid": 321,
            "source_root": str(source),
            "resume_continuous": True,
            "objective": "resume me",
            "reason": "release mismatch",
            "requested_at": 1,
        },
    )
    monkeypatch.setattr(
        daemon_worker,
        'read_daemon_status',
        lambda path: daemon_worker.DaemonStatus(
            alive=False,
            pid=None,
            started_at_iso=None,
            uptime_seconds=None,
            life_dir=life,
        ),
    )
    writes = []
    monkeypatch.setattr(
        daemon_worker,
        'write_continuous_config',
        lambda path, **kwargs: writes.append((path, kwargs)),
    )
    starts = []
    monkeypatch.setattr(
        daemon_lifecycle,
        'start_project_daemon',
        lambda project_id, **kwargs: starts.append((project_id, kwargs)) or {"rc": 0},
    )

    result = daemon_upgrade._complete_scheduled_daemon_upgrade(
        sid,
        life_dir=life,
        global_root=root,
    )

    assert result == {"rc": 0, "upgraded": True}
    assert writes == [(life, {"enabled": True, "objective": "resume me"})]
    assert starts == [(sid, {"global_root": root, "resume_continuous": True})]
    assert not (life / server.project_state.DAEMON_UPGRADE_REQUEST_FILE).exists()


def test_explicit_stop_cancels_scheduled_restart_without_resurrection(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx
    source = root / "checkout"
    source.mkdir()
    request = {
        "schema_version": 1,
        "sid": sid,
        "expected_pid": 321,
        "source_root": str(source),
        "resume_continuous": True,
        "objective": "keep running",
        "reason": "release mismatch",
        "requested_at": 1,
    }
    daemon_upgrade._write_daemon_upgrade_request(life, request)
    monkeypatch.setattr(runtime_identity_module, 'runtime_identity', lambda: {"source_root": str(source)})
    status = daemon_worker.DaemonStatus(
        alive=True,
        pid=321,
        started_at_iso=None,
        uptime_seconds=1.0,
        life_dir=life,
    )
    monkeypatch.setattr(daemon_worker, 'read_daemon_status', lambda path: status)
    monkeypatch.setattr(
        daemon_protocol,
        'daemon_protocol_compatibility',
        lambda value: (False, "release mismatch"),
    )
    monkeypatch.setattr(
        daemon_protocol,
        'daemon_runtime_owned_by_current_source',
        lambda value: True,
    )

    def explicit_stop_wins(path, **kwargs):
        (life / server.project_state.DAEMON_UPGRADE_REQUEST_FILE).unlink()
        return 0

    monkeypatch.setattr(daemon_worker, 'stop_daemon', explicit_stop_wins)
    starts = []
    monkeypatch.setattr(
        daemon_lifecycle,
        'start_project_daemon',
        lambda *args, **kwargs: starts.append((args, kwargs)) or {"rc": 0},
    )

    result = daemon_upgrade._complete_scheduled_daemon_upgrade(
        sid,
        life_dir=life,
        global_root=root,
    )

    assert result == {
        "rc": 0,
        "upgraded": False,
        "reason": "upgrade was cancelled by a newer daemon command",
    }
    assert starts == []


def test_webapi_startup_resumes_pending_daemon_upgrades(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx
    monkeypatch.setattr(
        project_state,
        "daemon_upgrade_pending",
        lambda path: Path(path) == life,
    )
    scheduled = []
    monkeypatch.setattr(
        daemon_upgrade,
        'schedule_project_daemon_upgrade',
        lambda project_id, **kwargs: (
            scheduled.append((project_id, kwargs)) or {"rc": 0, "scheduled": True}
        ),
    )

    assert daemon_upgrade.reconcile_pending_daemon_upgrades([root]) == [sid]
    assert scheduled == [(sid, {"global_root": root})]


def test_schedule_daemon_upgrade_retries_after_thread_start_failure(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx
    source = root / "checkout"
    source.mkdir()
    status = daemon_worker.DaemonStatus(
        alive=True,
        pid=321,
        started_at_iso=None,
        uptime_seconds=1.0,
        life_dir=life,
    )
    monkeypatch.setattr(runtime_identity_module, 'runtime_identity', lambda: {"source_root": str(source)})
    monkeypatch.setattr(daemon_worker, 'read_daemon_status', lambda path: status)
    monkeypatch.setattr(
        daemon_protocol,
        'daemon_protocol_compatibility',
        lambda value: (False, "release mismatch"),
    )
    monkeypatch.setattr(
        daemon_protocol,
        'daemon_runtime_owned_by_current_source',
        lambda value: True,
    )
    monkeypatch.setattr(
        daemon_worker,
        'read_continuous_state',
        lambda path: SimpleNamespace(enabled=False, objective=""),
    )

    class BrokenThread:
        def __init__(self, *, target, name, daemon):
            pass

        def start(self):
            raise RuntimeError("thread unavailable")

    monkeypatch.setattr(threading, "Thread", BrokenThread)
    daemon_upgrade._SCHEDULED_DAEMON_UPGRADES.clear()

    with pytest.raises(RuntimeError, match="thread unavailable"):
        daemon_upgrade.schedule_project_daemon_upgrade(sid, global_root=root)

    assert str(life.resolve()) not in daemon_upgrade._SCHEDULED_DAEMON_UPGRADES
    assert (life / server.project_state.DAEMON_UPGRADE_REQUEST_FILE).is_file()


def test_daemon_upgrade_drains_and_restores_continuous_mode(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx
    monkeypatch.setattr(
        daemon_worker,
        'read_daemon_status',
        lambda path: daemon_worker.DaemonStatus(
            alive=True,
            pid=321,
            started_at_iso=None,
            uptime_seconds=5.0,
            life_dir=Path(path),
            pid_path=Path(path) / "daemon.pid",
        ),
    )
    stops = []
    monkeypatch.setattr(
        daemon_worker,
        'stop_daemon',
        lambda *args, **kwargs: stops.append(kwargs) or 0,
    )
    monkeypatch.setattr(
        daemon_worker,
        'read_continuous_state',
        lambda path: SimpleNamespace(enabled=True, objective="keep researching"),
    )
    writes = []
    monkeypatch.setattr(
        daemon_worker,
        'write_continuous_config',
        lambda path, **kwargs: writes.append((path, kwargs)),
    )
    starts = []
    monkeypatch.setattr(
        daemon_lifecycle,
        'start_project_daemon',
        lambda project_id, **kwargs: starts.append((project_id, kwargs)) or {"rc": 0},
    )

    result = daemon_upgrade.upgrade_project_daemon(sid, global_root=root)

    assert result == {"rc": 0, "upgraded": True}
    assert stops == [
        {
            "drain": True,
            "drain_timeout": 0.0,
            "force": False,
        }
    ]
    assert writes[0][1] == {
        "enabled": True,
        "objective": "keep researching",
    }
    assert starts == [
        (
            sid,
            {"global_root": root, "resume_continuous": True},
        )
    ]


def test_daemon_upgrade_schedules_restart_when_active_mission_is_still_running(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx
    source = root / "checkout"
    source.mkdir()
    monkeypatch.setattr(
        runtime_identity_module,
        'runtime_identity',
        lambda: {"source_root": str(source)},
    )
    monkeypatch.setattr(
        daemon_worker,
        'read_daemon_status',
        lambda path: daemon_worker.DaemonStatus(
            alive=True,
            pid=321,
            started_at_iso=None,
            uptime_seconds=5.0,
            life_dir=Path(path),
            pid_path=Path(path) / "daemon.pid",
        ),
    )
    monkeypatch.setattr(daemon_worker, 'stop_daemon', lambda *args, **kwargs: 2)
    monkeypatch.setattr(
        daemon_worker,
        'read_continuous_state',
        lambda path: SimpleNamespace(enabled=True, objective="keep researching"),
    )
    scheduled = []
    monkeypatch.setattr(
        daemon_upgrade,
        'schedule_project_daemon_upgrade',
        lambda project_id, **kwargs: (
            scheduled.append((project_id, kwargs))
            or {"rc": 0, "scheduled": True, "reason": "draining"}
        ),
    )

    result = daemon_upgrade.upgrade_project_daemon(sid, global_root=root)

    assert result == {"rc": 0, "scheduled": True, "reason": "draining"}
    assert scheduled == [(sid, {"global_root": root})]
    request = daemon_upgrade._read_daemon_upgrade_request(life)
    assert request is not None
    assert request["expected_pid"] == 321
    assert request["resume_continuous"] is True


def test_project_update_preserves_legacy_continuous_objective(ctx) -> None:
    root, sid, life = ctx
    (life / "continuous.json").write_text(
        json.dumps({"enabled": True, "objective": "Keep studying"}),
        encoding="utf-8",
    )
    client = TestClient(server.create_app(global_root=root))

    r = client.patch(f"/api/projects/{sid}", json={"name": "Legacy research"})

    assert r.status_code == 200
    meta = json.loads((life / "session.json").read_text())
    assert meta["display_name"] == "Legacy research"
    assert meta["objective"] == "Keep studying"


def test_project_delete_moves_stopped_session_to_trash(ctx) -> None:
    root, sid, life = ctx
    workdir = root / "workspaces" / sid
    workdir.mkdir(parents=True)
    (workdir / "result.txt").write_text("operator result", encoding="utf-8")
    meta = json.loads((life / "session.json").read_text(encoding="utf-8"))
    meta["workdir"] = str(workdir)
    (life / "session.json").write_text(json.dumps(meta), encoding="utf-8")
    services = _daemon_services(alive=False)
    client = TestClient(server.create_app(global_root=root, daemon_services=services))

    r = client.delete(f"/api/projects/{sid}")

    assert r.status_code == 200
    assert not life.exists()
    assert (root / r.json()["trash_path"]).is_dir()
    assert r.json()["workdir"] == str(workdir)
    assert r.json()["workdir_preserved"] is True
    assert (workdir / "result.txt").read_text(encoding="utf-8") == "operator result"
    touch_session(root, sid, display_name="must not resurrect")
    assert not life.exists()


def test_project_trash_can_be_listed_and_restored(ctx) -> None:
    root, sid, life = ctx
    services = _daemon_services(alive=False)
    client = TestClient(server.create_app(global_root=root, daemon_services=services))
    deleted = client.delete(f"/api/projects/{sid}").json()

    entries = client.get("/api/trash").json()["entries"]
    entry = next(item for item in entries if item["sid"] == sid)
    assert entry["trash_path"] == deleted["trash_path"]

    restored = client.post(f"/api/trash/{quote(entry['trash_id'], safe='')}/restore")
    assert restored.status_code == 200
    assert restored.json() == {"ok": True, "sid": sid}
    assert life.is_dir()
    assert client.get("/api/trash").json()["entries"] == []


def test_trash_restore_rejects_date_bucket(ctx) -> None:
    root, sid, _life = ctx
    services = _daemon_services(alive=False)
    client = TestClient(server.create_app(global_root=root, daemon_services=services))
    deleted = client.delete(f"/api/projects/{sid}").json()
    bucket = str(Path(deleted["trash_path"]).parent)

    assert project_crud.restore_trashed_project(bucket, global_root=root) is None


def test_trash_restore_rejects_duplicate_sid_in_another_root(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    sid = "s-duplicate"
    _make_project(primary, sid)
    _make_project(secondary, sid)
    services = _daemon_services(alive=False)
    assert project_crud.delete_project(sid, global_root=secondary, read_status=services.read_status)["ok"] is True
    client = TestClient(server.create_app(global_root=primary, session_roots=[secondary], daemon_services=services))
    entry = client.get("/api/trash").json()["entries"][0]

    response = client.post(f"/api/trash/{quote(entry['trash_id'], safe='')}/restore")

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_project_delete_refuses_live_daemon(ctx, monkeypatch) -> None:
    root, sid, life = ctx
    services = _daemon_services(alive=True)
    client = TestClient(server.create_app(global_root=root, daemon_services=services))

    r = client.delete(f"/api/projects/{sid}")

    assert r.status_code == 409
    assert life.is_dir()
    assert "pause" in r.json()["detail"]


# ── retired Python-REPL parity commands ───────────────────────────────────


def test_plan_preview_delegates_to_manager_planner(ctx, monkeypatch) -> None:
    root, sid, _ = ctx
    monkeypatch.setattr(
        "argus.webapi.manager_bridge.manager_plan",
        lambda sid, text, *, global_root=None: {
            "steps": [{"title": "Check premise", "detail": "first"}],
            "notes": [],
            "error": "",
        },
    )
    client = TestClient(server.create_app(global_root=root))
    body = client.post(f"/api/projects/{sid}/plan", json={"text": "prove it"}).json()
    assert body["steps"][0]["title"] == "Check premise"


def test_config_set_persists_cockpit_knob(ctx, monkeypatch) -> None:
    root, sid, _ = ctx
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    client = TestClient(server.create_app(global_root=root))
    r = client.post(
        f"/api/projects/{sid}/config/set",
        json={"name": "model", "value": "gpt-5.6-sol"},
    )
    assert r.status_code == 200
    assert json.loads((root / "config.json").read_text())["ARGUS_SKILL_MODEL"] == "gpt-5.6-sol"
    assert (
        client.post(
            f"/api/projects/{sid}/config/set",
            json={"name": "not_a_knob", "value": "x"},
        ).status_code
        == 400
    )


def test_config_set_does_not_report_success_when_persistence_fails(
    ctx,
    monkeypatch,
) -> None:
    from argus.core import knob_store

    root, sid, _ = ctx
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.delenv("ARGUS_SKILL_MODEL", raising=False)
    monkeypatch.setattr(knob_store, "write_persisted_knob", lambda *_args: False)

    response = TestClient(server.create_app(global_root=root)).post(
        f"/api/projects/{sid}/config/set",
        json={"name": "model", "value": "gpt-test"},
    )

    assert response.status_code == 500
    assert "could not be persisted" in response.json()["detail"]
    assert "ARGUS_SKILL_MODEL" not in os.environ


def test_config_set_validates_and_normalizes_typed_values(ctx, monkeypatch) -> None:
    root, sid, life = ctx
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.delenv("ARGUS_SKILL_MAX_ACTIVE_DAEMONS", raising=False)
    client = TestClient(server.create_app(global_root=root))

    ok = client.post(
        f"/api/projects/{sid}/config/set",
        json={"name": "max_daemons", "value": " 3 "},
    )
    assert ok.status_code == 200
    assert ok.json()["value"] == "3"
    assert (
        json.loads((root / "config.json").read_text())["ARGUS_SKILL_MAX_ACTIVE_DAEMONS"] == "3"
    )

    invalid = client.post(
        f"/api/projects/{sid}/config/set",
        json={"name": "max_daemons", "value": "many"},
    )
    assert invalid.status_code == 400
    assert "non-negative integer" in invalid.json()["detail"]


def test_identity_set_and_skills_and_reset(ctx, monkeypatch) -> None:
    root, sid, life = ctx
    monkeypatch.setattr(mission_items, 'run_skill_command', lambda tokens, **_kwargs: "skills:" + " ".join(tokens))
    monkeypatch.setattr(
        "argus.webapi.manager_state.reset_manager_context",
        lambda sid, *, global_root=None: True,
    )
    client = TestClient(server.create_app(global_root=root))
    assert (
        client.post(f"/api/projects/{sid}/identity", json={"text": "Operator A"}).status_code == 200
    )
    assert "Operator A" in LifeMemory.open(life).identity.read()
    assert (
        client.post(f"/api/projects/{sid}/skills", json={"args": "promote demo"}).json()["text"]
        == "skills:promote demo"
    )
    assert client.post(f"/api/projects/{sid}/reset").json()["ok"] is True


# ── unknown project → 404 on every POST ────────────────────────────────────


# ── auth (bearer token) ────────────────────────────────────────────────────


def test_ws_requires_token_when_configured(ctx) -> None:
    root, sid, life = ctx
    (life / "events.jsonl").write_text('{"type": "loop.start"}\n')
    app = server.create_app(global_root=root, auth_token="secret123")
    url = f"/api/projects/{sid}/stream"
    bearer = "argus.bearer." + "secret123".encode().hex()
    with TestClient(app) as tc:
        # No token, a wrong one, or the token in the logged URL → closed.
        for attempt in (
            {},
            {"headers": {"Authorization": "Bearer nope"}},
            {"subprotocols": ["argus", "argus.bearer." + b"nope".hex()]},
            {"url": f"{url}?token=secret123"},
        ):
            with tc.websocket_connect(attempt.pop("url", url), **attempt) as ws:
                with pytest.raises(WebSocketDisconnect) as closed:
                    ws.receive_json()
                assert closed.value.code == 4401
        # The bearer header (CLI, TUI, portal) or a browser's subprotocol → open.
        with tc.websocket_connect(url, headers={"Authorization": "Bearer secret123"}) as ws:
            assert ws.accepted_subprotocol is None
            assert ws.receive_json()["type"] == "loop.start"
        with tc.websocket_connect(url, subprotocols=["argus", bearer]) as ws:
            assert ws.accepted_subprotocol == "argus"
            assert ws.receive_json()["type"] == "loop.start"


def test_uvicorn_logs_redact_url_tokens() -> None:
    import logging

    import uvicorn

    config = server._uvicorn_log_config(uvicorn)
    log_filter = config["filters"]["argus"]["()"]()
    assert all(handler["filters"] == ["argus"] for handler in config["handlers"].values())
    handshake = logging.LogRecord(
        "uvicorn.error", logging.INFO, __file__, 0, '%s - "WebSocket %s" [accepted]',
        ("127.0.0.1:5000", "/api/projects/s-1/stream?replay=40&token=fake-secret&view=ui"), None,
    )
    pairing = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/?token=fake-secret", "1.1", 200), None,
    )
    assert log_filter.filter(handshake) and log_filter.filter(pairing)
    assert handshake.getMessage().endswith('stream?replay=40&token=<redacted>&view=ui" [accepted]')
    assert pairing.getMessage().endswith('"GET /?token=<redacted> HTTP/1.1" 200')


def test_a_configured_token_also_guards_the_reads(ctx) -> None:
    """A LAN bind mints a token and says it protects the surface. It must.

    `argus --web --web-host 0.0.0.0` prints "a token was generated for this
    run" and the flag help promises that a non-loopback bind always requires a
    bearer token. Every POST honoured that; the reads did not, so any host on
    the network could fetch the project list, the journal, the events, the
    snapshot and the full agent transcript by asking. `/api/system/doctor` was
    guarded while `/api/projects/{sid}/doctor` beside it was not, which is what
    an omission looks like rather than a decision.
    """
    root, sid, _ = ctx
    client = TestClient(server.create_app(global_root=root, auth_token="secret123"))
    reads = (
        "/api/projects",
        f"/api/projects/{sid}/snapshot",
        f"/api/projects/{sid}/events",
        f"/api/projects/{sid}/status",
        f"/api/projects/{sid}/journal",
        f"/api/projects/{sid}/transcript",
        f"/api/projects/{sid}/doctor",
    )
    for path in reads:
        assert client.get(path).status_code == 401, f"{path} served without a token"
        assert (
            client.get(path, headers={"Authorization": "Bearer secret123"}).status_code
            != 401
        ), f"{path} refused the configured token"


def test_choosing_the_shared_model_for_every_role_releases_the_role_pins(ctx, monkeypatch) -> None:
    """A role knob wins over ARGUS_SKILL_MODEL, so a picker that sets only the
    shared knob changes nothing on a host whose roles were pinned at setup."""
    root, sid, _ = ctx
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.delenv("ARGUS_SKILL_ENGINEER_MODEL", raising=False)
    (root / "config.json").write_text(json.dumps({
        "ARGUS_SKILL_MODEL": "old-shared", "ARGUS_SKILL_ENGINEER_MODEL": "pinned-a",
        "ARGUS_SKILL_REVIEWER_MODEL": "pinned-b",
    }))
    client = TestClient(server.create_app(global_root=root))

    plain = client.post(f"/api/projects/{sid}/config/set", json={"name": "model", "value": "new-shared"}).json()
    assert plain["released_role_pins"] == [] and set(plain["role_pins"]) == {"ARGUS_SKILL_ENGINEER_MODEL", "ARGUS_SKILL_REVIEWER_MODEL"}

    body = client.post(
        f"/api/projects/{sid}/config/set",
        json={"name": "model", "value": "new-shared", "apply_to_roles": True},
    ).json()
    assert body["released_role_pins"] == ["ARGUS_SKILL_ENGINEER_MODEL", "ARGUS_SKILL_REVIEWER_MODEL"]
    assert body["role_pins"] == {}
    stored = json.loads((root / "config.json").read_text())
    assert stored["ARGUS_SKILL_MODEL"] == "new-shared"
    assert stored["ARGUS_SKILL_ENGINEER_MODEL"] == "" and stored["ARGUS_SKILL_REVIEWER_MODEL"] == ""
    from argus.core.knobs import resolve_role_model

    assert resolve_role_model("engineer", role_env="ARGUS_SKILL_ENGINEER_MODEL", env={}) == "new-shared"


def test_the_config_snapshot_offers_model_options_from_catalog_usage_and_knobs(ctx, monkeypatch, tmp_path) -> None:
    root, sid, _ = ctx
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    catalog = tmp_path / "pi"
    catalog.mkdir()
    (catalog / "models.json").write_text(json.dumps({"providers": {"argus": {"models": [{"id": "cat-a"}, {"id": "cat-b"}]}}}))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(catalog))
    (root / "config.json").write_text(json.dumps({"ARGUS_SKILL_MODEL": "cat-a", "ARGUS_SKILL_LIGHT_MODEL": "auto"}))
    import time as _time

    usage = root / "projects" / sid / "usage.jsonl"
    usage.parent.mkdir(parents=True, exist_ok=True)
    usage.write_text(
        json.dumps({"model": "seen-x", "completed_at": _time.time() - 60, "output_tokens": 5}) + "\n"
        + json.dumps({"model": "failed-only", "completed_at": _time.time() - 30, "error": "429", "output_tokens": 0}) + "\n"
        + json.dumps({"model": "ancient", "completed_at": _time.time() - 90 * 86400, "output_tokens": 5}) + "\n"
    )
    client = TestClient(server.create_app(global_root=root))
    body = client.get(f"/api/projects/{sid}/config").json()
    options = {row["model"]: row["source"] for row in body["model_options"]}
    assert options["cat-a"] == "catalog" and options["cat-b"] == "catalog"
    assert options["seen-x"] == "seen"
    assert "failed-only" not in options and "ancient" not in options
    assert body["model_options"][0]["model"] == "seen-x"  # most recently used first
