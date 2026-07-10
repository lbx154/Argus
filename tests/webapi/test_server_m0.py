"""M0 tests for the web/TUI backend API (argus_skill/webapi/server.py).

Uses a temp global_root with a hand-built fake project so no daemon is needed.
Skips cleanly if the ``[web]`` extra (fastapi) is not installed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from argus_skill.webapi import server

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


def _make_project(root: Path, sid: str = "s-testaaaa") -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text(
        json.dumps({"type": "mission.started", "text": "hi", "ts": time.time()}) + "\n"
        + json.dumps({"type": "round.review.completed", "status": "done",
                      "reason": "ok", "ts": time.time()}) + "\n",
        encoding="utf-8",
    )
    (life / "backlog.jsonl").write_text(
        json.dumps({"id": "abc123", "title": "do X", "objective": "do X fully",
                    "status": "pending", "priority": 100, "ts": time.time()}) + "\n",
        encoding="utf-8",
    )
    return life


# ── pure helpers (no HTTP) ────────────────────────────────────────────────

def test_project_life_dir_resolves_and_guards(tmp_path: Path) -> None:
    life = _make_project(tmp_path)
    assert server.project_life_dir("s-testaaaa", global_root=tmp_path) == life.resolve()
    # traversal + missing → None (never escapes projects/)
    assert server.project_life_dir("../../etc", global_root=tmp_path) is None
    assert server.project_life_dir("s-nope", global_root=tmp_path) is None


def test_build_snapshot_shape_and_failsoft(tmp_path: Path) -> None:
    _make_project(tmp_path)
    snap = server.build_snapshot("s-testaaaa", global_root=tmp_path)
    assert snap is not None
    assert set(snap) == {"session", "daemon", "roles", "backlog", "recent_events", "spend_usd"}
    assert len(snap["roles"]) == 4  # manager/planner/engineer/reviewer
    assert {r["role"] for r in snap["roles"]} == {"manager", "planner", "engineer", "reviewer"}
    assert len(snap["recent_events"]) == 2
    assert snap["backlog"][0]["title"] == "do X"
    assert snap["daemon"]["alive"] is False  # no daemon running
    assert isinstance(snap["spend_usd"], (int, float))  # authoritative settled spend
    # unknown project → None (not an exception)
    assert server.build_snapshot("s-nope", global_root=tmp_path) is None


def test_daemon_backend_follows_engineer_role_not_stale_status(tmp_path: Path, monkeypatch) -> None:
    """The daemon pill's backend must reflect what role turns actually run on
    (resolved live, same as the roles panel), NOT the ``backend`` frozen into
    daemon.status.json at boot. A daemon started before a backend switch leaves a
    stale field — here ``codex`` — that must never mislabel a copilot run."""
    _make_project(tmp_path, "s-becons01")
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    # A stale status.json claiming codex (as a pre-switch daemon would have written).
    (tmp_path / "projects" / "s-becons01" / "daemon.status.json").write_text(
        json.dumps({"pid": 999999, "backend": "codex",
                    "started_at_iso": "2020-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    snap = server.build_snapshot("s-becons01", global_root=tmp_path)
    assert snap is not None
    eng = next(r for r in snap["roles"] if r["role"] == "engineer")
    assert eng["backend"] == "copilot"  # roles resolve live from the env knob
    # the pill agrees with the roles panel, NOT the stale codex in status.json
    assert snap["daemon"]["backend"] == "copilot"


def test_list_projects(tmp_path: Path) -> None:
    _make_project(tmp_path)
    projects = server.list_projects(global_root=tmp_path)
    ids = {p["id"] for p in projects}
    assert "s-testaaaa" in ids
    p = next(p for p in projects if p["id"] == "s-testaaaa")
    assert p["daemon_alive"] is False


def test_list_projects_hides_empty_shells_and_caps(tmp_path: Path) -> None:
    # three meaningful projects (events + backlog) …
    for sid in ("s-aaaa1111", "s-bbbb2222", "s-cccc3333"):
        _make_project(tmp_path, sid)
    # … and one content-less shell (no events/backlog/transcript, no daemon)
    (tmp_path / "projects" / "s-empty0000").mkdir(parents=True)

    # default hides the empty shell (picker shows real work, not litter)
    ids = {p["id"] for p in server.list_projects(global_root=tmp_path)}
    assert "s-empty0000" not in ids
    assert {"s-aaaa1111", "s-bbbb2222", "s-cccc3333"} <= ids

    # opt-in surfaces every dir
    assert "s-empty0000" in {
        p["id"] for p in server.list_projects(global_root=tmp_path, include_empty=True)
    }

    # limit bounds the per-item daemon-status reads
    assert len(server.list_projects(global_root=tmp_path, limit=2)) == 2


# ── REST endpoints (TestClient) ───────────────────────────────────────────

@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    _make_project(tmp_path)
    return TestClient(server.create_app(global_root=tmp_path))


def test_get_projects(client: TestClient) -> None:
    r = client.get("/api/projects")
    assert r.status_code == 200
    assert any(p["id"] == "s-testaaaa" for p in r.json()["projects"])


def test_get_projects_limit_param(client: TestClient) -> None:
    r = client.get("/api/projects?limit=1")
    assert r.status_code == 200
    assert len(r.json()["projects"]) <= 1


def test_get_snapshot(client: TestClient) -> None:
    r = client.get("/api/projects/s-testaaaa/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert len(body["roles"]) == 4
    assert body["backlog"][0]["objective"] == "do X fully"


def test_get_compact_snapshot_omits_heavy_objective_and_adds_ui_state(client: TestClient) -> None:
    r = client.get("/api/projects/s-testaaaa/snapshot?compact=true&events_limit=1")
    assert r.status_code == 200
    body = r.json()
    assert body["backlog"][0]["title"] == "do X"
    assert body["backlog"][0]["objective"] == ""
    assert body["continuous"] == {
        "enabled": False,
        "objective": "",
        "done_reason": "",
        "done_at": "",
    }
    assert body["pending_questions"] == []
    assert len(body["recent_events"]) == 1


def test_get_events(client: TestClient) -> None:
    r = client.get("/api/projects/s-testaaaa/events?limit=5")
    assert r.status_code == 200
    types = [e["type"] for e in r.json()["events"]]
    assert types == ["mission.started", "round.review.completed"]


def test_unknown_project_404(client: TestClient) -> None:
    assert client.get("/api/projects/s-nope/snapshot").status_code == 404
    assert client.get("/api/projects/s-nope/events").status_code == 404


# ── WebSocket stream: replay then live tail ───────────────────────────────

def test_ws_stream_replays_then_tails_live(tmp_path: Path) -> None:
    life = _make_project(tmp_path)
    app = server.create_app(global_root=tmp_path)
    with TestClient(app) as tc:
        with tc.websocket_connect("/api/projects/s-testaaaa/stream?replay=10") as ws:
            e1 = ws.receive_json()
            e2 = ws.receive_json()
            assert [e1["type"], e2["type"]] == ["mission.started", "round.review.completed"]
            # append a new event; the tail must push it
            with (life / "events.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "engineer.progress",
                                     "kind": "assistant_message",
                                     "text": "live!", "ts": time.time()}) + "\n")
            e3 = ws.receive_json()
            assert e3["type"] == "engineer.progress"
            assert e3["text"] == "live!"


def test_ws_unknown_project_closes(tmp_path: Path) -> None:
    app = server.create_app(global_root=tmp_path)
    with TestClient(app) as tc:
        with pytest.raises(Exception):  # noqa: PT011 — starlette closes with 4404
            with tc.websocket_connect("/api/projects/s-nope/stream") as ws:
                ws.receive_json()
