"""All tenant content and ledgers in this module are synthetic fixtures."""
import json
import os
import sqlite3
from contextlib import closing

import pytest

from argus_skill.trial.analytics import Analytics, AnalyticsError


@pytest.fixture
def setup(tmp_path):
    now = [2_000_000_000.0]
    tenants = {
        "trial-01": {"data_dir": str(tmp_path / "customer"), "internal_test": False},
        "trial-02": {"data_dir": str(tmp_path / "idle"), "internal_test": False},
        "trial-03": {"data_dir": str(tmp_path / "investor-persona"), "internal_test": True},
    }
    meter, compute = tmp_path / "usage.sqlite3", tmp_path / "compute.sqlite3"
    with closing(sqlite3.connect(meter)) as db, db:
        db.execute("CREATE TABLE trial_keys(key_id TEXT,used INTEGER,credential_hash TEXT)")
        db.executemany(
            "INSERT INTO trial_keys VALUES(?,?,?)",
            [("trial-01", 123, "DO-NOT-READ"), ("trial-02", 456, "DO-NOT-READ"),
             ("trial-03", 10000, "DO-NOT-READ"), ("unconfigured", 999999, "DO-NOT-READ")],
        )
    with closing(sqlite3.connect(compute)) as db, db:
        db.execute("CREATE TABLE jobs(tenant_id TEXT,status TEXT,charged INTEGER,reserved INTEGER,spec TEXT)")
        db.executemany(
            "INSERT INTO jobs VALUES(?,?,?,?,?)",
            [
                ("trial-01", "succeeded", 3600, 7200, "DO-NOT-READ"),
                ("trial-01", "running", 7200, 8000, "DO-NOT-READ"),
                ("trial-02", "failed", 100, 300, "DO-NOT-READ"),
                ("trial-03", "queued", 90000, 99999, "DO-NOT-READ"),
                ("unconfigured", "running", 999999, 999999, "DO-NOT-READ"),
            ],
        )
    analytics = Analytics(
        tmp_path / "analytics", tenants, meter, compute,
        notice_version="notice-2026-09", clock=lambda: now[0],
    )
    return analytics, now, tmp_path


def consent(analytics, tenant="trial-01"):
    return analytics.record_consent(tenant, analytics.notice_version)


def project(setup, tenant="trial-01", sid="s-fixture", metadata=True):
    analytics, _, _ = setup
    consent(analytics, tenant)
    root = analytics.tenants[tenant]["data_dir"] / "home/.argus-skill/projects" / sid
    root.mkdir(parents=True)
    if metadata:
        (root / "session.json").write_text(json.dumps({
            "id": sid, "display_name": "A research task", "created": 100,
            "objective": "Measure a synthetic fixture", "cwd": "/PRIVATE_WORKSPACE",
        }))
    return root


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_consent_is_explicit_versioned_durable_and_idempotent(setup):
    analytics, now, _ = setup
    assert not analytics.consented("trial-01", analytics.notice_version)
    assert analytics.record_request("trial-01", "GET", "/", 200, 1) is None
    with pytest.raises(AnalyticsError, match="consent_required") as exc:
        analytics.projects("trial-01")
    assert exc.value.status == 403
    first = consent(analytics)
    now[0] += 10
    assert consent(analytics) == first
    assert first["consented_at"] == now[0] - 10
    assert not analytics.consented("trial-01", "next-version")
    next_instance = Analytics(
        analytics.path.parent, analytics.tenants, analytics.trial_db, analytics.compute_db,
        notice_version="next-version", clock=lambda: now[0],
    )
    assert next_instance.consented("trial-01", analytics.notice_version)
    assert next_instance.record_request("trial-01", "GET", "/", 200, 1) is None
    next_instance.record_consent("trial-01", "next-version")
    assert next_instance.record_request("trial-01", "GET", "/", 200, 1) == 1
    with pytest.raises(AnalyticsError) as exc:
        analytics.record_consent("missing", "version")
    assert exc.value.status == 404


def test_request_metadata_never_stores_query_or_dynamic_path_content(setup):
    analytics, _, _ = setup
    consent(analytics)
    analytics.record_request(
        "trial-01", "POST",
        "/api/projects/SECRET-IN-SID/message?token=QUERY-SECRET&input=RAW-TEXT", 202, 25.5,
    )
    analytics.record_request("trial-01", "GET", "/RAW-FILENAME/PRIVATE?token=secret", 200, 1)
    with closing(sqlite3.connect(analytics.path)) as db:
        dump = "\n".join(db.iterdump())
    for forbidden in ("SECRET-IN-SID", "QUERY-SECRET", "RAW-TEXT", "RAW-FILENAME", "PRIVATE"):
        assert forbidden not in dump
    assert "/api/projects/:sid/message" in dump
    assert "/:other" in dump
    assert "AUTOINCREMENT" in dump
    assert analytics.dashboard()["recent_task_requests"][0]["elapsed_ms"] == 25.5


def test_activity_separates_polling_submission_failure_and_internal_tests(setup):
    from argus_skill.trial.interaction_capture import Capture

    analytics, now, _ = setup
    for tenant in analytics.tenants:
        consent(analytics, tenant)
    analytics.record_request("trial-01", "POST", "/api/projects/s-one/message", 200, 1)
    analytics.record_request("trial-03", "POST", "/api/projects/s-investor/tasks", 202, 1)
    for tenant, sid, route in (("trial-01", "s-one", "message"), ("trial-03", "s-investor", "tasks")):
        captured = Capture(analytics, tenant, sid, f"/api/projects/{sid}/{route}", {"text": "Synthetic task"})
        captured.feed(json.dumps({"kind": "task", "item": {"id": f"task-{tenant}", "status": "pending"}}).encode())
        captured.finish(200, True, "application/json")
    now[0] += 301
    analytics.record_request("trial-02", "GET", "/api/projects/s-idle/status", 200, 1)
    analytics.record_request("trial-02", "POST", "/api/projects/s-idle/tasks", 500, 1)
    dashboard = analytics.dashboard()
    assert dashboard["summary"]["active_codes_last_5min"] == 1
    assert dashboard["summary"]["task_active_accounts"] == 1
    assert dashboard["summary"]["task_active_accounts_last_5min"] == 0
    assert dashboard["summary"]["message_active_accounts"] == 1
    assert dashboard["accounts"][0]["accepted_message_requests"] == 1
    assert dashboard["summary"]["issued_accounts"] == 2
    assert dashboard["summary"]["consented_accounts"] == 2
    assert dashboard["internal_testing"]["summary"]["task_active_accounts"] == 1
    assert {row["tenant_id"] for row in dashboard["recent_task_requests"]} == {
        "trial-01", "trial-02",
    }
    assert dashboard["task_types"] == {"message": 1, "task": 1}
    combined = analytics.dashboard(include_internal=True)
    assert combined["summary"]["task_active_accounts"] == 2
    assert combined["external_accounts"]["task_active_accounts"] == 1
    assert combined["policy"]["internal_tests_are_customer_traction"] is False
    now[0] += 86400
    assert analytics.dashboard()["summary"]["task_active_accounts"] == 0


def test_http_success_is_not_task_acceptance(setup):
    from argus_skill.trial.interaction_capture import Capture

    analytics, _, _ = setup
    consent(analytics, "trial-01")
    results = [
        {"kind": "chat", "reply": "Hello"},
        {"kind": "error", "reply": "Nothing queued"},
        {"kind": "task", "item": None, "dispatch_state": "planner_pending"},
        {"kind": "task", "item": {"id": "existing-task"}, "duplicate": True},
    ]
    for result in results:
        captured = Capture(analytics, "trial-01", "s-one", "/api/projects/s-one/message", {"text": "Synthetic"})
        captured.feed(json.dumps(result).encode())
        captured.finish(200, True, "application/json")
        analytics.record_request("trial-01", "POST", "/api/projects/s-one/message", 200, 1)
    analytics.record_request("trial-01", "POST", "/api/projects/s-one/tasks", 202, 1)
    analytics.record_request("trial-01", "POST", "/compute/jobs", 201, 1)
    result = analytics.dashboard()
    assert result["summary"]["task_active_accounts"] == 0
    assert result["summary"]["accepted_task_requests"] == 0
    assert result["summary"]["accepted_message_requests"] == 4
    assert result["summary"]["accepted_compute_job_requests"] == 1
    assert result["summary"]["compute_active_accounts"] == 1


def test_token_breakdown_matches_receipts_without_mutating_ledger(setup):
    analytics, _, _ = setup
    with closing(sqlite3.connect(analytics.trial_db)) as db, db:
        db.execute("CREATE TABLE trial_requests(key_id TEXT,state TEXT,charged INTEGER)")
        db.executemany("INSERT INTO trial_requests VALUES(?,?,?)", [
            ("trial-01", "settled", 23), ("trial-01", "active", 40),
            ("trial-01", "unknown", 50), ("trial-01", "interrupted", 10),
            ("trial-02", "settled", 450), ("trial-03", "settled", 10000),
        ])
    before = analytics.trial_db.read_bytes()
    summary = analytics.dashboard()["summary"]
    assert {key: summary[key] for key in (
        "tokens_settled", "tokens_reserved", "tokens_uncertain", "tokens_unattributed",
    )} == {"tokens_settled": 473, "tokens_reserved": 40, "tokens_uncertain": 60, "tokens_unattributed": 6}
    assert summary["token_used"] == 579
    assert summary["token_breakdown_state"] == "ok"
    assert analytics.trial_db.read_bytes() == before


def test_legacy_token_receipts_are_unavailable_not_zero(setup):
    analytics, _, _ = setup
    account = analytics.dashboard()["accounts"][0]
    assert account["token_breakdown_state"] == "legacy"
    assert account["tokens_settled"] is None
    assert account["tokens_reserved"] is None
    assert account["tokens_uncertain"] is None
    assert account["tokens_unattributed"] == account["token_used"] == 123


def test_quota_aggregates_match_readonly_ledgers_and_unknown_is_not_zero(setup):
    analytics, _, _ = setup
    before_meter = analytics.trial_db.read_bytes()
    before_compute = analytics.compute_db.read_bytes()
    result = analytics.dashboard()
    assert result["summary"]["token_used"] == 579
    assert result["summary"]["gpu_seconds_used"] == 3700
    # Compute's own status API treats nonfinal charged amounts as reservations.
    assert result["summary"]["gpu_seconds_reserved"] == 7200
    assert result["internal_testing"]["summary"]["token_used"] == 10000
    assert all(row["token_limit"] == 10_000_000 for row in result["accounts"])
    assert all(row["gpu_seconds_limit"] == 720000 for row in result["accounts"])
    assert "DO-NOT-READ" not in json.dumps(result)
    assert analytics.trial_db.read_bytes() == before_meter
    assert analytics.compute_db.read_bytes() == before_compute
    analytics.trial_db = analytics.path.parent / "missing-meter"
    analytics.compute_db = analytics.path.parent / "missing-compute"
    summary = analytics.dashboard()["summary"]
    assert summary["issued_count_complete"] is False
    assert summary["token_used"] is None
    assert summary["gpu_seconds_used"] is None
    assert not analytics.trial_db.exists()
    assert not analytics.compute_db.exists()


def test_trace_observable_workflow_redaction_and_whole_reasoning_row_exclusion(setup):
    analytics, _, _ = setup
    root = project(setup)
    secrets = [
        "ghp_" + "a" * 30, "github_pat_" + "b" * 30, "sk-proj-" + "c" * 30,
        "argus_trial_" + "d" * 64, "Bearer SHORT", "password=short",
    ]
    write_rows(root / "transcript.jsonl", [
        {"ts": 10, "role": "operator", "text": "Request " + " ".join(secrets)},
        {"ts": 20, "role": "argus", "text": "Delivered a result",
         "delivery": {"targets": [{"path": "results/figure.pdf", "size_bytes": 12}]}},
    ])
    write_rows(root / "events.jsonl", [
        {"type": "engineer.progress", "kind": "tool_call", "tool_name": "bash",
         "arguments": {"command": "echo done", "Authorization": "hidden-auth",
                       "Cookie": "hidden-cookie", "OPENAI_API_KEY": "hidden-key"}},
        {"type": "tool.result", "output": "Observable stdout", "status": "ok"},
        {"type": "usage.recorded", "input_tokens": 20, "cost_usd": 0.02, "ts": 19},
        {"type": "assistant.reasoning", "text": "PRIVATE-REASON-1"},
        {"type": "engineer.progress", "kind": "chain_of_thought", "text": "PRIVATE-REASON-2"},
        {"type": "tool.result", "data": {"scratchpad": "PRIVATE-REASON-3"}},
        {"type": "tool.result", "reasoning": "PRIVATE-REASON-4", "output": "DROP-WHOLE-ROW"},
        {"type": "tool.result", "data": '{"type":"reasoning","text":"PRIVATE-REASON-5"}'},
        {"type": "agent.io.stream", "line": "PRIVATE-RAW-PROVIDER"},
        {"type": "role.session.turn", "text": "PRIVATE-MANAGER"},
    ])
    write_rows(root / "backlog.jsonl", [
        {"id": "task-1", "title": "Fit fixture", "objective": "Use fixture inputs",
         "status": "done", "finished_ts": 20, "tags": ["physics"]},
    ])
    (root / "daemon.status.json").write_text(json.dumps({"status": "idle", "ts": 30}))
    (root / "agent_io.jsonl").write_text("PRIVATE-PROVIDER-CREDENTIALS")
    exported = analytics.export_trace("trial-01", "s-fixture")
    for secret in secrets + ["hidden-auth", "hidden-cookie", "hidden-key",
                             "PRIVATE-", "DROP-WHOLE-ROW"]:
        assert secret not in exported
    assert "[REDACTED]" in exported or "<REDACTED:" in exported
    for observed in ("Observable stdout", "Delivered a result", "figure.pdf", "cost_usd"):
        assert observed in exported
    trace = json.loads(exported)
    assert trace["sources"]["events.jsonl"]["suppressed_rows"] == 7
    assert trace["policy"]["operator_review_required_before_training_or_publication"] is True
    assert trace["policy"]["redaction"] == "best-effort"
    assert not trace["truncated"]
    assert trace["project"]["title"] == "A research task"
    # Nothing from trace is copied to the operator index.
    with closing(sqlite3.connect(analytics.path)) as db:
        dump = "\n".join(db.iterdump())
    assert "Observable stdout" not in dump
    assert "fixture inputs" not in dump


@pytest.mark.parametrize("sid", ["../s-other", "/s-other", "a/b", ".", "..", "a\\b", "%2e%2e"])
def test_trace_rejects_sid_traversal(setup, sid):
    analytics, _, _ = setup
    consent(analytics)
    with pytest.raises(AnalyticsError) as exc:
        analytics.trace("trial-01", sid)
    assert exc.value.status == 400


def test_missing_project_and_invalid_metadata_are_explicit_errors(setup):
    analytics, _, _ = setup
    root = project(setup)
    with pytest.raises(AnalyticsError) as exc:
        analytics.trace("trial-01", "s-missing")
    assert exc.value.status == 404
    (root.parent / "s-empty").mkdir()
    with pytest.raises(AnalyticsError) as exc:
        analytics.trace("trial-01", "s-empty")
    assert exc.value.status == 404
    (root / "session.json").write_text('{"id":"s-someone-else"}')
    with pytest.raises(AnalyticsError) as exc:
        analytics.trace("trial-01", "s-fixture")
    assert exc.value.status == 422
    assert analytics.projects("trial-01")["skipped"] == 2


@pytest.mark.parametrize("component", ["session", "file", "home", "hardlink", "fifo"])
def test_trace_denies_symlinks_intermediate_links_and_special_files(setup, component):
    analytics, _, base = setup
    root = project(setup)
    other = project(setup, "trial-03", "s-other")
    (other / "events.jsonl").write_text('{"text":"OTHER-TENANT"}\n')
    if component == "session":
        (root.parent / "s-link").symlink_to(other, target_is_directory=True)
        sid = "s-link"
    elif component == "file":
        (root / "events.jsonl").symlink_to(other / "events.jsonl")
        sid = "s-fixture"
    elif component == "hardlink":
        os.link(other / "events.jsonl", root / "events.jsonl")
        sid = "s-fixture"
    elif component == "fifo":
        os.mkfifo(root / "events.jsonl")
        sid = "s-fixture"
    else:
        home = analytics.tenants["trial-01"]["data_dir"] / "home"
        moved = base / "moved-home"
        home.rename(moved)
        home.symlink_to(moved, target_is_directory=True)
        sid = "s-fixture"
    with pytest.raises(AnalyticsError) as exc:
        analytics.trace("trial-01", sid)
    assert exc.value.code == "unsafe_path"


def test_projects_bounded_metadata_and_legacy_trace(setup, monkeypatch):
    analytics, _, _ = setup
    project(setup, sid="s-first")
    project(setup, sid="s-second")
    legacy = project(setup, sid="legacy", metadata=False)
    write_rows(legacy / "transcript.jsonl", [{"role": "operator", "text": "legacy input"}])
    (legacy.parent / "s-alias").symlink_to(legacy, target_is_directory=True)
    result = analytics.projects("trial-01")
    assert {row["id"] for row in result["projects"]} == {"s-first", "s-second"}
    assert result["skipped"] == 2
    assert analytics.trace("trial-01", "legacy")["project"] is None
    monkeypatch.setattr("argus_skill.trial.analytics.MAX_PROJECTS", 1)
    assert analytics.projects("trial-01")["truncated"]


def test_export_byte_row_limits_invalid_records_and_missing_sources_are_visible(setup, monkeypatch):
    analytics, _, _ = setup
    root = project(setup)
    write_rows(root / "events.jsonl", [{"type": "tool.result", "text": f"result-{i}"} for i in range(8)])
    limited = json.loads(analytics.export_trace("trial-01", "s-fixture", limit=2))
    assert len(limited["rows"]) == 2
    assert limited["truncated"]
    assert limited["sources"]["transcript.jsonl"]["state"] == "missing"
    monkeypatch.setattr("argus_skill.trial.analytics.MAX_FILE_BYTES", 70)
    byte_limited = analytics.trace("trial-01", "s-fixture")
    assert byte_limited["sources"]["events.jsonl"]["truncated"]
    assert len(byte_limited["rows"]) == 1
    (root / "events.jsonl").write_text('not-json\n{"type":"tool.result","text":"OK"}\n')
    bad = analytics.trace("trial-01", "s-fixture")
    assert bad["sources"]["events.jsonl"]["invalid_rows"] == 1
    assert bad["rows"][0]["data"]["text"] == "OK"
    with pytest.raises(ValueError):
        analytics.trace("trial-01", "s-fixture", limit=5001)


def test_retention_prunes_only_owned_events_and_never_reuses_ids(setup):
    analytics, now, _ = setup
    root = project(setup)
    original = (root / "session.json").read_bytes()
    first = analytics.record_request("trial-01", "GET", "/", 200, 1)
    now[0] += 30 * 86400
    assert analytics.prune() == 0
    now[0] += 1
    assert analytics.prune() == 1
    assert analytics.consented("trial-01", analytics.notice_version)
    assert (root / "session.json").read_bytes() == original
    second = analytics.record_request("trial-01", "GET", "/", 200, 1)
    assert second > first
    now[0] += 31 * 86400
    analytics.dashboard()
    with closing(sqlite3.connect(analytics.path)) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def test_bad_configuration_and_request_values_are_rejected(setup):
    analytics, _, base = setup
    with pytest.raises(ValueError, match="separate"):
        Analytics(base / "customer/index", analytics.tenants, analytics.trial_db, analytics.compute_db)
    consent(analytics)
    with pytest.raises(ValueError):
        analytics.record_request("trial-01", "POST", "/", 200, float("nan"))
    with pytest.raises(ValueError):
        analytics.record_request("trial-01", "SECRET-AS-METHOD", "/", 200, 1)
    with pytest.raises(ValueError):
        analytics.dashboard(days=31)


def test_secret_session_name_and_nonfinite_untrusted_numbers_are_safe_on_export(setup):
    analytics, _, _ = setup
    sid = "argus_trial_" + "f" * 64
    root = project(setup, sid=sid)
    (root / "session.json").write_text(json.dumps({
        "id": sid, "display_name": "https://private-user:private-pass@example.invalid",
    }))
    (root / "events.jsonl").write_text('{"type":"usage.recorded","cost_usd":1e999}\n')
    output = analytics.export_trace("trial-01", sid)
    assert sid not in output
    assert "private-user" not in output
    assert "private-pass" not in output
    assert json.loads(output)["rows"][0]["data"]["cost_usd"] is None
