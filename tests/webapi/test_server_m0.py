"""M0 tests for the web/TUI backend API (argus_skill/webapi/server.py).

Uses a temp global_root with a hand-built fake project so no daemon is needed.
Skips cleanly if the ``[web]`` extra (fastapi) is not installed.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from argus_skill.core.cost_control import CostControlLockBusyError
from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.core.transcript import append_turn
from argus_skill.core.usage import UsageLedger, UsageRecord
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


def test_project_label_does_not_use_raw_operator_transcript(tmp_path: Path) -> None:
    sid = "s-rawlabel"
    life_dir = tmp_path / "projects" / sid
    write_session_meta(
        tmp_path,
        SessionMeta(id=sid, created=1, last_active=1, cwd=str(life_dir)),
    )
    append_turn(
        life_dir,
        "operator",
        "write paper; Manager owns the right sidebar",
    )

    project = next(
        item
        for item in project_state.list_projects(
            global_root=tmp_path,
            include_empty=True,
        )
        if item["id"] == sid
    )

    assert project["label"] == sid


def test_project_index_bounds_large_text_without_truncating_snapshot(
    tmp_path: Path,
) -> None:
    sid = "s-large-index"
    life_dir = tmp_path / "projects" / sid
    objective = "first objective line\n" + ("x" * (2 * 1024 * 1024))
    display_name = "large project\n" + ("n" * 1024)
    write_session_meta(
        tmp_path,
        SessionMeta(
            id=sid,
            display_name=display_name,
            objective=objective,
            created=1,
            last_active=1,
            cwd=str(life_dir),
        ),
    )

    project = next(
        item
        for item in project_state.list_projects(
            global_root=tmp_path,
            include_empty=True,
        )
        if item["id"] == sid
    )
    snapshot = project_state.build_snapshot(sid, global_root=tmp_path)

    assert snapshot is not None
    assert snapshot["session"]["objective"] == objective
    assert len(project["display_name"]) <= 180
    assert len(project["label"]) <= 180
    assert "\n" not in project["label"]
    assert len(project["objective"]) <= 1_000
    assert len(json.dumps({"projects": [project]})) < 5_000


def test_project_index_bounds_fallback_label_without_changing_id(tmp_path: Path) -> None:
    sid = "s-" + ("x" * 200)
    life_dir = tmp_path / "projects" / sid
    write_session_meta(
        tmp_path,
        SessionMeta(id=sid, created=1, last_active=1, cwd=str(life_dir)),
    )

    project = next(
        item
        for item in project_state.list_projects(
            global_root=tmp_path,
            include_empty=True,
        )
        if item["id"] == sid
    )

    assert project["id"] == sid
    assert len(project["label"]) <= 180


def test_project_cost_feed_reports_call_ledger_spend(tmp_path: Path) -> None:
    sid = "s-cost-feed"
    life_dir = tmp_path / "projects" / sid
    write_session_meta(
        tmp_path,
        SessionMeta(
            id=sid,
            display_name="Cost feed",
            objective="measure spend",
            created=1,
            last_active=1,
            cwd=str(life_dir),
        ),
    )
    UsageLedger(life_dir, migrate_legacy=False).append(
        UsageRecord(
            call_id="cost-call-1",
            project_id=sid,
            mission_id="mission-1",
            provider="copilot",
            model="gpt-5.6-sol",
            run_label="engineer-r1",
            started_at=10,
            completed_at=11,
            status="completed",
            input_tokens=100,
            cached_input_tokens=0,
            output_tokens=20,
            reasoning_output_tokens=5,
            premium_requests=1.0,
            pricing_status="priced",
            pricing_tier="copilot_token",
            cost_usd=0.125,
            cost_basis="token",
        )
    )

    client = TestClient(server.create_app(global_root=tmp_path))
    response = client.get("/api/projects/costs")

    assert response.status_code == 200
    row = next(item for item in response.json()["projects"] if item["id"] == sid)
    assert row["spend_usd"] == pytest.approx(0.125)
    assert row["known_cost_usd"] == pytest.approx(0.125)
    assert row["spend_status"] == "priced"
    assert row["usage_calls"] == 1
    assert row["premium_requests"] == pytest.approx(1.0)
    assert row["updated_at"] > 0


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


def test_project_life_dir_resolves_and_guards(tmp_path: Path) -> None:
    life = _make_project(tmp_path)
    assert server.project_life_dir("s-testaaaa", global_root=tmp_path) == life.resolve()
    # traversal + missing → None (never escapes projects/)
    assert server.project_life_dir("../../etc", global_root=tmp_path) is None
    assert server.project_life_dir("s-nope", global_root=tmp_path) is None


def test_snapshot_reuses_cost_control_cache_during_transient_lock_contention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_project(tmp_path)
    fresh = {
        "day": "2026-07-20",
        "active_reservations": 1,
        "unresolved_calls": 0,
    }
    calls = 0

    def snapshot(*, global_root):
        nonlocal calls
        calls += 1
        if calls == 1:
            return fresh
        raise CostControlLockBusyError("busy")

    project_state._COST_CONTROL_CACHE.clear()
    monkeypatch.setattr(project_state, "_HOST_SNAPSHOT_CACHE_TTL_SECONDS", 0.0)
    monkeypatch.setattr(project_state, "cost_control_snapshot", snapshot)

    first = project_state.build_snapshot("s-testaaaa", global_root=tmp_path)
    second = project_state.build_snapshot("s-testaaaa", global_root=tmp_path)

    assert first is not None and first["cost_control"] == fresh
    assert second is not None
    assert second["partial"] is False
    assert second["cost_control"] == {**fresh, "snapshot_stale": True}


def test_snapshot_reuses_host_cost_and_usage_across_projects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_project(tmp_path, "s-host-one")
    _make_project(tmp_path, "s-host-two")
    calls = {"cost": 0, "usage": 0}

    def cost(*, global_root):
        calls["cost"] += 1
        return {"day": "2026-07-27", "active_reservations": 0}

    def usage(*, global_root, now=None):
        calls["usage"] += 1
        return project_state._empty_usage_summary()

    import argus_skill.life.supervisor as supervisor_module

    monkeypatch.setattr(project_state, "cost_control_snapshot", cost)
    monkeypatch.setattr(supervisor_module, "global_daily_usage_summary", usage)
    with project_state._COST_CONTROL_CACHE_LOCK:
        project_state._COST_CONTROL_CACHE.clear()
    with project_state._GLOBAL_USAGE_CACHE_LOCK:
        project_state._GLOBAL_USAGE_CACHE.clear()

    assert server.build_snapshot("s-host-one", global_root=tmp_path) is not None
    assert server.build_snapshot("s-host-two", global_root=tmp_path) is not None
    assert calls == {"cost": 1, "usage": 1}


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

    import argus_skill.life.supervisor as supervisor_module

    monkeypatch.setattr(project_state, "project_usage_summary", lambda _root: project_usage)
    monkeypatch.setattr(supervisor_module, "global_daily_usage_summary", global_usage)
    with project_state._GLOBAL_USAGE_CACHE_LOCK:
        project_state._GLOBAL_USAGE_CACHE.clear()

    snap = server.build_snapshot(
        "s-usage-floor",
        global_root=tmp_path,
        compact=True,
    )

    assert snap is not None
    assert snap["usage_summary"]["call_count"] == 1
    assert snap["global_usage_summary"]["call_count"] == 1
    assert snap["global_spend_usd"] == 0.25
    assert calls >= 1


def test_compact_snapshot_refreshes_host_projections_off_request_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_project(tmp_path, "s-nonblocking-host")
    started = threading.Event()
    release = threading.Event()
    request_thread = threading.get_ident()
    refresh_threads: list[int] = []

    def slow_cost(*, global_root):
        refresh_threads.append(threading.get_ident())
        started.set()
        assert release.wait(timeout=5.0)
        return {"day": "2026-07-27", "active_reservations": 0}

    def usage(*, global_root, now=None):
        return project_state._empty_usage_summary()

    import argus_skill.life.supervisor as supervisor_module

    monkeypatch.setattr(project_state, "cost_control_snapshot", slow_cost)
    monkeypatch.setattr(supervisor_module, "global_daily_usage_summary", usage)
    with project_state._COST_CONTROL_CACHE_LOCK:
        project_state._COST_CONTROL_CACHE.clear()
    with project_state._GLOBAL_USAGE_CACHE_LOCK:
        project_state._GLOBAL_USAGE_CACHE.clear()
    with project_state._HOST_REFRESHING_LOCK:
        project_state._HOST_REFRESHING.clear()

    snap = server.build_snapshot(
        "s-nonblocking-host",
        global_root=tmp_path,
        compact=True,
    )

    try:
        assert snap is not None
        assert snap["cost_control"] is None
        assert snap["global_usage_summary"]["call_count"] == 0
        assert started.wait(timeout=1.0)
        assert len(refresh_threads) == 1
        assert refresh_threads[0] != request_thread
    finally:
        release.set()

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with project_state._HOST_REFRESHING_LOCK:
            if not project_state._HOST_REFRESHING:
                break
        time.sleep(0.01)
    with project_state._HOST_REFRESHING_LOCK:
        assert not project_state._HOST_REFRESHING


def test_project_index_and_routes_span_machine_session_roots(
    tmp_path: Path,
) -> None:
    from argus_skill.life.memory import BacklogItem, LifeMemory

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


def test_primary_duplicate_owns_listing_and_routes(tmp_path: Path) -> None:
    primary = tmp_path / "private"
    machine = tmp_path / "machine"
    (primary / "projects" / "s-duplicate").mkdir(parents=True)
    _make_project(machine, "s-duplicate")
    write_session_meta(
        machine,
        SessionMeta(
            id="s-duplicate",
            display_name="Machine copy",
            last_active=20,
        ),
    )
    client = TestClient(
        server.create_app(
            global_root=primary,
            session_roots=[machine],
        )
    )

    assert client.get("/api/projects").json()["projects"] == []
    assert client.get("/api/projects/s-duplicate/events").json() == {"events": []}


def test_project_limit_backfills_sessions_shadowed_by_primary_root(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "private"
    machine = tmp_path / "machine"
    activity_base = time.time() + 1_000
    for index in range(3):
        sid = f"s-duplicate{index}"
        (primary / "projects" / sid).mkdir(parents=True)
        _make_project(machine, sid)
        write_session_meta(
            machine,
            SessionMeta(
                id=sid,
                display_name=f"Shadowed {index}",
                last_active=activity_base + 100 - index,
            ),
        )
    for index in range(2):
        sid = f"s-unique{index}"
        _make_project(machine, sid)
        write_session_meta(
            machine,
            SessionMeta(
                id=sid,
                display_name=f"Unique {index}",
                last_active=activity_base + 90 - index,
            ),
        )
    client = TestClient(
        server.create_app(
            global_root=primary,
            session_roots=[machine],
        )
    )

    ids = [project["id"] for project in client.get("/api/projects?limit=2").json()["projects"]]
    assert ids == ["s-unique0", "s-unique1"]


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
    runtime = meta["runtime"]
    assert runtime["release_id"].startswith("0.1.2+")
    assert runtime["release_matches_source"] is (
        runtime["manifest_source_digest"] == runtime["runtime_source_digest"]
    )


def test_web_serve_refuses_strict_release_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(
        "argus_skill.core.runtime_identity.release_match_preflight_error",
        lambda: "release mismatch",
    )

    with pytest.raises(RuntimeError, match="webapi refused inconsistent release"):
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
    source = (Path(__file__).parents[2] / "frontend" / "core" / "src" / "protocol.ts").read_text(
        encoding="utf-8"
    )
    assert f"name: '{API_PROTOCOL_NAME}'" in source
    assert f"major: {API_PROTOCOL_MAJOR}" in source
    assert f"minServerMinor: {API_PROTOCOL_MINOR}" in source
    assert f"SNAPSHOT_SCHEMA_VERSION = {SNAPSHOT_SCHEMA_VERSION}" in source
    capabilities_block = source.split("REQUIRED_API_CAPABILITIES = [", 1)[1].split("] as const", 1)[
        0
    ]
    assert tuple(re.findall(r"'([^']+)'", capabilities_block)) == API_CAPABILITIES


def test_build_snapshot_shape_and_failsoft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "55")
    _make_project(tmp_path)
    snap = server.build_snapshot("s-testaaaa", global_root=tmp_path)
    assert snap is not None
    assert set(snap) == {
        "schema_version",
        "session",
        "daemon",
        "roles",
        "backlog",
        "recent_events",
        "spend_usd",
        "spend_status",
        "usage_summary",
        "global_spend_usd",
        "global_spend_status",
        "global_usage_summary",
        "request_usage",
        "cost_control",
        "daemon_commands",
        "observability",
        "mission_view",
        "partial",
        "diagnostics",
    }
    assert snap["schema_version"] == SNAPSHOT_SCHEMA_VERSION
    assert snap["partial"] is False
    assert snap["diagnostics"] == []
    assert snap["cost_control"]["active_reservations"] == 0
    assert snap["cost_control"]["unresolved_calls"] == 0
    assert snap["daemon_commands"]["revision"] == 0
    assert snap["observability"]["slo"]["status"] == "healthy"
    assert snap["mission_view"]["schema_version"] == 6
    assert len(snap["roles"]) == 4  # manager/planner/engineer/reviewer
    assert {r["role"] for r in snap["roles"]} == {"manager", "planner", "engineer", "reviewer"}
    assert len(snap["recent_events"]) == 2
    assert snap["backlog"][0]["title"] == "do X"
    assert snap["daemon"]["alive"] is False  # no daemon running
    assert snap["daemon"]["global_daily_cap_usd"] == 55.0
    assert snap["spend_usd"] is None
    assert snap["spend_status"] == "empty"
    assert snap["usage_summary"]["call_count"] == 0
    assert snap["global_usage_summary"]["call_count"] == 0
    # unknown project → None (not an exception)
    assert server.build_snapshot("s-nope", global_root=tmp_path) is None


def test_build_snapshot_reuses_host_metrics_across_project_switches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_project(tmp_path, "s-first")
    _make_project(tmp_path, "s-second")
    calls = 0

    def fake_metrics_snapshot(*, root, cost_control=None):
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


def test_compact_snapshot_never_runs_expensive_metrics_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_project(tmp_path, "s-fast")
    calls = 0

    def slow_metrics_snapshot(*, root, cost_control=None):
        nonlocal calls
        calls += 1
        return {"slo": {"status": "healthy"}, "root": str(root)}

    monkeypatch.setattr(project_state, "metrics_snapshot", slow_metrics_snapshot)
    with project_state._METRICS_CACHE_LOCK:
        project_state._METRICS_CACHE.clear()
    try:
        snap = server.build_snapshot("s-fast", global_root=tmp_path, compact=True)

        assert snap is not None
        assert snap["observability"] is None
        assert calls == 0

        full = server.build_snapshot("s-fast", global_root=tmp_path)
        assert full is not None
        assert full["observability"]["slo"]["status"] == "healthy"
        assert calls == 1

        refreshed = server.build_snapshot("s-fast", global_root=tmp_path, compact=True)
        assert refreshed is not None
        assert refreshed["observability"]["slo"]["status"] == "healthy"
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
    assert "global_daily_cap_usd" in snap["daemon"]
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
    assert p["daemon_protocol_compatible"] is None
    assert p["daemon_protocol_error"] == ""
    assert p["daemon_source_owned"] is False
    assert p["daemon_upgrade_pending"] is False


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


def test_web_project_index_hides_legacy_internal_dirs(tmp_path: Path) -> None:
    real = _make_project(tmp_path, "s-real0001")
    write_session_meta(
        tmp_path,
        SessionMeta(id="s-real0001", created=1, last_active=1, cwd=str(real)),
    )
    legacy = tmp_path / "projects" / "07197071cf43"
    legacy.mkdir(parents=True)
    (legacy / "events.jsonl").write_text(
        '{"type":"life.mission.completed","title":"internal"}\n',
        encoding="utf-8",
    )
    client = TestClient(server.create_app(global_root=tmp_path))

    ids = {row["id"] for row in client.get("/api/projects").json()["projects"]}

    assert "s-real0001" in ids
    assert "07197071cf43" not in ids


# ── REST endpoints (TestClient) ───────────────────────────────────────────


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    _make_project(tmp_path)
    return TestClient(server.create_app(global_root=tmp_path))


def test_get_projects(client: TestClient) -> None:
    r = client.get("/api/projects")
    assert r.status_code == 200
    assert any(p["id"] == "s-testaaaa" for p in r.json()["projects"])


def test_get_meta_is_public_versioned_and_uncached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_DESKTOP_LAUNCH_NONCE", "desktop-launch-secret")
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
    assert r.headers["x-argus-release"].startswith("0.1.2+")
    assert r.json()["protocol"]["major"] == API_PROTOCOL_MAJOR
    assert r.json()["authentication"] == {
        "required": True,
        "authenticated": False,
    }
    assert r.json()["runtime"]["source_root"] == "<redacted>"
    assert r.json()["runtime"]["desktop_launch_nonce"] is None
    assert authenticated.json()["runtime"]["source_root"] != "<redacted>"
    assert authenticated.json()["authentication"] == {
        "required": True,
        "authenticated": True,
    }
    assert (
        authenticated.json()["runtime"]["desktop_launch_nonce"]
        == "desktop-launch-secret"
    )


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
    rows = [json.loads(line) for line in (tmp_path / "metrics.jsonl").read_text().splitlines()]
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
        "open_ended": True,
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
    client: TestClient,
    tmp_path: Path,
) -> None:
    life = tmp_path / "projects" / "s-testaaaa"
    with (life / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "agent.io.stream", "line": "large raw frame"}) + "\n")
        handle.write(
            json.dumps(
                {
                    "type": "provider.request.started",
                    "call_id": "call-1",
                    "run_label": "scientist.skill_distill",
                }
            )
            + "\n"
        )
        handle.write(json.dumps({"type": "ui.argus", "text": "visible reply"}) + "\n")

    body = client.get("/api/projects/s-testaaaa/events?limit=10&view=ui").json()

    assert [event["type"] for event in body["events"]] == [
        "mission.started",
        "round.review.completed",
        "provider.request.started",
        "ui.argus",
    ]


def test_get_events_ui_view_scans_past_large_raw_tail(
    client: TestClient,
    tmp_path: Path,
) -> None:
    events = tmp_path / "projects" / "s-testaaaa" / "events.jsonl"
    with events.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "ui.operator", "text": "persistent turn"}) + "\n")
        for index in range(40):
            handle.write(
                json.dumps(
                    {
                        "type": "agent.io.stream",
                        "line": f"{index}:" + ("x" * 10_000),
                    }
                )
                + "\n"
            )

    body = client.get("/api/projects/s-testaaaa/events?limit=10&view=ui").json()

    assert any(
        event.get("type") == "ui.operator" and event.get("text") == "persistent turn"
        for event in body["events"]
    )


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
                fh.write(
                    json.dumps(
                        {
                            "type": "engineer.progress",
                            "kind": "assistant_message",
                            "text": "live!",
                            "ts": time.time(),
                        }
                    )
                    + "\n"
                )
            e3 = ws.receive_json()
            assert e3["type"] == "engineer.progress"
            assert e3["text"] == "live!"
            # Starlette's TestClient exit stack sends disconnect and then
            # immediately cancels its task group.  Give the already-waiting
            # server receive task a brief chance to consume the explicit
            # disconnect so Python 3.13 does not surface the harness race as a
            # cancelled application future.
            ws.close()
            time.sleep(0.05)


def test_ws_unknown_project_closes(tmp_path: Path) -> None:
    app = server.create_app(global_root=tmp_path)
    with TestClient(app) as tc:
        with pytest.raises(Exception):  # noqa: PT011 — starlette closes with 4404
            with tc.websocket_connect("/api/projects/s-nope/stream") as ws:
                ws.receive_json()
