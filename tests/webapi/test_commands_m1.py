"""M1 tests for the web/TUI backend API command surface (POST endpoints + auth).

Daemon start/stop are monkeypatched so no real subprocess is spawned.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.life.memory import LifeMemory
from argus_skill.webapi import server

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


def _make_project(root: Path, sid: str = "s-cmd00001") -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").touch()
    (life / "backlog.jsonl").touch()
    return life


@pytest.fixture()
def ctx(tmp_path: Path):
    life = _make_project(tmp_path)
    return tmp_path, "s-cmd00001", life


# ── tasks ─────────────────────────────────────────────────────────────────

def test_post_task_appends_to_backlog(ctx) -> None:
    root, sid, life = ctx
    client = TestClient(server.create_app(global_root=root))
    r = client.post(f"/api/projects/{sid}/tasks",
                    json={"text": "optimize the kernel", "autostart_daemon": False})
    assert r.status_code == 200
    assert r.json()["item"]["objective"] == "optimize the kernel"
    # went through the real Backlog store (flock CAS), not a raw write
    items = LifeMemory.open(life).backlog.all()
    assert len(items) == 1 and items[0].objective == "optimize the kernel"


def test_post_task_honours_inline_flags(ctx) -> None:
    root, sid, life = ctx
    client = TestClient(server.create_app(global_root=root))
    r = client.post(f"/api/projects/{sid}/tasks",
                    json={"text": "tune it --once --budget=$7", "autostart_daemon": False})
    item = r.json()["item"]
    assert item["objective"] == "tune it"           # flags stripped
    assert item["iterate"] is False                  # --once
    assert item["max_cost_usd"] == 7.0               # --budget enforced (not the $30 default)


def test_post_task_empty_400(ctx) -> None:
    root, sid, _ = ctx
    client = TestClient(server.create_app(global_root=root))
    assert client.post(f"/api/projects/{sid}/tasks",
                       json={"text": "   "}).status_code == 400


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
    assert "daemon" in r.json()                       # daemon-ensure result returned
    assert spawned.get("life_dir") == life.resolve()  # lazy spawn fired (no daemon was alive)


def test_start_project_daemon_returns_replacement_candidates_at_cap(
    tmp_path, monkeypatch,
) -> None:
    target = _make_project(tmp_path, "s-target001")
    running = _make_project(tmp_path, "s-running01")
    (running / "session.json").write_text(
        json.dumps({
            "id": "s-running01",
            "display_name": "Existing work",
            "last_active": 1,
        }),
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


def test_replace_project_daemon_parks_state_then_starts_target(
    tmp_path, monkeypatch,
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
    monkeypatch.setattr(server, "_max_active_daemons", lambda config: 1)
    monkeypatch.setattr(server, "_active_daemon_count", lambda config: len(running))
    monkeypatch.setattr(server, "stop_daemon", fake_stop)
    monkeypatch.setattr(server, "spawn_detached_daemon", fake_spawn)

    result = server.replace_project_daemon(
        "s-target001",
        "s-victim001",
        global_root=tmp_path,
    )
    assert result is not None and result["rc"] == 0
    assert result["parked_session"] == "s-victim001"
    assert spawned == [target.resolve()]
    parked = json.loads((victim / "daemon.parked.json").read_text())
    assert parked["state_preserved"] is True
    assert parked["replaced_by"] == "s-target001"
    assert parked["unfinished_tasks"][0]["title"] == "unfinished work"
    events = [
        json.loads(line)
        for line in (victim / "events.jsonl").read_text().splitlines()
    ]
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
    ctx, monkeypatch,
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
    r = client.post(f"/api/projects/{sid}/continuous",
                    json={"enabled": True, "objective": "keep improving X"})
    assert r.status_code == 200
    cfg = json.loads((life / "continuous.json").read_text())
    assert cfg["enabled"] is True and cfg["objective"] == "keep improving X"
    assert spawned == {
        "continuous": True,
        "objective": "keep improving X",
        "resume_continuous": True,
    }


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


# ── retired Python-REPL parity commands ───────────────────────────────────

def test_plan_preview_delegates_to_manager_planner(ctx, monkeypatch) -> None:
    root, sid, _ = ctx
    monkeypatch.setattr(
        "argus_skill.webapi.manager_bridge.manager_plan",
        lambda sid, text, *, global_root=None: {
            "steps": [{"title": "Check premise", "detail": "first"}],
            "notes": [], "error": "",
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
    assert client.post(
        f"/api/projects/{sid}/config/set",
        json={"name": "not_a_knob", "value": "x"},
    ).status_code == 400


def test_config_set_validates_and_normalizes_typed_values(ctx, monkeypatch) -> None:
    root, sid, _ = ctx
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    client = TestClient(server.create_app(global_root=root))

    ok = client.post(
        f"/api/projects/{sid}/config/set",
        json={"name": "daily_cap", "value": "$12.50"},
    )
    assert ok.status_code == 200
    assert ok.json()["value"] == "12.5"
    assert json.loads((root / "config.json").read_text())["ARGUS_SKILL_DAILY_CAP_USD"] == "12.5"

    invalid = client.post(
        f"/api/projects/{sid}/config/set",
        json={"name": "daily_cap", "value": "unlimited-ish"},
    )
    assert invalid.status_code == 400
    assert "finite non-negative" in invalid.json()["detail"]


def test_identity_set_and_skills_and_reset(ctx, monkeypatch) -> None:
    root, sid, life = ctx
    monkeypatch.setattr(server, "run_skill_command", lambda tokens: "skills:" + " ".join(tokens))
    monkeypatch.setattr(
        "argus_skill.webapi.manager_bridge.reset_manager_context",
        lambda sid, *, global_root=None: True,
    )
    client = TestClient(server.create_app(global_root=root))
    assert client.post(f"/api/projects/{sid}/identity", json={"text": "Operator A"}).status_code == 200
    assert "Operator A" in LifeMemory.open(life).identity.read()
    assert client.post(f"/api/projects/{sid}/skills", json={"args": "promote demo"}).json()["text"] == "skills:promote demo"
    assert client.post(f"/api/projects/{sid}/reset").json()["ok"] is True


# ── unknown project → 404 on every POST ────────────────────────────────────

def test_post_unknown_project_404(ctx, monkeypatch) -> None:
    root, _, _ = ctx
    monkeypatch.setattr(server, "spawn_detached_daemon", lambda *a, **k: 0)
    monkeypatch.setattr(server, "stop_daemon", lambda *a, **k: 0)
    client = TestClient(server.create_app(global_root=root))
    for path, body in [
        ("tasks", {"text": "x"}), ("nudge", {"text": "x"}),
        ("continuous", {"enabled": False}), ("daemon/start", None), ("daemon/stop", None),
        ("daemon/replace", {"victim_sid": "s-other"}),
        ("plan", {"text": "x"}), ("identity", {"text": "x"}),
        ("config/set", {"name": "model", "value": "x"}),
        ("skills", {"args": "ls"}), ("reset", None),
    ]:
        r = client.post(f"/api/projects/s-nope/{path}", json=body)
        assert r.status_code == 404, path


# ── auth (bearer token) ────────────────────────────────────────────────────

def test_bearer_auth_on_posts(ctx) -> None:
    root, sid, _ = ctx
    app = server.create_app(global_root=root, auth_token="secret123")
    client = TestClient(app)
    body = {"text": "x", "autostart_daemon": False}
    # missing / wrong token → 401
    assert client.post(f"/api/projects/{sid}/tasks", json=body).status_code == 401
    assert client.post(f"/api/projects/{sid}/tasks", json=body,
                       headers={"Authorization": "Bearer nope"}).status_code == 401
    # correct token → 200
    ok = client.post(f"/api/projects/{sid}/tasks", json=body,
                     headers={"Authorization": "Bearer secret123"})
    assert ok.status_code == 200
    # reads stay open (no auth on GET)
    assert client.get(f"/api/projects/{sid}/snapshot").status_code == 200
    # Artifact reads are deliberately protected because they expose project files.
    assert client.get(f"/api/projects/{sid}/artifacts").status_code == 401
    assert client.get(
        f"/api/projects/{sid}/artifacts",
        headers={"Authorization": "Bearer secret123"},
    ).status_code == 200


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
