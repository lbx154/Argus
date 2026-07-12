"""M0 tests for the web/TUI backend API (argus_skill/webapi/server.py).

Uses a temp global_root with a hand-built fake project so no daemon is needed.
Skips cleanly if the ``[web]`` extra (fastapi) is not installed.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from argus_skill.webapi import project_state, server
from argus_skill.webapi.protocol import (
    API_CAPABILITIES,
    API_PROTOCOL_MAJOR,
    API_PROTOCOL_MINOR,
    API_PROTOCOL_NAME,
    SNAPSHOT_SCHEMA_VERSION,
    build_api_meta,
)

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


def test_api_meta_identifies_protocol_capabilities_and_loaded_checkout() -> None:
    meta = build_api_meta()
    assert meta["service"] == "argus-skill-webapi"
    assert meta["protocol"] == {
        "name": API_PROTOCOL_NAME,
        "major": API_PROTOCOL_MAJOR,
        "minor": API_PROTOCOL_MINOR,
    }
    assert meta["snapshot_schema_version"] == SNAPSHOT_SCHEMA_VERSION
    assert meta["capabilities"] == list(API_CAPABILITIES)
    assert Path(meta["runtime"]["source_root"]) == Path(__file__).parents[2]
    assert meta["runtime"]["pid"] > 0
    assert meta["runtime"]["release_id"].startswith("0.1.0+")
    assert meta["runtime"]["release_matches_source"] is True


def test_static_web_cache_policy_keeps_shell_fresh_and_hashes_immutable() -> None:
    assert server._web_cache_control("/") == "no-store"
    assert server._web_cache_control("/index.html") == "no-store"
    assert server._web_cache_control("/assets/App-deadbeef.js") == (
        "public, max-age=31536000, immutable"
    )
    assert server._web_cache_control("/api/projects") == ""


def test_static_web_assets_are_gzip_compressed(tmp_path: Path) -> None:
    assets = Path(__file__).parents[2] / "frontend" / "web" / "dist" / "assets"
    script = next(path for path in assets.glob("index-*.js") if path.stat().st_size > 1024)
    with TestClient(server.create_app(global_root=tmp_path)) as client:
        response = client.get(
            f"/assets/{script.name}",
            headers={"Accept-Encoding": "gzip"},
        )

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_frontend_protocol_constants_match_backend_contract() -> None:
    source = (
        Path(__file__).parents[2] / "frontend" / "core" / "src" / "protocol.ts"
    ).read_text(encoding="utf-8")
    assert f"name: '{API_PROTOCOL_NAME}'" in source
    assert f"major: {API_PROTOCOL_MAJOR}" in source
    assert f"minServerMinor: {API_PROTOCOL_MINOR}" in source
    assert f"SNAPSHOT_SCHEMA_VERSION = {SNAPSHOT_SCHEMA_VERSION}" in source
    capabilities_block = source.split(
        "REQUIRED_API_CAPABILITIES = [", 1
    )[1].split("] as const", 1)[0]
    assert tuple(re.findall(r"'([^']+)'", capabilities_block)) == API_CAPABILITIES


def test_build_snapshot_shape_and_failsoft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_PER_MISSION_CAP_USD", "7.5")
    monkeypatch.setenv("ARGUS_SKILL_DAILY_CAP_USD", "19.25")
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "55")
    _make_project(tmp_path)
    snap = server.build_snapshot("s-testaaaa", global_root=tmp_path)
    assert snap is not None
    assert set(snap) == {
        "schema_version", "session", "daemon", "roles", "backlog",
        "recent_events", "spend_usd", "spend_status", "usage_summary",
        "request_usage", "cost_control", "daemon_commands", "observability",
        "mission_view", "partial", "diagnostics",
    }
    assert snap["schema_version"] == SNAPSHOT_SCHEMA_VERSION
    assert snap["partial"] is False
    assert snap["diagnostics"] == []
    assert snap["cost_control"]["active_reservations"] == 0
    assert snap["cost_control"]["unresolved_calls"] == 0
    assert snap["daemon_commands"]["revision"] == 0
    assert snap["observability"]["slo"]["status"] == "healthy"
    assert snap["mission_view"]["schema_version"] == 1
    assert len(snap["roles"]) == 4  # manager/planner/engineer/reviewer
    assert {r["role"] for r in snap["roles"]} == {"manager", "planner", "engineer", "reviewer"}
    assert len(snap["recent_events"]) == 2
    assert snap["backlog"][0]["title"] == "do X"
    assert snap["daemon"]["alive"] is False  # no daemon running
    assert snap["daemon"]["per_mission_cap_usd"] == 7.5
    assert snap["daemon"]["daily_cap_usd"] == 19.25
    assert snap["daemon"]["global_daily_cap_usd"] == 55.0
    assert snap["spend_usd"] is None
    assert snap["spend_status"] == "empty"
    assert snap["usage_summary"]["call_count"] == 0
    # unknown project → None (not an exception)
    assert server.build_snapshot("s-nope", global_root=tmp_path) is None


def test_build_snapshot_reuses_host_metrics_across_project_switches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_project(tmp_path, "s-first")
    _make_project(tmp_path, "s-second")
    calls = 0

    def fake_metrics_snapshot(*, root):
        nonlocal calls
        calls += 1
        return {"slo": {"status": "healthy"}, "root": str(root)}

    monkeypatch.setattr(project_state, "metrics_snapshot", fake_metrics_snapshot)
    with project_state._METRICS_CACHE_LOCK:
        project_state._METRICS_CACHE.clear()
    try:
        assert server.build_snapshot("s-first", global_root=tmp_path) is not None
        assert server.build_snapshot("s-second", global_root=tmp_path) is not None
        assert calls == 1
    finally:
        with project_state._METRICS_CACHE_LOCK:
            project_state._METRICS_CACHE.clear()


def test_build_snapshot_marks_failsoft_sections_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_project(tmp_path)

    def broken_status(_life_dir: Path):
        raise RuntimeError("status sidecar is unreadable")

    monkeypatch.setattr(project_state, "read_daemon_status", broken_status)
    snap = server.build_snapshot("s-testaaaa", global_root=tmp_path)
    assert snap is not None
    assert snap["partial"] is True
    assert snap["daemon"]["read_status"] == "error"
    assert snap["daemon"]["read_error"] == "status sidecar is unreadable"
    assert "daily_cap_usd" in snap["daemon"]
    assert snap["diagnostics"] == [{
        "section": "daemon",
        "error_type": "RuntimeError",
        "message": "status sidecar is unreadable",
    }]


def test_build_snapshot_marks_running_legacy_daemon_incompatible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = _make_project(tmp_path)
    monkeypatch.setattr(
        project_state,
        "read_daemon_status",
        lambda _life_dir: server.DaemonStatus(
            alive=True,
            pid=123,
            started_at_iso=None,
            uptime_seconds=5.0,
            life_dir=life,
        ),
    )

    snap = server.build_snapshot("s-testaaaa", global_root=tmp_path)

    assert snap is not None
    assert snap["partial"] is True
    assert snap["daemon"]["protocol_compatible"] is False
    assert "no protocol metadata" in snap["daemon"]["protocol_error"]
    assert snap["diagnostics"][0]["section"] == "daemon_protocol"


def test_snapshot_auxiliary_failures_keep_schema_and_report_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_project(tmp_path)

    def broken(name: str):
        def _raise(*_args, **_kwargs):
            raise RuntimeError(f"{name} unavailable")

        return _raise

    monkeypatch.setattr(
        project_state,
        "project_usage_summary",
        broken("usage"),
    )
    monkeypatch.setattr(
        project_state,
        "read_session_meta",
        broken("session"),
    )
    monkeypatch.setattr(
        project_state,
        "provider_usage_snapshot",
        broken("request usage"),
    )

    snap = server.build_snapshot("s-testaaaa", global_root=tmp_path)

    assert snap is not None
    assert snap["partial"] is True
    assert snap["session"]["id"] == "s-testaaaa"
    assert snap["spend_usd"] is None
    assert snap["usage_summary"]["call_count"] == 0
    assert snap["request_usage"] is None
    assert {item["section"] for item in snap["diagnostics"]} >= {
        "usage",
        "session",
        "request_usage",
    }
    repeated = server.build_snapshot("s-testaaaa", global_root=tmp_path)
    assert repeated is not None
    assert "usage" in {item["section"] for item in repeated["diagnostics"]}


def test_server_reexports_project_state_read_api() -> None:
    assert server.build_snapshot is project_state.build_snapshot
    assert server.list_projects is project_state.list_projects
    assert server.project_life_dir is project_state.project_life_dir


def test_malformed_daemon_admission_is_visible_in_snapshot_diagnostics(
    tmp_path: Path,
) -> None:
    life = _make_project(tmp_path)
    (life / project_state.DAEMON_ADMISSION_FILE).write_text(
        "{broken",
        encoding="utf-8",
    )

    snap = server.build_snapshot("s-testaaaa", global_root=tmp_path)

    assert snap is not None
    assert snap["partial"] is True
    assert snap.get("daemon_admission") is None
    assert "daemon_admission" in {
        item["section"] for item in snap["diagnostics"]
    }


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


def test_get_meta_is_public_versioned_and_uncached(tmp_path: Path) -> None:
    app = server.create_app(global_root=tmp_path, auth_token="secret")
    with TestClient(app) as client:
        r = client.get("/api/meta")
        authenticated = client.get(
            "/api/meta",
            headers={"Authorization": "Bearer secret"},
        )
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-argus-protocol"] == (
        f"argus.webapi/{API_PROTOCOL_MAJOR}.{API_PROTOCOL_MINOR}"
    )
    assert r.headers["x-argus-release"].startswith("0.1.0+")
    assert r.json()["protocol"]["major"] == API_PROTOCOL_MAJOR
    assert r.json()["runtime"]["source_root"] == "<redacted>"
    assert authenticated.json()["runtime"]["source_root"] != "<redacted>"


def test_metrics_endpoints_expose_json_slo_and_prometheus(client: TestClient) -> None:
    payload = client.get("/api/metrics")
    assert payload.status_code == 200
    assert payload.json()["slo"]["status"] in {"healthy", "degraded"}
    prometheus = client.get("/metrics")
    assert prometheus.status_code == 200
    assert "text/plain" in prometheus.headers["content-type"]
    assert "argus_slo_healthy" in prometheus.text


def test_web_metrics_use_route_templates_instead_of_project_ids(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    with TestClient(server.create_app(global_root=tmp_path)) as client:
        response = client.get("/api/projects/s-testaaaa/snapshot")
    assert response.status_code == 200
    rows = [
        json.loads(line)
        for line in (tmp_path / "metrics.jsonl").read_text().splitlines()
    ]
    request_metric = next(row for row in rows if row["name"] == "web.request")
    assert request_metric["labels"]["path"] == "/api/projects/{sid}/snapshot"
    assert "s-testaaaa" not in request_metric["labels"]["path"]


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


def test_get_events_ui_view_filters_raw_transport_frames(
    client: TestClient, tmp_path: Path,
) -> None:
    life = tmp_path / "projects" / "s-testaaaa"
    with (life / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "agent.io.stream", "line": "large raw frame"}) + "\n")
        handle.write(json.dumps({"type": "ui.argus", "text": "visible reply"}) + "\n")

    body = client.get("/api/projects/s-testaaaa/events?limit=10&view=ui").json()

    assert [event["type"] for event in body["events"]] == [
        "mission.started",
        "round.review.completed",
        "ui.argus",
    ]


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
