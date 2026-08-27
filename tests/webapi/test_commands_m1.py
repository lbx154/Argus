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

from argus_skill.core.session import SessionMeta, touch_session, write_session_meta
from argus_skill.daemon.state import write_continuous_config
from argus_skill.life.memory import LifeMemory
from argus_skill.manager import config_intent, front_door
from argus_skill.manager.front_door import (
    ManagerHandoffError,
    ManagerHandoffSupersededError,
)
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.webapi import (
    daemon_lifecycle,
    manager_dispatch,
    manager_state,
    project_state,
    server,
)

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


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
        def classify_front_door(self, _text, *, lifetime_sink=None, **_kwargs):
            if lifetime_sink is not None:
                lifetime_sink("standing")
            return None, None, "complex"

        def decide_vertical(self, text, **kwargs):
            return SimpleNamespace(execution_task=execution_for(text))

        def commit_vertical_decision(self, text, decision, **kwargs):
            return SimpleNamespace(execution_task=decision.execution_task)

    ensure = lambda chat_state, mem: SimpleNamespace(manager=_Manager())
    monkeypatch.setattr(front_door, "_ensure_manager_runner", ensure)
    monkeypatch.setattr(config_intent, "_ensure_manager_runner", ensure)


# ── tasks ─────────────────────────────────────────────────────────────────


def test_post_task_appends_to_backlog(ctx) -> None:
    root, sid, life = ctx
    client = TestClient(server.create_app(global_root=root))
    r = client.post(
        f"/api/projects/{sid}/tasks",
        json={"text": "optimize the kernel", "autostart_daemon": False},
    )
    assert r.status_code == 200
    assert r.json()["item"]["objective"] == "optimize the kernel"
    # went through the real Backlog store (flock CAS), not a raw write
    items = LifeMemory.open(life).backlog.all()
    assert len(items) == 1 and items[0].objective == "optimize the kernel"
    assert items[0].manager_decision == {
        "require_independent_review": True,
        "routed": True,
    }


def test_post_task_preserves_active_continuous_campaign_governance(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx
    workspace = root / "workspace"
    workspace.mkdir()
    write_session_meta(
        root,
        SessionMeta(
            id=sid,
            cwd=str(life),
            workdir=str(workspace),
            launch_cwd=str(root),
        ),
    )
    persist_vertical(
        workspace,
        "math",
        research_target_level="doctoral",
        workflow_mode="staged",
    )
    write_continuous_config(
        life,
        enabled=True,
        objective="prove the selected Erdős conjecture",
    )
    commits: list[str] = []

    class _Manager:
        project_root = workspace

        def decide_vertical(self, text, **kwargs):
            return SimpleNamespace(
                execution_task="verify the migrated scope artifact",
                vertical="software",
                workflow_mode="direct",
            )

        def commit_vertical_decision(self, text, decision, **kwargs):
            commits.append(text)
            persist_vertical(workspace, "software", workflow_mode="direct")
            return SimpleNamespace(
                execution_task=decision.execution_task,
                vertical="software",
                workflow_mode="direct",
                kind="software",
                regular=True,
                stages=[],
                headline=lambda: "software · direct",
            )

        def plan_stages(self, vertical):
            assert vertical == "math"
            return ["scope", "solve", "review"]

        @staticmethod
        def _kind_for(vertical):
            assert vertical == "math"
            return "research"

    manager_state._STATES.clear()
    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda chat_state, mem: SimpleNamespace(manager=_Manager()),
    )

    response = server.enqueue_task(
        sid,
        "verify scope without changing the campaign",
        global_root=root,
    )

    assert response is not None
    assert response["objective"] == "verify the migrated scope artifact"
    assert commits == []
    pipeline = json.loads((workspace / ".argus" / "PIPELINE_STATE.json").read_text())
    assert pipeline["vertical"] == "math"
    assert pipeline["workflow_mode"] == "staged"
    assert pipeline["research_target_level"] == "doctoral"


def test_post_task_honours_once_flag(ctx) -> None:
    root, sid, life = ctx
    client = TestClient(server.create_app(global_root=root))
    r = client.post(
        f"/api/projects/{sid}/tasks", json={"text": "tune it --once", "autostart_daemon": False}
    )
    item = r.json()["item"]
    assert item["objective"] == "tune it"  # flags stripped
    assert item["iterate"] is False  # --once


def test_post_task_enqueues_only_manager_execution_handoff(ctx, monkeypatch) -> None:
    root, sid, life = ctx
    captured = {}

    def fake_handoff(call_sid, text, persist, **kwargs):
        captured.update(sid=call_sid, text=text, **kwargs)
        return persist("write the MRAM paper", None)

    monkeypatch.setattr(manager_dispatch, "manager_bounded_handoff", fake_handoff)
    client = TestClient(server.create_app(global_root=root))
    raw = "write the MRAM paper; Manager owns the right sidebar"
    response = client.post(
        f"/api/projects/{sid}/tasks",
        json={"text": raw, "autostart_daemon": False},
    )

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["objective"] == "write the MRAM paper"
    assert captured["sid"] == sid
    assert captured["text"] == raw
    assert captured["root_task_id"] == item["id"]
    persisted = LifeMemory.open(life).backlog.all()[0]
    assert persisted.objective == "write the MRAM paper"
    # Even a narrow/fake handoff that omits Division details has completed the
    # Manager gate.  The daemon must not classify the claimed item again.
    assert persisted.manager_decision == {"routed": True}


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

    monkeypatch.setattr(server, "spawn_detached_daemon", fake_spawn)
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
        return server.DaemonStatus(
            alive=alive,
            pid=123 if alive else None,
            started_at_iso=None,
            uptime_seconds=None,
            life_dir=path,
            pid_path=path / "daemon.pid",
        )

    monkeypatch.setattr(server, "read_daemon_status", fake_status)
    monkeypatch.setattr(project_state, "read_daemon_status", fake_status)
    monkeypatch.setattr(server, "_max_active_daemons", lambda config: 1)
    monkeypatch.setattr(server, "_active_daemon_count", lambda config: 1)
    monkeypatch.setattr(
        server,
        "spawn_detached_daemon",
        lambda config, quiet=True: spawned.append(config.life_dir) or 0,
    )

    result = server.start_project_daemon("s-target001", global_root=tmp_path)
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
    snapshot = server.build_snapshot("s-target001", global_root=tmp_path)
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
        return server.DaemonStatus(
            alive=alive,
            pid=44 if alive else None,
            started_at_iso=None,
            uptime_seconds=None,
            life_dir=path,
            pid_path=path / "daemon.pid",
        )

    monkeypatch.setattr(server, "read_daemon_status", fake_status)
    monkeypatch.setattr(server, "_max_active_daemons", lambda config: 1)
    monkeypatch.setattr(server, "_active_daemon_count", lambda config: 1)
    monkeypatch.setattr(
        server,
        "list_running_daemons",
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
        server,
        "replace_project_daemon",
        lambda sid, victim_sid, **kwargs: (
            replaced.update(
                sid=sid,
                victim_sid=victim_sid,
            )
            or {"rc": 0}
        ),
    )

    result = server.start_project_daemon(
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
    server.enqueue_task("s-victim001", "unfinished work", global_root=tmp_path)
    running = {"s-victim001"}
    spawned = []

    def fake_status(path):
        path = Path(path)
        alive = path.name in running
        return server.DaemonStatus(
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

    monkeypatch.setattr(server, "read_daemon_status", fake_status)
    monkeypatch.setattr(project_state, "read_daemon_status", fake_status)
    monkeypatch.setattr(server, "_max_active_daemons", lambda config: 1)
    monkeypatch.setattr(server, "_active_daemon_count", lambda config: len(running))
    monkeypatch.setattr(server, "stop_daemon", fake_stop)
    monkeypatch.setattr(server, "spawn_detached_daemon", fake_spawn)
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

    result = server.replace_project_daemon(
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


def test_post_nudge_queues_inbox_and_emits_event(ctx) -> None:
    root, sid, life = ctx
    client = TestClient(server.create_app(global_root=root))
    r = client.post(f"/api/projects/{sid}/nudge", json={"text": "don't nudge, fix the framework"})
    assert r.status_code == 200 and r.json()["ok"] is True
    # inbox.jsonl got the message
    inbox = [json.loads(ln) for ln in (life / "inbox.jsonl").read_text().splitlines() if ln.strip()]
    assert inbox and inbox[0]["text"] == "don't nudge, fix the framework"
    # and a life.inbox.queued event shows on the stream (via /events)
    types = [e["type"] for e in client.get(f"/api/projects/{sid}/events").json()["events"]]
    assert "life.inbox.queued" in types


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

    monkeypatch.setattr(server, "spawn_detached_daemon", fake_spawn)
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


def test_set_continuous_persists_only_manager_execution_handoff(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx
    raw = "study MRAM continuously; Manager decides the right sidebar"
    _install_manager(monkeypatch, lambda text: "study MRAM continuously")

    assert (
        server.set_continuous(
            sid,
            enabled=True,
            objective=raw,
            global_root=root,
        )
        is True
    )

    cfg = json.loads((life / "continuous.json").read_text())
    assert cfg["objective"] == "study MRAM continuously"
    assert raw not in (life / "continuous.json").read_text()


def test_disable_continuous_is_immediate_and_ignores_submitted_objective(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx
    server.write_continuous_config(
        life,
        enabled=True,
        objective="clean current objective",
    )
    bridge_state = manager_state._chat_state_for(sid)
    bridge_state["config"]["continuous"] = True
    bridge_state["continuous_objective"] = "clean current objective"

    def unexpected_handoff(*args, **kwargs):
        raise AssertionError("disable must not wait for Manager")

    monkeypatch.setattr(
        manager_dispatch,
        "manager_continuous_handoff",
        unexpected_handoff,
    )

    assert (
        server.set_continuous(
            sid,
            enabled=False,
            objective="raw stale UI objective; Manager owns the sidebar",
            global_root=root,
        )
        is True
    )
    assert bridge_state["config"]["continuous"] is False
    assert bridge_state["continuous_objective"] == ""
    state = server.read_continuous_state(life)
    assert state.enabled is False
    assert state.objective == "clean current objective"


def test_disable_continuous_surfaces_persistence_failure(
    ctx,
    monkeypatch,
) -> None:
    root, sid, _life = ctx
    bridge_state = manager_state._chat_state_for(sid)
    bridge_state["config"]["continuous"] = True
    bridge_state["continuous_objective"] = "still active"
    monkeypatch.setattr(
        "argus_skill.daemon.state.disable_continuous_config",
        lambda life_dir: SimpleNamespace(enabled=True),
    )

    with pytest.raises(ManagerHandoffError, match="could not be persisted"):
        server.set_continuous(
            sid,
            enabled=False,
            global_root=root,
        )

    assert bridge_state["config"]["continuous"] is True
    assert bridge_state["continuous_objective"] == "still active"


def test_enable_continuous_reprocesses_stored_objective(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx
    raw = "legacy objective; Manager owns the sidebar"
    server.write_continuous_config(
        life,
        enabled=False,
        objective=raw,
        open_ended=True,
    )
    seen = {}

    def clean_handoff(text):
        seen["text"] = text
        return "clean legacy objective"

    _install_manager(monkeypatch, clean_handoff)

    assert (
        server.set_continuous(
            sid,
            enabled=True,
            objective="",
            global_root=root,
        )
        is True
    )
    state = server.read_continuous_state(life)
    assert seen["text"] == raw
    assert state.enabled is True
    assert state.objective == "clean legacy objective"
    assert state.open_ended is True


def test_post_continuous_rejects_enable_without_any_objective(ctx) -> None:
    root, sid, _life = ctx
    client = TestClient(server.create_app(global_root=root))

    response = client.post(
        f"/api/projects/{sid}/continuous",
        json={"enabled": True, "objective": ""},
    )

    assert response.status_code == 400


def test_enable_continuous_does_not_overwrite_newer_same_value_stop(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx
    server.write_continuous_config(
        life,
        enabled=False,
        objective="paused objective",
    )
    commits = []
    manager_state._STATES.clear()

    class _Manager:
        def decide_vertical(self, text, **kwargs):
            server.set_continuous(
                sid,
                enabled=False,
                objective=text,
                global_root=root,
            )
            return SimpleNamespace(execution_task="clean objective")

        def commit_vertical_decision(self, text, decision, **kwargs):
            commits.append(text)
            return SimpleNamespace(execution_task=decision.execution_task)

    monkeypatch.setattr(
        front_door,
        "_ensure_manager_runner",
        lambda chat_state, mem: SimpleNamespace(manager=_Manager()),
    )

    with pytest.raises(ManagerHandoffSupersededError):
        server.set_continuous(
            sid,
            enabled=True,
            objective="new objective",
            global_root=root,
        )

    state = server.read_continuous_state(life)
    assert state.enabled is False
    assert state.objective == "paused objective"
    assert commits == []


# ── daemon start/stop (monkeypatched — no real subprocess) ─────────────────


def test_daemon_start_delegates(ctx, monkeypatch) -> None:
    root, sid, life = ctx
    calls = {}

    def fake_spawn(config, *, quiet=False):
        calls["life_dir"] = config.life_dir
        calls["quiet"] = quiet
        return 0

    monkeypatch.setattr(server, "spawn_detached_daemon", fake_spawn)
    client = TestClient(server.create_app(global_root=root))
    r = client.post(f"/api/projects/{sid}/daemon/start")
    assert r.status_code == 200 and r.json()["rc"] == 0
    assert calls["life_dir"] == life.resolve() and calls["quiet"] is True


def test_daemon_start_surfaces_clean_launcher_failure(ctx, monkeypatch) -> None:
    root, sid, _life = ctx

    def fail_spawn(_config, *, quiet=False):
        assert quiet is True
        raise RuntimeError("ModuleNotFoundError: No module named 'uvicorn'")

    monkeypatch.setattr(server, "spawn_detached_daemon", fail_spawn)
    client = TestClient(server.create_app(global_root=root))
    response = client.post(f"/api/projects/{sid}/daemon/start")

    assert response.status_code == 200
    assert response.json()["rc"] == 2
    assert "ModuleNotFoundError: No module named 'uvicorn'" in response.json()["error"]


def test_daemon_start_surfaces_captured_helper_stderr(ctx, monkeypatch, caplog) -> None:
    root, sid, _life = ctx
    diagnostic = "Traceback: UnicodeEncodeError during Windows daemon bootstrap"

    def fake_spawn(config, *, quiet=False):
        assert quiet is True
        config.last_spawn_error = diagnostic
        return 1

    monkeypatch.setattr(server, "spawn_detached_daemon", fake_spawn)
    client = TestClient(server.create_app(global_root=root))

    response = client.post(f"/api/projects/{sid}/daemon/start")

    assert response.status_code == 200
    body = response.json()
    assert body["rc"] == 1
    assert body["startup_diagnostic"] == diagnostic
    assert body["error"] == f"background executor failed to start (rc=1): {diagnostic}"
    assert diagnostic in caplog.text


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

    monkeypatch.setattr(server, "spawn_detached_daemon", fake_spawn)
    monkeypatch.setattr(daemon_lifecycle, "_running_on_windows", lambda: True)

    result = server.start_project_daemon(sid, global_root=root)

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
        config.last_spawn_error = "ModuleNotFoundError: No module named argus_skill"
        return 1

    monkeypatch.setattr(server, "spawn_detached_daemon", fake_spawn)
    monkeypatch.setattr(daemon_lifecycle, "_running_on_windows", lambda: True)

    result = server.start_project_daemon(sid, global_root=root)

    assert result is not None and result["rc"] == 1
    assert attempts == 1


def test_daemon_start_accepts_runtime_published_after_transient_launcher_failure(
    ctx,
    monkeypatch,
) -> None:
    root, sid, _life = ctx
    attempts = 0
    original_status = server.read_daemon_status

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

    monkeypatch.setattr(server, "spawn_detached_daemon", fake_spawn)
    monkeypatch.setattr(server, "read_daemon_status", fake_status)
    monkeypatch.setattr(daemon_lifecycle, "_running_on_windows", lambda: True)

    result = server.start_project_daemon(sid, global_root=root)

    assert result is not None and result["rc"] == 0
    assert result["startup_retried"] is True
    assert "startup_diagnostic" not in result
    assert attempts == 1


def test_daemon_start_resume_reenables_preserved_continuous_objective(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx
    write_continuous_config(
        life,
        enabled=False,
        objective="continue the proof campaign",
        open_ended=False,
        done_reason="operator drain-stop",
    )
    spawned = {}

    def fake_spawn(config, *, quiet=False):
        spawned["objective"] = config.continuous_objective
        spawned["resume_continuous"] = config.resume_continuous
        spawned["open_ended"] = config.continuous_open_ended
        return 0

    monkeypatch.setattr(server, "spawn_detached_daemon", fake_spawn)

    result = server.start_project_daemon(
        sid,
        global_root=root,
        resume_continuous=True,
    )

    assert result is not None and result["rc"] == 0
    state = server.read_continuous_state(life)
    assert state.enabled is True
    assert state.objective == "continue the proof campaign"
    assert state.done_reason == ""
    assert spawned == {
        "objective": "continue the proof campaign",
        "resume_continuous": True,
        "open_ended": False,
    }


def test_daemon_start_does_not_resume_planner_completed_campaign(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx
    write_continuous_config(
        life,
        enabled=False,
        objective="completed campaign",
        done_reason="planner declared project done",
    )
    spawned = {}

    def fake_spawn(config, *, quiet=False):
        spawned["objective"] = config.continuous_objective
        spawned["resume_continuous"] = config.resume_continuous
        return 0

    monkeypatch.setattr(server, "spawn_detached_daemon", fake_spawn)

    result = server.start_project_daemon(
        sid,
        global_root=root,
        resume_continuous=True,
    )

    assert result is not None and result["rc"] == 0
    state = server.read_continuous_state(life)
    assert state.enabled is False
    assert state.done_reason == "planner declared project done"
    assert spawned == {"objective": "", "resume_continuous": False}


def test_daemon_stop_delegates(ctx, monkeypatch) -> None:
    root, sid, life = ctx
    seen = {}

    def fake_stop(life_dir=None, *, timeout=10.0, drain=False, drain_timeout=1800.0, force=False):
        seen["life_dir"] = life_dir
        seen["drain"] = drain
        return 0

    monkeypatch.setattr(server, "stop_daemon", fake_stop)
    client = TestClient(server.create_app(global_root=root))
    r = client.post(f"/api/projects/{sid}/daemon/stop", json={"drain": True})
    assert r.status_code == 200 and r.json()["rc"] == 0
    assert seen["life_dir"] == life.resolve() and seen["drain"] is True


def test_daemon_upgrade_restarts_from_current_web_release(ctx, monkeypatch) -> None:
    root, sid, _life = ctx
    calls = []
    monkeypatch.setattr(
        server,
        "upgrade_project_daemon",
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
        server,
        "schedule_project_daemon_upgrade",
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
    status = server.DaemonStatus(
        alive=True,
        pid=321,
        started_at_iso=None,
        uptime_seconds=5.0,
        life_dir=life,
        pid_path=life / "daemon.pid",
    )
    monkeypatch.setattr(server, "read_daemon_status", lambda path: status)
    monkeypatch.setattr(
        server,
        "daemon_protocol_compatibility",
        lambda value: (False, "release mismatch"),
    )
    monkeypatch.setattr(
        server,
        "daemon_runtime_owned_by_current_source",
        lambda value: True,
    )
    source = root / "checkout"
    source.mkdir()
    monkeypatch.setattr(
        server,
        "runtime_identity",
        lambda: {"source_root": str(source)},
    )
    monkeypatch.setattr(
        server,
        "read_continuous_state",
        lambda path: SimpleNamespace(enabled=True, objective="keep going"),
    )
    stops = []
    monkeypatch.setattr(
        server,
        "stop_daemon",
        lambda path, **kwargs: stops.append((path, kwargs)) or 2,
    )
    starts = []
    monkeypatch.setattr(
        server,
        "start_project_daemon",
        lambda project_id, **kwargs: starts.append((project_id, kwargs)) or {"rc": 0},
    )
    locks = []

    @contextmanager
    def execution_lock(path, *, blocking=True):
        locks.append((path, blocking))
        yield True

    monkeypatch.setattr(server, "daemon_command_execution_lock", execution_lock)

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

    monkeypatch.setattr(server.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(server.threading, "Timer", DeferredTimer)
    server._SCHEDULED_DAEMON_UPGRADES.clear()

    result = server.schedule_project_daemon_upgrade(sid, global_root=root)

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
    monkeypatch.setattr(server, "runtime_identity", lambda: {"source_root": str(source)})
    server._write_daemon_upgrade_request(
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
        server,
        "read_daemon_status",
        lambda path: server.DaemonStatus(
            alive=False,
            pid=None,
            started_at_iso=None,
            uptime_seconds=None,
            life_dir=life,
        ),
    )
    writes = []
    monkeypatch.setattr(
        server,
        "write_continuous_config",
        lambda path, **kwargs: writes.append((path, kwargs)),
    )
    starts = []
    monkeypatch.setattr(
        server,
        "start_project_daemon",
        lambda project_id, **kwargs: starts.append((project_id, kwargs)) or {"rc": 0},
    )

    result = server._complete_scheduled_daemon_upgrade(
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
    server._write_daemon_upgrade_request(life, request)
    monkeypatch.setattr(server, "runtime_identity", lambda: {"source_root": str(source)})
    status = server.DaemonStatus(
        alive=True,
        pid=321,
        started_at_iso=None,
        uptime_seconds=1.0,
        life_dir=life,
    )
    monkeypatch.setattr(server, "read_daemon_status", lambda path: status)
    monkeypatch.setattr(
        server,
        "daemon_protocol_compatibility",
        lambda value: (False, "release mismatch"),
    )
    monkeypatch.setattr(
        server,
        "daemon_runtime_owned_by_current_source",
        lambda value: True,
    )

    def explicit_stop_wins(path, **kwargs):
        (life / server.project_state.DAEMON_UPGRADE_REQUEST_FILE).unlink()
        return 0

    monkeypatch.setattr(server, "stop_daemon", explicit_stop_wins)
    starts = []
    monkeypatch.setattr(
        server,
        "start_project_daemon",
        lambda *args, **kwargs: starts.append((args, kwargs)) or {"rc": 0},
    )

    result = server._complete_scheduled_daemon_upgrade(
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
        server,
        "schedule_project_daemon_upgrade",
        lambda project_id, **kwargs: (
            scheduled.append((project_id, kwargs)) or {"rc": 0, "scheduled": True}
        ),
    )

    assert server.reconcile_pending_daemon_upgrades([root]) == [sid]
    assert scheduled == [(sid, {"global_root": root})]


def test_webapi_startup_hook_runs_daemon_upgrade_reconciliation(
    ctx,
    monkeypatch,
) -> None:
    root, _sid, _life = ctx
    calls = []
    monkeypatch.setattr(
        server,
        "reconcile_pending_daemon_upgrades",
        lambda roots: calls.append(roots) or [],
    )

    with TestClient(server.create_app(global_root=root)):
        pass

    assert calls == [[root.resolve()]]


def test_schedule_daemon_upgrade_retries_after_thread_start_failure(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx
    source = root / "checkout"
    source.mkdir()
    status = server.DaemonStatus(
        alive=True,
        pid=321,
        started_at_iso=None,
        uptime_seconds=1.0,
        life_dir=life,
    )
    monkeypatch.setattr(server, "runtime_identity", lambda: {"source_root": str(source)})
    monkeypatch.setattr(server, "read_daemon_status", lambda path: status)
    monkeypatch.setattr(
        server,
        "daemon_protocol_compatibility",
        lambda value: (False, "release mismatch"),
    )
    monkeypatch.setattr(
        server,
        "daemon_runtime_owned_by_current_source",
        lambda value: True,
    )
    monkeypatch.setattr(
        server,
        "read_continuous_state",
        lambda path: SimpleNamespace(enabled=False, objective=""),
    )

    class BrokenThread:
        def __init__(self, *, target, name, daemon):
            pass

        def start(self):
            raise RuntimeError("thread unavailable")

    monkeypatch.setattr(server.threading, "Thread", BrokenThread)
    server._SCHEDULED_DAEMON_UPGRADES.clear()

    with pytest.raises(RuntimeError, match="thread unavailable"):
        server.schedule_project_daemon_upgrade(sid, global_root=root)

    assert str(life.resolve()) not in server._SCHEDULED_DAEMON_UPGRADES
    assert (life / server.project_state.DAEMON_UPGRADE_REQUEST_FILE).is_file()


def test_daemon_upgrade_drains_and_restores_continuous_mode(
    ctx,
    monkeypatch,
) -> None:
    root, sid, life = ctx
    monkeypatch.setattr(
        server,
        "read_daemon_status",
        lambda path: server.DaemonStatus(
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
        server,
        "stop_daemon",
        lambda *args, **kwargs: stops.append(kwargs) or 0,
    )
    monkeypatch.setattr(
        server,
        "read_continuous_state",
        lambda path: SimpleNamespace(enabled=True, objective="keep researching"),
    )
    writes = []
    monkeypatch.setattr(
        server,
        "write_continuous_config",
        lambda path, **kwargs: writes.append((path, kwargs)),
    )
    starts = []
    monkeypatch.setattr(
        server,
        "start_project_daemon",
        lambda project_id, **kwargs: starts.append((project_id, kwargs)) or {"rc": 0},
    )

    result = server.upgrade_project_daemon(sid, global_root=root)

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
        server,
        "runtime_identity",
        lambda: {"source_root": str(source)},
    )
    monkeypatch.setattr(
        server,
        "read_daemon_status",
        lambda path: server.DaemonStatus(
            alive=True,
            pid=321,
            started_at_iso=None,
            uptime_seconds=5.0,
            life_dir=Path(path),
            pid_path=Path(path) / "daemon.pid",
        ),
    )
    monkeypatch.setattr(server, "stop_daemon", lambda *args, **kwargs: 2)
    monkeypatch.setattr(
        server,
        "read_continuous_state",
        lambda path: SimpleNamespace(enabled=True, objective="keep researching"),
    )
    scheduled = []
    monkeypatch.setattr(
        server,
        "schedule_project_daemon_upgrade",
        lambda project_id, **kwargs: (
            scheduled.append((project_id, kwargs))
            or {"rc": 0, "scheduled": True, "reason": "draining"}
        ),
    )

    result = server.upgrade_project_daemon(sid, global_root=root)

    assert result == {"rc": 0, "scheduled": True, "reason": "draining"}
    assert scheduled == [(sid, {"global_root": root})]
    request = server._read_daemon_upgrade_request(life)
    assert request is not None
    assert request["expected_pid"] == 321
    assert request["resume_continuous"] is True


def test_daemon_command_idempotency_and_revision_fencing(ctx, monkeypatch) -> None:
    root, sid, _life = ctx
    starts = []
    stops = []
    monkeypatch.setattr(
        server,
        "start_project_daemon",
        lambda project_id, **kwargs: starts.append(project_id) or {"rc": 0, "already_alive": False},
    )
    monkeypatch.setattr(
        server,
        "stop_project_daemon",
        lambda project_id, **kwargs: stops.append(project_id) or {"rc": 0},
    )
    client = TestClient(server.create_app(global_root=root))

    body = {"command_id": "cmd-start", "expected_revision": 0}
    first = client.post(f"/api/projects/{sid}/daemon/start", json=body).json()
    duplicate = client.post(f"/api/projects/{sid}/daemon/start", json=body).json()

    assert starts == [sid]
    assert first["command_status"] == duplicate["command_status"] == "applied"
    assert first["command_revision"] == duplicate["command_revision"] == 3

    conflict = client.post(
        f"/api/projects/{sid}/daemon/stop",
        json={"command_id": "cmd-start", "expected_revision": 0},
    ).json()
    assert conflict["command_status"] == "rejected"
    assert conflict["rc"] == 3
    assert "command_id conflict" in conflict["error"]
    assert stops == []

    stale = client.post(
        f"/api/projects/{sid}/daemon/stop",
        json={"command_id": "cmd-stop", "expected_revision": 0},
    ).json()
    assert stale["command_status"] == "rejected"
    assert stale["rc"] == 3
    assert "stale command revision" in stale["error"]
    assert stops == []


def test_project_update_renames_session(ctx) -> None:
    root, sid, life = ctx
    (life / "session.json").write_text(
        json.dumps({"id": sid, "display_name": "old", "cwd": str(life)}),
        encoding="utf-8",
    )
    client = TestClient(server.create_app(global_root=root))

    r = client.patch(
        f"/api/projects/{sid}",
        json={"name": "  Research\n  console  "},
    )

    assert r.status_code == 200
    assert r.json()["name"] == "Research console"
    assert json.loads((life / "session.json").read_text())["display_name"] == "Research console"


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


def test_project_delete_moves_stopped_session_to_trash(ctx, monkeypatch) -> None:
    root, sid, life = ctx
    workdir = root / "workspaces" / sid
    workdir.mkdir(parents=True)
    (workdir / "result.txt").write_text("operator result", encoding="utf-8")
    meta = json.loads((life / "session.json").read_text(encoding="utf-8"))
    meta["workdir"] = str(workdir)
    (life / "session.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(
        server,
        "read_daemon_status",
        lambda path: server.DaemonStatus(
            alive=False,
            pid=None,
            started_at_iso=None,
            uptime_seconds=None,
            life_dir=Path(path),
            pid_path=Path(path) / "daemon.pid",
        ),
    )
    client = TestClient(server.create_app(global_root=root))

    r = client.delete(f"/api/projects/{sid}")

    assert r.status_code == 200
    assert not life.exists()
    assert (root / r.json()["trash_path"]).is_dir()
    assert r.json()["workdir"] == str(workdir)
    assert r.json()["workdir_preserved"] is True
    assert (workdir / "result.txt").read_text(encoding="utf-8") == "operator result"
    touch_session(root, sid, display_name="must not resurrect")
    assert not life.exists()


def test_project_delete_releases_warm_manager_runner(ctx, monkeypatch) -> None:
    root, sid, _life = ctx
    closed: list[str] = []
    state = manager_state._chat_state_for(sid)
    state["manager_runner"] = SimpleNamespace(
        _backend=SimpleNamespace(
            close_acp_clients=lambda: closed.append("acp"),
        ),
        reset_chat_session=lambda: closed.append("session"),
    )
    monkeypatch.setattr(
        server,
        "read_daemon_status",
        lambda path: server.DaemonStatus(
            alive=False,
            pid=None,
            started_at_iso=None,
            uptime_seconds=None,
            life_dir=Path(path),
            pid_path=Path(path) / "daemon.pid",
        ),
    )

    response = TestClient(server.create_app(global_root=root)).delete(f"/api/projects/{sid}")

    assert response.status_code == 200
    assert closed == ["acp", "session"]
    assert sid not in manager_state._STATES


def test_project_trash_can_be_listed_and_restored(ctx, monkeypatch) -> None:
    root, sid, life = ctx
    monkeypatch.setattr(
        server,
        "read_daemon_status",
        lambda path: server.DaemonStatus(
            alive=False,
            pid=None,
            started_at_iso=None,
            uptime_seconds=None,
            life_dir=Path(path),
            pid_path=Path(path) / "daemon.pid",
        ),
    )
    client = TestClient(server.create_app(global_root=root))
    deleted = client.delete(f"/api/projects/{sid}").json()

    entries = client.get("/api/trash").json()["entries"]
    entry = next(item for item in entries if item["sid"] == sid)
    assert entry["trash_path"] == deleted["trash_path"]

    restored = client.post(f"/api/trash/{quote(entry['trash_id'], safe='')}/restore")
    assert restored.status_code == 200
    assert restored.json() == {"ok": True, "sid": sid}
    assert life.is_dir()
    assert client.get("/api/trash").json()["entries"] == []


def test_trash_restore_rejects_date_bucket(ctx, monkeypatch) -> None:
    root, sid, _life = ctx
    monkeypatch.setattr(
        server,
        "read_daemon_status",
        lambda path: server.DaemonStatus(
            alive=False,
            pid=None,
            started_at_iso=None,
            uptime_seconds=None,
            life_dir=Path(path),
            pid_path=Path(path) / "daemon.pid",
        ),
    )
    client = TestClient(server.create_app(global_root=root))
    deleted = client.delete(f"/api/projects/{sid}").json()
    bucket = str(Path(deleted["trash_path"]).parent)

    assert server.restore_trashed_project(bucket, global_root=root) is None


def test_trash_restore_rejects_duplicate_sid_in_another_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    sid = "s-duplicate"
    _make_project(primary, sid)
    _make_project(secondary, sid)
    monkeypatch.setattr(
        server,
        "read_daemon_status",
        lambda path: server.DaemonStatus(
            alive=False,
            pid=None,
            started_at_iso=None,
            uptime_seconds=None,
            life_dir=Path(path),
            pid_path=Path(path) / "daemon.pid",
        ),
    )
    assert server.delete_project(sid, global_root=secondary)["ok"] is True
    client = TestClient(server.create_app(global_root=primary, session_roots=[secondary]))
    entry = client.get("/api/trash").json()["entries"][0]

    response = client.post(f"/api/trash/{quote(entry['trash_id'], safe='')}/restore")

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_project_delete_refuses_live_daemon(ctx, monkeypatch) -> None:
    root, sid, life = ctx
    monkeypatch.setattr(
        server,
        "read_daemon_status",
        lambda path: server.DaemonStatus(
            alive=True,
            pid=123,
            started_at_iso=None,
            uptime_seconds=5.0,
            life_dir=Path(path),
            pid_path=Path(path) / "daemon.pid",
        ),
    )
    client = TestClient(server.create_app(global_root=root))

    r = client.delete(f"/api/projects/{sid}")

    assert r.status_code == 409
    assert life.is_dir()
    assert "pause" in r.json()["detail"]


# ── retired Python-REPL parity commands ───────────────────────────────────


def test_plan_preview_delegates_to_manager_planner(ctx, monkeypatch) -> None:
    root, sid, _ = ctx
    monkeypatch.setattr(
        "argus_skill.webapi.manager_bridge.manager_plan",
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
    from argus_skill.core import knob_store

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
    client = TestClient(server.create_app(global_root=root))

    ok = client.post(
        f"/api/projects/{sid}/config/set",
        json={"name": "global_daily_cap", "value": "$12.50"},
    )
    assert ok.status_code == 200
    assert ok.json()["value"] == "12.5"
    assert (
        json.loads((root / "config.json").read_text())["ARGUS_SKILL_GLOBAL_DAILY_CAP_USD"] == "12.5"
    )

    invalid = client.post(
        f"/api/projects/{sid}/config/set",
        json={"name": "global_daily_cap", "value": "unlimited-ish"},
    )
    assert invalid.status_code == 400
    assert "finite non-negative" in invalid.json()["detail"]


def test_config_get_reads_host_global_budget(ctx, monkeypatch) -> None:
    root, sid, life = ctx
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.delenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", raising=False)
    (root / "config.json").write_text(
        json.dumps({"ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "33"}),
        encoding="utf-8",
    )

    response = TestClient(server.create_app(global_root=root)).get(f"/api/projects/{sid}/config")

    assert response.status_code == 200
    knobs = {row["name"]: row for row in response.json()["operator_knobs"]}
    assert knobs["ARGUS_SKILL_GLOBAL_DAILY_CAP_USD"]["value"] == "33.0"
    assert knobs["ARGUS_SKILL_GLOBAL_DAILY_CAP_USD"]["source"] == "global:config.json"


def test_budget_config_batch_is_atomic(ctx, monkeypatch) -> None:
    root, sid, life = ctx
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    client = TestClient(server.create_app(global_root=root))
    values = {
        "global_daily_cap": "120",
        "codex_daily_requests": "400",
        "copilot_daily_requests": "800",
        "copilot_daily_premium": "300",
    }

    invalid = client.post(
        f"/api/projects/{sid}/config/budget",
        json={"values": {**values, "copilot_daily_premium": "not-a-number"}},
    )
    assert invalid.status_code == 400
    assert not (root / "config.json").exists()

    saved = client.post(
        f"/api/projects/{sid}/config/budget",
        json={"values": values},
    )
    assert saved.status_code == 200
    persisted = json.loads((root / "config.json").read_text())
    assert persisted["ARGUS_SKILL_GLOBAL_DAILY_CAP_USD"] == "120"
    assert persisted["ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP"] == "300"


def test_budget_config_does_not_report_success_when_persistence_fails(
    monkeypatch,
    tmp_path,
) -> None:
    from argus_skill.core import knob_store

    monkeypatch.setattr(knob_store, "write_persisted_knobs", lambda values: False)
    with pytest.raises(RuntimeError, match="could not be persisted"):
        server.set_budget_config(
            {
                "global_daily_cap": "120",
                "codex_daily_requests": "400",
                "copilot_daily_requests": "800",
                "copilot_daily_premium": "300",
            },
            project_state_dir=tmp_path / "project",
            global_root=tmp_path,
        )
    assert not (tmp_path / "project" / "budget.json").exists()
    assert not (tmp_path / "global_budget.json").exists()


def test_identity_set_and_skills_and_reset(ctx, monkeypatch) -> None:
    root, sid, life = ctx
    monkeypatch.setattr(server, "run_skill_command", lambda tokens: "skills:" + " ".join(tokens))
    monkeypatch.setattr(
        "argus_skill.webapi.manager_state.reset_manager_context",
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


def test_post_unknown_project_404(ctx, monkeypatch) -> None:
    root, _, _ = ctx
    monkeypatch.setattr(server, "spawn_detached_daemon", lambda *a, **k: 0)
    monkeypatch.setattr(server, "stop_daemon", lambda *a, **k: 0)
    client = TestClient(server.create_app(global_root=root))
    for path, body in [
        ("tasks", {"text": "x"}),
        ("nudge", {"text": "x"}),
        ("continuous", {"enabled": False}),
        ("daemon/start", None),
        ("daemon/stop", None),
        ("daemon/replace", {"victim_sid": "s-other"}),
        ("daemon/upgrade", None),
        ("config/budget", {"values": {}}),
        ("plan", {"text": "x"}),
        ("identity", {"text": "x"}),
        ("config/set", {"name": "model", "value": "x"}),
        ("skills", {"args": "ls"}),
        ("reset", None),
    ]:
        r = client.post(f"/api/projects/s-nope/{path}", json=body)
        assert r.status_code == 404, path
    assert client.patch("/api/projects/s-nope", json={"name": "missing"}).status_code == 404
    assert client.delete("/api/projects/s-nope").status_code == 404


# ── auth (bearer token) ────────────────────────────────────────────────────


def test_bearer_auth_on_posts(ctx) -> None:
    root, sid, _ = ctx
    app = server.create_app(global_root=root, auth_token="secret123")
    client = TestClient(app)
    body = {"text": "x", "autostart_daemon": False}
    # missing / wrong token → 401
    assert client.post(f"/api/projects/{sid}/tasks", json=body).status_code == 401
    assert (
        client.post(
            f"/api/projects/{sid}/tasks", json=body, headers={"Authorization": "Bearer nope"}
        ).status_code
        == 401
    )
    # correct token → 200
    ok = client.post(
        f"/api/projects/{sid}/tasks", json=body, headers={"Authorization": "Bearer secret123"}
    )
    assert ok.status_code == 200
    # Reads are protected too. The line here used to read "reads stay open (no
    # auth on GET)" directly above a note that artifact reads are protected
    # "because they expose project files" — and the transcript, journal and
    # snapshot expose more than the artifacts do.
    assert client.get(f"/api/projects/{sid}/snapshot").status_code == 401
    assert (
        client.get(
            f"/api/projects/{sid}/snapshot",
            headers={"Authorization": "Bearer secret123"},
        ).status_code
        == 200
    )
    # Artifact reads are deliberately protected because they expose project files.
    assert client.get(f"/api/projects/{sid}/artifacts").status_code == 401
    assert (
        client.get(
            f"/api/projects/{sid}/artifacts",
            headers={"Authorization": "Bearer secret123"},
        ).status_code
        == 200
    )


def test_project_management_requires_token(ctx) -> None:
    root, sid, _ = ctx
    client = TestClient(server.create_app(global_root=root, auth_token="secret123"))

    assert client.patch(f"/api/projects/{sid}", json={"name": "x"}).status_code == 401
    assert client.delete(f"/api/projects/{sid}").status_code == 401


def test_sensitive_admin_reads_require_token(ctx) -> None:
    root, sid, _ = ctx
    client = TestClient(server.create_app(global_root=root, auth_token="secret123"))

    assert client.get(f"/api/projects/{sid}/config").status_code == 401
    assert client.get(f"/api/projects/{sid}/identity").status_code == 401
    assert client.get("/api/metrics").status_code == 401
    assert client.get("/api/trash").status_code == 401


def test_ws_requires_token_when_configured(ctx) -> None:
    root, sid, _ = ctx
    app = server.create_app(global_root=root, auth_token="secret123")
    with TestClient(app) as tc:
        # wrong token → closed
        with pytest.raises(Exception):  # noqa: PT011
            with tc.websocket_connect(f"/api/projects/{sid}/stream?token=nope") as ws:
                ws.receive_json()
        # right token → connects (no events yet, but the connection stays open)
        with tc.websocket_connect(f"/api/projects/{sid}/stream?token=secret123&replay=0") as ws:
            assert ws is not None
            ws.close()


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


def test_the_default_localhost_bind_stays_open(ctx) -> None:
    """No token configured is the ordinary `argus --web` case; nothing changes."""
    root, sid, _ = ctx
    client = TestClient(server.create_app(global_root=root))
    for path in ("/api/projects", f"/api/projects/{sid}/journal"):
        assert client.get(path).status_code == 200
