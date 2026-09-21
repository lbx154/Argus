"""Real teammate entry cancellation with private locks and offline providers."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from contextlib import nullcontext

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.apps import _runtime
from argus.core import file_lock
from argus.core.models import RunnerResult
from argus.core.operator_context import OperatorContextStore, append_directive
from argus.core.run_gateway import current_run_interrupt_reason, run_interrupt_scope
from argus.team import teammate_entry


def _environment(tmp_path, monkeypatch, backend):
    workspace = tmp_path / "workspace"
    (workspace / ".argus").mkdir(parents=True)
    (workspace / ".argus/PIPELINE_STATE.json").write_text(json.dumps({
        "vertical": "software", "current_stage": "implementation", "workflow_mode": "direct",
    }))
    policy = tmp_path / "policy"
    append_directive(policy, "Preserve the private fixture.", expected_revision=0)
    monkeypatch.setenv("ARGUS_OPERATOR_CONTEXT_DIR", str(policy))
    monkeypatch.setenv("ARGUS_SKILL_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_MODEL", "offline")
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_MODEL", "offline")
    monkeypatch.setenv("ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING", "0")
    monkeypatch.setenv("ARGUS_SKILL_TEAM_TASK_ID", "stop-fixture")
    monkeypatch.setattr("argus.adapters.agent_cli_backend.AgentCliBackend", lambda **_kwargs: backend)
    return workspace, policy


def _review(status):
    return CannedResponse(review_action=(('approve_review' if status == 'done' else 'revise_review'), {'review': ('offline review') + '\n\n' + ('none')}))


def _mission_thread(tmp_path, monkeypatch, workspace, *, inherited_abort=False):
    from argus.engineer.round_reviewer import RoundReviewerMixin

    captured = {"abort": threading.Event(), "done": threading.Event(), "reason_deliveries": 0}
    namespace = teammate_entry._build_runner_ns
    usage = _runtime._SkillLoopRunner._set_usage_context
    call_reviewer = RoundReviewerMixin._call_reviewer_once

    def capture_namespace(*args, **kwargs):
        result = namespace(*args, **kwargs)
        captured["stop"] = kwargs["stop_event"]
        return result

    def one_shot_abort():
        if captured["abort"].is_set() and not captured["reason_deliveries"]:
            captured["reason_deliveries"] += 1
            return "operator abort requested: retain the original reason"
        return None

    def record_cleanup(self, value):
        result = usage(self, value)
        if value is None and (captured.get("stop", threading.Event()).is_set() or captured["abort"].is_set()):
            # This real execute-finally callback must still be able to commit
            # state after cancellation; no cancelled read-lock budget leaks in.
            assert file_lock.current_file_lock_wait_budget() is None
            with (tmp_path / "cleanup.lock").open("a+b") as handle, file_lock.exclusive_file_lock(handle):
                (tmp_path / "cleanup-completed").write_text("settled after cancellation")
            captured["cleanup_reason"] = current_run_interrupt_reason()
        return result

    def record_reviewer(self, **kwargs):
        review = call_reviewer(self, **kwargs)
        captured["reviewer_fatal_error"] = review.backend_fatal_error
        return review

    monkeypatch.setattr(teammate_entry, "_build_runner_ns", capture_namespace)
    monkeypatch.setattr(_runtime._SkillLoopRunner, "_set_usage_context", record_cleanup)
    monkeypatch.setattr(RoundReviewerMixin, "_call_reviewer_once", record_reviewer)

    def invoke():
        try:
            with run_interrupt_scope(one_shot_abort) if inherited_abort else nullcontext():
                captured["result"] = teammate_entry.run_one_engineer_mission(
                    "Inspect the private fixture.", cwd=str(workspace), life_dir=tmp_path / "worker",
                    max_rounds=2, timeout_s=0,
                )
            captured["scope_after"] = current_run_interrupt_reason()
            captured["lock_budget_after"] = file_lock.current_file_lock_wait_budget()
        except BaseException as exc:
            captured["exception"] = exc
        finally:
            captured["done"].set()

    worker = threading.Thread(target=invoke, name="teammate-stop-test", daemon=True)
    return captured, worker


def _assert_stopped(tmp_path, captured, inherited_abort, *, role="engineer"):
    assert "exception" not in captured, captured.get("exception")
    result = captured["result"]
    assert not result.success
    assert result.status == ("aborted" if inherited_abort else "paused_daemon_shutdown")
    reason = "operator abort requested: retain the original reason" if inherited_abort else "daemon stop requested"
    assert captured["cleanup_reason"] == reason
    events = [json.loads(line) for line in (tmp_path / "worker/events.jsonl").read_text().splitlines()]
    event_type = "round.review.completed" if role == "reviewer" else "round.main.completed"
    completed = next(event for event in events if event.get("type") == event_type)
    # The public abort verdict has a stable human-readable reason; the raw
    # first interrupt remains in the real Reviewer result/round record.
    assert reason in (captured["reviewer_fatal_error"] if role == "reviewer" else completed["fatal_error"])
    assert completed["stop_kind"] == ("operator_abort" if inherited_abort else "daemon_shutdown")
    assert (tmp_path / "cleanup-completed").read_text() == "settled after cancellation"
    assert captured["scope_after"] is None and captured["lock_budget_after"] is None
    if inherited_abort:
        assert captured["reason_deliveries"] == 1


@pytest.mark.parametrize("inherited_abort", [False, True])
def test_stop_during_real_operator_lock_wait_returns_before_release(tmp_path, monkeypatch, inherited_abort):
    backend = MemoryBackend()
    workspace, policy = _environment(tmp_path, monkeypatch, backend)
    captured, worker = _mission_thread(tmp_path, monkeypatch, workspace, inherited_abort=inherited_abort)
    lock_path = policy / "operator_context.lock"
    gate, held, release, contended = (threading.Event() for _ in range(4))
    execute, lock = _runtime._SkillLoopRunner.execute, file_lock.portalocker.lock
    read_budgets = []

    def at_execute(self, **kwargs):
        provider = kwargs["prelude_context_provider"]

        def current():
            gate.set()
            assert held.wait(3)
            return provider()

        kwargs["prelude_context_provider"] = current
        return execute(self, **kwargs)

    def observe_lock(handle, flags):
        try:
            return lock(handle, flags)
        except file_lock.portalocker.exceptions.LockException:
            if threading.current_thread() is worker and handle.name == str(lock_path):
                read_budgets.append(file_lock.current_file_lock_wait_budget())
                contended.set()
            raise

    def hold():
        assert gate.wait(3)
        with lock_path.open("a+b") as handle:
            lock(handle, file_lock.portalocker.LOCK_EX)
            held.set()
            try:
                release.wait(5)
            finally:
                file_lock.portalocker.unlock(handle)

    monkeypatch.setattr(_runtime._SkillLoopRunner, "execute", at_execute)
    monkeypatch.setattr(file_lock.portalocker, "lock", observe_lock)
    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    worker.start()
    try:
        assert contended.wait(3)
        assert not captured["done"].is_set()
        started_at = time.perf_counter()
        (captured["abort"] if inherited_abort else captured["stop"]).set()
        assert captured["done"].wait(0.75), "Stop still waits for the fixture lock"
        elapsed = time.perf_counter() - started_at
        assert not release.is_set() and holder.is_alive()
        assert backend.history == []
        assert read_budgets and all(budget is not None and math.isinf(budget[0]) for budget in read_budgets)
        _assert_stopped(tmp_path, captured, inherited_abort)
        (tmp_path / "stop-observation.json").write_text(json.dumps({
            "scenario": "contended_required_operator_lock", "inherited_abort": inherited_abort,
            "returned_after_seconds": elapsed, "threshold_seconds": 0.75,
            "returned_before_holder_release": True, "backend_calls": len(backend.history),
            "settlement_write_completed": (tmp_path / "cleanup-completed").exists(),
        }))
    finally:
        release.set()
        holder.join(3)
        worker.join(3)
    assert not holder.is_alive() and not worker.is_alive()
    assert backend.history == []


@pytest.mark.parametrize("inherited_abort", [False, True])
def test_stop_reaches_delayed_provider_and_never_starts_reviewer(tmp_path, monkeypatch, inherited_abort):
    started, release = threading.Event(), threading.Event()
    observed_reasons = []

    class DelayedBackend(MemoryBackend):
        def run_exec(self, *, prompt, options=None, run_label="", **kwargs):
            if run_label != "engineer-r1":
                return super().run_exec(prompt=prompt, options=options, run_label=run_label, **kwargs)
            self.history.append((run_label, prompt, options))
            started.set()
            callback = options.external_interrupt_reason_provider
            assert callable(callback)
            while not release.wait(0.01):
                reason = callback()
                if reason:
                    observed_reasons.extend([reason, callback()])
                    return RunnerResult(exit_code=130, fatal_error="External interrupt: " + reason,
                                        input_tokens=17, output_tokens=3, thread_id="local-delayed")
            return RunnerResult(exit_code=0, agent_messages=["fixture released"])

    backend = DelayedBackend()
    workspace, _policy = _environment(tmp_path, monkeypatch, backend)
    captured, worker = _mission_thread(tmp_path, monkeypatch, workspace, inherited_abort=inherited_abort)
    worker.start()
    try:
        assert started.wait(3)
        started_at = time.perf_counter()
        (captured["abort"] if inherited_abort else captured["stop"]).set()
        assert captured["done"].wait(0.75)
        elapsed = time.perf_counter() - started_at
        assert not release.is_set()
        _assert_stopped(tmp_path, captured, inherited_abort)
        (tmp_path / "stop-observation.json").write_text(json.dumps({
            "scenario": "cooperative_local_provider", "inherited_abort": inherited_abort,
            "returned_after_seconds": elapsed, "threshold_seconds": 0.75,
            "provider_released_manually": False,
            "backend_labels": [label for label, _prompt, _options in backend.history],
            "observed_interrupt_reasons": observed_reasons,
            "settlement_write_completed": (tmp_path / "cleanup-completed").exists(),
        }))
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert [label for label, _prompt, _options in backend.history] == ["engineer-r1"]
    assert len(observed_reasons) == 2 and observed_reasons[0] == observed_reasons[1]
    events = [json.loads(line) for line in (tmp_path / "worker/events.jsonl").read_text().splitlines()]
    completed = next(event for event in events if event.get("type") == "round.main.completed")
    assert completed["input_tokens"] == 17 and completed["output_tokens"] == 3


def test_uncancelled_long_mission_keeps_per_lock_wait_budget(tmp_path, monkeypatch):
    backend = MemoryBackend()
    workspace, policy = _environment(tmp_path, monkeypatch, backend)
    real_monotonic, lock = time.monotonic, file_lock.portalocker.lock
    elapsed = [0.0]
    contended = []

    def advance(_prompt, _options):
        elapsed[0] = 61.0  # Simulated elapsed mission time; no real minute wait.
        return "first implementation"

    def once_contended(handle, flags):
        if elapsed[0] and handle.name == str(policy / "operator_context.lock") and not contended:
            contended.append(file_lock.current_file_lock_wait_budget())
            raise file_lock.portalocker.exceptions.LockException("one synthetic contention")
        return lock(handle, flags)

    monkeypatch.setattr(file_lock.time, "monotonic", lambda: real_monotonic() + elapsed[0])
    monkeypatch.setattr(file_lock.portalocker, "lock", once_contended)
    backend.queue("engineer-r1", CannedResponse(message_factory=advance))
    backend.queue("engineer-r2", CannedResponse(message="final implementation"))
    backend.queue("reviewer", _review("continue"))
    backend.queue("reviewer", _review("done"))
    result = teammate_entry.run_one_engineer_mission(
        "Inspect the private fixture.", cwd=str(workspace), life_dir=tmp_path / "worker",
        max_rounds=2, timeout_s=0,
    )
    assert result.success and result.status == "done"
    assert len(contended) == 1 and contended[0] is not None and math.isinf(contended[0][0])
    # Reviewer receives only a prompt-read cancellation budget, not a deadline
    # measured from the beginning of this long-running mission.
    assert [label for label, _prompt, _options in backend.history] == [
        "engineer-r1", "reviewer", "engineer-r2", "reviewer",
    ]
    assert file_lock.current_file_lock_wait_budget() is None and current_run_interrupt_reason() is None


@pytest.mark.parametrize("scenario", ["reviewer", "experience", "knowledge_sqlite"])
@pytest.mark.parametrize("inherited_abort", [False, True])
def test_role_context_stop_returns_while_real_storage_lock_is_held(tmp_path, monkeypatch, scenario, inherited_abort):
    from argus.engineer import round_reviewer
    from argus.life import knowledge_recall

    backend = MemoryBackend()
    workspace, policy = _environment(tmp_path, monkeypatch, backend)
    role = "reviewer" if scenario == "reviewer" else "teammate"
    once = append_directive(policy, "Still owed to the next real role call.",
                            applies_to_roles=(role,), lifetime="once", expected_revision=1)
    backend.queue("engineer-r1", CannedResponse(message="implementation ready"))
    captured, worker = _mission_thread(tmp_path, monkeypatch, workspace, inherited_abort=inherited_abort)
    gate, held, release, contended = (threading.Event() for _ in range(4))
    lock_path = (policy / "operator_context.lock" if scenario == "reviewer" else
                 policy / "failure_experiences.jsonl.lock" if scenario == "experience" else
                 tmp_path / "worker/knowledge-recall.sqlite3")
    projection = round_reviewer._active_manager_directive_for_reviewer
    recall = knowledge_recall.render_memory_recall
    lock, connect = file_lock.portalocker.lock, sqlite3.connect
    budgets = []

    def before_read():
        gate.set()
        assert held.wait(3)

    def project(*args, **kwargs):
        if scenario == "reviewer":
            before_read()
        return projection(*args, **kwargs)

    def recalled(*args, **kwargs):
        if scenario != "reviewer":
            before_read()
        return recall(*args, **kwargs)

    def observe_lock(handle, flags):
        try:
            return lock(handle, flags)
        except file_lock.portalocker.exceptions.LockException:
            if threading.current_thread() is worker and os.path.samestat(os.fstat(handle.fileno()), lock_path.stat()):
                budgets.append(file_lock.current_file_lock_wait_budget())
                contended.set()
            raise

    class ObservedConnection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            if threading.current_thread() is worker and held.is_set() and not release.is_set():
                budgets.append(file_lock.current_file_lock_wait_budget())
                contended.set()
            return super().execute(statement, *args, **kwargs)

    def observed_connect(database, *args, **kwargs):
        if str(database) == str(lock_path) and threading.current_thread() is worker:
            kwargs["factory"] = ObservedConnection
        return connect(database, *args, **kwargs)

    def hold():
        assert gate.wait(3)
        if scenario == "knowledge_sqlite":
            db = connect(lock_path)
            db.execute("CREATE TABLE probe_lock (value INTEGER)")
            db.commit()
            db.execute("BEGIN EXCLUSIVE")
            held.set()
            try:
                release.wait(5)
            finally:
                db.rollback()
                db.close()
        else:
            with lock_path.open("a+b") as handle:
                lock(handle, file_lock.portalocker.LOCK_EX)
                held.set()
                try:
                    release.wait(5)
                finally:
                    file_lock.portalocker.unlock(handle)

    monkeypatch.setattr(round_reviewer, "_active_manager_directive_for_reviewer", project)
    monkeypatch.setattr(knowledge_recall, "render_memory_recall", recalled)
    monkeypatch.setattr(file_lock.portalocker, "lock", observe_lock)
    monkeypatch.setattr(sqlite3, "connect", observed_connect)
    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    worker.start()
    try:
        assert contended.wait(3), captured
        started = time.perf_counter()
        (captured["abort"] if inherited_abort else captured["stop"]).set()
        assert captured["done"].wait(0.75), "role preparation still waits for its storage holder"
        elapsed = time.perf_counter() - started
        assert holder.is_alive() and not release.is_set()
        assert [label for label, _prompt, _options in backend.history] == (["engineer-r1"] if role == "reviewer" else [])
        assert budgets and all(budget is not None and math.isinf(budget[0]) for budget in budgets)
        _assert_stopped(tmp_path, captured, inherited_abort, role=role)
        (tmp_path / "stop-observation.json").write_text(json.dumps({
            "scenario": scenario, "inherited_abort": inherited_abort,
            "returned_after_seconds": elapsed, "threshold_seconds": 0.75,
            "returned_before_holder_release": True,
            "provider_labels": [label for label, _prompt, _options in backend.history],
            "retained_interrupt_reason": captured["cleanup_reason"],
            "reviewer_fatal_error": captured.get("reviewer_fatal_error"),
            "cleanup_write_completed": (tmp_path / "cleanup-completed").exists(),
        }))
    finally:
        release.set()
        holder.join(3)
        worker.join(3)
    assert not holder.is_alive() and not worker.is_alive()
    # Stop before acquisition must not spend an instruction that no model saw.
    assert once.revision in {d.revision for d in OperatorContextStore(policy).project(role, consume_once=False).directives}
    OperatorContextStore(policy).project(role)
    assert once.revision not in {d.revision for d in OperatorContextStore(policy).project(role, consume_once=False).directives}


@pytest.mark.parametrize("corruption", ["missing", "corrupt"])
def test_required_reviewer_projection_failure_never_calls_reviewer(tmp_path, monkeypatch, corruption):
    backend = MemoryBackend()
    workspace, policy = _environment(tmp_path, monkeypatch, backend)

    def corrupt_after_engineer(_prompt, _options):
        ledger = policy / "operator_context.jsonl"
        if corruption == "missing":
            ledger.unlink()
        else:
            ledger.write_text("{not a complete operator ledger")
        return "implementation ready"

    backend.queue("engineer-r1", CannedResponse(message_factory=corrupt_after_engineer))
    result = teammate_entry.run_one_engineer_mission(
        "Inspect the private fixture.", cwd=str(workspace), life_dir=tmp_path / "worker",
        max_rounds=1, timeout_s=0,
    )
    assert not result.success and result.status == "error"
    assert "Reviewer OperatorContext is unavailable" in result.reason
    assert [label for label, _prompt, _options in backend.history] == ["engineer-r1"]
    assert file_lock.current_file_lock_wait_budget() is None


def test_stop_after_reviewer_lock_acquisition_preserves_atomic_once_consumption(tmp_path, monkeypatch):
    backend = MemoryBackend()
    workspace, policy = _environment(tmp_path, monkeypatch, backend)
    once = append_directive(policy, "One atomic Reviewer projection.", applies_to_roles=("reviewer",),
                            lifetime="once", expected_revision=1)
    backend.queue("engineer-r1", CannedResponse(message="implementation ready"))
    captured, worker = _mission_thread(tmp_path, monkeypatch, workspace)
    save = OperatorContextStore._save
    checkpointed = []

    def save_then_stop(self, document, **kwargs):
        if threading.current_thread() is worker and once.revision in document.state.get("consumed_once", []):
            assert file_lock.current_file_lock_wait_budget() is not None
            captured["stop"].set()
            checkpointed.append(once.revision)
        return save(self, document, **kwargs)

    monkeypatch.setattr(OperatorContextStore, "_save", save_then_stop)
    worker.start()
    worker.join(3)
    assert not worker.is_alive()
    _assert_stopped(tmp_path, captured, False, role="reviewer")
    assert checkpointed == [once.revision]
    assert once.revision not in {d.revision for d in OperatorContextStore(policy).project("reviewer", consume_once=False).directives}
    assert [label for label, _prompt, _options in backend.history] == ["engineer-r1"]
