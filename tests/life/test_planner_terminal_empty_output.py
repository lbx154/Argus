"""Terminal empty-plan lifecycle regressions."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from argus.core.event_catalog import EventType
from argus.core.models import RunnerResult
from argus.life.context_packet import (
    create_mission_context,
    record_reviewed_handoff,
)
from argus.life.memory import BacklogItem, LifeMemory, MemoryBundle
from argus.life.supervisor._config import LifeSupervisorConfig
from argus.life.supervisor._constants import (
    PLAN_AWAITING,
    PLAN_ERROR,
    PLAN_RETRY,
    PLAN_TERMINAL_IDLE,
)
from argus.life.supervisor._core import LifeSupervisor
from argus.manager import Manager
from argus.planner import PlannerVerdict
from tests.hand_off import plan, submit

pytestmark = pytest.mark.usefixtures("targeted_vertical")


@pytest.mark.parametrize("report_count", [1, 12])
def test_operator_behind_native_host_reports_reaches_planner_before_completion(
    tmp_path: Path, monkeypatch, report_count: int,
) -> None:
    from argus.apps._inbox import queue_inbox_message
    from argus.apps._inbox_delivery import DurableInboxReceiver
    from argus.core.operator_context import IntakeDecision

    supervisor, backend, sink = _make_supervisor(
        tmp_path, monkeypatch, terminal_stage_done=True,
    )
    supervisor.config.open_ended = False
    receiver = DurableInboxReceiver(
        supervisor.memory.root, consumer="planner",
        project_root=Path(supervisor.config.artifact_root),
    )
    callback = lambda: None
    monkeypatch.setattr(callback, "operator_receiver", receiver, raising=False)
    supervisor.config.user_inbox = callback
    monkeypatch.setattr(
        "argus.apps._inbox_delivery.classify_operator_message",
        lambda *args, **kwargs: IntakeDecision(kind="ephemeral"),
    )
    prompts = []
    run_exec = backend.run_exec

    def record_prompt(**kwargs):
        if kwargs["run_label"].startswith("planner.cycle"):
            assert not any(
                event.get("completion_kind") == "project_completed" for event in sink.events
            )
            prompts.append(kwargs["prompt"])
        return run_exec(**kwargs)

    monkeypatch.setattr(backend, "run_exec", record_prompt)
    for index in range(report_count):
        queue_inbox_message(
            supervisor.memory.root, f"Retained verification {index} finished.",
            source="subagent",
        )
    instruction = "Inspect the newly requested delivery constraint before closing."
    queue_inbox_message(supervisor.memory.root, instruction, source="operator")
    try:
        assert supervisor._bounded_completion_reason() == ""
        assert not any(
            event.get("completion_kind") == "project_completed" for event in sink.events
        )
        supervisor._plan_next_work()
        assert prompts
        assert instruction in prompts[0]
    finally:
        receiver.release_pending()


def test_held_lock_skips_one_inbox_read_alerts_once_and_loses_no_message(
    tmp_path: Path, monkeypatch,
) -> None:
    import threading

    from argus.apps._inbox import queue_inbox_message
    from argus.apps._inbox_delivery import DurableInboxReceiver
    from argus.core import file_lock
    from argus.core.operator_context import IntakeDecision

    supervisor, _backend, sink = _make_supervisor(tmp_path, monkeypatch, terminal_stage_done=False)
    receiver = DurableInboxReceiver(
        supervisor.memory.root, consumer="planner",
        project_root=Path(supervisor.config.artifact_root),
    )
    callback = lambda: None
    monkeypatch.setattr(callback, "operator_receiver", receiver, raising=False)
    supervisor.config.user_inbox = callback
    monkeypatch.setattr(
        "argus.apps._inbox_delivery.classify_operator_message",
        lambda *args, **kwargs: IntakeDecision(kind="ephemeral"),
    )
    monkeypatch.setattr(file_lock, "DEFAULT_FILE_LOCK_TIMEOUT_SECONDS", 0.2)
    instruction = "Also report the p95 latency."
    queue_inbox_message(supervisor.memory.root, instruction, source="operator")

    holding, release = threading.Event(), threading.Event()

    def hold() -> None:
        with supervisor.memory.backlog._locked():
            holding.set()
            release.wait(30)

    holder = threading.Thread(target=hold)
    holder.start()
    assert holding.wait(5)
    try:
        # Someone else holds the lock: planning goes on without the message.
        started = time.monotonic()
        assert supervisor._drain_user_inbox() == []
        assert supervisor._drain_user_inbox() == []
        assert time.monotonic() - started < 10
    finally:
        release.set()
        holder.join()
    alerts = [event for event in sink.events if event.get("operator_alert")]
    assert [alert["reason"] for alert in alerts] == ["operator_inbox_unread"]
    try:
        assert supervisor._drain_user_inbox() == [instruction]
    finally:
        receiver.release_pending()


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _NullRunner:
    pass


class _EmptyPlannerThenManagerRunner:
    def __init__(
        self,
        *,
        manager_action: str = "hold",
        manager_target_stage: str = "delivery",
    ) -> None:
        self.manager_action = manager_action
        self.manager_target_stage = manager_target_stage
        self.planner_calls = 0
        self.manager_calls = 0

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        if run_label.startswith("planner.cycle"):
            self.planner_calls += 1
            submit(options, plan("manager", summary='the final reviewer certification is already complete and there is no legal follow-up work'))
            payload = None
        else:
            assert run_label == "manager-stage"
            self.manager_calls += 1
            payload = {
                "action": self.manager_action,
                "target_stage": self.manager_target_stage,
                "reason": "final delivery remains certified; hold terminal stage",
            }
        return RunnerResult(
            exit_code=0,
            agent_messages=[json.dumps(payload)] if payload else [],
            stdout_lines=[],
            stderr_lines=[],
            thread_id=(
                "planner-thread"
                if run_label.startswith("planner.cycle")
                else None
            ),
            fatal_error=None,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
        )


class _ContentFilterPlannerRunner(_EmptyPlannerThenManagerRunner):
    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.planner_calls += 1
        return RunnerResult(
            exit_code=1,
            agent_messages=[],
            stdout_lines=[],
            stderr_lines=[],
            thread_id=(
                "planner-thread"
                if run_label.startswith("planner.cycle")
                else None
            ),
            fatal_error=(
                "Copilot content filtering blocked the response; the identical "
                "prompt must not be retried"
            ),
            stop_kind="permanent_error",
        )


class _TaskPlannerRunner(_EmptyPlannerThenManagerRunner):
    def __init__(self, stage: str = "delivery") -> None:
        super().__init__()
        self.stage = stage

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        payload = None
        if run_label.startswith("planner.cycle"):
            self.planner_calls += 1
            submit(options, plan(
                summary="one concrete repair remains",
                # A decision that creates tasks while a stage is active must name one.
                advance_to_stage=self.stage,
                tasks=[{
                    "key": "planner-empty-repair",
                    "title": "Repair empty planner verdict handling",
                    "objective": "Update planner lifecycle handling and run the focused tests.",
                    "hypothesis": "Empty verdict recovery loses concrete next work.",
                    "goal_contribution": "Restore reliable Planner continuation.",
                    "expected_regressions": "Planner retry status may remain noisy during repair.",
                    "decision_rule": "Replan if the empty output comes from provider failure.",
                    "acceptance_check": "pytest tests/planner/test_planner.py",
                    "scope": "bounded",
                }],
            ))
        else:
            assert run_label == "manager-stage"
            self.manager_calls += 1
            payload = {
                "action": self.manager_action,
                "target_stage": self.manager_target_stage,
                "reason": "final delivery remains certified; hold terminal stage",
            }
        return RunnerResult(
            exit_code=0,
            agent_messages=[json.dumps(payload)] if payload else [],
            stdout_lines=[],
            stderr_lines=[],
            thread_id=(
                "planner-thread"
                if run_label.startswith("planner.cycle")
                else None
            ),
            fatal_error=None,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
        )


def _write_software_state(project: Path, *, done: bool) -> None:
    """Write a delivery stage that is genuinely certified when ``done``.

    ``status: done`` alone is not certification. `software` declares
    COMPLETION_CONTRACT_VERSION = 1, so the terminal reconciliation checks for a
    matching ``completion_contract_sha256`` — the guard that stops a prematurely
    "done" stage from reading as certified.

    This fixture used to write the status only, which made the "certified
    terminal" case it names unreachable. That mirrored production: until the
    completion livelock was fixed on 2026-07-26, a non-paper vertical could
    never obtain this certificate at all, so the scenario did not exist. Now
    that it does, the fixture builds it the way the Manager does.
    """
    research = project / "research"
    research.mkdir(parents=True, exist_ok=True)
    state_path = project / ".argus" / "PIPELINE_STATE.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    record: dict = {"status": "done" if done else "in_progress"}
    state_path.write_text(
        json.dumps(
            {
                "vertical": "software",
                "current_stage": "delivery",
                "stages": {"delivery": record},
            }
        ),
        encoding="utf-8",
    )
    if not done:
        return
    from argus.skills.stage_machine import completion_contract_fingerprint
    from argus.verticals._base import load_vertical_contract

    version = load_vertical_contract("software", project_root=project).completion_contract_version
    if version <= 0:
        return
    record["completion_contract_version"] = version
    record["completion_contract_sha256"] = completion_contract_fingerprint(
        project, "delivery", version=version
    )
    state_path.write_text(
        json.dumps(
            {
                "vertical": "software",
                "current_stage": "delivery",
                "stages": {"delivery": record},
            }
        ),
        encoding="utf-8",
    )


def _write_reviewed_scope_state(project: Path) -> None:
    research = project / "research"
    research.mkdir(parents=True, exist_ok=True)
    state_path = project / ".argus" / "PIPELINE_STATE.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "vertical": "targeted_lab",
                "current_stage": "scope",
                "research_target_level": "doctoral",
                "workflow_mode": "staged",
            }
        ),
        encoding="utf-8",
    )
    (research / "scope_definition.json").write_text(
        json.dumps(
            {
                "artifact_kind": "scope_definition",
                "stage": "scope",
                "research_target_level": "doctoral",
                "candidate_screening_started": False,
                "reviewer_decisive_check": {
                    "scope.problem_explicit": "satisfied",
                    "scope.success_criterion": "satisfied",
                },
            }
        ),
        encoding="utf-8",
    )


def _candidate_artifact_paths(project: Path) -> list[Path]:
    research = project / "research"
    return [path for path in research.rglob("*candidate*") if path.name != "scope_definition.json"]


def _make_supervisor(
    tmp_path: Path,
    monkeypatch,
    *,
    terminal_stage_done: bool,
    split_memory: bool = False,
    backend: _EmptyPlannerThenManagerRunner | None = None,
) -> tuple[LifeSupervisor, _EmptyPlannerThenManagerRunner, _RecordingSink]:
    project = tmp_path / "project"
    project.mkdir()
    _write_software_state(project, done=terminal_stage_done)
    if split_memory:
        memory = MemoryBundle.for_cwd(
            project,
            global_root=tmp_path / "global",
            fingerprint="s-empty-plan",
        )
        memory.global_mem.init()
        memory.project.init()
    else:
        memory = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink()
    backend = backend or _EmptyPlannerThenManagerRunner()
    runner = _NullRunner()
    runner.manager = Manager(
        project_root=project,
        execution_workdir=project,
        runner=backend,
    )
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="finish the private framework repair",
            paper_mission=False,
            final_certification_gate=False,
            open_ended=True,
            project_worktree=project,
            artifact_root=project,
        ),
        planner_runner=backend,
    )

    monkeypatch.setattr(supervisor, "_maybe_idle_after_unchanged_open_ended_done", lambda: None)
    monkeypatch.setattr(supervisor, "_resolve_vertical_once", lambda: None)
    monkeypatch.setattr(supervisor, "_wiki_collect_task_if_due_under_blocker", lambda: None)
    monkeypatch.setattr(supervisor, "_render_journal_for_planner", lambda: "")
    monkeypatch.setattr(supervisor, "_effective_final_certification_gate", lambda *_a, **_k: False)
    monkeypatch.setattr(supervisor, "_planner_runtime_notes", lambda: "")
    return supervisor, backend, sink


def test_bounded_completed_campaign_stops_before_planner_cycle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, _backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=True,
    )
    supervisor.config.open_ended = False
    monkeypatch.setattr(
        supervisor,
        "_manager_publish_project_report",
        lambda _reason, **_kwargs: "reported",
    )

    result = supervisor.run()

    assert result["stopped_by"] == "project_done"
    assert result["planning_cycles"] == 0
    assert not any(
        event.get("type") == EventType.LIFE_PLANNER_START
        for event in sink.events
    )


@pytest.mark.parametrize("terminal_stage_done", [False, True])
def test_native_host_report_does_not_block_bounded_planning_or_completion(
    tmp_path: Path, monkeypatch, terminal_stage_done: bool,
) -> None:
    from argus.apps._inbox import queue_inbox_message
    from argus.apps._inbox_delivery import DurableInboxReceiver, is_host_report
    from argus.life.supervisor._planning_cycle_helpers import _PlanCycleState

    supervisor, _backend, sink = _make_supervisor(
        tmp_path, monkeypatch, terminal_stage_done=terminal_stage_done,
    )
    supervisor.config.open_ended = False
    monkeypatch.setattr(
        supervisor, "_manager_publish_project_report", lambda _reason, **_kwargs: "reported",
    )
    receiver = DurableInboxReceiver(
        supervisor.memory.root, consumer="planner",
        project_root=Path(supervisor.config.artifact_root),
    )
    callback = lambda: None
    monkeypatch.setattr(callback, "operator_receiver", receiver, raising=False)
    supervisor.config.user_inbox = callback
    state = _PlanCycleState(None)
    assert supervisor._pc_intake_gate(state) is None
    state.manager_intent = {"vertical": "software", "workflow_mode": "staged"}
    queue_inbox_message(
        supervisor.memory.root, "Retained verification job finished.", source="subagent",
    )
    try:
        supervisor._operator_guidance_carryover = receiver.receive()
        assert is_host_report(supervisor._operator_guidance_carryover[0])
        result = supervisor._pc_preflight_shortcircuits(state)
        if terminal_stage_done:
            assert result is False
            assert any(
                event.get("completion_kind") == "project_completed"
                for event in sink.events
            )
        else:
            assert result is None
    finally:
        receiver.release_pending()


def test_bounded_completion_waits_for_live_backlog(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, _backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=True,
    )
    supervisor.config.open_ended = False
    item = supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Run the verification",
            objective="Run the verification before completing.",
        )
    )
    supervisor.memory.backlog.mark_running(item.id)

    assert supervisor._bounded_completion_reason() == ""

    from argus.life.supervisor._planning_cycle_helpers import _PlanCycleState

    state = _PlanCycleState(None)
    state.verdict = PlannerVerdict(
        project_done=True,
        new_tasks=[],
        summary="The visible artifact looks complete.",
    )
    assert supervisor._pc_normalize_project_done(state) == PLAN_RETRY
    assert any(
        event.get("diagnostic") == "bounded_completion_invariant_failed"
        and "live backlog remains" in str(event.get("reason") or "")
        for event in sink.events
    )
    feedback = supervisor._load_manager_planner_feedback()
    assert feedback is not None
    assert "live backlog remains" in str(feedback["reason"])
    assert "Do not create duplicate tasks" in (
        supervisor._manager_planner_feedback_runtime_note()
    )
    rejected_signature = str(feedback["evidence_signature"])

    supervisor.memory.backlog.mark_done(item.id)

    assert supervisor._manager_feedback_signature_for(
        "bounded_completion_invariant_failed"
    ) != rejected_signature
    assert supervisor._bounded_completion_reason()


@pytest.mark.parametrize("input_source", ["inbox", "carryover", "durable"])
@pytest.mark.parametrize("workflow_mode", ["staged", "direct"])
def test_bounded_certificate_cannot_skip_unhandled_operator_input(
    tmp_path: Path, monkeypatch, input_source: str, workflow_mode: str,
) -> None:
    from argus.core.operator_context import OperatorContextStore, operator_context_state_root
    from argus.life.supervisor._planning_cycle_helpers import _PlanCycleState
    from argus.manager.directive import record_operator_messages

    supervisor, _backend, sink = _make_supervisor(
        tmp_path, monkeypatch, terminal_stage_done=True,
    )
    supervisor.config.open_ended = False
    message = "Deliver the missing report through independent review; retain accepted engineering."
    context = OperatorContextStore(operator_context_state_root(supervisor.memory))
    if input_source == "inbox":
        messages = [message]
        supervisor.config.user_inbox = lambda: messages.pop(0) if messages else None
    elif input_source == "carryover":
        supervisor._operator_guidance_carryover = [message]
    else:
        record_operator_messages(context.root, [message])
    certificate = Path(supervisor.config.artifact_root) / ".argus" / "PIPELINE_STATE.json"
    original = certificate.read_bytes()
    states = []

    def plan():
        state = _PlanCycleState(None)
        assert supervisor._pc_intake_gate(state) is None
        assert message in "\n".join(state.operator_messages + state.fresh_operator_messages)
        assert state.had_operator_messages or state.has_unhandled_operator_input
        state.manager_intent = {"vertical": "software", "workflow_mode": workflow_mode}
        assert supervisor._pc_preflight_shortcircuits(state) is None
        assert supervisor.memory.backlog.active() == []
        states.append(state)
        return PLAN_AWAITING

    monkeypatch.setattr(supervisor, "_plan_next_work", plan)
    result = supervisor.run()
    assert result["stopped_by"] == PLAN_AWAITING
    assert len(states) == 1
    assert not any(event.get("project_done") is True for event in sink.events)
    assert certificate.read_bytes() == original
    assert context.acknowledged_revision("planner") == 0
    context.acknowledge("planner", context.revision)
    assert supervisor._bounded_completion_reason()


@pytest.mark.parametrize("input_source", ["durable", "native_inbox"])
@pytest.mark.parametrize("delivery", ["pending_event", "pending_report", "delivered"])
@pytest.mark.parametrize("verbosity", ["signal", "full"])
def test_completed_outbox_cannot_replay_over_new_input(
    tmp_path: Path, monkeypatch, input_source: str, delivery: str, verbosity: str,
) -> None:
    from argus.apps._inbox import count_pending_inbox_messages, queue_inbox_message
    from argus.apps._runtime_construction import _inbox_drainer_for
    from argus.core.operator_context import OperatorContextStore, append_directive
    from argus.life.event_log import JsonlEventSink
    from argus.life.planner_verdict_outbox import load_planner_verdict_outbox

    supervisor, _backend, _sink = _make_supervisor(
        tmp_path, monkeypatch, terminal_stage_done=True,
    )
    supervisor.config.open_ended = False
    life = supervisor.memory.root
    project = Path(supervisor.config.artifact_root)
    supervisor.sink = JsonlEventSink(None, life_dir=life, verbosity=verbosity)
    append = supervisor.sink._append
    if delivery == "pending_event":
        monkeypatch.setattr(
            supervisor.sink, "_append",
            lambda event: False if event.get("type") == "life.planner.verdict" else append(event),
        )
    delivered = delivery == "delivered"
    reports = []

    def report(_reason, **_kwargs):
        reports.append(True)
        return "reported" if delivered else "pending"

    monkeypatch.setattr(supervisor, "_manager_publish_project_report", report)
    assert supervisor._emit_bounded_project_completion("Original engineering accepted") == (
        False if delivered else PLAN_RETRY
    )
    monkeypatch.setattr(supervisor.sink, "_append", append)
    prior = load_planner_verdict_outbox(life)
    assert prior is not None and prior["delivered"] is delivered
    certificate = project / ".argus" / "PIPELINE_STATE.json"
    original = certificate.read_bytes()
    message = "Deliver the missing report without repeating accepted engineering."
    context = OperatorContextStore(life)
    classified = []
    receiver = None
    if input_source == "durable":
        append_directive(life, message, lifetime="once", expected_revision=context.revision)
    else:
        def classify(text, *, intake_sink, **_kwargs):
            classified.append(text)
            intake_sink({"kind": "ephemeral"})

        monkeypatch.setattr(
            supervisor, "_bound_manager",
            lambda: SimpleNamespace(classify_front_door=classify),
        )
        supervisor.config.user_inbox = _inbox_drainer_for(life, project_root=project)
        receiver = supervisor.config.user_inbox.operator_receiver
        queue_inbox_message(life, message, source="operator")

    class PlannerReached(BaseException):
        pass

    calls = []

    def plan(_planner, **kwargs):
        assert message in str(kwargs)
        calls.append(True)
        raise PlannerReached

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan)
    try:
        for _ in range(2):
            with pytest.raises(PlannerReached):
                supervisor._plan_next_work()
        assert len(calls) == 2
        assert len(reports) == (0 if delivery == "pending_event" else 1)
        assert load_planner_verdict_outbox(life) is None
        assert certificate.read_bytes() == original
        assert supervisor.memory.backlog.active() == []
        assert context.acknowledged_revision("planner") == 0
        events = [json.loads(line) for line in (life / "events.jsonl").read_text(
            encoding="utf-8",
        ).splitlines()]
        assert sum(
            event.get("type") == "life.planner.verdict"
            and event.get("delivery_id") == prior["delivery_id"]
            for event in events
        ) == (0 if delivery == "pending_event" else 1)
        discarded = [event for event in events
                     if event.get("type") == "life.planner.verdict.discarded"]
        assert len(discarded) == 1
        assert discarded[0]["prior_verdict"] == prior["event"]
        assert discarded[0]["prior_delivered"] is delivered
        if receiver is not None:
            assert classified == [message]
            assert count_pending_inbox_messages(life) == 1
    finally:
        if receiver is not None:
            receiver.release_pending()


def test_completion_outbox_survives_failed_supersession_persistence(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus.core.operator_context import OperatorContextStore, append_directive
    from argus.life.event_log import JsonlEventSink
    from argus.life.planner_verdict_outbox import load_planner_verdict_outbox
    from argus.life.supervisor._planning_cycle_helpers import _PlanCycleState

    supervisor, _backend, _sink = _make_supervisor(
        tmp_path, monkeypatch, terminal_stage_done=True,
    )
    supervisor.config.open_ended = False
    life = supervisor.memory.root
    supervisor.sink = JsonlEventSink(None, life_dir=life, verbosity="signal")
    monkeypatch.setattr(supervisor.sink, "_append", lambda _event: False)
    assert supervisor._emit_bounded_project_completion("Original engineering accepted") == PLAN_RETRY
    prior = load_planner_verdict_outbox(life)
    assert prior is not None and prior["delivered"] is False
    context = OperatorContextStore(life)
    append_directive(
        life, "Deliver the missing report.", lifetime="once", expected_revision=context.revision,
    )
    certificate = Path(supervisor.config.artifact_root) / ".argus" / "PIPELINE_STATE.json"
    original = certificate.read_bytes()

    for _ in range(2):
        assert supervisor._pc_intake_gate(_PlanCycleState(None)) == PLAN_RETRY
        assert load_planner_verdict_outbox(life) == prior
        assert context.acknowledged_revision("planner") == 0
        assert certificate.read_bytes() == original
        assert supervisor.memory.backlog.active() == []


def test_bounded_completion_keeps_late_received_input_for_fresh_intake(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus.life.supervisor._planning_cycle_helpers import _PlanCycleState

    supervisor, _backend, sink = _make_supervisor(
        tmp_path, monkeypatch, terminal_stage_done=True,
    )
    supervisor.config.open_ended = False
    message = "A new report requirement arrived after intake."
    supervisor.config.user_inbox = lambda: None
    state = _PlanCycleState(None)
    assert supervisor._pc_intake_gate(state) is None
    messages = [message]
    supervisor.config.user_inbox = lambda: messages.pop(0) if messages else None
    state.manager_intent = {"vertical": "software", "workflow_mode": "staged"}
    assert supervisor._pc_preflight_shortcircuits(state) == PLAN_RETRY
    assert supervisor._operator_guidance_carryover == [message]
    assert not any(event.get("project_done") is True for event in sink.events)
    next_state = _PlanCycleState(None)
    assert supervisor._pc_intake_gate(next_state) is None
    assert message in "\n".join(next_state.operator_messages + next_state.fresh_operator_messages)
    assert supervisor._operator_guidance_carryover == []


def test_direct_research_can_complete_its_bounded_deliverable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, backend, _sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
    )
    supervisor.config.open_ended = False
    project = Path(supervisor.config.artifact_root)
    from argus.skills import stage_machine
    from argus.skills.vertical_select import persist_vertical

    persist_vertical(
        project,
        "research",
        research_target_level="exploratory",
        workflow_mode="direct",
    )
    objective = "Complete the reviewed direct research deliverable."
    handoff = {
        "version": 4,
        "objective": objective,
        "vertical": "research",
        "domain": "",
        "continuous_generation": 1,
        "intent_id": "intent-current",
        "recorded_at": time.time(),
    }
    (project / "manager-handoff.json").write_text(
        json.dumps(handoff),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        stage_machine,
        "_ensure_stage_completion",
        lambda *_args, **_kwargs: None,
    )
    stage_machine.complete_final_stage(
        project,
        reason="The reviewed direct objective is complete.",
        allow_early_completion=True,
    )
    state = json.loads(
        (project / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert state["stages"]["idea"]["status"] == "done"
    assert state["stages"]["idea"]["completion_intent_id"] == "intent-current"
    assert state["stages"]["idea"]["completion_objective"] == objective
    from argus.skills.vertical_select import vertical_completion_certificate_status

    assert vertical_completion_certificate_status(project, "research")["ok"] is True
    handoff["intent_id"] = "intent-replacement"
    (project / "manager-handoff.json").write_text(
        json.dumps(handoff),
        encoding="utf-8",
    )
    status = vertical_completion_certificate_status(project, "research")
    assert status["ok"] is False
    assert status["reason"] == "completion belongs to a different Manager intent"
    assert backend.planner_calls == 0


def test_bounded_staged_research_still_requires_submission_journal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, _backend, _sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
    )
    supervisor.config.open_ended = False
    project = Path(supervisor.config.artifact_root)
    from argus.skills import stage_machine
    from argus.skills.vertical_select import persist_vertical

    persist_vertical(
        project,
        "research",
        research_target_level="exploratory",
        workflow_mode="staged",
    )
    state_path = project / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["current_stage"] = "submission"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(
        stage_machine,
        "_ensure_stage_completion",
        lambda *_args, **_kwargs: None,
    )
    stage_machine.complete_final_stage(
        project,
        reason="The staged final stage is complete.",
    )

    assert supervisor._bounded_completion_reason() == ""


def test_standing_campaign_is_not_stopped_by_bounded_completion_certificate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, _backend, _sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=True,
    )
    planner_calls = 0

    def plan_once(*args, **kwargs):
        nonlocal planner_calls
        planner_calls += 1
        return PLAN_TERMINAL_IDLE

    monkeypatch.setattr(supervisor, "_plan_next_work", plan_once)

    result = supervisor.run()

    assert result["stopped_by"] == PLAN_TERMINAL_IDLE
    assert planner_calls == 1


def test_content_filtered_planner_disarms_campaign_instead_of_retrying(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus.daemon.state import (
        read_continuous_state,
        write_continuous_config,
    )

    backend = _ContentFilterPlannerRunner()
    supervisor, _backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        backend=backend,
    )
    write_continuous_config(
        supervisor.memory.root,
        enabled=True,
        objective=supervisor.config.continuous_objective,
    )

    assert supervisor._plan_next_work() == PLAN_ERROR

    state = read_continuous_state(supervisor.memory.root)
    assert state.enabled is False
    assert "operator reformulation required" in state.done_reason
    assert backend.planner_calls == 1
    error = next(event for event in sink.events if event.get("type") == "life.planner.error")
    assert error["operator_alert"] is True
    assert error["stop_kind"] == "permanent_error"


def test_execution_host_failure_pauses_planner_until_explicit_rearm(tmp_path, monkeypatch):
    from argus.daemon.state import read_continuous_state, write_continuous_config

    diagnostic = (
        "Code Mode is unavailable because failed to spawn code-mode host "
        "/codex/codex-code-mode-host: host executable was not found. "
        "Code mode will fail closed."
    )

    class HostFailureRunner(_ContentFilterPlannerRunner):
        def run_exec(self, **kwargs):
            result = super().run_exec(**kwargs)
            result.exit_code = 0
            result.fatal_error = diagnostic
            result.stop_kind = "backend_unavailable"
            return result

    backend = HostFailureRunner()
    supervisor, _, sink = _make_supervisor(
        tmp_path, monkeypatch, terminal_stage_done=False, backend=backend,
    )
    write_continuous_config(
        supervisor.memory.root, enabled=True,
        objective=supervisor.config.continuous_objective, open_ended=False,
    )
    assert supervisor._plan_next_work() == PLAN_AWAITING
    state = read_continuous_state(supervisor.memory.root)
    assert (state.enabled, state.objective, state.open_ended) == (
        False, supervisor.config.continuous_objective, False,
    )
    assert state.done_reason == diagnostic
    assert backend.planner_calls == 1

    # A fresh supervisor reads the same durable gate, before invoking Planner.
    restored = LifeSupervisor(
        memory=supervisor.memory, runner=supervisor.runner,
        planner_runner=backend, config=supervisor.config, sink=sink,
    )
    monkeypatch.setattr(restored, "_resolve_vertical_once", lambda: None)
    for _ in range(3):
        assert restored._plan_next_work() == PLAN_AWAITING
    assert backend.planner_calls == 1
    write_continuous_config(
        supervisor.memory.root, enabled=True, objective=state.objective, open_ended=False,
    )
    assert restored._runtime_failure_circuit_block() is None
    assert restored._plan_next_work() == PLAN_AWAITING
    assert backend.planner_calls == 2
    assert not read_continuous_state(supervisor.memory.root).enabled



class _StructuredPlannerRunner(_EmptyPlannerThenManagerRunner):
    def __init__(self) -> None:
        super().__init__()
        self.extra_tasks: list[dict] = []
        self.settle: list[int] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        assert run_label.startswith("planner.cycle")
        self.planner_calls += 1
        submit(options, plan(
            summary="one structured task remains",
            # The fixture project sits in the `delivery` stage, and a decision
            # that creates tasks while a stage is active must name one. This
            # fixture is not testing stage selection.
            advance_to_stage="delivery",
            tasks=[{
                "key": "typed-task",
                "title": "Execute typed work",
                "objective": "Run the task exactly as structured.",
                "scope": "bounded",
            }, *self.extra_tasks],
            settle_instructions=self.settle,
        ))
        return RunnerResult(exit_code=0, thread_id="planner-thread")


class _SettlingStructuredPlannerRunner(_StructuredPlannerRunner):
    """Proposes the typed task plus one new task and settles an instruction."""

    def __init__(self) -> None:
        super().__init__()
        self.extra_tasks.append({
            "key": "fresh-task",
            "title": "Profile the loader",
            "objective": "Profile loader startup.",
            "scope": "bounded",
        })


def test_structured_settlement_counts_a_duplicate_of_live_work(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus.core.operator_context import (
        OperatorContextStore,
        append_directive,
        operator_context_state_root,
    )

    backend = _SettlingStructuredPlannerRunner()
    supervisor, backend, _sink = _make_supervisor(
        tmp_path, monkeypatch, terminal_stage_done=False, backend=backend,
    )
    existing = supervisor.memory.backlog.add(BacklogItem.new(
        title="Assess publication-scale evidence gaps",
        objective="Inspect the current evidence boundary.",
        node_key="typed-task",
        tags=["planner"],
    ))
    root = operator_context_state_root(supervisor.memory)
    store = OperatorContextStore(root)
    idle = append_directive(
        root, "Run the typed work next.", scope="mission",
        lifetime="bounded_increment", expected_revision=store.revision,
    )
    backend.settle = [idle.revision]

    supervisor._plan_next_work()

    active = supervisor.memory.backlog.active()
    assert existing.id in [item.id for item in active]
    assert "Profile the loader" in [item.title for item in active]
    visible = [
        record.revision
        for record in store.project("planner", consume_once=False).directives
    ]
    assert idle.revision not in visible


def test_legacy_backlog_item_ignores_work_kind() -> None:
    item = BacklogItem.from_jsonable({
        "id": "legacy-item",
        "ts": 1.0,
        "title": "Deliver optimized algorithm",
        "objective": "Set up the environment, validate, and ship.",
        "work_kind": "validation",
    })

    assert not hasattr(item, "work_kind")
    assert "work_kind" not in item.to_jsonable()


def test_nonterminal_planning_replays_unassessed_current_stage_review_first(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        split_memory=True,
    )
    backend.manager_action = "advance"
    backend.manager_target_stage = "solve"
    project = Path(supervisor.config.project_worktree)
    _write_reviewed_scope_state(project)
    assert _candidate_artifact_paths(project) == []
    item = supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Define the mathematical scope",
            objective="State the admissible conjecture class and completion bar.",
            tags=["planner", "scope:bounded"],
        )
    )
    mission_path = create_mission_context(
        life_dir=supervisor.memory.project_root,
        mission_id=item.id,
        stage="scope",
        objective=item.objective,
        scope="bounded",
    )
    record_reviewed_handoff(
        mission_context_path=mission_path,
        round_index=1,
        engineer_summary="",
        review=SimpleNamespace(
            status="done",
            reason="The scope checklist is satisfied by the current artifacts.",
            next_action="",
            operator_question="",
        ),
        checkpoint_path=None,
    )
    supervisor.memory.backlog.mark_done(
        item.id,
        outcome={
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "not_assessed",
            "interruption_kind": "none",
            "resumable": False,
        },
    )
    assert not (supervisor.memory.root / "handoffs").exists()

    assert supervisor._plan_next_work() == PLAN_RETRY

    assert backend.planner_calls == 0
    assert backend.manager_calls == 1
    state = json.loads((project / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "solve"
    assert state["research_target_level"] == "doctoral"
    assert _candidate_artifact_paths(project) == []
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.outcome["stage_certification"] == "certified"
    assert supervisor.memory.backlog.pending() == []
    assert not any(event.get("type") == "life.planner.error" for event in sink.events)
    assert any(
        event.get("type") == "life.manager.stage_decision"
        and event.get("action") == "advance"
        and event.get("trigger") == "reviewed_stage_empty_plan_reconciliation"
        and event.get("recovered_item_id") == item.id
        for event in sink.events
    )


@pytest.mark.parametrize("scale_sufficient", [False, True])
def test_reviewed_experiment_reaches_planner_before_paper(
    tmp_path: Path, monkeypatch, scale_sufficient: bool,
) -> None:
    from argus.skills.stage_machine import current_stage
    from argus.skills.vertical_select import persist_vertical

    target = "paper" if scale_sufficient else "experiment"
    title = "Write the paper" if scale_sufficient else "Expand released task coverage"

    class ScalePlannerRunner(_EmptyPlannerThenManagerRunner):
        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            assert run_label.startswith("planner.cycle")
            self.planner_calls += 1
            assert "Post-result experiment scale assessment" in prompt
            submit(options, plan(
                summary="Reviewed coverage is sufficient for writing."
                if scale_sufficient
                else "Reviewed coverage needs more official task settings.",
                advance_to_stage=target,
                tasks=[{
                    "key": "scale-followup",
                    "title": title,
                    "objective": f"{title} using the existing implementation and official benchmark.",
                    "hypothesis": "The next step addresses the reviewed coverage.",
                    "goal_contribution": "Support the operator research objective.",
                    "expected_regressions": "Preserve accepted raw results.",
                    "decision_rule": "Use coverage and precision for the objective.",
                    "acceptance_check": "Review direct artifacts against the objective.",
                    "scope": "bounded",
                }],
            ))
            return RunnerResult(
                exit_code=0,
                stdout_lines=[],
                stderr_lines=[],
            )

    supervisor, backend, _sink = _make_supervisor(
        tmp_path, monkeypatch, terminal_stage_done=False,
        backend=ScalePlannerRunner(),
    )
    project = Path(supervisor.config.project_worktree)
    persist_vertical(project, "research", workflow_mode="staged")
    state_path = project / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text())
    state["current_stage"] = "experiment"
    state_path.write_text(json.dumps(state))
    item = supervisor.memory.backlog.add(BacklogItem.new(
        title="Run the current experiment",
        objective="Evaluate the existing official task panel.",
        tags=["planner", "scope:bounded"],
    ))
    mission_path = create_mission_context(
        life_dir=supervisor.memory.root, mission_id=item.id,
        stage="experiment", objective=item.objective, scope="bounded",
    )
    record_reviewed_handoff(
        mission_context_path=mission_path, round_index=1,
        engineer_summary="The configured panel is complete.",
        review=SimpleNamespace(
            status="done", reason="The current comparison is accepted.",
            next_action="", operator_question="",
        ),
        checkpoint_path=None,
    )
    supervisor.memory.backlog.mark_done(item.id, outcome={
        "execution_status": "completed",
        "review_status": "done",
        "stage_certification": "not_assessed",
        "interruption_kind": "none",
        "resumable": False,
    })

    supervisor._plan_next_work()

    assert backend.planner_calls == 1
    assert backend.manager_calls == 0
    assert current_stage(project) == target
    assert [row.title for row in supervisor.memory.backlog.pending()] == [title]


def test_newer_replan_review_blocks_older_stage_replay(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend = _TaskPlannerRunner(stage="scope")
    supervisor, backend, _sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        backend=backend,
        split_memory=True,
    )
    project = Path(supervisor.config.project_worktree)
    _write_reviewed_scope_state(project)
    older = supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Define the mathematical scope",
            objective="State the admissible conjecture class and completion bar.",
            tags=["planner", "scope:bounded", "stage:scope"],
        )
    )
    older_mission = create_mission_context(
        life_dir=supervisor.memory.project_root,
        mission_id=older.id,
        stage="scope",
        objective=older.objective,
        scope="bounded",
    )
    record_reviewed_handoff(
        mission_context_path=older_mission,
        round_index=1,
        engineer_summary="",
        review=SimpleNamespace(
            status="done",
            reason="The previous scope evidence passed.",
            next_action="",
            operator_question="",
        ),
        checkpoint_path=None,
    )
    supervisor.memory.backlog.mark_done(
        older.id,
        outcome={
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "deferred",
            "interruption_kind": "none",
            "resumable": False,
        },
    )
    newer = supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Challenge the mathematical scope",
            objective="Test whether the accepted scope evidence remains valid.",
            tags=["planner", "scope:bounded", "stage:scope"],
        )
    )
    newer_mission = create_mission_context(
        life_dir=supervisor.memory.project_root,
        mission_id=newer.id,
        stage="scope",
        objective=newer.objective,
        scope="bounded",
    )
    record_reviewed_handoff(
        mission_context_path=newer_mission,
        round_index=1,
        engineer_summary="",
        review=SimpleNamespace(
            status="continue",
            reason="The newer evidence invalidates the previous scope decision.",
            next_action="Repair the scope evidence.",
            operator_question="",
        ),
        checkpoint_path=None,
    )
    supervisor.memory.backlog.update(
        newer.id,
        status="failed",
        finished_ts=time.time() + 1,
        outcome={
            "execution_status": "ended",
            "review_status": "continue",
            "stage_certification": "deferred",
            "interruption_kind": "none",
            "resumable": False,
        },
    )

    assert supervisor._plan_next_work() is True

    assert backend.planner_calls == 1
    assert backend.manager_calls == 0
    state = json.loads((project / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "scope"


def test_bounded_continuous_campaign_replays_deferred_stage_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A bounded staged campaign still traverses stages.

    ``open_ended`` decides whether a Planner ``project_done`` is honoured, not
    whether stages may advance; gating the replay on it meant no vertical whose
    completion gate is not ``certified`` could ever leave its first stage. The
    item carries ``stage_certification="deferred"`` — a Planner node that held
    the stage rather than closing it — which is precisely the reviewed evidence
    this replay exists to recover.
    """
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        split_memory=True,
    )
    supervisor.config.open_ended = False
    backend.manager_action = "advance"
    backend.manager_target_stage = "solve"
    project = Path(supervisor.config.project_worktree)
    _write_reviewed_scope_state(project)
    item = supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Define the mathematical scope",
            objective="State the admissible conjecture class and completion bar.",
            tags=["planner", "scope:bounded", "bounded_dag_node"],
        )
    )
    mission_path = create_mission_context(
        life_dir=supervisor.memory.project_root,
        mission_id=item.id,
        stage="scope",
        objective=item.objective,
        scope="bounded",
    )
    record_reviewed_handoff(
        mission_context_path=mission_path,
        round_index=1,
        engineer_summary="",
        review=SimpleNamespace(
            status="done",
            reason="The scope checklist is satisfied by the current artifacts.",
            next_action="",
            operator_question="",
        ),
        checkpoint_path=None,
    )
    supervisor.memory.backlog.mark_done(
        item.id,
        outcome={
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "deferred",
            "interruption_kind": "none",
            "resumable": False,
        },
    )
    assert supervisor._plan_next_work() == PLAN_RETRY

    state = json.loads((project / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "solve"
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.outcome["stage_certification"] == "certified"
    assert any(
        event.get("type") == "life.manager.stage_decision"
        and event.get("action") == "advance"
        and event.get("trigger") == "reviewed_stage_empty_plan_reconciliation"
        and event.get("recovered_item_id") == item.id
        for event in sink.events
    )


def test_direct_objective_is_not_queued_again_once_its_replacement_is_reviewed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """s-6f0e6213: the operator's request ran once, then was done again.

    The front door handed the request straight to an Engineer, who stopped on a
    rounding conflict; the Planner superseded that task with its own, and a
    Reviewer accepted it. The next planning pass found no live work and no
    completion certificate, read that as "never handed out", and queued the
    operator's words as a new task, so an Engineer and a Reviewer did it all
    again. The accepted review goes to the Manager instead, and the project ends.
    """
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        split_memory=True,
    )
    objective = (
        "给 timefmt 加一个 format_hours(seconds)：输出整小时数，例如 7200 → \"2 h\"，"
        "舍入方式和包里现有的整单位函数保持一致，负数抛 ValueError。"
    )
    supervisor.config.open_ended = False
    supervisor.config.continuous_objective = objective
    backend.manager_action = "complete"
    pipeline = Path(supervisor.config.project_worktree) / ".argus" / "PIPELINE_STATE.json"
    pipeline.write_text(
        json.dumps({**json.loads(pipeline.read_text(encoding="utf-8")), "workflow_mode": "direct"}),
        encoding="utf-8",
    )
    state_root = supervisor.memory.project_root
    (state_root / "continuous.json").write_text(
        json.dumps({"enabled": True, "objective": objective, "generation": 1}),
        encoding="utf-8",
    )
    (state_root / "events.jsonl").write_text(
        json.dumps({
            "type": "life.manager.intent.completed",
            "execution_task": objective,
            "continuous_generation": 1,
            "vertical": "software",
            "workflow_mode": "direct",
        }) + "\n",
        encoding="utf-8",
    )
    backlog = supervisor.memory.backlog
    asked = backlog.add(BacklogItem.new(
        title="为 timefmt 添加整小时格式化函数",
        objective=objective,
        tags=["manager", "scope:bounded", "review:required", "manager_direct",
              "stage_closing", "stage:delivery"],
        original_objective=objective,
    ))
    backlog.update(asked.id, status="superseded")
    replacement = backlog.add(BacklogItem.new(
        title="实现并独立审阅整小时格式化功能",
        objective="在 timefmt/core.py 添加 format_hours，仿照 format_minutes 截去不足整小时的部分。",
        tags=["planner", "scope:bounded", "bounded_dag_node", "review:required",
              "stage:delivery"],
        original_objective=objective,
    ))
    record_reviewed_handoff(
        mission_context_path=create_mission_context(
            life_dir=state_root,
            mission_id=replacement.id,
            stage="delivery",
            objective=replacement.objective,
            scope="bounded",
        ),
        round_index=1,
        engineer_summary="",
        review=SimpleNamespace(
            status="done",
            reason="format_hours 已通过独立审阅，可交付。",
            next_action="",
            operator_question="",
        ),
        checkpoint_path=None,
    )
    backlog.mark_done(replacement.id, outcome={
        "execution_status": "completed",
        "review_status": "done",
        "stage_certification": "deferred",
        "interruption_kind": "none",
        "resumable": False,
    })
    monkeypatch.setattr(
        supervisor, "_manager_publish_project_report", lambda _reason, **_kwargs: "reported",
    )

    assert supervisor._plan_next_work() == PLAN_RETRY
    assert [row.id for row in backlog.history()] == [asked.id, replacement.id]
    assert backend.manager_calls == 1
    assert any(
        event.get("type") == "life.manager.stage_decision"
        and event.get("action") == "complete"
        and event.get("recovered_item_id") == replacement.id
        for event in sink.events
    )

    assert supervisor._plan_next_work() is False
    assert [row.id for row in backlog.history()] == [asked.id, replacement.id]
    assert backend.planner_calls == 0


def test_deterministic_stage_gate_hold_is_not_re_adjudicated_next_cycle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend = _TaskPlannerRunner()
    backend.manager_action = "advance"
    backend.manager_target_stage = "solve"
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        backend=backend,
        split_memory=True,
    )
    project = Path(supervisor.config.project_worktree)
    _write_reviewed_scope_state(project)
    monkeypatch.setattr(
        "argus.core.vertical_contract.VerticalContract.completion_issues",
        lambda *_args, **_kwargs: ("scope evidence is incomplete",),
    )
    item = supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Define the mathematical scope",
            objective="State the admissible conjecture class and completion bar.",
            tags=["planner", "scope:bounded", "stage:scope"],
        )
    )
    mission_path = create_mission_context(
        life_dir=supervisor.memory.project_root,
        mission_id=item.id,
        stage="scope",
        objective=item.objective,
        scope="bounded",
    )
    record_reviewed_handoff(
        mission_context_path=mission_path,
        round_index=1,
        engineer_summary="",
        review=SimpleNamespace(
            status="done",
            reason="The scope is ready to advance.",
            next_action="",
            operator_question="",
        ),
        checkpoint_path=None,
    )
    supervisor.memory.backlog.mark_done(
        item.id,
        outcome={
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "not_assessed",
            "interruption_kind": "none",
            "resumable": False,
        },
    )
    assert supervisor._reconcile_reviewed_stage_empty_plan(None) == ""
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.outcome["stage_certification"] == "not_certified"
    assert backend.manager_calls == 1
    assert any(
        event.get("type") == "life.manager.stage_decision"
        and event.get("source") == "stage_completion_gate_hold"
        for event in sink.events
    )

    assert supervisor._reconcile_reviewed_stage_empty_plan(None) == ""
    assert backend.manager_calls == 1


def test_review_only_item_is_never_replayed_into_stage_writer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, _backend, _sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        split_memory=True,
    )
    project = Path(supervisor.config.project_worktree)
    _write_reviewed_scope_state(project)
    item = supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Review bounded candidate",
            objective="Assess a candidate without changing the formal stage.",
            tags=[
                "planner",
                "scope:bounded",
                "review:required",
                "stage_transition:skip",
            ],
        )
    )
    mission_path = create_mission_context(
        life_dir=supervisor.memory.project_root,
        mission_id=item.id,
        stage="scope",
        objective=item.objective,
        scope="bounded",
    )
    record_reviewed_handoff(
        mission_context_path=mission_path,
        round_index=1,
        engineer_summary="",
        review=SimpleNamespace(
            status="done",
            reason="The bounded candidate review is complete.",
            next_action="",
            operator_question="",
        ),
        checkpoint_path=None,
    )
    supervisor.memory.backlog.mark_done(
        item.id,
        outcome={
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "not_assessed",
            "interruption_kind": "none",
            "resumable": False,
        },
    )

    assert supervisor._latest_unassessed_review_for_current_stage() is None
    state = json.loads(
        (project / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert state["current_stage"] == "scope"


@pytest.mark.parametrize("second_run_accepts", [True, False])
def test_replay_reads_only_the_latest_run_of_a_requeued_mission(
    tmp_path: Path,
    monkeypatch,
    second_run_accepts: bool,
) -> None:
    supervisor, _backend, _sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        split_memory=True,
    )
    project = Path(supervisor.config.project_worktree)
    _write_reviewed_scope_state(project)
    item = supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Define the mathematical scope",
            objective="State the admissible conjecture class.",
            tags=["planner", "scope:bounded"],
        )
    )

    def run(objective: str, statuses: list[str], marker: str) -> None:
        mission_path = create_mission_context(
            life_dir=supervisor.memory.project_root,
            mission_id=item.id,
            stage="scope",
            objective=objective,
            scope="bounded",
        )
        for round_index, status in enumerate(statuses, start=1):
            record_reviewed_handoff(
                mission_context_path=mission_path,
                round_index=round_index,
                engineer_summary="",
                review=SimpleNamespace(
                    status=status,
                    reason=f"{marker} round {round_index}",
                    next_action="",
                    operator_question="",
                ),
                checkpoint_path=None,
            )

    # The first run is accepted at round 3; a requeue then rewrites the
    # objective and the second run counts its rounds from one again.
    run(item.objective, ["continue", "continue", "done"], "OLD")
    run(
        "State the admissible conjecture class and its completion bar.",
        ["continue", "done" if second_run_accepts else "continue"],
        "NEW",
    )
    supervisor.memory.backlog.mark_done(
        item.id,
        outcome={
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "not_assessed",
            "interruption_kind": "none",
            "resumable": False,
        },
    )

    recovered = supervisor._latest_unassessed_review_for_current_stage()

    if second_run_accepts:
        assert recovered is not None
        assert recovered[1].reason == "NEW round 2"
    else:
        assert recovered is None
    handoffs = supervisor.memory.project_root / "handoffs" / item.id
    assert json.loads((handoffs / "round-0003.json").read_text())["review"]["reason"] == (
        "OLD round 3"
    )



def test_terminal_replan_trigger_revises_active_same_plan_siblings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        backend=_StructuredPlannerRunner(),
    )
    trigger = BacklogItem(
        id=BacklogItem.new_id(),
        ts=time.time(),
        title="Score completed run",
        objective="Score the completed run.",
        status="failed",
        plan_id="plan-1",
        plan_version=1,
        node_key="score-run",
    )
    sibling = BacklogItem(
        id=BacklogItem.new_id(),
        ts=time.time(),
        title="Gate matched follow-up",
        objective="Gate the matched follow-up.",
        status="pending",
        plan_id="plan-1",
        plan_version=1,
        node_key="gate-follow-up",
    )
    supervisor.memory.backlog.add_many([trigger, sibling])

    outcome = supervisor._plan_next_work(
        revision_request={
            "item_id": trigger.id,
            "expected_plan_id": "plan-1",
            "expected_plan_version": 1,
            "review_reason": "the completed run invalidates the sibling path",
        }
    )

    assert outcome is True
    assert backend.planner_calls == 1
    stored = {item.id: item for item in supervisor.memory.backlog.all()}
    assert stored[trigger.id].status == "failed"
    assert stored[sibling.id].status == "superseded"
    replacement = next(
        item for item in stored.values()
        if item.plan_id != "plan-1" and item.status == "pending"
    )
    assert replacement.plan_version == 2
    proposed = next(
        event for event in sink.events
        if event.get("type") == "life.plan.revision.proposed"
    )
    assert proposed["active_item_ids"] == [sibling.id]
    committed = next(
        event for event in sink.events
        if event.get("type") == "life.plan.revision.committed"
    )
    assert committed["superseded_item_ids"] == [sibling.id]


def test_terminal_replan_trigger_without_active_siblings_plans_fresh(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        backend=_StructuredPlannerRunner(),
    )
    trigger = BacklogItem(
        id=BacklogItem.new_id(),
        ts=time.time(),
        title="Score completed run",
        objective="Score the completed run.",
        status="failed",
        plan_id="plan-1",
        plan_version=1,
        node_key="score-run",
    )
    supervisor.memory.backlog.add(trigger)

    outcome = supervisor._plan_next_work(
        revision_request={
            "item_id": trigger.id,
            "expected_plan_id": "plan-1",
            "expected_plan_version": 1,
            "review_reason": "the completed run leaves no same-plan work",
        }
    )

    assert outcome is True
    assert backend.planner_calls == 1
    stored = {item.id: item for item in supervisor.memory.backlog.all()}
    assert stored[trigger.id].status == "failed"
    pending = supervisor.memory.backlog.pending()
    assert len(pending) == 1
    assert pending[0].plan_version == 1
    rejected = next(
        event for event in sink.events
        if event.get("type") == "life.plan.revision.rejected"
    )
    assert "planning fresh work instead" in rejected["reason"]
    assert rejected["expected_plan_id"] == "plan-1"
    assert rejected["expected_plan_version"] == 1


def test_stale_terminal_replan_trigger_still_rejects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        backend=_StructuredPlannerRunner(),
    )
    trigger = BacklogItem(
        id=BacklogItem.new_id(),
        ts=time.time(),
        title="Score old completed run",
        objective="Score the old completed run.",
        status="failed",
        plan_id="plan-1",
        plan_version=1,
        node_key="score-old-run",
    )
    active_current = BacklogItem(
        id=BacklogItem.new_id(),
        ts=time.time(),
        title="Continue current run",
        objective="Continue the current run.",
        status="pending",
        plan_id="plan-2",
        plan_version=2,
        node_key="continue-current",
    )
    supervisor.memory.backlog.add_many([trigger, active_current])

    outcome = supervisor._plan_next_work(
        revision_request={
            "item_id": trigger.id,
            "expected_plan_id": "plan-2",
            "expected_plan_version": 2,
            "review_reason": "old evidence must not revise the current plan",
        }
    )

    assert outcome == PLAN_ERROR
    assert backend.planner_calls == 0
    stored = {item.id: item for item in supervisor.memory.backlog.all()}
    assert stored[active_current.id].status == "pending"
    rejected = next(
        event for event in sink.events
        if event.get("type") == "life.plan.revision.rejected"
    )
    assert rejected["reason"] == "plan revision conflict: active revision changed"


def test_unversioned_item_replan_degrades_to_planning_not_a_dead_end(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """An item predating plan versioning must not make replanning impossible.

    The revision path compare-and-swaps an active plan. An item with no
    ``plan_id`` has no plan to swap, and the intake used to answer that with
    ``PLAN_ERROR``: the Reviewer's replan is discarded, the cycle backs off, the
    item is claimed again, and the same mission reruns. Being unversioned is not
    a condition that resolves, so that loop has no exit — the same shape as the
    replan deadlock above, reached by a different door. One such item is live on
    this host.

    With nothing to supersede, the honest degradation is an ordinary planning
    cycle: the Planner still gets to decide what happens next.
    """
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
    )

    legacy = BacklogItem(
        id=BacklogItem.new_id(),
        ts=time.time(),
        title="恢复 Erdős 52 任意 n frontier 研究",
        objective="resume the frontier search",
        status="running",
    )
    supervisor.memory.backlog.add(legacy)
    assert not legacy.plan_id

    supervisor._plan_next_work(
        revision_request={
            "item_id": legacy.id,
            "review_reason": "this direction is exhausted",
        }
    )

    # The exit from the loop is that the Planner gets invoked at all: before,
    # intake returned PLAN_ERROR without ever calling it, and "unversioned" never
    # stops being true. This stub Planner then returns no tasks, so the cycle
    # still ends in an error — that is the stub, not the dead end.
    assert backend.planner_calls >= 1, "the Planner never got to decide"
    rejected = [
        event
        for event in sink.events
        if event.get("type") == "life.plan.revision.rejected"
    ]
    assert rejected == [], (
        "an unversioned item has no versioned plan revision to reject"
    )
