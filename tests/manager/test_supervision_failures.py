"""Failure provenance through the real supervision entry, with an offline runner."""
from __future__ import annotations

import json
import socket
from concurrent.futures import CancelledError

import pytest

from argus.adapters.agent_cli_backend import _raw_backend_stop_kind
from argus.core.event_catalog import EventType
from argus.core.models import RunnerResult
from argus.core.role_actions import PREFIX
from argus.core.role_tool_bridge import bridge_request
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
    """Only the call's own loopback action bridge may be reached."""
    connect = socket.socket.connect

    def loopback_only(self, address):
        if address[0] != "127.0.0.1":
            raise AssertionError("Supervision regression tests must not open network connections")
        return connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)


class Backend:
    """An offline Manager: hands off ``payload`` if given, then ends with ``outcome``.

    A refused handoff is the model giving up after the tool's error.
    """

    def __init__(self, outcome, before_return=None, payload=None):
        self.outcome = outcome
        self.before_return = before_return
        self.payload = payload
        self.calls = []
        self.refusals = []

    def fork(self):
        return self

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        assert run_label == "manager-supervision"
        assert not options.disable_tools and options.force_safe_mode and options.sandbox_mode == "read-only"
        self.calls.append((run_label, resume_thread_id))
        if self.payload:
            try:
                bridge_request(PREFIX, "hand_off", self.payload, env=options.extension_env)
            except ValueError as refusal:
                self.refusals.append(str(refusal))
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


def ok():
    return RunnerResult(exit_code=0, call_id=CALL_ID, thread_id="offline-persistent-manager", agent_messages=["Judged."])


def reply(*, steer=False, summary="The observed grouping checks passed; continue the planned validation.", refs=None):
    return {
        "to": "team", "summary": summary,
        "note": "Repair grouping while preserving the missing-value acceptance check." if steer else "",
        "evidence_refs": ["backlog.jsonl"] if refs is None else refs, "assumptions": [],
    }


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
        body = {"code": "payment_required", "message": "Payment Required"}
        if case in {"quota-with-private-fields", "quota-in-stderr"}:
            body.update(secret=SECRET, prompt=PROMPT_MARKER, raw_body=BODY_MARKER)
        fatal = "HTTP 402: " + json.dumps(body)
        kind = _raw_backend_stop_kind(fatal_error=fatal, exit_code=1)
        assert kind == "provider_fence"
        code = None
    else:
        fatal = "Global daily budget exhausted: " + SECRET + " " + PROMPT_MARKER
        kind, code = "budget_exhausted", None
    if case == "quota-in-stderr":
        outcome = RunnerResult(exit_code=1, stderr_lines=[fatal], call_id=CALL_ID)
    else:
        outcome = RunnerResult(exit_code=1, fatal_error=fatal, stop_kind=kind, call_id=CALL_ID)
    backend = Backend(outcome)
    record = supervision.supervise(Manager(tmp_path, runner=backend), tmp_path, event)
    assert record["status"] == "failed" and not record.get("decision") and not record.get("effects")
    assert_failure(tmp_path, record, stage="provider", code=code, stop_kind=kind, exit_code=1, call_id=CALL_ID)
    assert controls(tmp_path) == before
    assert load_active_manager_directive(tmp_path) is None and not (tmp_path / "inbox.jsonl").exists()
    assert len(backend.calls) == 1


@pytest.mark.parametrize("origin", ["provider-exception", "trusted-timeout"])
def test_timeouts_are_provider_failures_without_applying_a_late_decision(tmp_path, origin):
    event = project(tmp_path)
    before = controls(tmp_path)
    if origin == "trusted-timeout":
        backend = Backend(RunnerResult(exit_code=1, stop_kind="transient_error", call_id=CALL_ID,
                                       fatal_error="HTTP 504 Gateway Timeout: " + SECRET + " " + PROMPT_MARKER))
    else:
        backend = Backend(TimeoutError("Provider timed out: " + SECRET + " " + PROMPT_MARKER))
    record = supervision.supervise(Manager(tmp_path, runner=backend), tmp_path, event)
    assert record["status"] == "failed"
    assert not record.get("decision")
    assert_failure(tmp_path, record, stage="provider", code="timeout", stop_kind="transient_error",
                   exit_code=1 if origin == "trusted-timeout" else None,
                   call_id=CALL_ID if origin != "provider-exception" else None)
    assert controls(tmp_path) == before and len(backend.calls) == 1


@pytest.mark.parametrize("origin", ["callback", "operator_abort", "operator_pause", "daemon_shutdown", "external-prefix"])
def test_external_cancellation_has_its_own_code_and_preserves_prior_control(tmp_path, origin):
    event = project(tmp_path)
    before = controls(tmp_path)
    cancelled = [False]
    canonical = origin != "callback"
    kind = "operator_abort" if origin == "external-prefix" else origin
    fatal = "External interrupt: operator abort requested: " if origin == "external-prefix" else "Trusted runner cancellation: "
    outcome = RunnerResult(exit_code=130, fatal_error=fatal + PROMPT_MARKER,
                           stop_kind=None if origin == "external-prefix" else kind, call_id=CALL_ID) if canonical else ok()
    backend = Backend(outcome, before_return=None if canonical else lambda: cancelled.__setitem__(0, True),
                      payload=None if canonical else reply())
    record = supervision.supervise(Manager(tmp_path, runner=backend), tmp_path, event,
                                   cancelled=lambda: origin == "callback" and cancelled[0])
    assert record["status"] == ("failed" if canonical else "superseded")
    assert not record.get("decision")
    assert_failure(tmp_path, record, stage="provider", code="cancelled",
                   stop_kind=kind if canonical else None,
                   exit_code=130 if canonical else 0, call_id=CALL_ID)
    assert controls(tmp_path) == before and len(backend.calls) == 1


def test_goal_supersession_is_distinct_from_provider_failure_and_keeps_the_new_goal(tmp_path):
    event = project(tmp_path)
    updated = []

    def replace_goal():
        write_continuous_config(tmp_path, enabled=True, objective="New operator objective")
        updated.append(controls(tmp_path))

    backend = Backend(ok(), before_return=replace_goal, payload=reply(steer=True))
    record = supervision.supervise(Manager(tmp_path, runner=backend), tmp_path, event)
    assert record["status"] == "superseded" and not record.get("decision")
    assert_failure(tmp_path, record, stage="provider", code="superseded", exit_code=0, call_id=CALL_ID)
    assert controls(tmp_path) == updated[0]
    assert read_continuous_state(tmp_path).objective == "New operator objective"
    assert load_active_manager_directive(tmp_path) is None and len(backend.calls) == 1


@pytest.mark.parametrize("invalid", [{"to": "erase-project"}, {"evidence_refs": ["unobserved/private.json"]}, None])
def test_a_turn_without_an_accepted_handoff_has_decision_stage_and_no_control_effects(tmp_path, invalid):
    """A refused call goes back to the model; a turn that ends without a good one decides nothing."""
    event = project(tmp_path)
    before = controls(tmp_path)
    backend = Backend(ok(), payload=invalid and {**reply(steer=True), **invalid})
    record = supervision.supervise(Manager(tmp_path, runner=backend), tmp_path, event)
    assert record["status"] == "failed" and not record.get("decision") and not record.get("effects") and not record["cited_refs"]
    emitted = assert_failure(tmp_path, record, stage="decision", code="handoff_missing", exit_code=0, call_id=CALL_ID)
    assert emitted["error_message"] and emitted["error_message"] == record["error_message"]
    assert len(backend.refusals) == (invalid is not None)
    # Only our own check text is persisted, never text the model wrote.
    assert "unobserved/private.json" not in emitted["error_message"]
    assert controls(tmp_path) == before and load_active_manager_directive(tmp_path) is None
    assert len(backend.calls) == 1


def test_provider_failure_event_carries_a_message_without_raw_provider_text(tmp_path):
    event = project(tmp_path)
    backend = Backend(RunnerResult(exit_code=1, call_id=CALL_ID, fatal_error="boom " + SECRET + " " + PROMPT_MARKER))
    record = supervision.supervise(Manager(tmp_path, runner=backend), tmp_path, event)
    emitted = assert_failure(tmp_path, record, stage="provider", exit_code=1, call_id=CALL_ID)
    assert emitted["error_message"] == "RuntimeError"


@pytest.mark.parametrize("failure_class", [OSError, CancelledError])
def test_delivery_error_keeps_the_issued_decision_and_replays_without_another_model_call(tmp_path, monkeypatch, failure_class):
    from argus.manager import directive

    event = project(tmp_path)
    backend = Backend(ok(), payload=reply(steer=True))
    manager = Manager(tmp_path, runner=backend)
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
    recovered = supervision.supervise(Manager(tmp_path, runner=backend), tmp_path, event)
    assert recovered["status"] == "applied" and recovered["decision"] == issued["decision"]
    assert recovered["effects"]["directive_delivered"] and len(backend.calls) == 1
    assert load_active_manager_directive(tmp_path).text == issued["decision"]["directive"]
    assert all(not recovered.get(field) for field in ("failure_stage", "error_code", "error", "error_type", "failure_reason"))
    assert read_continuous_state(tmp_path).objective == "Validate separate grouped means"
    assert not (tmp_path / "inbox.jsonl").exists()
