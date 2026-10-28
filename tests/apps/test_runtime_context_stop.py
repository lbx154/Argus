"""The ordinary daemon runner observes Stop before role-context providers."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextvars import copy_context
from types import SimpleNamespace

import pytest

from argus.adapters.agent_cli_backend._core import AgentCliBackend
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.agent_cli.agent_cli_runner import RunnerOptions as CliRunnerOptions
from argus.apps import _runtime, _runtime_execute
from argus.apps._runtime_interrupt import current_execution_interrupt_provider
from argus.core import file_lock
from argus.core.models import RunnerOptions, RunnerResult
from argus.core.operator_context import OperatorContextStore, append_directive
from argus.core.run_gateway import (
    RunExecGateway,
    current_run_interrupt_provider,
    current_run_interrupt_reason,
)
from argus.daemon._life_worker_runtime_context import _runner_namespace
from argus.daemon.config import LifeWorkerConfig
from argus.engineer import round_reviewer
from argus.life import knowledge_recall
from argus.life.memory import BacklogItem, LifeMemory, request_running_item_abort
from argus.life.supervisor._mission_execution_runtime import _mission_memory_prelude
from tests.hand_off import hand_off


def _runtime_case(tmp_path, monkeypatch, *, enable_abort=True, with_stop=True):
    workspace, state = tmp_path / "workspace", tmp_path / "state"
    (workspace / ".argus").mkdir(parents=True)
    (workspace / ".argus/PIPELINE_STATE.json").write_text(json.dumps({
        "vertical": "software", "current_stage": "implementation", "workflow_mode": "direct",
    }))
    monkeypatch.setenv("ARGUS_SKILL_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_MODEL", "offline")
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_MODEL", "offline")
    monkeypatch.setenv("ARGUS_SKILL_LEARNING", "0")
    append_directive(state, "Keep the current required policy.", expected_revision=0)
    memory = LifeMemory.open(state)
    item = memory.backlog.add(BacklogItem.new(title="current", objective="Inspect this synthetic fixture."))
    memory.backlog.mark_running(item.id)
    stop, requested, done = threading.Event(), threading.Event(), threading.Event()
    captured = {"events": [], "gateway_interrupts": [], "adapter_invocations": []}

    class Backend(MemoryBackend):
        def run_exec(self, *, prompt, options=None, run_label="", **kwargs):
            captured["adapter_invocations"].append(run_label)
            # The real adapter evaluates the default before the per-call hook.
            for callback in [self.default_interrupt, getattr(options, "external_interrupt_reason_provider", None)]:
                reason = callback() if callback else None
                if reason:
                    return RunnerResult(exit_code=130, fatal_error="External interrupt: " + reason)
            return super().run_exec(prompt=prompt, options=options, run_label=run_label, **kwargs)

    backend = Backend()

    def factory(**kwargs):
        backend.default_interrupt = kwargs.get("default_interrupt_reason_provider")
        return backend

    monkeypatch.setattr("argus.adapters.agent_cli_backend.AgentCliBackend", factory)
    ns = _runner_namespace(LifeWorkerConfig(life_dir=state, project_workdir=workspace,
                                           global_root=tmp_path / "global", backend="codex",
                                           engineer_model="offline", reviewer_model="offline"))
    ns.stop_event = stop if with_stop else None
    ns.enable_mission_abort_signal = enable_abort
    ns.max_rounds = 1
    runner = _runtime._SkillLoopRunner(ns)
    runner._active_mission_id = item.id
    usage, gateway = _runtime._SkillLoopRunner._set_usage_context, RunExecGateway.execute

    def cleanup(self, value):
        result = usage(self, value)
        if value is None and requested.is_set():
            captured["cleanup_reason"] = current_run_interrupt_reason()
            assert file_lock.current_file_lock_wait_budget() is None
            with (tmp_path / "cleanup.lock").open("a+b") as handle, file_lock.exclusive_file_lock(handle):
                (tmp_path / "cleanup-completed").write_text("settled")
        return result

    def record_gateway(self, request):
        result = gateway(self, request)
        if result.fatal_error and "External interrupt:" in result.fatal_error:
            captured["gateway_interrupts"].append(result.fatal_error)
        return result

    monkeypatch.setattr(_runtime._SkillLoopRunner, "_set_usage_context", cleanup)
    monkeypatch.setattr(RunExecGateway, "execute", record_gateway)

    def execute(*, target=None, provider=None):
        return runner.execute(
            objective=item.objective, sink=SimpleNamespace(handle_event=captured["events"].append),
            preplanned=True, holds_stage_authority=False, mission_id=item.id if target is None else target,
            prelude_context_provider=provider or (lambda: _mission_memory_prelude(memory, item, stop_event=ns.stop_event)),
        )

    def invoke():
        try:
            captured["outcome"] = execute()
        except BaseException as exc:
            captured["exception"] = exc
        finally:
            captured["scope_after"] = current_run_interrupt_reason()
            captured["execution_provider_after"] = current_execution_interrupt_provider()
            captured["budget_after"] = file_lock.current_file_lock_wait_budget()
            done.set()

    return SimpleNamespace(workspace=workspace, state=state, memory=memory, item=item, stop=stop,
                           requested=requested, done=done, captured=captured, backend=backend,
                           runner=runner, execute=execute,
                           worker=threading.Thread(target=invoke, name="ordinary-runtime-stop", daemon=True))


@pytest.mark.parametrize("scenario", ["engineer", "reviewer", "experience", "knowledge_sqlite"])
@pytest.mark.parametrize("signal", ["stop", "abort"])
def test_ordinary_execution_stops_while_role_storage_holder_remains_locked(tmp_path, monkeypatch, scenario, signal):
    case = _runtime_case(tmp_path, monkeypatch)
    role = "reviewer" if scenario == "reviewer" else "engineer"
    once = append_directive(case.state, "Exactly one current role projection.", applies_to_roles=(role,),
                            lifetime="once", expected_revision=1)
    case.backend.queue("engineer-r1", CannedResponse(message="implementation ready"))
    gate, held, release, contended = (threading.Event() for _ in range(4))
    lock_path = case.state / ("operator_context.lock" if scenario in {"engineer", "reviewer"}
                              else "failure_experiences.jsonl.lock" if scenario == "experience"
                              else "knowledge-recall.sqlite3")
    engineer, reviewer, recall = (_runtime_execute._engineer_guidance,
                                   round_reviewer._active_manager_directive_for_reviewer,
                                   knowledge_recall.render_memory_recall)
    lock, connect = file_lock.portalocker.lock, sqlite3.connect

    def gate_read():
        gate.set()
        assert held.wait(3)

    def engineer_read(*args, **kwargs):
        if scenario == "engineer":
            gate_read()
        return engineer(*args, **kwargs)

    def reviewer_read(*args, **kwargs):
        if scenario == "reviewer":
            gate_read()
        return reviewer(*args, **kwargs)

    def memory_read(*args, **kwargs):
        if scenario in {"experience", "knowledge_sqlite"}:
            gate_read()
        return recall(*args, **kwargs)

    def observe_lock(handle, flags):
        try:
            return lock(handle, flags)
        except file_lock.portalocker.exceptions.LockException:
            if threading.current_thread() is case.worker and os.path.samestat(os.fstat(handle.fileno()), lock_path.stat()):
                assert file_lock.current_file_lock_wait_budget() is not None
                contended.set()
            raise

    class ObservedConnection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            if threading.current_thread() is case.worker and held.is_set() and not release.is_set():
                assert file_lock.current_file_lock_wait_budget() is not None
                contended.set()
            return super().execute(statement, *args, **kwargs)

    def observed_connect(database, *args, **kwargs):
        if str(database) == str(lock_path) and threading.current_thread() is case.worker:
            kwargs["factory"] = ObservedConnection
        return connect(database, *args, **kwargs)

    def hold():
        assert gate.wait(3)
        if scenario == "knowledge_sqlite":
            db = connect(lock_path)
            db.execute("CREATE TABLE holder (value INTEGER)")
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

    monkeypatch.setattr(_runtime_execute, "_engineer_guidance", engineer_read)
    monkeypatch.setattr(round_reviewer, "_active_manager_directive_for_reviewer", reviewer_read)
    monkeypatch.setattr(knowledge_recall, "render_memory_recall", memory_read)
    monkeypatch.setattr(file_lock.portalocker, "lock", observe_lock)
    monkeypatch.setattr(sqlite3, "connect", observed_connect)
    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    case.worker.start()
    try:
        assert contended.wait(3), case.captured
        started = time.perf_counter()
        if signal == "abort":
            assert request_running_item_abort(case.state, reason="ordinary original reason") == (True, case.item.id)
        else:
            case.stop.set()
        case.requested.set()
        assert case.done.wait(0.75), case.captured
        elapsed = time.perf_counter() - started
        assert holder.is_alive() and not release.is_set()
        assert "exception" not in case.captured, case.captured
        outcome = case.captured["outcome"]
        assert (outcome.status, outcome.park_reason) == (
            ("aborted", "") if signal == "abort" else ("parked", "daemon_shutdown")
        )
        reason = "operator abort requested: ordinary original reason" if signal == "abort" else "daemon stop requested"
        assert case.captured["cleanup_reason"] == reason
        assert case.captured["gateway_interrupts"] and all(reason in error for error in case.captured["gateway_interrupts"])
        assert case.captured["adapter_invocations"] == (["engineer-r1"] if role == "reviewer" else [])
        assert case.captured["scope_after"] is None and case.captured["execution_provider_after"] is None
        assert case.captured["budget_after"] is None and (tmp_path / "cleanup-completed").exists()
        if signal == "abort":
            assert not (case.state / "running_item_abort.json").exists()
        (tmp_path / "stop-observation.json").write_text(json.dumps({
            "scenario": scenario, "signal": signal, "elapsed_seconds": elapsed,
            "returned_before_holder_release": True, "retained_reason": reason,
            "gateway_interrupts": case.captured["gateway_interrupts"],
            "adapter_invocations": case.captured["adapter_invocations"], "cleanup_write_completed": True,
        }))
    finally:
        release.set()
        holder.join(3)
        case.worker.join(3)
    assert not holder.is_alive() and not case.worker.is_alive()
    if scenario in {"engineer", "reviewer"}:
        assert once.revision in {d.revision for d in OperatorContextStore(case.state).project(role, consume_once=False).directives}


def _queue_success(backend, *, first_message=None):
    backend.queue("engineer-r1", CannedResponse(message="implementation ready", message_factory=first_message))
    backend.queue("reviewer", CannedResponse(action=hand_off("done", "verified")))


def _translated_watchdog(case):
    adapter = SimpleNamespace(
        _deps={"CliRunnerOptions": CliRunnerOptions}, _backend_name="copilot",
        _default_interrupt_reason_provider=case.backend.default_interrupt,
        _default_watchdog_soft_idle_seconds=0, _default_watchdog_stalled_idle_seconds=0,
        _default_watchdog_hard_idle_seconds=0,
    )
    return AgentCliBackend._translate_options(
        adapter, RunnerOptions(external_interrupt_reason_provider=current_run_interrupt_provider()),
    ).external_interrupt_reason_provider


@pytest.mark.parametrize("different_root", [False, True])
@pytest.mark.parametrize("plain_watchdog", [False, True])
def test_late_context_cannot_consume_the_next_mission_abort(tmp_path, monkeypatch, different_root, plain_watchdog):
    case = _runtime_case(tmp_path, monkeypatch)
    old_contexts = []
    watchdogs = []

    def capture(_prompt, _options):
        old_contexts.append(copy_context())
        watchdogs.append(_translated_watchdog(case))
        return "implementation ready"

    _queue_success(case.backend, first_message=capture)
    assert case.execute().success
    assert current_execution_interrupt_provider() is None and current_run_interrupt_reason() is None
    next_root = tmp_path / "next-state" if different_root else case.state
    memory = LifeMemory.open(next_root)
    next_item = memory.backlog.add(BacklogItem.new(title="next", objective="next mission"))
    memory.backlog.mark_running(next_item.id)
    case.runner._manager_session_root = next_root
    case.runner._active_mission_id = next_item.id
    assert request_running_item_abort(next_root, reason="next mission only") == (True, next_item.id)
    before = {p.name: p.read_bytes() for p in [next_root / "running_item_abort.json"]}
    observed = []

    def late():
        if plain_watchdog:
            observed.extend([watchdogs[0](), watchdogs[0]()])
        else:
            observed.append(old_contexts[0].run(case.backend.default_interrupt))
            observed.append(old_contexts[0].run(current_run_interrupt_reason))

    thread = threading.Thread(target=late)
    thread.start()
    thread.join(1)
    assert not thread.is_alive()
    assert observed == ["mission execution scope ended"] * 2
    assert before == {p.name: p.read_bytes() for p in [next_root / "running_item_abort.json"]}
    # A fresh execute on the same cached runner owns the new identity and may
    # consume its abort. The expired context cannot steal it first.
    outcome = case.execute(target=next_item.id)
    assert outcome.status == "aborted"
    assert not (next_root / "running_item_abort.json").exists()
    assert case.captured["adapter_invocations"] == ["engineer-r1", "reviewer"]
