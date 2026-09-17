"""Failure provenance through the real supervision entry, with an offline runner."""
from __future__ import annotations

import json
import socket
from concurrent.futures import CancelledError

import pytest

from argus.adapters.agent_cli_backend import _raw_backend_stop_kind
from argus.core.event_catalog import EventType
from argus.core.models import RunnerResult
from argus.daemon.state import read_continuous_state, write_continuous_config
from argus.life.memory import Backlog, BacklogItem
from argus.manager import Manager, supervision
from argus.manager.directive import load_active_manager_directive
from argus.manager.observation import control_identity

CALL_ID = "offline-supervision-call"
SECRET = "ghp_" + "a" * 36
PROMPT_MARKER = "PRIVATE_PROVIDER_PROMPT_MUST_NOT_ESCAPE"
BODY_MARKER = "PRIVATE_PROVIDER_BODY_MUST_NOT_ESCAPE"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Supervision regression tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


class Backend:
    def __init__(self, outcome, before_return=None):
        self.outcome = outcome
        self.before_return = before_return
        self.calls = []

    def fork(self):
        return self

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        assert run_label == "manager-supervision"
        assert options.disable_tools and options.force_safe_mode and options.sandbox_mode == "read-only"
        self.calls.append((run_label, resume_thread_id))
        if self.before_return:
            self.before_return()
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def project(root):
    write_continuous_config(root, enabled=True, objective="Validate separate grouped means")
    item = BacklogItem.new(item_id="grouped-check", title="Check grouping", objective="Preserve missing-value exclusions")
    item.acceptance_check = "Compute each group separately and exclude missing values"
    Backlog(root / "backlog.jsonl").add(item)
    return {"type": EventType.LIFE_MISSION_COMPLETED, "item_id": item.id}


def controls(root):
    return control_identity(root), (root / "backlog.jsonl").read_bytes()


def reply(*, action="continue", reason="The observed grouping checks passed; continue the planned validation.", refs=None):
    return RunnerResult(exit_code=0, call_id=CALL_ID, thread_id="offline-persistent-manager", agent_messages=[json.dumps({
        "action": action, "reason": reason,
        "directive": "Repair grouping while preserving the missing-value acceptance check." if action == "steer" else "",
        "evidence_refs": ["backlog.jsonl"] if refs is None else refs,
    })])


def failed_event(root, record):
    events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
    matching = [event for event in events if event["type"] == EventType.LIFE_MANAGER_SUPERVISION_FAILED
                and event.get("supervision_id") == record["id"]]
    assert matching
    return matching[-1]


def assert_failure(root, record, *, stage, code=None, stop_kind=None, exit_code=None, call_id=None):
    event = failed_event(root, record)
    assert record["failure_stage"] == event["failure_stage"] == stage
    assert event["status"] == record["status"]
    assert event["reason"] == event["summary"] == record["failure_reason"]
    assert record["failure_reason"]
    error_type = record.get("error_type") or record.get("error")
    assert isinstance(error_type, str) and error_type.isidentifier()
    assert event["error_type"] == error_type
    for key, expected in (("error_code", code), ("stop_kind", stop_kind)):
        if expected is not None:
            assert record[key] == event[key] == expected
        elif key == "error_code":
            assert not record.get(key) and not event.get(key)
    if exit_code is not None:
        assert type(record["backend_exit_code"]) is int
        assert record["backend_exit_code"] == event["backend_exit_code"] == exit_code
    if call_id is not None:
        assert record["call_id"] == event["call_id"] == call_id
    persisted = [json.loads(path.read_text()) for path in (root / "manager-supervision").glob("*.json")]
    text = json.dumps([record, event, persisted])
    assert all(marker not in text for marker in (SECRET, PROMPT_MARKER, BODY_MARKER))
    return event


@pytest.mark.parametrize("case", ["quota", "quota-with-private-fields", "quota-in-stderr", "local-budget"])
def test_budget_failures_keep_provider_provenance_without_exposing_raw_details(tmp_path, case):
    event = project(tmp_path)
    before = controls(tmp_path)
    if case.startswith("quota"):
        body = {"code": "trial_quota_exceeded", "message": "Insufficient trial tokens for this request."}
        if case in {"quota-with-private-fields", "quota-in-stderr"}:
            body.update(secret=SECRET, prompt=PROMPT_MARKER, raw_body=BODY_MARKER)
        fatal = "402: " + json.dumps(body)
        kind = _raw_backend_stop_kind(fatal_error=fatal, exit_code=1)
        assert kind == "provider_fence"
        code = "trial_quota_exceeded"
    else:
        fatal = "Global daily budget exhausted: " + SECRET + " " + PROMPT_MARKER
        kind, code = "budget_exhausted", None
    if case == "quota-in-stderr":
        outcome = RunnerResult(exit_code=1, stderr_lines=[fatal], call_id=CALL_ID)
    else:
        outcome = RunnerResult(exit_code=1, fatal_error=fatal, stop_kind=kind, call_id=CALL_ID)
    backend = Backend(outcome)
    record = supervision.supervise(Manager(tmp_path, runner=backend, memory_maintenance_enabled=False), tmp_path, event)
    assert record["status"] == "failed" and not record.get("decision") and not record.get("effects")
    assert_failure(tmp_path, record, stage="provider", code=code, stop_kind=kind, exit_code=1, call_id=CALL_ID)
    assert controls(tmp_path) == before
    assert load_active_manager_directive(tmp_path) is None and not (tmp_path / "inbox.jsonl").exists()
    assert len(backend.calls) == 1


@pytest.mark.parametrize("origin", ["provider-exception", "trusted-timeout", "host-deadline"])
def test_timeouts_are_provider_failures_without_applying_a_late_decision(tmp_path, monkeypatch, origin):
    event = project(tmp_path)
    before = controls(tmp_path)
    now = [100.0]
    if origin == "host-deadline":
        monkeypatch.setattr(supervision.time, "monotonic", lambda: now[0])
        backend = Backend(reply(), before_return=lambda: now.__setitem__(0, 131.0))
    elif origin == "trusted-timeout":
        backend = Backend(RunnerResult(exit_code=1, stop_kind="transient_error", call_id=CALL_ID,
                                       fatal_error="HTTP 504 Gateway Timeout: " + SECRET + " " + PROMPT_MARKER))
    else:
        backend = Backend(TimeoutError("Provider timed out: " + SECRET + " " + PROMPT_MARKER))
    record = supervision.supervise(Manager(tmp_path, runner=backend, memory_maintenance_enabled=False), tmp_path, event)
    assert record["status"] == ("superseded" if origin == "host-deadline" else "failed")
    assert not record.get("decision")
    assert_failure(tmp_path, record, stage="provider", code="timeout", stop_kind="transient_error",
                   exit_code=0 if origin == "host-deadline" else 1 if origin == "trusted-timeout" else None,
                   call_id=CALL_ID if origin != "provider-exception" else None)
    assert controls(tmp_path) == before and len(backend.calls) == 1


@pytest.mark.parametrize("origin", ["callback", "foreground-yield", "operator_abort", "operator_pause", "daemon_shutdown", "external-prefix"])
def test_external_cancellation_has_its_own_code_and_preserves_prior_control(tmp_path, monkeypatch, origin):
    event = project(tmp_path)
    before = controls(tmp_path)
    cancelled = [False]
    if origin == "foreground-yield":
        monkeypatch.setattr(supervision, "manager_session_yield_reason",
                            lambda root: "Interactive Manager request is waiting" if cancelled[0] else None)
    canonical = origin not in {"callback", "foreground-yield"}
    kind = "operator_abort" if origin == "external-prefix" else origin
    fatal = "External interrupt: operator abort requested: " if origin == "external-prefix" else "Trusted runner cancellation: "
    outcome = RunnerResult(exit_code=130, fatal_error=fatal + PROMPT_MARKER,
                           stop_kind=None if origin == "external-prefix" else kind, call_id=CALL_ID) if canonical else reply()
    backend = Backend(outcome, before_return=None if canonical else lambda: cancelled.__setitem__(0, True))
    record = supervision.supervise(Manager(tmp_path, runner=backend, memory_maintenance_enabled=False), tmp_path, event,
                                   cancelled=lambda: origin == "callback" and cancelled[0])
    assert record["status"] == ("failed" if canonical else "superseded")
    assert not record.get("decision")
    assert_failure(tmp_path, record, stage="provider", code="superseded" if origin == "foreground-yield" else "cancelled",
                   stop_kind=kind if canonical else None,
                   exit_code=130 if canonical else 0, call_id=CALL_ID)
    assert controls(tmp_path) == before and len(backend.calls) == 1


def test_goal_supersession_is_distinct_from_provider_failure_and_keeps_the_new_goal(tmp_path):
    event = project(tmp_path)
    updated = []

    def replace_goal():
        write_continuous_config(tmp_path, enabled=True, objective="New operator objective")
        updated.append(controls(tmp_path))

    backend = Backend(reply(action="steer"), before_return=replace_goal)
    record = supervision.supervise(Manager(tmp_path, runner=backend, memory_maintenance_enabled=False), tmp_path, event)
    assert record["status"] == "superseded" and not record.get("decision")
    assert_failure(tmp_path, record, stage="provider", code="superseded", exit_code=0, call_id=CALL_ID)
    assert controls(tmp_path) == updated[0]
    assert read_continuous_state(tmp_path).objective == "New operator objective"
    assert load_active_manager_directive(tmp_path) is None and len(backend.calls) == 1


@pytest.mark.parametrize("kind", [None, "not-a-canonical-stop-kind"])
def test_unclassified_provider_failure_does_not_invent_a_stop_kind_or_code(tmp_path, kind):
    event = project(tmp_path)
    before = controls(tmp_path)
    backend = Backend(RunnerResult(exit_code=1, call_id=CALL_ID, stop_kind=kind,
                                   fatal_error="Unstructured runner failure: " + SECRET + " " + PROMPT_MARKER))
    record = supervision.supervise(Manager(tmp_path, runner=backend, memory_maintenance_enabled=False), tmp_path, event)
    assert record["status"] == "failed" and not record.get("decision")
    emitted = assert_failure(tmp_path, record, stage="provider", exit_code=1, call_id=CALL_ID)
    assert not record.get("stop_kind") and not emitted.get("stop_kind")
    assert controls(tmp_path) == before and len(backend.calls) == 1


@pytest.mark.parametrize("invalid", ["action", "unobserved-reference"])
def test_invalid_decisions_have_decision_stage_and_no_control_effects(tmp_path, invalid):
    event = project(tmp_path)
    before = controls(tmp_path)
    outcome = reply(action="erase-project") if invalid == "action" else reply(action="steer", refs=["unobserved/private.json"])
    backend = Backend(outcome)
    record = supervision.supervise(Manager(tmp_path, runner=backend, memory_maintenance_enabled=False), tmp_path, event)
    assert record["status"] == "failed" and not record.get("decision") and not record.get("effects") and not record["cited_refs"]
    assert_failure(tmp_path, record, stage="decision", exit_code=0, call_id=CALL_ID)
    assert controls(tmp_path) == before and load_active_manager_directive(tmp_path) is None
    assert len(backend.calls) == 1


@pytest.mark.parametrize("failure_class", [OSError, CancelledError])
def test_delivery_error_keeps_the_issued_decision_and_replays_without_another_model_call(tmp_path, monkeypatch, failure_class):
    from argus.manager import directive

    event = project(tmp_path)
    backend = Backend(reply(action="steer"))
    manager = Manager(tmp_path, runner=backend, memory_maintenance_enabled=False)
    original = directive.set_active_manager_directive

    def unavailable(*args, **kwargs):
        raise failure_class("Directive delivery stopped: " + SECRET + " " + PROMPT_MARKER)

    with monkeypatch.context() as fault:
        fault.setattr(directive, "set_active_manager_directive", unavailable)
        issued = supervision.supervise(manager, tmp_path, event)
    assert issued["status"] == "issued" and issued["decision"]["action"] == "steer"
    assert_failure(tmp_path, issued, stage="commit", code="cancelled" if failure_class is CancelledError else None,
                   exit_code=0, call_id=CALL_ID)
    if failure_class is CancelledError:
        reason = issued["failure_reason"].lower()
        assert "cancelled" in reason and "recover" in reason and "superseded" not in reason
    assert load_active_manager_directive(tmp_path) is None
    assert directive.set_active_manager_directive is original
    recovered = supervision.supervise(Manager(tmp_path, runner=backend, memory_maintenance_enabled=False), tmp_path, event)
    assert recovered["status"] == "applied" and recovered["decision"] == issued["decision"]
    assert recovered["effects"]["directive_delivered"] and len(backend.calls) == 1
    assert load_active_manager_directive(tmp_path).text == issued["decision"]["directive"]
    assert all(not recovered.get(field) for field in ("failure_stage", "error_code", "error", "error_type", "failure_reason"))
    assert read_continuous_state(tmp_path).objective == "Validate separate grouped means"
    assert not (tmp_path / "inbox.jsonl").exists()


def test_commit_deadline_keeps_the_existing_superseded_fence(tmp_path, monkeypatch):
    event = project(tmp_path)
    before = controls(tmp_path)
    now = [100.0]
    monkeypatch.setattr(supervision.time, "monotonic", lambda: now[0])
    original = supervision._apply

    def expire_before_delivery(*args, **kwargs):
        now[0] = 131.0
        return original(*args, **kwargs)

    monkeypatch.setattr(supervision, "_apply", expire_before_delivery)
    backend = Backend(reply(action="steer"))
    manager = Manager(tmp_path, runner=backend, memory_maintenance_enabled=False)
    record = supervision.supervise(manager, tmp_path, event)
    assert record["status"] == "superseded" and record["decision"]["action"] == "steer"
    assert_failure(tmp_path, record, stage="commit", code="timeout", exit_code=0, call_id=CALL_ID)
    assert not supervision.recover_issued_supervision(manager, tmp_path)
    assert controls(tmp_path) == before and load_active_manager_directive(tmp_path) is None
    assert len(backend.calls) == 1


def test_successful_model_prose_with_error_literals_is_not_provider_failure_metadata(tmp_path):
    event = project(tmp_path)
    reason = "The observed checks include HTTP 402, trial_quota_exceeded, timeout and cancelled examples; all checks passed."
    outcome = reply(reason=reason)
    outcome.stderr_lines = ["Recovered warning: HTTP 402 trial_quota_exceeded timeout cancelled", SECRET, PROMPT_MARKER]
    backend = Backend(outcome)
    record = supervision.supervise(Manager(tmp_path, runner=backend, memory_maintenance_enabled=False), tmp_path, event)
    assert record["status"] == "applied" and record["decision"]["reason"] == reason
    assert all(not record.get(field) for field in ("failure_stage", "stop_kind", "error_code", "error", "error_type"))
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert not any(row["type"] == EventType.LIFE_MANAGER_SUPERVISION_FAILED for row in events)
    assert all(marker not in json.dumps([record, events]) for marker in (SECRET, PROMPT_MARKER))
    assert len(backend.calls) == 1
