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

def test_post_continuous_writes_config(ctx) -> None:
    root, sid, life = ctx
    client = TestClient(server.create_app(global_root=root))
    r = client.post(f"/api/projects/{sid}/continuous",
                    json={"enabled": True, "objective": "keep improving X"})
    assert r.status_code == 200
    cfg = json.loads((life / "continuous.json").read_text())
    assert cfg["enabled"] is True and cfg["objective"] == "keep improving X"


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


# ── unknown project → 404 on every POST ────────────────────────────────────

def test_post_unknown_project_404(ctx, monkeypatch) -> None:
    root, _, _ = ctx
    monkeypatch.setattr(server, "spawn_detached_daemon", lambda *a, **k: 0)
    monkeypatch.setattr(server, "stop_daemon", lambda *a, **k: 0)
    client = TestClient(server.create_app(global_root=root))
    for path, body in [
        ("tasks", {"text": "x"}), ("nudge", {"text": "x"}),
        ("continuous", {"enabled": False}), ("daemon/start", None), ("daemon/stop", None),
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
