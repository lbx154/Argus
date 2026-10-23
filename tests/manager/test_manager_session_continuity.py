"""Long-lived Manager identity and durable control continuity."""
from __future__ import annotations

import json
import multiprocessing as mp
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict

import portalocker
import pytest

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.core.models import RunnerOptions, RunnerResult
from argus.core.operator_context import OperatorContextStore, append_directive, append_revoke
from argus.daemon.state import write_continuous_config
from argus.life.memory import Backlog, BacklogItem
from argus.manager._session_ops import ManagerLockCancelled, _ManagerSession
from argus.manager.directive import (
    clear_active_manager_directive,
    set_active_manager_directive,
)
from argus.manager.session_context import SESSION_HANDOFF_HISTORY_BYTES, session_handoff
from argus.manager.session_continuity import (
    SESSION_CONTROL_CAPSULE_BYTES,
    ManagerSessionContinuityUnavailable,
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Continuity regressions cannot call a provider")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(AgentCliBackend, "run_exec", forbidden)


class Backend:
    backend = "pi"

    def __init__(self, *, reported=True, input_tokens=20000, failure=""):
        self.calls = []
        self.reported = reported
        self.input_tokens = input_tokens
        self.failure = failure
        self.results = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        from argus.core.file_lock import _WAIT_BUDGET

        assert _WAIT_BUDGET.get() is None, "Provider execution must not inherit the state-read deadline"
        self.calls.append({"prompt": prompt, "resume": resume_thread_id, "options": options})
        if self.failure == "exception" and len(self.calls) == 5:
            raise RuntimeError("Transport ended after an unknown outcome")
        failed = self.failure == "quota" and len(self.calls) == 5
        result = RunnerResult(
            exit_code=1 if failed else 0,
            thread_id=resume_thread_id or f"provider-{len(self.calls)}",
            agent_messages=[] if failed else ["Decision: keep the independent-group acceptance."],
            fatal_error="402 trial_quota_exceeded" if failed else None,
            input_tokens=self.input_tokens if self.reported and not failed else 0,
            cached_input_tokens=self.input_tokens - 10 if self.reported and not failed else 0,
            output_tokens=25 if self.reported and not failed else 0,
            input_tokens_present=self.reported and not failed,
            cached_input_tokens_present=self.reported and not failed,
            output_tokens_present=self.reported and not failed,
        )
        self.results.append(result)
        return result


def state(root):
    return json.loads((root / ".manager_session.json").read_text())


def turn(root, backend, prompt="Message:\nWhat changed?", *, options=None):
    # Reconstruct the wrapper each turn, as daemon/frontend process restarts do.
    return _ManagerSession(backend, root).run_exec(
        prompt=prompt, options=options or RunnerOptions(model="fixture/model"), run_label="manager-ask",
    )


def new_thread(root, backend, **options):
    """A changed model is the remaining Argus-side reason for a fresh thread."""
    return turn(root, backend, options=RunnerOptions(model="fixture/other", **options))


def capsule(call):
    body = call["prompt"].split("## Current durable Manager continuity\n", 1)[1].split("\n", 1)[1]
    value, _end = json.JSONDecoder().raw_decode(body)
    assert len(json.dumps(value, ensure_ascii=False).encode()) <= SESSION_CONTROL_CAPSULE_BYTES
    return value


def test_manager_resumes_one_provider_thread_and_keeps_its_identity(tmp_path):
    backend = Backend()
    for _ in range(13):
        turn(tmp_path, backend)
    # The backend compacts its own context: no size or turn count starts a new thread.
    assert [call["resume"] for call in backend.calls] == [None] + ["provider-1"] * 12
    assert state(tmp_path)["provider_generation"] == 1
    assert "capacity" not in state(tmp_path)
    logical_id = state(tmp_path)["logical_manager_id"]
    new_thread(tmp_path, backend)
    assert backend.calls[-1]["resume"] is None
    assert state(tmp_path)["logical_manager_id"] == logical_id
    assert state(tmp_path)["provider_generation"] == 2
    assert state(tmp_path)["last_rotation"]["from_thread_id"] == "provider-1"
    assert all((result.input_tokens, result.cached_input_tokens, result.output_tokens) == (20000, 19990, 25)
               for result in backend.results)


def test_cancel_before_a_new_thread_does_not_call_backend_or_change_pointer(tmp_path):
    backend = Backend()
    turn(tmp_path, backend)
    before = (tmp_path / ".manager_session.json").read_bytes()
    with pytest.raises(ManagerLockCancelled):
        new_thread(tmp_path, backend, external_interrupt_reason_provider=lambda: "operator stop")
    assert len(backend.calls) == 1
    assert (tmp_path / ".manager_session.json").read_bytes() == before


@pytest.mark.parametrize("failure", ["quota", "exception"])
def test_a_refused_or_uncertain_call_is_not_replayed(tmp_path, failure):
    backend = Backend(failure=failure)
    for _ in range(4):
        turn(tmp_path, backend)
    logical_id = state(tmp_path)["logical_manager_id"]
    if failure == "exception":
        with pytest.raises(RuntimeError, match="unknown outcome"):
            turn(tmp_path, backend)
    else:
        assert turn(tmp_path, backend).fatal_error == "402 trial_quota_exceeded"
    assert len(backend.calls) == 5
    assert state(tmp_path)["logical_manager_id"] == logical_id


def test_handoff_has_one_utf8_history_budget_and_keeps_the_current_request_whole():
    previous = {"recent_turns": [{"kind": "manager-ask", "request_excerpt": "往事" * 2000,
                                   "answer_excerpt": "旧决定" * 3000} for _ in range(4)]}
    prompt = "Message:\n" + "完整的新要求" * 2000
    handoff = session_handoff(previous, prompt, "the configured model or backend changed")
    history = handoff.split("\n", 2)[2].removesuffix("\n\n" + prompt)
    assert len(history.encode()) <= SESSION_HANDOFF_HISTORY_BYTES
    assert handoff.endswith(prompt)
    assert json.loads(history)


def test_new_manager_thread_rehydrates_current_controls_questions_and_issued_decision(tmp_path):
    write_continuous_config(tmp_path, enabled=True, objective="MEANS_2_AND_12 remain the accepted design.")
    task = BacklogItem.new(title="Validate units", objective="Keep both original groups.")
    task.status = "running"
    task.pending_question = "UNANSWERED_UNITS: are these seconds or milliseconds?"
    task.acceptance_check = "Preserve independent means 2 and 12."
    Backlog(tmp_path / "backlog.jsonl").add(task)
    directive = set_active_manager_directive(tmp_path, "ACTIVE_STEER: never pool the groups.",
                                            source="manager.supervision.fixture")
    receipt_dir = tmp_path / "manager-supervision"
    receipt_dir.mkdir()
    issued = {"id": "a" * 64, "status": "issued", "decision": {
        "action": "steer", "reason": "ISSUED_NOT_APPLIED: await the operator's unit answer."}, "effects": {}}
    (receipt_dir / (issued["id"] + ".json")).write_text(json.dumps(issued))
    (receipt_dir / "latest.json").write_text(json.dumps(issued))
    permission = append_directive(tmp_path, "LATEST_AUTHORITY: inspect only; do not change source files.",
                                  expected_revision=0, applies_to_roles=("manager",))
    canonical_paths = [tmp_path / "continuous.json", tmp_path / "backlog.jsonl",
                       tmp_path / "active_manager_directive.json", receipt_dir / "latest.json",
                       receipt_dir / (issued["id"] + ".json"), tmp_path / "operator_context.jsonl"]
    before = {path: path.read_bytes() for path in canonical_paths}
    backend = Backend()
    turn(tmp_path, backend)
    new_thread(tmp_path, backend)
    call = backend.calls[-1]
    assert call["resume"] is None
    assert all(value in call["prompt"] for value in ["MEANS_2_AND_12", "UNANSWERED_UNITS",
                                                    "ACTIVE_STEER", "ISSUED_NOT_APPLIED", "LATEST_AUTHORITY"])
    assert directive.revision in call["prompt"]
    assert '"status": "issued"' in call["prompt"]
    assert {path: path.read_bytes() for path in canonical_paths} == before
    assert asdict(OperatorContextStore(tmp_path).project("manager", consume_once=False).directives[-1])["revision"] == permission.revision


def test_new_threads_keep_active_steer_after_a_newer_continue_and_refresh_resolved_questions(tmp_path):
    write_continuous_config(tmp_path, enabled=True, objective="Keep both groups")
    backlog = Backlog(tmp_path / "backlog.jsonl")
    for number in range(8):
        backlog.add(BacklogItem.new(title=str(number), objective="An independent current task"))
    question = BacklogItem.new(title="Ninth", objective="Resolve units before continuing")
    question.priority = 99
    question.pending_question = "NINTH_UNANSWERED: which units?"
    backlog.add(question)
    directive = set_active_manager_directive(tmp_path, "STILL_ACTIVE_STEER: use groups separately.", source="manager.supervision.old")
    directory = tmp_path / "manager-supervision"
    directory.mkdir()
    latest = {"id": "b" * 64, "status": "applied", "decision": {"action": "continue", "reason": "A newer observation retained course."}}
    (directory / "latest.json").write_text(json.dumps(latest))
    (directory / (latest["id"] + ".json")).write_text(json.dumps(latest))
    backend = Backend()
    turn(tmp_path, backend)
    new_thread(tmp_path, backend)
    first = capsule(backend.calls[-1])
    assert first["active_manager_directive"]["revision"] == directive.revision
    assert first["supervision_receipt"]["decision"]["action"] == "continue"
    assert first["pending_questions"] == [{"item_id": question.id, "status": "pending", "pending_question": question.pending_question}]
    backlog.update(question.id, pending_question="")
    clear_active_manager_directive(tmp_path)
    turn(tmp_path, backend)
    second = capsule(backend.calls[-1])
    assert second["active_manager_directive"] is None
    assert second["pending_questions"] == []
    assert state(tmp_path)["provider_generation"] == 3


def test_new_thread_uses_canonical_receipt_status_when_latest_pointer_lags(tmp_path):
    directory = tmp_path / "manager-supervision"
    directory.mkdir()
    identity = "c" * 32
    (directory / "latest.json").write_text(json.dumps({"id": identity, "status": "applied", "decision": {"reason": "STALE_APPLIED"}}))
    canonical = {"id": identity, "status": "issued", "decision": {"action": "steer", "reason": "PENDING_DELIVERY"}, "effects": {}}
    path = directory / (identity + ".json")
    path.write_text(json.dumps(canonical))
    before = path.read_bytes()
    backend = Backend()
    turn(tmp_path, backend)
    new_thread(tmp_path, backend)
    assert capsule(backend.calls[-1])["supervision_receipt"] == canonical
    assert "STALE_APPLIED" not in backend.calls[-1]["prompt"]
    assert path.read_bytes() == before


def test_scope_changed_directive_is_not_revived_on_a_new_thread(tmp_path):
    write_continuous_config(tmp_path, enabled=True, objective="Old objective")
    set_active_manager_directive(tmp_path, "OLD_SCOPE_ONLY", source="manager.supervision.old")
    backend = Backend()
    turn(tmp_path, backend)
    write_continuous_config(tmp_path, enabled=True, objective="New objective")
    new_thread(tmp_path, backend)
    current = capsule(backend.calls[-1])
    assert current["active_manager_directive"] is None
    assert current["continuous"]["objective"] == "New objective"


def test_a_turn_beside_a_busy_session_carries_the_current_operator_permission(tmp_path):
    granted = append_directive(tmp_path, "OLD_PERMISSION_ONLY: source writes allowed.", expected_revision=0, applies_to_roles=("manager",))
    backend = Backend()
    for _ in range(4):
        turn(tmp_path, backend)
    revoked = append_revoke(tmp_path, granted.revision, reason="Permission changed", expected_revision=granted.revision)
    append_directive(tmp_path, "NEW_PERMISSION_ONLY: read only.", expected_revision=revoked.revision, applies_to_roles=("manager",))
    with (tmp_path / ".manager_session.lock").open("a+b") as lock:
        portalocker.lock(lock, portalocker.LOCK_EX)
        try:
            turn(tmp_path, backend)
        finally:
            portalocker.unlock(lock)
    assert "another Manager turn holds the shared thread" in backend.calls[-1]["prompt"]
    assert "NEW_PERMISSION_ONLY" in backend.calls[-1]["prompt"]
    assert "OLD_PERMISSION_ONLY" not in backend.calls[-1]["prompt"]


@pytest.mark.parametrize("damage", ["invalid_json", "nan", "unreadable"])
def test_bad_backlog_aborts_a_new_thread_without_a_plain_provider_fallback(tmp_path, monkeypatch, damage):
    from argus.core import scoped_file

    Backlog(tmp_path / "backlog.jsonl").add(BacklogItem.new(title="Pending", objective="Keep the question"))
    backend = Backend()
    turn(tmp_path, backend)
    before = (tmp_path / ".manager_session.json").read_bytes()
    backlog = tmp_path / "backlog.jsonl"
    if damage == "unreadable":
        original = scoped_file.open_regular_file

        def unavailable(path, *args, **kwargs):
            if path == backlog:
                raise PermissionError("Synthetic unreadable backlog")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(scoped_file, "open_regular_file", unavailable)
    else:
        with backlog.open("a") as handle:
            handle.write('{bad json}\n' if damage == "invalid_json" else '{"id":"bad","ts":NaN}\n')
    with pytest.raises(ManagerSessionContinuityUnavailable):
        new_thread(tmp_path, backend)
    assert len(backend.calls) == 1
    assert (tmp_path / ".manager_session.json").read_bytes() == before


@pytest.mark.parametrize("damage", ["oversized_field", "nan"])
def test_incomplete_control_is_explicit_and_never_cut_into_a_partial_instruction(tmp_path, damage):
    write_continuous_config(tmp_path, enabled=True, objective="Keep original constraints")
    directive = set_active_manager_directive(tmp_path, "BEGIN_MUST_NOT_BE_CUT " + "strict requirement " * 1500 + " END_MUST_NOT_BE_CUT",
                                            source="manager.supervision.fixture")
    if damage == "nan":
        (tmp_path / "active_manager_directive.json").write_text('{"text":NaN,"version":1}')
    backend = Backend()
    turn(tmp_path, backend)
    new_thread(tmp_path, backend)
    current = capsule(backend.calls[-1])
    assert current["active_manager_directive"] is None
    assert current["unobserved"]
    assert "BEGIN_MUST_NOT_BE_CUT" not in json.dumps(current)
    assert "END_MUST_NOT_BE_CUT" not in json.dumps(current)
    if damage == "nan":
        assert current["sources"]["active_manager_directive.json"]["status"] == "unobserved"
        assert "unobserved_source_signature" in current["sources"]["active_manager_directive.json"]
    else:
        assert directive.revision


def _hold_file_lock(path, held, release):
    with open(path, "a+b") as handle:
        portalocker.lock(handle, portalocker.LOCK_EX)
        held.set()
        release.wait(8)
        portalocker.unlock(handle)


@pytest.mark.parametrize("owner", ["thread", "process"])
@pytest.mark.parametrize("interrupt", [False, True])
def test_backlog_contention_does_not_trap_a_new_manager_thread(tmp_path, monkeypatch, owner, interrupt):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    backlog.add(BacklogItem.new(title="Keep", objective="Preserve current work"))
    backend = Backend()
    turn(tmp_path, backend)
    before = (tmp_path / ".manager_session.json").read_bytes()
    stopped, entered = threading.Event(), threading.Event()
    original = Backlog._locked
    context = mp.get_context("spawn")
    held, release = context.Event(), context.Event()
    if owner == "process":
        holder = context.Process(target=_hold_file_lock, args=(str(backlog._lock_path), held, release))
    else:
        def hold_thread_lock():
            with original(backlog):
                held.set()
                release.wait(8)

        holder = threading.Thread(target=hold_thread_lock)
    holder.start()
    assert held.wait(3)

    @contextmanager
    def watched_lock(instance, **kwargs):
        entered.set()
        with original(instance, **kwargs):
            yield

    monkeypatch.setattr(Backlog, "_locked", watched_lock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(new_thread, tmp_path, backend,
            external_interrupt_reason_provider=lambda: "operator stop" if stopped.is_set() else None)
        try:
            assert entered.wait(2)
            if interrupt:
                stopped.set()
            expected = ManagerLockCancelled if interrupt else ManagerSessionContinuityUnavailable
            with pytest.raises(expected):
                pending.result(timeout=1.5)
            assert not release.is_set()  # Returned before either lock owner was released.
        finally:
            release.set()
            holder.join(timeout=3)
    assert not holder.is_alive()
    assert len(backend.calls) == 1
    assert (tmp_path / ".manager_session.json").read_bytes() == before


@pytest.mark.parametrize("interrupt", [False, True])
def test_permission_lock_contention_cannot_drop_authority_or_delay_stop(tmp_path, monkeypatch, interrupt):
    append_directive(tmp_path, "READ_ONLY_CURRENT_PERMISSION", expected_revision=0, applies_to_roles=("manager",))
    backend = Backend()
    for _ in range(4):
        turn(tmp_path, backend)
    before = (tmp_path / ".manager_session.json").read_bytes()
    held, release, entered, stopped = (threading.Event() for _ in range(4))
    holder = threading.Thread(target=_hold_file_lock, args=(str(tmp_path / "operator_context.lock"), held, release))
    holder.start()
    assert held.wait(2)
    original = portalocker.lock

    def watched(handle, flags):
        if str(handle.name).endswith("operator_context.lock"):
            entered.set()
        return original(handle, flags)

    monkeypatch.setattr(portalocker, "lock", watched)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(turn, tmp_path, backend, options=RunnerOptions(model="fixture/model",
            external_interrupt_reason_provider=lambda: "operator stop" if stopped.is_set() else None))
        try:
            assert entered.wait(2)
            if interrupt:
                stopped.set()
            expected = ManagerLockCancelled if interrupt else ManagerSessionContinuityUnavailable
            with pytest.raises(expected):
                pending.result(timeout=1.5)
            assert not release.is_set()
        finally:
            release.set()
            holder.join(timeout=3)
    assert len(backend.calls) == 4
    assert (tmp_path / ".manager_session.json").read_bytes() == before
    stopped.clear()
    turn(tmp_path, backend)
    assert "READ_ONLY_CURRENT_PERMISSION" in backend.calls[-1]["prompt"]


def test_bounded_continuity_read_still_recovers_the_canonical_backlog_commit(tmp_path):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    task = BacklogItem.new(title="Current", objective="Preserve the committed question")
    task.pending_question = "BEFORE_RECOVERY"
    backlog.add(task)
    backend = Backend()
    turn(tmp_path, backend)
    task.pending_question = "AFTER_CANONICAL_RECOVERY"
    completed = BacklogItem.new(title="Completed", objective="Previously finished work")
    completed.status = "done"
    backlog._commit_path.write_text(json.dumps({"version": 1, "archive_offset": 0,
                                               "live": [task.to_jsonable()], "terminal": [completed.to_jsonable()]}))
    new_thread(tmp_path, backend)
    assert capsule(backend.calls[-1])["pending_questions"][0]["pending_question"] == "AFTER_CANONICAL_RECOVERY"
    assert json.loads(backlog._commit_path.read_text()) == {"version": 1}
    assert [item.id for item in backlog.history()] == [completed.id, task.id]
