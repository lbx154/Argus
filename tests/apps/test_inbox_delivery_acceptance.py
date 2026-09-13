"""Delivery proofs use actual ordinary runner and built-in inbox boundaries."""

from __future__ import annotations

import importlib.util
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import argus_skill
from argus_skill.apps import _runtime
from argus_skill.apps._inbox import queue_inbox_message
from argus_skill.apps._runtime_construction import _inbox_drainer_for
from argus_skill.core.file_lock import FileLockCancelled
from argus_skill.core.operator_context import (
    OperatorContextStore,
    OperatorContextUnavailable,
)
from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig


def _load_repo_fixture(relative, module_name):
    source = Path(argus_skill.__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(module_name, source / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runtime_helper():
    return _load_repo_fixture(
        "tests/apps/test_runtime_context_stop.py", "_inbox_ordinary_runtime_fixture"
    )


@pytest.fixture
def planner_helper():
    return _load_repo_fixture(
        "tests/life/test_planner_delegation_flow.py", "_inbox_ordinary_planner_fixture"
    )


@pytest.fixture(autouse=True)
def inbox_fixture_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "isolated-home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "isolated-cache"))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "off")
    monkeypatch.setenv("ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING", "0")


def ephemeral_classifier(calls):
    def classify(text, *, intake_sink, **kwargs):
        calls.append(text)
        intake_sink({"kind": "ephemeral"})
        return SimpleNamespace(kind="ephemeral")

    return classify


def execute_restarted(case, runtime_helper, monkeypatch, classifier):
    # Construct a new ordinary runner against the same on-disk inbox/state.
    restarted = _runtime._SkillLoopRunner(case.runner._args)
    monkeypatch.setattr(restarted.manager, "classify_front_door", classifier)
    runtime_helper._queue_success(case.backend)
    outcome = restarted.execute(
        objective=case.item.objective,
        sink=SimpleNamespace(handle_event=case.captured["events"].append),
        preplanned=True,
        holds_stage_authority=False,
        mission_id=case.item.id,
        prelude_context_provider=lambda: "Fresh local prelude after restart.",
    )
    assert outcome.success and outcome.status == "done"
    return restarted


def test_ordinary_transient_survives_required_prelude_failure(
    tmp_path, monkeypatch, runtime_helper
):
    case = runtime_helper._runtime_case(tmp_path, monkeypatch)
    calls = []
    classify = ephemeral_classifier(calls)
    monkeypatch.setattr(case.runner.manager, "classify_front_door", classify)
    text = "EPHEMERAL-PRELUDE-FAULT: consider this only for the pending prompt."
    queue_inbox_message(case.state, text, source="private-delivery-acceptance")

    def unavailable():
        raise OperatorContextUnavailable(
            "private required prelude fault after inbox intake"
        )

    with pytest.raises(OperatorContextUnavailable):
        case.execute(provider=unavailable)
    assert calls == [text]
    assert not case.backend.history
    assert all(
        getattr(record, "text", "") != text
        for record in OperatorContextStore(case.state).records()
    )
    execute_restarted(case, runtime_helper, monkeypatch, classify)
    prompts = [
        prompt
        for label, prompt, options in case.backend.history
        if label == "engineer-r1"
    ]
    assert len(prompts) == 1 and text in prompts[0], (
        "Unsettled transient input disappeared before successful prompt assembly"
    )
    assert calls == [text], (
        "A frozen transient decision must not be reclassified after restart"
    )
    case.backend.history.clear()
    execute_restarted(case, runtime_helper, monkeypatch, classify)
    assert all(text not in prompt for label, prompt, options in case.backend.history), (
        "Successfully assembled transient was redelivered"
    )


def supervisor_for(root, workspace, classifier, resolver):
    memory = LifeMemory.open(root)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=SimpleNamespace(),
        sink=JsonlEventSink(None, life_dir=root, verbosity="full"),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            project_worktree=workspace,
            user_inbox=_inbox_drainer_for(root, project_root=workspace),
            pending_question_resolver=resolver,
        ),
    )
    supervisor._bound_manager = lambda: SimpleNamespace(classify_front_door=classifier)
    return supervisor


@pytest.mark.parametrize("resolver_failure", ["exception", "unresolved"])
def test_builtin_pending_question_failure_retains_transient_for_restart(
    tmp_path, resolver_failure
):
    root, workspace = tmp_path / "state", tmp_path / "workspace"
    workspace.mkdir()
    text = "EPHEMERAL-PENDING-ANSWER: the requested local detail is forty-two."
    queue_inbox_message(root, text, source="private-delivery-acceptance")
    calls, first_deliveries, second_deliveries = [], [], []
    classify = ephemeral_classifier(calls)

    def failure(item, message):
        first_deliveries.append(message)
        if resolver_failure == "exception":
            raise OSError("private pending-question persistence failure")
        return {"resolved": False}

    item = SimpleNamespace(id="original-question", pending_question="local detail")
    first = supervisor_for(root, workspace, classify, failure)
    assert first._resolve_pending_question_from_inbox([item]) is False
    assert first_deliveries == [text]

    def success(item, message):
        second_deliveries.append(message)
        return {"resolved": True}

    restarted = supervisor_for(root, workspace, classify, success)
    assert restarted._resolve_pending_question_from_inbox([item]) is True, (
        "Unresolved transient answer disappeared across supervisor restart"
    )
    assert second_deliveries == [text] and calls == [text]
    final = supervisor_for(root, workspace, classify, success)
    assert final._resolve_pending_question_from_inbox([item]) is False
    assert second_deliveries == [text]


def test_ordinary_classifier_does_not_hold_queue_lock_or_allow_second_consumer_to_steal(
    tmp_path, monkeypatch, runtime_helper
):
    case = runtime_helper._runtime_case(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    first = "FIRST-LEASED-EPHEMERAL"
    second = "SECOND-UNCLAIMED-EPHEMERAL"
    calls = []

    def classify(text, *, intake_sink, **kwargs):
        calls.append(text)
        if text == first:
            entered.set()
            assert release.wait(3)
        intake_sink({"kind": "ephemeral"})
        return SimpleNamespace(kind="ephemeral")

    monkeypatch.setattr(case.runner.manager, "classify_front_door", classify)
    queue_inbox_message(case.state, first, source="private-concurrent-delivery")
    runtime_helper._queue_success(case.backend)
    case.worker.start()
    try:
        assert entered.wait(3)
        other = supervisor_for(
            case.state, case.workspace, classify, lambda *_args: {"resolved": False}
        )
        assert other._drain_user_inbox() == []
        queue_inbox_message(case.state, second, source="private-concurrent-delivery")
        assert calls == [first]
    finally:
        release.set()
        case.worker.join(4)
    assert not case.worker.is_alive()
    assert "exception" not in case.captured and case.captured["outcome"].success
    prompts = [
        prompt
        for label, prompt, _options in case.backend.history
        if label == "engineer-r1"
    ]
    assert len(prompts) == 1 and first in prompts[0] and second not in prompts[0]
    try:
        assert other._drain_user_inbox(max_messages=1) == [second]
        assert calls == [first, second]
    finally:
        other._release_operator_inbox()


def test_ordinary_stop_during_classification_keeps_unfrozen_message_without_authority(
    tmp_path, monkeypatch, runtime_helper
):
    from argus_skill.apps._inbox import count_pending_inbox_messages

    case = runtime_helper._runtime_case(tmp_path, monkeypatch)
    text = "STOP-BEFORE-FREEZE-PRIVATE-MARKER"
    calls = []

    def stopped(text, *, intake_sink, **kwargs):
        calls.append(text)
        case.stop.set()
        raise FileLockCancelled("private classifier sees real persistent Stop")

    monkeypatch.setattr(case.runner.manager, "classify_front_door", stopped)
    queue_inbox_message(case.state, text, source="private-stop-proof")
    outcome = case.execute()
    assert not outcome.success and not case.backend.history
    assert count_pending_inbox_messages(case.state) == 1
    assert all(
        text not in getattr(record, "text", "")
        for record in OperatorContextStore(case.state).records()
    )
    case.stop.clear()
    execute_restarted(case, runtime_helper, monkeypatch, ephemeral_classifier(calls))
    assert calls == [text, text], (
        "A Stop before freeze must leave classification retryable"
    )
    assert any(
        text in prompt
        for label, prompt, _options in case.backend.history
        if label == "engineer-r1"
    )


def test_ordinary_frozen_message_survives_real_canonical_lock_object_fault(
    tmp_path, monkeypatch, runtime_helper
):
    from pathlib import Path

    from argus_skill.apps._inbox import count_pending_inbox_messages
    from argus_skill.core import operator_context

    case = runtime_helper._runtime_case(tmp_path, monkeypatch)
    text = "CANONICAL-LOCK-OBJECT-FAULT-PRIVATE-MARKER"
    calls = []

    def classify(text, *, intake_sink, **kwargs):
        calls.append(text)
        intake_sink({"kind": "standing_directive"})
        return SimpleNamespace(kind="standing_directive")

    monkeypatch.setattr(case.runner.manager, "classify_front_door", classify)
    queue_inbox_message(case.state, text, source="private-canonical-fault")
    original = operator_context.apply_operator_delivery
    injected = []

    def real_storage_fault(plan, item_id):
        lock = Path(plan["target_root"]) / "operator_context.lock"
        saved = lock.with_name("private-original-lock")
        lock.rename(saved)
        lock.mkdir()
        injected.append(dict(item_id))
        try:
            return original(plan, item_id)
        finally:
            lock.rmdir()
            saved.rename(lock)

    ledger = (case.state / "operator_context.jsonl").read_bytes()
    with monkeypatch.context() as fault:
        fault.setattr(operator_context, "apply_operator_delivery", real_storage_fault)
        with pytest.raises(OperatorContextUnavailable):
            case.execute()
    assert len(injected) == 1 and calls == [text] and not case.backend.history
    assert (case.state / "operator_context.jsonl").read_bytes() == ledger
    assert count_pending_inbox_messages(case.state) == 1
    execute_restarted(case, runtime_helper, monkeypatch, classify)
    assert calls == [text], (
        "Frozen routing must not rerun the Manager after storage recovery"
    )
    assert (
        sum(
            text == getattr(record, "text", "")
            for record in OperatorContextStore(case.state).records()
        )
        == 1
    )
    assert any(
        text in prompt
        for label, prompt, _options in case.backend.history
        if label == "engineer-r1"
    )


def test_idle_intake_releases_lease_when_supervisor_run_exits_on_tick_failure(
    tmp_path, monkeypatch
):
    root, workspace = tmp_path / "state", tmp_path / "workspace"
    workspace.mkdir()
    text = "IDLE-TRANSIENT-EXIT-PRIVATE-MARKER"
    queue_inbox_message(root, text, source="private-idle-exit")
    calls = []
    classify = ephemeral_classifier(calls)
    first = supervisor_for(
        root, workspace, classify, lambda *_args: {"resolved": False}
    )
    first.config.continuous = True
    first.config.continuous_objective = "Continue the local fixture."
    first._idle_since = time.monotonic() - 1

    def broken_tick():
        raise OSError("private tick fault after idle intake")

    monkeypatch.setattr(first, "tick", broken_tick)
    try:
        result = first.run()
        assert result["stopped_by"] == "supervisor_error" and calls == [text]
        delivered = []

        def resolve(item, message):
            delivered.append(message)
            return {"resolved": True}

        restarted = supervisor_for(root, workspace, classify, resolve)
        assert (
            restarted._resolve_pending_question_from_inbox(
                [SimpleNamespace(id="original-question")]
            )
            is True
        )
        assert delivered == [text] and calls == [text]
    finally:
        # The assertion above must pass without fixture-driven release.
        release = getattr(first, "_release_operator_inbox", None)
        if callable(release):
            release()


def test_real_planner_zero_revision_transient_waits_for_successful_backlog_commit(
    tmp_path, monkeypatch, planner_helper
):
    from argus_skill.apps._inbox import count_pending_inbox_messages

    workspace, root = tmp_path / "workspace", tmp_path / "state"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    text = "PLANNER-ZERO-REVISION-TRANSIENT-PRIVATE-MARKER"
    calls = []
    classify = ephemeral_classifier(calls)
    reply = "\n".join(
        [
            "PROJECT_DONE=false",
            "REASON=delegate the isolated local task",
            "TASK_KEY=private-proof",
            "TASK_TITLE=Inspect local queue evidence",
            "TASK_OBJECTIVE=Inspect the isolated queue and write the requested local report.",
            "TASK_HYPOTHESIS=The delivery identity remains stable after restart.",
            "TASK_GOAL_CONTRIBUTION=Verify durable operator input.",
            "TASK_EXPECTED_REGRESSIONS=Message ordering may change.",
            "TASK_DECISION_RULE=Revise if message identity changes.",
            "TASK_ACCEPTANCE_CHECK=Read the local evidence.",
        ]
    )

    def make():
        backend = planner_helper._PlannerBackend([reply])
        supervisor = planner_helper._supervisor(workspace, root, backend)
        supervisor.config.user_inbox = _inbox_drainer_for(root, project_root=workspace)
        monkeypatch.setattr(
            supervisor,
            "_bound_manager",
            lambda: SimpleNamespace(classify_front_door=classify),
        )
        return supervisor, backend

    first, backend = make()
    queue_inbox_message(root, text, source="private-planner-proof")
    assert OperatorContextStore(root).revision == 0
    original = first.memory.backlog.add_many
    injected = []

    def fail_real_commit(*args, **kwargs):
        lock = root / "backlog.jsonl.lock"
        saved = lock.with_name("private-backlog-original-lock")
        assert lock.is_file()
        lock.rename(saved)
        lock.mkdir()
        injected.append(True)
        try:
            return original(*args, **kwargs)
        finally:
            lock.rmdir()
            saved.rename(lock)

    with monkeypatch.context() as fault:
        fault.setattr(first.memory.backlog, "add_many", fail_real_commit)
        try:
            result = first._plan_next_work()
        except OSError:
            result = None
        assert result is not True
    assert injected and count_pending_inbox_messages(root) == 1
    assert (
        calls == [text]
        and len(backend.calls) == 1
        and text in backend.calls[0]["prompt"]
    )
    assert not first.memory.backlog.pending()
    restarted, retry_backend = make()
    assert restarted._plan_next_work() is True
    assert len(retry_backend.calls) == 1 and text in retry_backend.calls[0]["prompt"]
    assert calls == [text] and OperatorContextStore(root).revision == 0
    assert count_pending_inbox_messages(root) == 0
    assert [item.title for item in restarted.memory.backlog.pending()] == [
        "Inspect local queue evidence"
    ]


@pytest.mark.parametrize("physical_messages", [1, 2])
def test_real_planner_deduplicates_carryover_by_identity_and_settles_equal_text_messages(
    tmp_path, monkeypatch, planner_helper, physical_messages
):
    from argus_skill.apps._inbox import count_pending_inbox_messages
    from argus_skill.skills.stage_machine import current_stage

    workspace, root = tmp_path / "workspace", tmp_path / "state"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    text = "PHYSICAL-IDENTITY-PLANNER-PRIVATE-MARKER"
    calls = []
    backend = planner_helper._PlannerBackend(
        [
            "\n".join(
                [
                    "PROJECT_DONE=false",
                    "REASON=use the current operator detail",
                    "TASK_KEY=identity-proof",
                    "TASK_TITLE=Verify distinct operator messages",
                    "TASK_OBJECTIVE=Inspect the local delivery identities and report the result.",
                    "TASK_HYPOTHESIS=Same text can represent distinct physical messages.",
                    "TASK_GOAL_CONTRIBUTION=Preserve operator delivery identity.",
                    "TASK_EXPECTED_REGRESSIONS=Repeated guidance may appear twice.",
                    "TASK_DECISION_RULE=Revise if a physical message is silently dropped.",
                    "TASK_ACCEPTANCE_CHECK=Inspect the private delivery evidence.",
                ]
            )
        ]
    )
    supervisor = planner_helper._supervisor(workspace, root, backend)
    supervisor.config.user_inbox = _inbox_drainer_for(root, project_root=workspace)
    monkeypatch.setattr(
        supervisor,
        "_bound_manager",
        lambda: SimpleNamespace(classify_front_door=ephemeral_classifier(calls)),
    )
    queue_inbox_message(root, text, source="private-identity-proof")
    if physical_messages == 2:
        stage = current_stage(workspace)
        assert stage
        queue_inbox_message(root, text, source="private-identity-proof", stage=stage)
    carried = supervisor._drain_user_inbox()
    assert len(carried) == physical_messages
    assert len({message.delivery_id for message in carried}) == physical_messages
    supervisor._operator_guidance_carryover = list(carried)
    try:
        assert supervisor._plan_next_work() is True
        assert len(backend.calls) == 1
        assert backend.calls[0]["prompt"].count(text) == physical_messages
        assert len(calls) == physical_messages
        assert count_pending_inbox_messages(root) == 0
    finally:
        supervisor._release_operator_inbox()


@pytest.mark.parametrize(
    "failed_transition", ["accept_inbox_claim", "acknowledge_inbox_claim"]
)
def test_ordinary_applied_once_replay_does_not_resurrect_through_live_turn(
    tmp_path, monkeypatch, runtime_helper, failed_transition
):
    from argus_skill.apps import _inbox

    case = runtime_helper._runtime_case(tmp_path, monkeypatch)
    text = "APPLIED-ONCE-REPLAY-MUST-NOT-REAPPEAR"
    calls = []

    def classify(text, *, intake_sink, **kwargs):
        calls.append(text)
        intake_sink(
            {
                "kind": "revocation",
                "target_revision": 0,
                "applies_to_roles": ["engineer"],
            }
        )
        return SimpleNamespace(kind="revocation")

    monkeypatch.setattr(case.runner.manager, "classify_front_door", classify)
    queue_inbox_message(case.state, text, source="private-once-replay")
    failed = []

    def before_source_transition(*args, **kwargs):
        failed.append(True)
        raise OSError(
            "private fault after canonical effect and receipt, before source transition"
        )

    with monkeypatch.context() as fault:
        fault.setattr(_inbox, failed_transition, before_source_transition)
        with pytest.raises(OperatorContextUnavailable):
            case.execute()
    assert failed and calls == [text] and not case.backend.history
    store = OperatorContextStore(case.state)
    accepted_revision = store.revision
    assert accepted_revision == 2
    projection = store.project("engineer", mission_id=case.item.id, consume_once=True)
    assert sum(text in record.text for record in projection.directives) == 1
    store.compact()
    assert not any(
        text in record.text
        for record in store.project(
            "engineer", mission_id=case.item.id, consume_once=False
        ).directives
    )
    execute_restarted(case, runtime_helper, monkeypatch, classify)
    assert calls == [text] and store.revision == accepted_revision
    assert _inbox.count_pending_inbox_messages(case.state) == 0
    assert all(
        text not in prompt for label, prompt, _options in case.backend.history
    ), "A canonical replay bypassed once consumption through raw live_turn"
