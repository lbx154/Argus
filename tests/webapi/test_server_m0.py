"""M0 tests for the web/TUI backend API (argus/webapi/server.py).

Uses a temp global_root with a hand-built fake project so no daemon is needed.
Skips cleanly if the ``[web]`` extra (fastapi) is not installed.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from argus.core.session import SessionMeta, write_session_meta
from argus.daemon import life_worker as daemon_worker
from argus.webapi import project_state, server
from argus.webapi.protocol import (
    API_CAPABILITIES,
    API_PROTOCOL_MAJOR,
    API_PROTOCOL_MINOR,
    API_PROTOCOL_NAME,
    SNAPSHOT_SCHEMA_VERSION,
    build_api_meta,
)

fastapi = pytest.importorskip("fastapi")
from fastapi import Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_meta_redacts_state_directory_before_authentication(tmp_path: Path) -> None:
    client = TestClient(server.create_app(global_root=tmp_path, auth_token="fixture-only"))
    assert client.get("/api/meta").json()["runtime"]["state_root"] is None
    authenticated = client.get(
        "/api/meta", headers={"Authorization": "Bearer fixture-only"},
    )
    assert authenticated.json()["runtime"]["state_root"] == str(tmp_path.resolve())


def _make_project(root: Path, sid: str = "s-testaaaa") -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text(
        json.dumps({"type": "mission.started", "text": "hi", "ts": time.time()})
        + "\n"
        + json.dumps(
            {"type": "round.review.completed", "status": "done", "reason": "ok", "ts": time.time()}
        )
        + "\n",
        encoding="utf-8",
    )
    (life / "backlog.jsonl").write_text(
        json.dumps(
            {
                "id": "abc123",
                "title": "do X",
                "objective": "do X fully",
                "status": "pending",
                "priority": 100,
                "ts": time.time(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return life


# ── pure helpers (no HTTP) ────────────────────────────────────────────────


def test_compact_snapshot_never_reports_global_usage_below_project_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_project(tmp_path, "s-usage-floor")
    empty = project_state._empty_usage_summary()
    project_usage = replace(
        empty,
        call_count=1,
        known_cost_usd=0.25,
        cost_usd=0.25,
        pricing_status="priced",
        priced_calls=1,
    )
    calls = 0

    def global_usage(*, global_root, now=None):
        nonlocal calls
        calls += 1
        return project_usage

    import argus.life.supervisor as supervisor_module

    monkeypatch.setattr(project_state, "project_usage_summary", lambda _root: project_usage)
    monkeypatch.setattr(supervisor_module, "global_daily_usage_summary", global_usage)
    with project_state._GLOBAL_USAGE_CACHE_LOCK:
        project_state._GLOBAL_USAGE_CACHE.clear()

    snap = project_state.build_snapshot(
        "s-usage-floor",
        global_root=tmp_path,
        compact=True,
    )

    assert snap is not None
    assert snap["usage_summary"]["call_count"] == 1
    assert snap["global_usage_summary"]["call_count"] == 1
    assert snap["global_spend_usd"] == 0.25
    assert calls >= 1


def test_project_index_and_routes_span_machine_session_roots(
    tmp_path: Path,
) -> None:
    from argus.life.memory import BacklogItem, LifeMemory

    primary = tmp_path / "private"
    machine = tmp_path / "machine"
    _make_project(primary, "s-private1")
    machine_life = _make_project(machine, "s-machine1")
    machine_backlog = LifeMemory.open(machine_life).backlog
    running = machine_backlog.add(BacklogItem.new(title="machine task", objective="work"))
    machine_backlog.mark_running(running.id)
    write_session_meta(
        primary,
        SessionMeta(
            id="s-private1",
            display_name="Private",
            last_active=10,
            launch_cwd="/workspace/private",
        ),
    )
    write_session_meta(
        machine,
        SessionMeta(
            id="s-machine1",
            display_name="Machine",
            last_active=20,
            launch_cwd="/workspace/machine",
        ),
    )

    client = TestClient(
        server.create_app(
            global_root=primary,
            session_roots=[machine],
        )
    )

    index = client.get("/api/projects").json()
    assert [project["id"] for project in index["projects"]] == [
        "s-machine1",
        "s-private1",
    ]
    assert client.get("/api/projects/s-machine1/snapshot").status_code == 200
    assert client.get("/api/projects/s-machine1/events?view=ui").status_code == 200

    renamed = client.patch(
        "/api/projects/s-machine1",
        json={"name": "Renamed machine session"},
    )
    assert renamed.status_code == 200
    assert (
        json.loads(
            (machine / "projects" / "s-machine1" / "session.json").read_text(encoding="utf-8")
        )["display_name"]
        == "Renamed machine session"
    )

    aborted = client.post(
        "/api/projects/s-machine1/mission/abort",
        json={"reason": "operator stop"},
    )
    assert aborted.status_code == 200
    assert aborted.json()["item_id"] == running.id
    assert (machine_life / "running_item_abort.json").exists()


def test_isolated_home_does_not_implicitly_include_user_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated = tmp_path / "isolated"
    user_home = tmp_path / "home"
    _make_project(isolated, "s-isolated")
    _make_project(user_home / ".argus-skill", "s-userhome")
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(isolated))
    monkeypatch.delenv("ARGUS_SKILL_WEB_SESSION_ROOTS", raising=False)

    client = TestClient(server.create_app())

    ids = {project["id"] for project in client.get("/api/projects").json()["projects"]}
    assert ids == {"s-isolated"}


def test_api_meta_identifies_protocol_capabilities_and_loaded_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_DESKTOP_LAUNCH_NONCE", "desktop-launch-test")
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
    assert meta["runtime"]["desktop_launch_nonce"] == "desktop-launch-test"


def test_web_serve_refuses_a_configured_root_the_launcher_did_not_load(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_SOURCE_ROOT", str(tmp_path / "other-worktree"))

    with pytest.raises(RuntimeError, match="webapi refused mismatched source root"):
        server.serve()


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
    source = (Path(__file__).parents[2] / "packages/contracts/src/apiProtocol.generated.ts").read_text(
        encoding="utf-8"
    )
    assert f"name: {json.dumps(API_PROTOCOL_NAME)}" in source
    assert f"major: {API_PROTOCOL_MAJOR}" in source
    assert f"minServerMinor: {API_PROTOCOL_MINOR}" in source
    assert f"SNAPSHOT_SCHEMA_VERSION = {SNAPSHOT_SCHEMA_VERSION}" in source
    capabilities_block = source.split("REQUIRED_API_CAPABILITIES = [", 1)[1].split("] as const", 1)[
        0
    ]
    required = json.loads(f"[{capabilities_block}]")
    assert required == list(API_CAPABILITIES[: len(required)])


def test_build_snapshot_marks_failsoft_sections_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_project(tmp_path)

    def broken_status(_life_dir: Path):
        raise RuntimeError("status sidecar is unreadable")

    monkeypatch.setattr(project_state, "read_daemon_status", broken_status)
    snap = project_state.build_snapshot("s-testaaaa", global_root=tmp_path)
    assert snap is not None
    assert snap["partial"] is True
    assert snap["daemon"]["read_status"] == "error"
    assert snap["daemon"]["read_error"] == "status sidecar is unreadable"
    assert "mission_width" in snap["daemon"]
    assert snap["diagnostics"] == [
        {
            "section": "daemon",
            "error_type": "RuntimeError",
            "message": "status sidecar is unreadable",
        }
    ]


def test_build_snapshot_marks_running_legacy_daemon_incompatible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life = _make_project(tmp_path)
    monkeypatch.setattr(
        project_state,
        "read_daemon_status",
        lambda _life_dir: daemon_worker.DaemonStatus(
            alive=True,
            pid=123,
            started_at_iso="2026-10-21T00:00:00+00:00",
            uptime_seconds=5.0,
            life_dir=life,
        ),
    )

    snap = project_state.build_snapshot("s-testaaaa", global_root=tmp_path)

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

    snap = project_state.build_snapshot("s-testaaaa", global_root=tmp_path)

    assert snap is not None
    assert snap["partial"] is True
    assert snap["session"]["id"] == "s-testaaaa"
    assert snap["spend_usd"] is None
    assert snap["usage_summary"]["call_count"] == 0
    assert {item["section"] for item in snap["diagnostics"]} >= {
        "usage",
        "session",
    }
    repeated = project_state.build_snapshot("s-testaaaa", global_root=tmp_path)
    assert repeated is not None
    assert "usage" in {item["section"] for item in repeated["diagnostics"]}


def test_server_reexports_project_state_read_api() -> None:
    assert project_state.build_snapshot is project_state.build_snapshot
    assert project_state.list_projects is project_state.list_projects
    assert project_state.project_life_dir is project_state.project_life_dir


def test_malformed_daemon_admission_is_visible_in_snapshot_diagnostics(
    tmp_path: Path,
) -> None:
    life = _make_project(tmp_path)
    (life / project_state.DAEMON_ADMISSION_FILE).write_text(
        "{broken",
        encoding="utf-8",
    )

    snap = project_state.build_snapshot("s-testaaaa", global_root=tmp_path)

    assert snap is not None
    assert snap["partial"] is True
    assert snap.get("daemon_admission") is None
    assert "daemon_admission" in {item["section"] for item in snap["diagnostics"]}


def test_daemon_backend_follows_engineer_role_not_stale_status(tmp_path: Path, monkeypatch) -> None:
    """The daemon pill's backend must reflect what role turns actually run on
    (resolved live, same as the roles panel), NOT the ``backend`` frozen into
    daemon.status.json at boot. A daemon started before a backend switch leaves a
    stale field — here ``codex`` — that must never mislabel a copilot run."""
    _make_project(tmp_path, "s-becons01")
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    # A stale status.json claiming codex (as a pre-switch daemon would have written).
    (tmp_path / "projects" / "s-becons01" / "daemon.status.json").write_text(
        json.dumps({"pid": 999999, "backend": "codex", "started_at_iso": "2020-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    snap = project_state.build_snapshot("s-becons01", global_root=tmp_path)
    assert snap is not None
    eng = next(r for r in snap["roles"] if r["role"] == "engineer")
    assert eng["backend"] == "copilot"  # roles resolve live from the env knob
    # the pill agrees with the roles panel, NOT the stale codex in status.json
    assert snap["daemon"]["backend"] == "copilot"


# ── REST endpoints (TestClient) ───────────────────────────────────────────


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    _make_project(tmp_path)
    return TestClient(server.create_app(global_root=tmp_path))


def test_system_doctor_requires_auth_and_returns_typed_read_only_report(
    tmp_path: Path,
) -> None:
    app = server.create_app(global_root=tmp_path, auth_token="secret")
    with TestClient(app) as doctor_client:
        rejected = doctor_client.get("/api/system/doctor")
        response = doctor_client.get(
            "/api/system/doctor",
            headers={"Authorization": "Bearer secret"},
        )

    assert rejected.status_code == 401
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["target_fingerprint"]
    assert {item["scope"] for item in payload["findings"]} >= {
        "host", "install", "cli", "web", "desktop", "daemon",
    }


def test_system_resources_requires_auth_and_hides_device_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus.tools.resource_ledger import probe

    snapshot = {
        "enforcement": "advisory",
        "accelerators": [{
            "kind": "cuda",
            "status": "degraded",
            "visibility_env": "CUDA_VISIBLE_DEVICES",
            "devices": [{"identity": "GPU-abcdef0123456789", "index": "3", "name": "NVIDIA A100",
                         "total_memory_mib": 40960, "used_memory_mib": 12000,
                         "utilization_percent": 75, "visibility": "3"}],
            "detail": "partial telemetry",
        }],
    }
    monkeypatch.setattr(probe.ResourceProbe, "snapshot", lambda self: snapshot)
    app = server.create_app(global_root=tmp_path, auth_token="secret")
    route = next(
        route for route in app.routes
        if getattr(route, "path", "") == "/api/system/resources"
    )
    auth = route.dependant.dependencies[0].call

    def request(*authorization: str) -> Request:
        return Request({"type": "http", "headers": [(b"authorization", a.encode()) for a in authorization]})

    with pytest.raises(fastapi.HTTPException) as rejected:
        auth(request())
    assert rejected.value.status_code == 401
    assert auth(request("Bearer secret")) is None

    payload = route.endpoint()
    assert payload == {
        "schema_version": 1,
        "enforcement": "advisory",
        "accelerators": [{
            "kind": "cuda",
            "status": "degraded",
            "device_count": 1,
            "detail": "partial telemetry",
            "devices": [{"index": "3", "name": "NVIDIA A100", "total_memory_mib": 40960,
                         "used_memory_mib": 12000, "utilization_percent": 75}],
        }],
    }
    assert "abcdef0123456789" not in json.dumps(payload)


def test_metrics_endpoints_expose_json_slo_and_prometheus(client: TestClient) -> None:
    payload = client.get("/api/metrics")
    assert payload.status_code == 200
    assert payload.json()["slo"]["status"] in {"healthy", "degraded"}
    prometheus = client.get("/metrics")
    assert prometheus.status_code == 200
    assert "text/plain" in prometheus.headers["content-type"]
    assert "argus_slo_healthy" in prometheus.text


# ── WebSocket stream: replay then live tail ───────────────────────────────


def test_ws_unknown_project_closes(tmp_path: Path) -> None:
    app = server.create_app(global_root=tmp_path)
    with TestClient(app) as tc:
        with pytest.raises(Exception):  # noqa: PT011 — starlette closes with 4404
            with tc.websocket_connect("/api/projects/s-nope/stream") as ws:
                ws.receive_json()
