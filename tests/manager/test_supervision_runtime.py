from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from argus.core.event_catalog import EventType
from argus.core.models import RunnerOptions, RunnerResult
from argus.core.run_gateway import run_exec
from argus.daemon.state import read_continuous_state, write_continuous_config
from argus.life.event_log import JsonlEventSink
from argus.life.memory import Backlog, BacklogItem
from argus.manager import Manager
from argus.manager._session_ops import manager_pipeline_lock
from argus.manager.directive import load_active_manager_directive
from argus.manager.observation import observe_project
from argus.manager.session_context import conversation_backend
from argus.manager.supervision import schedule_supervision, supervise, waiting_for_evidence


def project(root, *, question=""):
    write_continuous_config(root, enabled=True, objective="Produce a validated grouped summary")
    backlog = Backlog(root / "backlog.jsonl")
    item = BacklogItem.new(title="Validate grouped means", objective="Preserve missing-value checks")
    item.pending_question = question
    item.status = "paused_operator" if question else "pending"
    backlog.add(item)
    return backlog, item


def review(root, status, reason):
    assert JsonlEventSink(None, life_dir=root).append({
        "type": EventType.ROUND_REVIEW_COMPLETED, "status": status, "reason": reason,
        "round_index": 1, "agent_layer": "reviewer",
    })


class EvidenceBackend:
    def __init__(self):
        self.calls = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.calls.append((prompt, resume_thread_id, run_label))
        if run_label == "manager-supervision":
            assert options.disable_tools and options.force_safe_mode and options.sandbox_mode == "read-only"
            facts, _ = json.JSONDecoder().raw_decode(prompt[prompt.index('{"objective"'):])
            if any(task["pending_question"] for task in facts["tasks"]):
                answer = {"action": "wait", "reason": "The author declaration is still missing; resume after the operator supplies it."}
            elif facts["review"].get("status") in {"continue", "blocked"}:
                answer = {"action": "steer", "reason": "The grouped-mean check fails; repair grouping before another review.",
                          "directive": "Repair the grouped mean while preserving the missing-value acceptance check."}
            else:
                answer = {"action": "continue", "reason": "The recorded grouping check passed; continue the planned validation."}
            answer["evidence_refs"] = ["backlog.jsonl" if answer["action"] == "wait" else "mission-view.json"]
            text = json.dumps(answer)
        else:
            text = "The grouped-mean validation is the current task."
        return RunnerResult(exit_code=0, agent_messages=[text], thread_id=resume_thread_id or "persistent-manager")

    def fork(self):
        child = type(self)()
        child.calls = self.calls
        return child


def test_runtime_incident_escalation_is_admitted_for_manager_supervision(
    tmp_path,
    monkeypatch,
):
    from argus.manager import supervision

    backend = EvidenceBackend()
    manager = Manager(tmp_path, runner=backend)
    key = str(tmp_path.resolve())
    monkeypatch.setattr(supervision, "_CLOSED", False)
    monkeypatch.setattr(supervision, "_PENDING", {})
    monkeypatch.setattr(supervision, "_WORKERS", {})
    monkeypatch.setattr(supervision, "_STOPPED_ROOTS", set())
    monkeypatch.setattr(supervision, "_dispatch_pending", lambda: None)

    accepted = schedule_supervision(
        manager,
        tmp_path,
        {
            "type": EventType.LIFE_RUNTIME_INCIDENT_ESCALATED,
            "event_id": "runtime-incident-1",
            "incident_id": "incident-1",
            "invariant": "running_requires_live_executor",
        },
    )

    assert accepted is True
    assert supervision._PENDING[key][2]["incident_id"] == "incident-1"


def test_runtime_incident_evidence_remains_visible_to_manager(tmp_path) -> None:
    project(tmp_path)
    event = {
        "type": EventType.LIFE_RUNTIME_INCIDENT_ESCALATED,
        "event_id": "runtime-incident-1",
        "incident_id": "incident-1",
        "detector": "running_stall_watchdog",
        "invariant": "running_requires_live_executor",
        "subject_kind": "mission",
        "subject_id": "mission-1",
        "severity": "error",
        "status": "escalated",
        "reason": "recovery postcondition failed",
        "manager_attention_required": True,
    }
    assert JsonlEventSink(None, life_dir=tmp_path).append(event)

    observation = observe_project(tmp_path)
    recorded = observation.facts["recent_events"][-1]

    assert recorded["incident_id"] == "incident-1"
    assert recorded["invariant"] == "running_requires_live_executor"
    assert recorded["manager_attention_required"] is True


def test_the_operator_s_answer_is_observed_beside_the_objective(tmp_path) -> None:
    """Trial, 2026-10-21: after the operator chose A, the Manager's
    supervision saw only the objective that said to wait for their choice."""
    from argus.core.operator_decision import build_operator_decision

    backlog, item = project(tmp_path, question="A or B?")
    backlog.update(item.id, park_reason="operator_question", operator_decision=build_operator_decision(
        item_id=item.id, title=item.title, reason="a choice only the operator can make", question="A or B?",
    ))
    backlog.resume_with_operator_reply(item.id, "A.", manager_decision="A.")

    assert observe_project(tmp_path).facts["operator_answers"] == ["A or B? → A. (the operator)"]


def test_dialogue_and_restarted_daemon_share_the_persistent_manager_identity(tmp_path, monkeypatch):
    from argus.manager import front_door
    from argus.webapi import manager_bridge, manager_state

    project(tmp_path)
    first = EvidenceBackend()
    manager = Manager(tmp_path, runner=first)
    frontend = SimpleNamespace(manager=manager, _backend=first)
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *args: frontend)
    reply = manager_bridge._answer_inline("persistent-sid", tmp_path, "What is running now?")
    assert "grouped-mean" in reply
    assert "Current project evidence" in first.calls[0][0]
    assert first.calls[0][1] is None

    second = EvidenceBackend()
    restarted = Manager(tmp_path, runner=second)
    record = supervise(restarted, tmp_path, {"type": "life.mission.completed", "item_id": "check"})
    assert record["status"] == "applied"
    assert second.calls[0][1] == "persistent-manager"

    manager_state.release_manager_context("persistent-sid")
    again = SimpleNamespace(manager=Manager(tmp_path, runner=second), _backend=second)
    result = run_exec(conversation_backend(again), prompt="Explain your last decision", options=RunnerOptions(), run_label="simple-1")
    assert result.thread_id == "persistent-manager"
    assert second.calls[-1][1] == "persistent-manager"


def test_new_review_evidence_changes_the_action_and_steering_reaches_engineer(tmp_path):
    _, item = project(tmp_path)
    backend = EvidenceBackend()
    manager = Manager(tmp_path, runner=backend)
    event = {"type": "life.mission.completed", "item_id": item.id}
    review(tmp_path, "done", "Grouping check passed")
    first = supervise(manager, tmp_path, event)
    assert first["decision"]["action"] == "continue"
    assert not (tmp_path / "inbox.jsonl").exists()
    assert supervise(manager, tmp_path, event)["id"] == first["id"]
    assert len(backend.calls) == 1

    review(tmp_path, "continue", "Grouped means were replaced with the overall mean")
    second = supervise(manager, tmp_path, event)
    assert second["status"] == "applied" and second["decision"]["action"] == "steer"
    assert second["id"] != first["id"]
    assert second["issued_at"] <= second["applied_at"]
    directive = load_active_manager_directive(tmp_path)
    assert directive and directive.revision == second["effects"]["directive_revision"]
    from argus.apps._runtime_execute import _engineer_guidance

    guidance = _engineer_guidance(tmp_path, tmp_path)
    assert directive.text in "\n".join(guidance)
    assert "Manager direction (project advisory)" in "\n".join(guidance)
    assert not (tmp_path / "inbox.jsonl").exists()
    assert read_continuous_state(tmp_path).objective == "Produce a validated grouped summary"
    assert supervise(manager, tmp_path, event)["id"] == second["id"]
    assert len(backend.calls) == 2


def test_changed_goal_rejects_a_slow_supervision_result_without_holding_pipeline_lock(tmp_path):
    project(tmp_path)
    entered, release = threading.Event(), threading.Event()

    class SlowBackend(EvidenceBackend):
        def run_exec(self, **kwargs):
            entered.set()
            assert release.wait(3)
            return super().run_exec(**kwargs)

    manager = Manager(tmp_path, runner=SlowBackend())
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(supervise, manager, tmp_path, {"type": "life.mission.completed"})
        try:
            assert entered.wait(1)
            # The real cross-process pipeline lock remains available during the model call.
            with manager_pipeline_lock(tmp_path):
                write_continuous_config(tmp_path, enabled=True, objective="New operator objective")
            release.set()
            result = pending.result(timeout=1)
            assert result["status"] == "superseded"
            assert not (tmp_path / "inbox.jsonl").exists()
            assert load_active_manager_directive(tmp_path) is None
            assert read_continuous_state(tmp_path).objective == "New operator objective"
        finally:
            release.set()


def test_unchanged_wait_ignores_polling_rounds_and_own_receipts_then_wakes_on_answer(tmp_path):
    backlog, item = project(tmp_path, question="Provide the author declaration")
    backend = EvidenceBackend()
    manager = Manager(tmp_path, runner=backend)
    event = {"type": "life.planner.verdict", "item_id": item.id, "reason": "Need author declaration", "ts": 10}
    first = supervise(manager, tmp_path, event)
    assert first["status"] == "applied" and first["decision"]["action"] == "wait"
    assert waiting_for_evidence(tmp_path)
    with (tmp_path / "events.jsonl").open("a") as log:
        for number in range(60):
            log.write(json.dumps({"type": "life.planner.deferred", "ts": number, "reason": "poll"}) + "\n")
    repeated = {**event, "ts": 100, "round_index": 11}
    assert supervise(manager, tmp_path, repeated)["id"] == first["id"]
    assert len(backend.calls) == 1 and waiting_for_evidence(tmp_path)
    # The wait preserves campaign authority and the question until real input arrives.
    assert read_continuous_state(tmp_path).enabled
    backlog.update(item.id, pending_question="")
    assert backlog.resume_parked()
    assert not waiting_for_evidence(tmp_path)
    assert observe_project(tmp_path, event=event).evidence_revision != first["evidence_revision"]


def test_failed_provider_does_not_modify_any_team_control(tmp_path):
    project(tmp_path)

    class FailedBackend:
        def run_exec(self, **kwargs):
            return RunnerResult(exit_code=1, fatal_error="provider failed")

    result = supervise(Manager(tmp_path, runner=FailedBackend()),
                       tmp_path, {"type": "life.mission.completed"})
    assert result["status"] == "failed"
    assert not (tmp_path / "inbox.jsonl").exists()
    assert load_active_manager_directive(tmp_path) is None


def wait_for_receipt(root, status, *, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            record = json.loads((root / "manager-supervision/latest.json").read_text())
        except (OSError, ValueError):
            record = {}
        if record.get("status") == status:
            return record
        time.sleep(0.01)
    raise AssertionError(f"No {status} supervision receipt arrived")


def test_running_review_schedules_supervision_before_the_mission_pipeline_finishes(tmp_path):
    from argus.life.memory import LifeMemory
    from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
    from argus.manager.supervision import shutdown_supervision

    backlog, item = project(tmp_path)
    backend = EvidenceBackend()
    manager = Manager(tmp_path, runner=backend)
    supervisor = LifeSupervisor(
        memory=LifeMemory.open(tmp_path), runner=SimpleNamespace(manager=manager),
        sink=JsonlEventSink(None, life_dir=tmp_path),
        config=LifeSupervisorConfig(),
    )
    backlog.mark_running(item.id)
    supervisor.sink.handle_event({
        "type": EventType.LIFE_MISSION_STARTED, "item_id": item.id,
        "title": item.title, "objective": item.objective,
    })
    try:
        with manager_pipeline_lock(tmp_path):
            supervisor.sink.handle_event({
                "type": EventType.ROUND_REVIEW_COMPLETED, "item_id": item.id, "review_source": "engineer_handoff",
                "status": "blocked", "reason": "The grouped mean check failed", "round_index": 1,
            })
            record = wait_for_receipt(tmp_path, "applied")
            assert record["decision"]["action"] == "steer"
            assert record["effects"]["directive_delivered"]
            assert backlog.active()[0].status == "running"
            assert record["cited_refs"] == [ref for ref in record["available_refs"] if ref["path"] == "mission-view.json"]
        assert len(backend.calls) == 1
    finally:
        assert shutdown_supervision(tmp_path) == 0


def test_shutdown_bounds_an_uncooperative_backend_and_rejects_its_late_result(tmp_path):
    from argus.manager.supervision import (
        schedule_supervision,
        shutdown_supervision,
        start_supervision,
    )

    project(tmp_path)
    entered, release = threading.Event(), threading.Event()

    class Uncooperative(EvidenceBackend):
        def run_exec(self, **kwargs):
            entered.set()
            assert release.wait(3)
            return super().run_exec(**kwargs)

    manager = Manager(tmp_path, runner=Uncooperative())
    start_supervision(tmp_path)
    assert schedule_supervision(manager, tmp_path, {"type": EventType.LIFE_RUNTIME_INCIDENT_ESCALATED})
    try:
        assert entered.wait(1)
        started = time.monotonic()
        assert shutdown_supervision(tmp_path, timeout=0.02) == 1
        assert time.monotonic() - started < 0.25
        assert not schedule_supervision(manager, tmp_path, {"type": EventType.LIFE_RUNTIME_INCIDENT_ESCALATED})
        release.set()
        wait_for_receipt(tmp_path, "superseded")
        assert not (tmp_path / "inbox.jsonl").exists()
        assert not (tmp_path / ".manager_session.json").exists()
    finally:
        release.set()
        assert shutdown_supervision(tmp_path) == 0


@pytest.mark.parametrize("remaining_work", ["none", "pending", "continuous", "issued"])
def test_completed_work_only_delivers_an_issued_decision(tmp_path, monkeypatch, remaining_work):
    from argus.manager import supervision

    backlog, item = project(tmp_path)
    backlog.update(item.id, status="done")
    write_continuous_config(tmp_path, enabled=remaining_work == "continuous", objective="Produce a validated grouped summary")
    if remaining_work == "pending":
        backlog.add(BacklogItem.new(title="Next validation", objective="Check the next batch"))
    if remaining_work == "issued":
        supervision._write(tmp_path / "manager-supervision/latest.json", {"status": "issued"})
    dispatched = []
    monkeypatch.setattr(supervision, "_dispatch_pending", lambda: dispatched.append(True))
    manager = Manager(tmp_path, runner=EvidenceBackend())
    supervision.start_supervision(tmp_path)
    try:
        admitted = supervision.schedule_supervision(manager, tmp_path, {
            "type": EventType.LIFE_MISSION_COMPLETED, "item_id": item.id,
            "success": True, "status": "done",
        })
        assert admitted is (remaining_work == "issued")
        assert bool(dispatched) is admitted
        assert manager.runner.calls == []
    finally:
        supervision.shutdown_supervision(tmp_path)


def test_unobserved_evidence_reference_cannot_authorize_steering(tmp_path):
    project(tmp_path)

    class Unfounded(EvidenceBackend):
        def run_exec(self, **kwargs):
            return RunnerResult(exit_code=0, agent_messages=[json.dumps({
                "action": "steer", "reason": "Claim from an unread file", "directive": "Change course",
                "evidence_refs": ["other-project/private.json"],
            })])

    result = supervise(Manager(tmp_path, runner=Unfounded()),
                       tmp_path, {"type": EventType.LIFE_MISSION_COMPLETED})
    assert result["status"] == "failed" and result["cited_refs"] == []
    assert load_active_manager_directive(tmp_path) is None
    assert not (tmp_path / "inbox.jsonl").exists()


def test_reply_followup_and_restarted_inspect_share_identity_and_refresh_evidence(tmp_path):
    from argus.apps._runtime import _SkillLoopRunner

    backlog, item = project(tmp_path)

    def frontend(backend):
        runner = _SkillLoopRunner.__new__(_SkillLoopRunner)
        runner._backend = backend
        runner._manager_session_root = tmp_path
        runner._args = SimpleNamespace(workdir=str(tmp_path), engineer_model="local-engineer")
        runner._next_seed_thread_id = runner.last_thread_id = None
        runner.manager = Manager(tmp_path, runner=backend)
        return runner

    sink = SimpleNamespace(handle_event=lambda event: None)
    first = EvidenceBackend()
    runner = frontend(first)
    assert runner._simple_quick_reply(objective="What are we checking?", sink=sink, lean=True).success
    backlog.update(item.id, last_error="Missing-value check has just failed")
    assert runner._simple_quick_reply(objective="What changed since your reply?", sink=sink, lean=True).success
    assert [call[1] for call in first.calls] == [None, "persistent-manager"]
    assert "Missing-value check has just failed" not in first.calls[0][0]
    assert "Missing-value check has just failed" in first.calls[1][0]

    second = EvidenceBackend()
    restarted = frontend(second)
    assert restarted._simple_quick_reply(objective="Explain the remaining check", sink=sink).success
    assert second.calls[0][1] == "persistent-manager"
    assert "Missing-value check has just failed" in second.calls[0][0]
    record = supervise(restarted.manager, tmp_path, {"type": EventType.LIFE_MISSION_COMPLETED})
    assert record["status"] == "applied" and second.calls[-1][1] == "persistent-manager"


def test_applied_wait_holds_unchanged_planning_until_new_operator_facts(tmp_path, monkeypatch):
    from argus.life.memory import LifeMemory
    from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
    from argus.life.supervisor._constants import PLAN_AWAITING, PLAN_ERROR
    from argus.planner import PlannerVerdict

    backlog, item = project(tmp_path, question="Supply the missing author declaration")
    manager = Manager(tmp_path, runner=EvidenceBackend())
    assert supervise(manager, tmp_path, {"type": EventType.LIFE_PLANNER_VERDICT})["status"] == "applied"
    calls = []

    def plan_next(_planner, **kwargs):
        calls.append(kwargs)
        return PlannerVerdict(
            project_done=False,
            reason="The independent checker is temporarily unavailable",
            error="The independent checker is temporarily unavailable",
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan_next)
    supervisor = LifeSupervisor(
        memory=LifeMemory.open(tmp_path), runner=SimpleNamespace(),
        sink=JsonlEventSink(None, life_dir=tmp_path), planner_runner=object(),
        config=LifeSupervisorConfig(
            continuous=True, continuous_objective=read_continuous_state(tmp_path).objective,
            project_worktree=tmp_path, artifact_root=tmp_path, final_certification_gate=False,
            open_ended=True,
        ),
    )
    supervisor._vertical_resolved = True
    # The WAIT is the Manager's answer to the evidence it judged: while that
    # holds, the Planner is not asked, and nothing new is said each pass; the
    # answer moves it.
    for _ in range(4):
        assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == []
    assert "requested operator facts" not in (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    backlog.update(item.id, pending_question="")
    assert backlog.resume_parked()
    assert supervisor._plan_next_work() == PLAN_ERROR
    assert len(calls) == 1


def test_saturated_scheduler_retains_latest_evidence_for_the_waiting_project(tmp_path):
    from argus.manager.supervision import (
        schedule_supervision,
        shutdown_supervision,
        start_supervision,
    )

    roots = [tmp_path / str(number) for number in range(3)]
    entered = [threading.Event(), threading.Event()]
    release = threading.Event()
    managers = []
    for number, root in enumerate(roots):
        project(root)
        backend = EvidenceBackend()
        if number < 2:
            class BusyBackend(EvidenceBackend):
                def run_exec(self, _entered=entered[number], **kwargs):
                    _entered.set()
                    assert release.wait(3)
                    return super().run_exec(**kwargs)

            backend = BusyBackend()
        managers.append(Manager(root, runner=backend))
        start_supervision(root)
    try:
        for number in range(2):
            assert schedule_supervision(managers[number], roots[number], {"type": EventType.LIFE_RUNTIME_INCIDENT_ESCALATED})
        assert all(signal.wait(1) for signal in entered)
        assert schedule_supervision(managers[2], roots[2], {"type": EventType.LIFE_RUNTIME_INCIDENT_ESCALATED, "event_id": "obsolete"})
        assert schedule_supervision(managers[2], roots[2], {"type": EventType.LIFE_RUNTIME_INCIDENT_ESCALATED, "event_id": "latest"})
        assert managers[2].runner.calls == []
        release.set()
        record = wait_for_receipt(roots[2], "applied")
        assert record["trigger"]["event_id"] == "latest"
        assert len(managers[2].runner.calls) == 1
    finally:
        release.set()
        for root in roots:
            assert shutdown_supervision(root) == 0


def test_model_change_rotates_provider_identity_with_a_durable_conversation_handoff(tmp_path):
    from argus.manager._session_ops import _ManagerSession

    backend = EvidenceBackend()
    session = _ManagerSession(backend, tmp_path)
    session.run_exec(prompt="Message:\nKeep the missing-value acceptance check", options=RunnerOptions(model="model-a"), run_label="manager-quick-reply")
    restarted = _ManagerSession(backend, tmp_path)
    restarted.run_exec(prompt="Message:\nWhat did we agree?", options=RunnerOptions(model="model-b"), run_label="manager-quick-reply")
    assert backend.calls[-1][1] is None
    assert "Manager session continuity handoff" in backend.calls[-1][0]
    assert "Keep the missing-value acceptance check" in backend.calls[-1][0]
    assert "The grouped-mean validation is the current task." in backend.calls[-1][0]
    state = json.loads((tmp_path / ".manager_session.json").read_text())
    assert state["identity"]["model"] == "model-b"
    assert state["rotation_reason"] == "the configured model or backend changed"


def test_an_interactive_ask_neither_waits_for_nor_interrupts_a_background_turn(tmp_path, monkeypatch):
    from argus.manager import front_door
    from argus.webapi import manager_bridge

    project(tmp_path)
    entered, release = threading.Event(), threading.Event()

    class Background(EvidenceBackend):
        def run_exec(self, **kwargs):
            if kwargs["run_label"] == "manager-supervision":
                entered.set()
                assert release.wait(5)
                assert not kwargs["options"].external_interrupt_reason_provider()
            return super().run_exec(**kwargs)

    backend = Background()
    manager = Manager(tmp_path, runner=backend)
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *args: SimpleNamespace(manager=manager, _backend=backend))
    assert "grouped-mean" in manager_bridge._answer_inline("priority-sid", tmp_path, "What is the task?")
    with ThreadPoolExecutor(max_workers=1) as pool:
        background = pool.submit(supervise, manager, tmp_path, {"type": EventType.LIFE_MISSION_COMPLETED})
        assert entered.wait(1)
        try:
            started = time.monotonic()
            assert "grouped-mean" in manager_bridge._answer_inline("priority-sid", tmp_path, "Please answer now")
            assert time.monotonic() - started < 1
        finally:
            release.set()
        assert background.result(timeout=5)["status"] == "applied"
    # The ask answered on a fresh thread; the background turn kept the shared one.
    resumes = {label: [] for _prompt, _resume, label in backend.calls}
    for _prompt, resume, label in backend.calls:
        resumes[label].append(resume)
    assert resumes["manager-ask"] == [None, None]
    assert resumes["manager-supervision"] == ["persistent-manager"]
    assert manager._session.thread_id == "persistent-manager"


@pytest.mark.parametrize("failure_point", ["directive", "continuous", "final_receipt"])
def test_issued_outbox_recovers_real_write_crashes_without_another_model_call(tmp_path, monkeypatch, failure_point):
    from pathlib import Path

    from argus.daemon.state import _continuous_config_path
    from argus.manager import supervision

    class ProcessCrash(BaseException):
        pass

    project(tmp_path)
    review(tmp_path, "continue", "The grouped check failed")
    backend = EvidenceBackend()
    manager = Manager(tmp_path, runner=backend)
    event = {"type": EventType.LIFE_MISSION_COMPLETED}
    original = supervision.os.replace

    def replace_then_crash(source, destination):
        target = Path(destination)
        crash = (
            failure_point == "directive" and target.name == "active_manager_directive.json"
            or failure_point == "continuous" and target == _continuous_config_path(tmp_path)
            or failure_point == "final_receipt" and target.parent.name == "manager-supervision"
            and target.name != "latest.json" and json.loads(Path(source).read_text()).get("status") == "applied"
        )
        original(source, destination)
        if crash:
            raise ProcessCrash()

    with monkeypatch.context() as fault:
        fault.setattr(supervision.os, "replace", replace_then_crash)
        with pytest.raises(ProcessCrash):
            supervise(manager, tmp_path, event)
    before = load_active_manager_directive(tmp_path)
    if failure_point == "directive":
        from argus.life.memory import LifeMemory
        from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
        from argus.manager.supervision import shutdown_supervision

        restarted = Manager(tmp_path, runner=backend)
        LifeSupervisor(
            memory=LifeMemory.open(tmp_path), runner=SimpleNamespace(manager=restarted),
            sink=JsonlEventSink(None, life_dir=tmp_path),
            config=LifeSupervisorConfig(project_worktree=tmp_path),
        )
        try:
            recovered = wait_for_receipt(tmp_path, "applied")
        finally:
            assert shutdown_supervision(tmp_path) == 0
    else:
        recovered = supervise(Manager(tmp_path, runner=backend), tmp_path, event)
    assert len(backend.calls) == 1
    assert not (tmp_path / "inbox.jsonl").exists()
    if failure_point == "continuous":
        assert recovered["status"] == "superseded"
        assert load_active_manager_directive(tmp_path) is None
    else:
        assert recovered["status"] == "applied"
        assert before and load_active_manager_directive(tmp_path).revision == before.revision
        assert recovered["effects"]["directive_delivered"]


def test_partial_delivery_never_overwrites_a_new_operator_direction(tmp_path, monkeypatch):
    from argus.manager import directive

    class ProcessCrash(BaseException):
        pass

    project(tmp_path)
    review(tmp_path, "continue", "Grouping failed")
    backend = EvidenceBackend()
    manager = Manager(tmp_path, runner=backend)
    original = directive.set_active_manager_directive

    def write_then_crash(*args, **kwargs):
        original(*args, **kwargs)
        raise ProcessCrash()

    with monkeypatch.context() as fault:
        fault.setattr(directive, "set_active_manager_directive", write_then_crash)
        with pytest.raises(ProcessCrash):
            supervise(manager, tmp_path, {"type": EventType.LIFE_MISSION_COMPLETED})
    newer = original(tmp_path, "Preserve the new operator direction", source="operator.explicit")
    result = supervise(manager, tmp_path, {"type": EventType.LIFE_MISSION_COMPLETED})
    assert result["status"] == "superseded"
    assert load_active_manager_directive(tmp_path).revision == newer.revision
    assert len(backend.calls) == 1


def role_loop(root, mission_id, backend):
    from argus import SkillLoop, SkillLoopConfig

    return SkillLoop(
        skills_dir=root / "skills", engineer_runner=backend, reviewer_runner=backend,
        config=SkillLoopConfig(
            engineer_model="fixture", reviewer_model="fixture", workflow_mode="direct",
            active_vertical="software", max_rounds=1,
            require_independent_review=True, require_post_task_learning=False,
            wiki_enabled=False, auto_init_wiki=False, session_id=mission_id,
            state_root=root,
        ),
    )


def task_at(backlog, item_id):
    return next(item for item in backlog.active() if item.id == item_id)


def test_wait_parks_only_task_a_and_independent_b_and_answered_a_can_run(tmp_path):
    from argus.adapters.memory_backend import CannedResponse, MemoryBackend
    from argus.life.memory import LifeMemory
    from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig

    backlog, task_a = project(tmp_path)
    supervisor = LifeSupervisor(
        memory=LifeMemory.open(tmp_path), runner=SimpleNamespace(),
        sink=JsonlEventSink(None, life_dir=tmp_path), config=LifeSupervisorConfig(project_worktree=tmp_path),
    )
    backlog.mark_running(task_a.id)
    backlog.update(task_a.id, pending_question="Provide the author declaration")
    task_b = BacklogItem.new(title="Independent lint check", objective="Check local syntax independently")
    backlog.add(task_b)
    backlog.mark_running(task_b.id)
    manager = Manager(tmp_path, runner=EvidenceBackend())
    receipt = supervise(manager, tmp_path, {"type": EventType.LIFE_PLANNER_VERDICT})
    assert receipt["waiting_task_ids"] == [task_a.id]
    assert waiting_for_evidence(tmp_path, task_a.id)
    assert not waiting_for_evidence(tmp_path, task_b.id)
    backend_a = MemoryBackend()
    outcome_a = role_loop(tmp_path, task_a.id, backend_a).run(task_a.objective, workdir=tmp_path)
    assert (outcome_a.status, outcome_a.park_reason) == ("parked", "operator_question")
    assert outcome_a.rounds == [] and backend_a.history == []
    state = SimpleNamespace(
        item=task_at(backlog, task_a.id), outcome=SimpleNamespace(final_review_status=""),
        status=outcome_a.status, park_reason=outcome_a.park_reason,
        stop_kind=outcome_a.stop_kind, stop_reason=outcome_a.reason,
        usage_summary=SimpleNamespace(pricing_status="known"), usd=0.0, known_usd=0.0,
        context_packet_path=None,
    )
    assert supervisor._maybe_pause_for_recoverable_stop(state)["status"] == "parked"
    assert task_at(backlog, task_a.id).pending_question == "Provide the author declaration"
    assert waiting_for_evidence(tmp_path, task_a.id)

    backend_b = MemoryBackend()
    backend_b.queue("engineer-r1", CannedResponse(message="The independent syntax check passed"))
    backend_b.queue("reviewer", CannedResponse(review_action=("submit_review", {'review': 'Syntax verified\n→ done'})))
    assert role_loop(tmp_path, task_b.id, backend_b).run(task_b.objective, workdir=tmp_path).successful
    backlog.update(task_b.id, status="done")
    assert waiting_for_evidence(tmp_path, task_a.id), "unrelated task completion cannot answer A's question"

    backlog.update(task_a.id, pending_question="")
    assert backlog.resume_parked()
    backlog.mark_running(task_a.id)
    assert not waiting_for_evidence(tmp_path, task_a.id)
    backend_a.queue("engineer-r1", CannedResponse(message="Applied the supplied author declaration"))
    backend_a.queue("reviewer", CannedResponse(review_action=("submit_review", {'review': 'Declaration verified\n→ done'})))
    assert role_loop(tmp_path, task_a.id, backend_a).run(task_a.objective, workdir=tmp_path).successful


def test_wait_arriving_during_engineer_work_finishes_the_write_and_skips_reviewer(tmp_path):
    from argus.adapters.memory_backend import CannedResponse, MemoryBackend

    backlog, item = project(tmp_path)
    backlog.mark_running(item.id)
    backlog.update(item.id, pending_question="Supply the author declaration")
    entered, release = threading.Event(), threading.Event()
    artifact = tmp_path / "checked.txt"

    def write_artifact(_prompt, _options):
        artifact.write_text("in progress")
        entered.set()
        assert release.wait(2)
        artifact.write_text("write complete")
        return "The finite local write completed"

    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message_factory=write_artifact))
    loop = role_loop(tmp_path, item.id, backend)
    manager = Manager(tmp_path, runner=EvidenceBackend())
    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(loop.run, item.objective, workdir=tmp_path)
        try:
            assert entered.wait(1)
            assert supervise(manager, tmp_path, {"type": EventType.LIFE_PLANNER_VERDICT})["status"] == "applied"
            assert not work.done() and artifact.read_text() == "in progress"
            release.set()
            outcome = work.result(timeout=1)
            assert outcome.status == "parked"
            assert outcome.rounds == [], "the control boundary must not fabricate a Reviewer verdict"
            assert artifact.read_text() == "write complete"
            assert [label for label, _, _ in backend.history] == ["engineer-r1"]
        finally:
            release.set()


def test_issued_event_failure_does_not_discard_the_durable_decision(tmp_path, monkeypatch):
    from argus.manager import supervision

    project(tmp_path)
    event = {"type": EventType.LIFE_PHASE_STARTED, "agent_layer": "engineer", "round_index": 2}
    backend = EvidenceBackend()
    manager = Manager(tmp_path, runner=backend)
    original = supervision._emit

    def unavailable(record, root, phase):
        if phase == "issued":
            raise OSError("event volume unavailable")
        return original(record, root, phase)

    monkeypatch.setattr(supervision, "_emit", unavailable)
    assert supervise(manager, tmp_path, event)["status"] == "applied"
    assert len(backend.calls) == 1


def test_phase_receipt_survives_a_crash_before_the_latest_index_is_replaced(tmp_path, monkeypatch):
    from pathlib import Path

    from argus.manager import supervision

    class ProcessCrash(BaseException):
        pass

    project(tmp_path)
    event = {"type": EventType.LIFE_PHASE_STARTED, "agent_layer": "engineer", "round_index": 2}
    backend = EvidenceBackend()
    manager = Manager(tmp_path, runner=backend)
    original = supervision.os.replace

    def replace_then_crash(source, destination):
        target = Path(destination)
        crash = (
            target.parent.name == "manager-supervision" and target.name != "latest.json"
            and json.loads(Path(source).read_text()).get("status") == "issued"
        )
        original(source, destination)
        if crash:
            raise ProcessCrash()

    with monkeypatch.context() as fault:
        fault.setattr(supervision.os, "replace", replace_then_crash)
        with pytest.raises(ProcessCrash):
            supervise(manager, tmp_path, event)
    assert json.loads((tmp_path / "manager-supervision/latest.json").read_text())["status"] == "evaluating"
    supervision.start_supervision(tmp_path)
    assert supervision.recover_issued_supervision(manager, tmp_path)
    try:
        recovered = wait_for_receipt(tmp_path, "applied")
        assert recovered["source_event"]["agent_layer"] == "engineer"
        assert recovered["source_event"]["round_index"] == 2
        assert len(backend.calls) == 1
    finally:
        assert supervision.shutdown_supervision(tmp_path) == 0


def test_background_delivery_retries_a_temporarily_busy_control_without_new_evidence(tmp_path, monkeypatch):
    from argus.manager import supervision

    project(tmp_path)
    backend = EvidenceBackend()
    manager = Manager(tmp_path, runner=backend)
    original = supervision._apply
    attempts = []

    def briefly_busy(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("daemon control is busy")
        return original(*args, **kwargs)

    monkeypatch.setattr(supervision, "_apply", briefly_busy)
    supervision.start_supervision(tmp_path)
    assert supervision.schedule_supervision(manager, tmp_path, {"type": EventType.LIFE_RUNTIME_INCIDENT_ESCALATED})
    try:
        assert wait_for_receipt(tmp_path, "applied")["status"] == "applied"
        assert len(attempts) == 2 and len(backend.calls) == 1
    finally:
        assert supervision.shutdown_supervision(tmp_path) == 0


def test_busy_control_is_not_reported_as_a_failure_on_every_attempt(tmp_path, monkeypatch):
    """While the daemon runs the mission a decision concerns, delivery waits for
    the next guidance boundary; that wait is not a stream of failure events."""
    from argus.manager import supervision

    project(tmp_path)
    backend = EvidenceBackend()
    manager = Manager(tmp_path, runner=backend)
    attempts = []

    def always_busy(*args, **kwargs):
        attempts.append(True)
        raise supervision.SupervisionBusy("daemon control is busy")

    monkeypatch.setattr(supervision, "_apply", always_busy)
    supervision.start_supervision(tmp_path)
    assert supervision.schedule_supervision(manager, tmp_path, {"type": EventType.LIFE_RUNTIME_INCIDENT_ESCALATED})
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and len(attempts) < 8:
            time.sleep(0.05)
    finally:
        assert supervision.shutdown_supervision(tmp_path) == 0
    assert len(attempts) == 8 and len(backend.calls) == 1

    latest = json.loads((tmp_path / "manager-supervision/latest.json").read_text())
    assert latest["status"] == "issued" and latest["error_code"] == "busy"
    assert "next guidance boundary" in latest["failure_reason"]
    types = [json.loads(line)["type"] for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert types.count(EventType.LIFE_MANAGER_SUPERVISION_ISSUED) == 1
    assert types.count(EventType.LIFE_MANAGER_SUPERVISION_FAILED) == 0

    # Once control frees, the same issued decision is delivered without another model call.
    monkeypatch.undo()
    delivered = supervise(Manager(tmp_path, runner=backend), tmp_path,
                          {"type": EventType.LIFE_MISSION_COMPLETED})
    assert delivered["status"] == "applied" and len(backend.calls) == 1
    types = [json.loads(line)["type"] for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert types.count(EventType.LIFE_MANAGER_SUPERVISION_APPLIED) == 1


class AskingBackend(EvidenceBackend):
    """A Manager that judges the work handed to it is the operator's to decide."""

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.calls.append((prompt, resume_thread_id, run_label))
        text = (
            "ACTION: continue\nREASON: Requirements A and B conflict; only the operator may choose.\n"
            "EVIDENCE_REFS: backlog.jsonl\n→ operator: Keep A or B?\n- Keep A\n- Keep B"
        )
        return RunnerResult(exit_code=0, agent_messages=[text], thread_id="persistent-manager")


def test_work_handed_to_the_manager_waits_on_the_question_it_puts_to_the_operator(tmp_path):
    """2026-10-20: an Engineer handed an A-or-B conflict to the Manager, the task
    failed, and the question hung on a separate Planner card. The Manager's
    question now parks the task that was handed to it, once it has settled."""
    from argus.life.memory import park_reason

    backlog, item = project(tmp_path)
    backlog.update(item.id, status="running")
    backend = AskingBackend()
    manager = Manager(tmp_path, runner=backend)
    event = {"type": EventType.ROUND_REVIEW_COMPLETED, "item_id": item.id,
             "consult_reason": "the Engineer handed the work to you: A and B conflict"}

    settling = supervise(manager, tmp_path, event)
    assert settling["status"] == "issued" and settling["error_code"] == "busy"
    assert task_at(backlog, item.id).pending_question == ""

    backlog.update(item.id, park_reason="research_open")
    delivered = supervise(manager, tmp_path, {"type": EventType.LIFE_MISSION_COMPLETED, "item_id": item.id})
    assert delivered["status"] == "applied" and delivered["effects"]["question_item_id"] == item.id
    asked = task_at(backlog, item.id)
    assert park_reason(asked) == "operator_question"
    assert asked.pending_question == asked.operator_decision["question"] == "Keep A or B?"
    assert [option["label"] for option in asked.operator_decision["options"]] == ["Keep A", "Keep B"]
    assert len(backend.calls) == 1
    # 2026-10-22: rounding "consistent with the package's whole-unit functions"
    # read two ways with two public behaviors. Told separately that such readings
    # were the operator's and that reversible decisions were its own, the Manager
    # chose one itself. The one rule every role shares now settles it, once.
    from argus.core.operator_context import JUDGMENT_INSTRUCTION

    prompt = " ".join(backend.calls[0][0].split())
    assert prompt.count(JUDGMENT_INSTRUCTION) == 1
    pending = [
        json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()
        if json.loads(line)["type"] == EventType.LIFE_OPERATOR_QUESTION_PENDING
    ]
    assert [(row["item_id"], row["question"], row["options"]) for row in pending] == [
        (item.id, "Keep A or B?", ["Keep A", "Keep B"]),
    ]


def test_a_reading_the_manager_chooses_for_an_unreachable_operator_reaches_the_report(tmp_path, monkeypatch):
    """With nobody to ask, the Manager's steer chose one reading of a requirement
    read two ways; the run report says it was chosen for the operator."""
    from argus.core.autonomy import read_autonomous_assumptions, render_autonomous_assumptions
    from argus.life.memory import LifeMemory
    from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
    from argus.manager import supervision
    from argus.roles.prompts.manager import build_project_completion_report_prompt

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    monkeypatch.setenv("ARGUS_AUTONOMY_RUN_ID", "assumption-run")
    reading = "format_hours truncates like format_minutes (5400 → 1 h), not half up like format_days"

    class ChoosingBackend(EvidenceBackend):
        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            self.calls.append((prompt, resume_thread_id, run_label))
            text = (
                "ACTION: steer\nREASON: The rounding requirement reads two ways.\n"
                "EVIDENCE_REFS: backlog.jsonl\nDIRECTIVE: Truncate partial hours.\n"
                f"ASSUMPTION: {reading}"
            )
            return RunnerResult(exit_code=0, agent_messages=[text], thread_id="persistent-manager")

    root = tmp_path / "state"
    _, item = project(root)
    backend = ChoosingBackend()
    record = supervise(Manager(root, runner=backend, memory_maintenance_enabled=False), root,
                       {"type": EventType.ROUND_REVIEW_COMPLETED, "item_id": item.id})
    assert record["status"] == "applied", record
    assert "ASSUMPTION:" in backend.calls[0][0]
    (row,) = read_autonomous_assumptions(root)
    assert (row["item_id"], row["reading"], row["source"]) == (item.id, reading, "manager_decision")
    assert f"the Manager decided for you: {reading}" in render_autonomous_assumptions(root)

    class Runner:
        manager = None

    supervisor = LifeSupervisor(
        memory=LifeMemory.open(root), runner=Runner(), sink=JsonlEventSink(None, life_dir=root),
        config=LifeSupervisorConfig(continuous=True, continuous_objective="o", project_worktree=tmp_path),
    )
    report = build_project_completion_report_prompt(
        objective="o", completion_reason="done",
        completion_context=supervisor._manager_project_completion_context(),
    )
    assert "Name each recorded assumption" in report and reading in report
    # A field that states nothing records nothing.
    assert supervision._decision(
        "ACTION: continue\nREASON: r\nEVIDENCE_REFS: backlog.jsonl\nASSUMPTION: none"
    )["assumption"] == ""
