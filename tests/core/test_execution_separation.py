"""Execution review unit: synthetic state and real backend with fake transport."""
import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import test_agent_cli_backend as fixture_support

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.core.dispatch_admission import AccountingAdmission
from argus.core.dispatch_safety import (
    DispatchSafetyError,
    assert_project_dispatch,
    provider_dispatch_guard,
    quiesce_project,
    safety_snapshot,
)
from argus.core.models import RunnerOptions
from argus.life.memory import Backlog, BacklogItem, IllegalStateTransition
from argus.webapi.server import create_app


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "projects" / "synthetic-project"
    p.mkdir(parents=True)
    return p

@pytest.fixture
def fake_agent_cli(monkeypatch):
    fixture_support.fake_agent_cli.__wrapped__(monkeypatch)

def test_quiesce_reports_inflight_not_stopped_and_no_next_provider_round(tmp_path, project):
    entered, finish = threading.Event(), threading.Event()
    def inflight():
        with provider_dispatch_guard(project):
            entered.set()
            finish.wait(3)
    thread = threading.Thread(target=inflight)
    thread.start()
    assert entered.wait(2)
    try:
        fenced = quiesce_project(root=tmp_path, project=project, expected_epoch=0, reason="stop dispatch")
        assert not fenced["quiescent"] and not fenced["daemon_stopped"]
        with pytest.raises(DispatchSafetyError):
            with provider_dispatch_guard(project):
                pytest.fail("new provider work")
    finally:
        finish.set()
        thread.join(3)
    assert safety_snapshot(project)["paused"]


def test_serial_successor_cannot_duplicate_retained_origin_even_with_parallel_override(tmp_path):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    origin = BacklogItem.new(title="Original real work", objective="Retained work")
    origin.status = "running"
    origin.running_owner = "primary"
    origin.attempt = 2
    successor = BacklogItem.new(title="Differently worded continuation", objective="Do not duplicate")
    successor.node_key = origin.id
    backlog._save([origin, successor])
    before = backlog.path.read_bytes()
    assert backlog.next_pending() is None
    assert backlog.claim_next(owner="successor") is None
    assert backlog.claim_next(owner="successor", respect_running=False) is None
    assert backlog.path.read_bytes() == before
    assert [(r.id, r.attempt, r.running_owner) for r in backlog.active() if r.status == "running"] == [(origin.id, 2, "primary")]


def test_real_wait_wake_and_1801_second_expiry_cannot_clear_dispatch_fence(tmp_path, project):

    from argus.life.supervisor import LifeSupervisor
    quiesce_project(root=tmp_path, project=project, expected_epoch=0, reason="reviewed repair required")
    supervisor = object.__new__(LifeSupervisor)
    supervisor.config = SimpleNamespace(continuous_objective="unchanged")
    supervisor._project_workdir = lambda: project
    watched = project / "campaign-state.json"
    watched.write_text('{"observation":1,"integrity":"unrepaired"}')
    revision = supervisor._planner_waiting_observed_revision(wake_on=["artifact_revision"], watched_paths=[watched.name])
    wait = {"active": True, "wait_mode": "event", "expires_at": 0,
            "observed_revision": revision, "wake_on": ["artifact_revision"], "watched_paths": [watched.name],
            "recheck_condition": "independently reviewed repair", "operator_action_required": False,
            "allow_verification_probe": False}
    supervisor._load_planner_waiting_contract_state = lambda: wait
    supervisor._write_planner_waiting_contract_state = lambda state: True
    supervisor._emit = lambda event: None
    supervisor._reset_idle_backoff = lambda: None
    watched.write_text('{"observation":2,"integrity":"unrepaired"}')
    supervisor._planner_event_wait_outcome()
    assert not wait["active"]  # ordinary scheduling hint CAN wake, not authorize
    with pytest.raises(DispatchSafetyError):
        assert_project_dispatch(project)
    supervisor._planner_visible_input_signature = lambda **kw: "unchanged"
    supervisor._planner_unchanged_skip_signature = "unchanged"
    supervisor._planner_unchanged_skip_armed_at = 0
    with patch("argus.life.supervisor._planning_context.time.monotonic", return_value=1801):
        supervisor._maybe_skip_unchanged_planner_cycle(SimpleNamespace(operator_context_revision=0, inbox_delivery_messages=()))
    with pytest.raises(DispatchSafetyError):
        assert_project_dispatch(project)
    assert safety_snapshot(project)["epoch"] == 1


@pytest.mark.parametrize("run_label", ["planner.cycle1", "engineer-r1", "reviewer-r1",
                                      "manager-frontdoor-classify", "manager-stage", "external-job"])
@pytest.mark.parametrize("cost_enabled", ["0", "1"])
def test_all_role_labels_honor_durable_quiesce_without_provider_spawn(tmp_path, monkeypatch, run_label, cost_enabled, fake_agent_cli):
    from argus.core.dispatch_safety import quiesce_project
    root = tmp_path / "root"
    project = root / "projects" / "synthetic-project"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", cost_enabled)
    backend = AgentCliBackend(backend="codex")
    backend.set_usage_context(project_root=project, global_root=root, mission_id="synthetic")
    calls = []
    monkeypatch.setattr(backend._runner.__class__, "run_exec", lambda *a, **kw: calls.append(1), raising=False)
    quiesce_project(root=root, project=project, expected_epoch=0, reason="accounting integrity")
    (project / "campaign-state.json").write_text('{"observation":"new"}')
    result = backend.run_exec(prompt="same stale objective", options=RunnerOptions(model="gpt-5.6-sol"), run_label=run_label)
    assert result.exit_code != 0 and not calls
    assert not (root / "cost-control.json").exists()


def test_pause_between_reservation_and_spawn_fences_actual_backend(tmp_path, monkeypatch, fake_agent_cli):
    from argus.core.dispatch_safety import quiesce_project
    root = tmp_path / "root"
    project = root / "projects" / "synthetic-project"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "1")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_GUARD", "0")
    backend = AgentCliBackend(backend="codex")
    backend.set_usage_context(project_root=project, global_root=root, mission_id="synthetic")
    translate = backend._translate_options
    def pause_then_translate(options):
        quiesce_project(root=root, project=project, expected_epoch=0, reason="concurrent operator pause")
        return translate(options)
    monkeypatch.setattr(backend, "_translate_options", pause_then_translate)
    calls = []
    monkeypatch.setattr(backend._runner.__class__, "run_exec", lambda *a, **kw: calls.append(1), raising=False)
    result = backend.run_exec(prompt="synthetic", options=RunnerOptions(model="gpt-5.6-sol"), run_label="planner")
    assert result.exit_code != 0 and not calls
    # This reservation is affirmatively never started, unlike interrupted work.
    assert json.loads((root / "cost-control.json").read_text())["reservations"] == []


def test_control_api_scope_auth_and_strict_epoch(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from argus.core.session import SessionMeta, write_session_meta
    for name in ('synthetic-a', 'synthetic-b'):
        write_session_meta(tmp_path, SessionMeta(id=name, created=1, last_active=1))
    header = {'Authorization':'Bearer synthetic-only-token'}
    path = '/api/projects/synthetic-a/dispatch-safety'
    with TestClient(create_app(global_root=tmp_path, auth_token='synthetic-only-token')) as client:
        assert client.get(path).status_code == 401
        for epoch in (True, '0', 0.5, -1):
            assert client.post(path+'/quiesce', headers=header, json={'expected_epoch':epoch,'reason':'hold'}).status_code == 422
        assert client.post(path+'/quiesce', headers=header, json={'expected_epoch':0,'reason':'hold','project_root':'/tmp/other'}).status_code == 422
        assert client.post('/api/projects/missing/dispatch-safety/quiesce', headers=header, json={'expected_epoch':0,'reason':'hold'}).status_code == 404
        assert client.post(path+'/quiesce', headers=header, json={'expected_epoch':0,'reason':'hold'}).status_code == 200
        assert client.post(path+'/quiesce', headers=header, json={'expected_epoch':0,'reason':'hold'}).status_code == 409
        assert client.get('/api/projects/synthetic-b/dispatch-safety', headers=header).json()['paused'] is False


def test_quiesce_has_no_accounting_dependency_even_with_damaged_ledger_and_cost_lock(tmp_path, project):
    from argus.core.cost_control import _locked
    ledger = project / "usage.jsonl"
    state = tmp_path / "cost-control.json"
    ledger.write_bytes(b'ENOSPC truncated historical evidence')
    state.write_bytes(b'corrupt cost state')
    before = {p: p.read_bytes() for p in (ledger, state)}
    with _locked(tmp_path):
        result = quiesce_project(root=tmp_path, project=project, expected_epoch=0, reason="explicit execution pause")
    assert result["paused"] and not result["accounting_settled"]
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert not (tmp_path / "cost-finalizers").exists()
    # Reopen via the persisted reader; no in-memory latch is needed.
    assert safety_snapshot(project)["epoch"] == 1
    with pytest.raises(DispatchSafetyError):
        assert_project_dispatch(project)


@pytest.mark.parametrize("mode", ["denied", "exception", "late-denied", "allowed-error"])
def test_small_admission_contract_never_grants_work_success(tmp_path, project, monkeypatch, fake_agent_cli, mode):
    from argus.adapters.agent_cli_backend import _accounting_admission as adapter
    calls, admissions, failures = [], [], []
    def recheck():
        if mode == "late-denied":
            raise RuntimeError("synthetic accounting evidence changed")
    def admission(ctx, model):
        admissions.append(ctx.call_id)
        if mode == "exception":
            raise RuntimeError("synthetic unreadable accounting")
        return AccountingAdmission(allowed=mode != "denied", reason="synthetic admission denial" if mode == "denied" else "",
            before_dispatch=recheck, execution_failed=failures.append, report_budget_events=False)
    monkeypatch.setattr(adapter, "accounting_admission", admission)
    backend = AgentCliBackend(backend="codex")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_GUARD", "0")
    backend.set_usage_context(project_root=project, global_root=tmp_path, mission_id="synthetic")
    def provider(*args, **kwargs):
        calls.append(1)
        raise OSError(28, "original ENOSPC execution failure")
    monkeypatch.setattr(type(backend._runner), "run_exec", provider)
    result = backend.run_exec(prompt="synthetic", options=RunnerOptions(model="gpt-5.6-sol"), run_label="engineer")
    assert len(admissions) == 1
    assert result.exit_code != 0
    assert len(calls) == (1 if mode == "allowed-error" else 0)
    if mode == "allowed-error":
        assert "ENOSPC" in result.fatal_error
    assert not (tmp_path / "cost-control.json").exists()
    assert not (tmp_path / "cost-finalizers").exists()


def test_accounting_persistence_error_keeps_original_execution_error(tmp_path, project, monkeypatch, fake_agent_cli):
    from argus.adapters.agent_cli_backend import _accounting_admission as adapter
    from argus.core.usage import UsageLedger
    monkeypatch.setattr(adapter, "accounting_admission", lambda ctx, model:
        AccountingAdmission(allowed=True, report_budget_events=False,
            before_dispatch=lambda: None, execution_failed=lambda reason: None))
    backend = AgentCliBackend(backend="codex")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_GUARD", "0")
    backend.set_usage_context(project_root=project, global_root=tmp_path, mission_id="synthetic")
    def provider(*args, **kwargs):
        raise OSError(28, "original ENOSPC execution failure")
    monkeypatch.setattr(type(backend._runner), "run_exec", provider)
    monkeypatch.setattr(UsageLedger, "append", lambda *a: (_ for _ in ()).throw(OSError(28, "separate ledger persistence failure")))
    result = backend.run_exec(prompt="synthetic", options=RunnerOptions(model="gpt-5.6-sol"), run_label="engineer")
    assert result.exit_code == -1
    assert "ENOSPC" in result.fatal_error
    assert "usage persistence failed" in result.fatal_error


@pytest.mark.parametrize("origin_status", ["running", "paused_external_work"])
def test_retry_requires_explicit_origin_transition_not_accounting_acceptance(tmp_path, origin_status):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    origin = BacklogItem.new(title="Original execution", objective="Retain original error and attempt")
    origin.status, origin.attempt = origin_status, 2
    origin.running_owner = "primary"
    origin.last_error = "ENOSPC execution error"
    successor = BacklogItem.new(title="Explicit successor", objective="Retry only after origin terminal transition")
    successor.node_key = origin.id
    backlog._save([origin, successor])
    assert AccountingAdmission(allowed=True).allowed
    assert backlog.claim_next(owner="retry", respect_running=False) is None
    assert backlog.mark_failed(origin.id, error="ENOSPC execution error").status == "failed"
    with pytest.raises(IllegalStateTransition):
        backlog.update(origin.id, status="pending")
    claimed = backlog.claim_next(owner="retry", expected_id=successor.id)
    assert claimed.id == successor.id and claimed.status == "running"
    retained = next(row for row in backlog.history() if row.id == origin.id)
    assert retained.status == "failed" and retained.attempt == 2
    assert retained.last_error == "ENOSPC execution error"


def test_regression_empty_existing_fence_cannot_clear_sticky_pause(tmp_path, project):
    quiesce_project(root=tmp_path, project=project, expected_epoch=0, reason='synthetic pause')
    (project / 'dispatch-safety.json').write_text('{}\n')
    with pytest.raises(DispatchSafetyError):
        assert_project_dispatch(project)



@pytest.mark.parametrize('change', [{'epoch': True}, {'epoch': -1}, {'epoch': 1.0}, {'epoch': '1'},
    {'audit': []}, {'audit': {}}, {'reason': 'different'}, {'version': True}, {'paused': False}])
def test_fence_epoch_and_audit_schema(tmp_path, project, change):
    assert safety_snapshot(project)['paused'] is False
    quiesce_project(root=tmp_path, project=project, expected_epoch=0, reason='synthetic')
    path = project / 'dispatch-safety.json'
    state = json.loads(path.read_text())
    path.write_text(json.dumps({**state, **change}))
    before = path.read_bytes()
    with pytest.raises(DispatchSafetyError):
        assert_project_dispatch(project)
    assert path.read_bytes() == before



@pytest.mark.parametrize('change', [{'at':None}, {'at':True}, {'at':-1}, {'owner_pid':False},
                                    {'from_epoch':False}, {'epoch':True}, {'reason':''}])
def test_fence_audit_typed_fields(tmp_path, project, change):
    quiesce_project(root=tmp_path, project=project, expected_epoch=0, reason='pause')
    path = project / 'dispatch-safety.json'
    data = json.loads(path.read_text())
    data['audit'][0].update(change)
    path.write_text(json.dumps(data))
    with pytest.raises(DispatchSafetyError):
        assert_project_dispatch(project)
