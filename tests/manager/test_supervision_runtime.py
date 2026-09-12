from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from argus_skill.core.event_catalog import EventType
from argus_skill.core.models import RunnerOptions, RunnerResult
from argus_skill.core.run_gateway import run_exec
from argus_skill.daemon.state import read_continuous_state, write_continuous_config
from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import Backlog, BacklogItem
from argus_skill.manager import Manager
from argus_skill.manager._session_ops import manager_pipeline_lock
from argus_skill.manager.directive import load_active_manager_directive
from argus_skill.manager.observation import observe_project
from argus_skill.manager.session_context import conversation_backend
from argus_skill.manager.supervision import supervise, waiting_for_evidence


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
            elif facts["review"].get("status") == "continue":
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


def test_dialogue_and_restarted_daemon_share_the_persistent_manager_identity(tmp_path, monkeypatch):
    from argus_skill.manager import front_door
    from argus_skill.webapi import manager_bridge, manager_state

    project(tmp_path)
    first = EvidenceBackend()
    manager = Manager(tmp_path, runner=first, memory_maintenance_enabled=False)
    frontend = SimpleNamespace(manager=manager, _backend=first)
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *args: frontend)
    reply = manager_bridge._answer_inline("persistent-sid", tmp_path, "What is running now?")
    assert "grouped-mean" in reply
    assert "Current project evidence" in first.calls[0][0]
    assert first.calls[0][1] is None

    second = EvidenceBackend()
    restarted = Manager(tmp_path, runner=second, memory_maintenance_enabled=False)
    record = supervise(restarted, tmp_path, {"type": "life.mission.completed", "item_id": "check"})
    assert record["status"] == "applied"
    assert second.calls[0][1] == "persistent-manager"

    manager_state.release_manager_context("persistent-sid")
    again = SimpleNamespace(manager=Manager(tmp_path, runner=second, memory_maintenance_enabled=False), _backend=second)
    result = run_exec(conversation_backend(again), prompt="Explain your last decision", options=RunnerOptions(), run_label="simple-1")
    assert result.thread_id == "persistent-manager"
    assert second.calls[-1][1] == "persistent-manager"


def test_new_review_evidence_changes_the_action_and_steering_reaches_engineer(tmp_path):
    _, item = project(tmp_path)
    backend = EvidenceBackend()
    manager = Manager(tmp_path, runner=backend, memory_maintenance_enabled=False)
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
    from argus_skill.apps._runtime_execute import _engineer_guidance

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

    manager = Manager(tmp_path, runner=SlowBackend(), memory_maintenance_enabled=False)
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
    manager = Manager(tmp_path, runner=backend, memory_maintenance_enabled=False)
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
    assert backlog.resume_paused(item.id)
    assert not waiting_for_evidence(tmp_path)
    assert observe_project(tmp_path, event=event).evidence_revision != first["evidence_revision"]


def test_failed_provider_does_not_modify_any_team_control(tmp_path):
    project(tmp_path)

    class FailedBackend:
        def run_exec(self, **kwargs):
            return RunnerResult(exit_code=1, fatal_error="provider failed")

    result = supervise(Manager(tmp_path, runner=FailedBackend(), memory_maintenance_enabled=False),
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
    from argus_skill.life.memory import LifeMemory
    from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig
    from argus_skill.manager.supervision import shutdown_supervision

    backlog, item = project(tmp_path)
    backend = EvidenceBackend()
    manager = Manager(tmp_path, runner=backend, memory_maintenance_enabled=False)
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
                "type": EventType.ROUND_REVIEW_COMPLETED, "item_id": item.id,
                "status": "continue", "reason": "The grouped mean check failed", "round_index": 1,
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
    from argus_skill.manager.supervision import (
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

    manager = Manager(tmp_path, runner=Uncooperative(), memory_maintenance_enabled=False)
    start_supervision(tmp_path)
    assert schedule_supervision(manager, tmp_path, {"type": EventType.LIFE_MISSION_COMPLETED})
    try:
        assert entered.wait(1)
        started = time.monotonic()
        assert shutdown_supervision(tmp_path, timeout=0.02) == 1
        assert time.monotonic() - started < 0.25
        assert not schedule_supervision(manager, tmp_path, {"type": EventType.LIFE_MISSION_COMPLETED})
        release.set()
        wait_for_receipt(tmp_path, "superseded")
        assert not (tmp_path / "inbox.jsonl").exists()
        assert not (tmp_path / ".manager_session.json").exists()
    finally:
        release.set()
        assert shutdown_supervision(tmp_path) == 0


def test_unobserved_evidence_reference_cannot_authorize_steering(tmp_path):
    project(tmp_path)

    class Unfounded(EvidenceBackend):
        def run_exec(self, **kwargs):
            return RunnerResult(exit_code=0, agent_messages=[json.dumps({
                "action": "steer", "reason": "Claim from an unread file", "directive": "Change course",
                "evidence_refs": ["other-project/private.json"],
            })])

    result = supervise(Manager(tmp_path, runner=Unfounded(), memory_maintenance_enabled=False),
                       tmp_path, {"type": EventType.LIFE_MISSION_COMPLETED})
    assert result["status"] == "failed" and result["cited_refs"] == []
    assert load_active_manager_directive(tmp_path) is None
    assert not (tmp_path / "inbox.jsonl").exists()


def test_reply_followup_and_restarted_inspect_share_identity_and_refresh_evidence(tmp_path):
    from argus_skill.apps._runtime import _SkillLoopRunner

    backlog, item = project(tmp_path)

    def frontend(backend):
        runner = _SkillLoopRunner.__new__(_SkillLoopRunner)
        runner._backend = backend
        runner._manager_session_root = tmp_path
        runner._args = SimpleNamespace(workdir=str(tmp_path), engineer_model="local-engineer")
        runner._next_seed_thread_id = runner.last_thread_id = None
        runner.manager = Manager(tmp_path, runner=backend, memory_maintenance_enabled=False)
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


def test_applied_wait_prevents_planner_calls_until_new_operator_facts(tmp_path, monkeypatch):
    from argus_skill.life.memory import LifeMemory
    from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig
    from argus_skill.life.supervisor._constants import PLAN_AWAITING, PLAN_ERROR
    from argus_skill.planner import PlannerVerdict

    backlog, item = project(tmp_path, question="Supply the missing author declaration")
    manager = Manager(tmp_path, runner=EvidenceBackend(), memory_maintenance_enabled=False)
    assert supervise(manager, tmp_path, {"type": EventType.LIFE_PLANNER_VERDICT})["status"] == "applied"
    calls = []

    def plan_next(_planner, **kwargs):
        calls.append(kwargs)
        return PlannerVerdict(project_done=False, error="The independent checker is temporarily unavailable")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", plan_next)
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
    for _ in range(5):
        assert supervisor._plan_next_work() == PLAN_AWAITING
    assert calls == []
    backlog.update(item.id, pending_question="")
    assert backlog.resume_paused(item.id)
    assert supervisor._plan_next_work() == PLAN_ERROR
    assert len(calls) == 1


def test_saturated_scheduler_retains_latest_evidence_for_the_waiting_project(tmp_path):
    from argus_skill.manager.supervision import (
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
        managers.append(Manager(root, runner=backend, memory_maintenance_enabled=False))
        start_supervision(root)
    try:
        for number in range(2):
            assert schedule_supervision(managers[number], roots[number], {"type": EventType.LIFE_MISSION_COMPLETED})
        assert all(signal.wait(1) for signal in entered)
        assert schedule_supervision(managers[2], roots[2], {"type": EventType.LIFE_MISSION_COMPLETED, "event_id": "obsolete"})
        assert schedule_supervision(managers[2], roots[2], {"type": EventType.LIFE_MISSION_COMPLETED, "event_id": "latest"})
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
    from argus_skill.manager._session_ops import _ManagerSession

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


def test_background_yields_the_shared_session_when_an_interactive_ask_arrives(tmp_path, monkeypatch):
    from argus_skill.manager import front_door
    from argus_skill.webapi import manager_bridge

    project(tmp_path)
    entered = threading.Event()

    class Cooperative(EvidenceBackend):
        def run_exec(self, **kwargs):
            if kwargs["run_label"] == "manager-supervision":
                entered.set()
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    reason = kwargs["options"].external_interrupt_reason_provider()
                    if reason:
                        return RunnerResult(exit_code=1, fatal_error=reason, thread_id="late-background-id")
                    time.sleep(0.005)
                raise AssertionError("Foreground conversation did not interrupt background Manager")
            return super().run_exec(**kwargs)

    backend = Cooperative()
    manager = Manager(tmp_path, runner=backend, memory_maintenance_enabled=False)
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *args: SimpleNamespace(manager=manager, _backend=backend))
    assert "grouped-mean" in manager_bridge._answer_inline("priority-sid", tmp_path, "What is the task?")
    with ThreadPoolExecutor(max_workers=1) as pool:
        background = pool.submit(supervise, manager, tmp_path, {"type": EventType.LIFE_MISSION_COMPLETED})
        assert entered.wait(1)
        started = time.monotonic()
        assert "grouped-mean" in manager_bridge._answer_inline("priority-sid", tmp_path, "Please answer now")
        assert time.monotonic() - started < 1
        assert background.result(timeout=1)["status"] == "superseded"
    assert backend.calls[-1][1] == "persistent-manager"
    assert manager._session.thread_id == "persistent-manager"
    assert not list((tmp_path / ".manager_session_foreground").glob("*.json"))


@pytest.mark.parametrize("failure_point", ["directive", "continuous", "final_receipt"])
def test_issued_outbox_recovers_real_write_crashes_without_another_model_call(tmp_path, monkeypatch, failure_point):
    from pathlib import Path

    from argus_skill.daemon.state import _continuous_config_path
    from argus_skill.manager import supervision

    class ProcessCrash(BaseException):
        pass

    project(tmp_path)
    review(tmp_path, "continue", "The grouped check failed")
    backend = EvidenceBackend()
    manager = Manager(tmp_path, runner=backend, memory_maintenance_enabled=False)
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
        from argus_skill.life.memory import LifeMemory
        from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig
        from argus_skill.manager.supervision import shutdown_supervision

        restarted = Manager(tmp_path, runner=backend, memory_maintenance_enabled=False)
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
        recovered = supervise(Manager(tmp_path, runner=backend, memory_maintenance_enabled=False), tmp_path, event)
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
    from argus_skill.manager import directive

    class ProcessCrash(BaseException):
        pass

    project(tmp_path)
    review(tmp_path, "continue", "Grouping failed")
    backend = EvidenceBackend()
    manager = Manager(tmp_path, runner=backend, memory_maintenance_enabled=False)
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
    from argus_skill import SkillLoop, SkillLoopConfig

    return SkillLoop(
        skills_dir=root / "skills", engineer_runner=backend, reviewer_runner=backend,
        config=SkillLoopConfig(
            engineer_model="fixture", reviewer_model="fixture", workflow_mode="direct",
            active_vertical="software", role_session_policy="fresh", max_rounds=1,
            require_independent_review=True, require_post_task_learning=False,
            wiki_enabled=False, auto_init_wiki=False, session_id=mission_id,
            operator_question_policy_root=root,
        ),
    )


def task_at(backlog, item_id):
    return next(item for item in backlog.active() if item.id == item_id)


def test_wait_parks_only_task_a_and_independent_b_and_answered_a_can_run(tmp_path):
    from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
    from argus_skill.life.memory import LifeMemory
    from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig

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
    manager = Manager(tmp_path, runner=EvidenceBackend(), memory_maintenance_enabled=False)
    receipt = supervise(manager, tmp_path, {"type": EventType.LIFE_PLANNER_VERDICT})
    assert receipt["waiting_task_ids"] == [task_a.id]
    assert waiting_for_evidence(tmp_path, task_a.id)
    assert not waiting_for_evidence(tmp_path, task_b.id)
    backend_a = MemoryBackend()
    outcome_a = role_loop(tmp_path, task_a.id, backend_a).run(task_a.objective, workdir=tmp_path)
    assert outcome_a.status == "paused_operator"
    assert outcome_a.rounds == [] and backend_a.history == []
    state = SimpleNamespace(
        item=task_at(backlog, task_a.id), outcome=SimpleNamespace(final_review_status=""),
        status=outcome_a.status, stop_kind=None, stop_reason=outcome_a.reason,
        usage_summary=SimpleNamespace(pricing_status="known"), usd=0.0, known_usd=0.0,
        context_packet_path=None,
    )
    assert supervisor._maybe_pause_for_recoverable_stop(state)["status"] == "paused_operator"
    assert task_at(backlog, task_a.id).pending_question == "Provide the author declaration"
    assert waiting_for_evidence(tmp_path, task_a.id)

    backend_b = MemoryBackend()
    backend_b.queue("engineer-r1", CannedResponse(message="The independent syntax check passed"))
    backend_b.queue("reviewer", CannedResponse(message=json.dumps({"status": "done", "reason": "Syntax verified", "next_action": ""})))
    assert role_loop(tmp_path, task_b.id, backend_b).run(task_b.objective, workdir=tmp_path).successful
    backlog.update(task_b.id, status="done")
    assert waiting_for_evidence(tmp_path, task_a.id), "unrelated task completion cannot answer A's question"

    backlog.update(task_a.id, pending_question="")
    assert backlog.resume_paused(task_a.id)
    backlog.mark_running(task_a.id)
    assert not waiting_for_evidence(tmp_path, task_a.id)
    backend_a.queue("engineer-r1", CannedResponse(message="Applied the supplied author declaration"))
    backend_a.queue("reviewer", CannedResponse(message=json.dumps({"status": "done", "reason": "Declaration verified", "next_action": ""})))
    assert role_loop(tmp_path, task_a.id, backend_a).run(task_a.objective, workdir=tmp_path).successful


def test_wait_arriving_during_engineer_work_finishes_the_write_and_skips_reviewer(tmp_path):
    from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend

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
    manager = Manager(tmp_path, runner=EvidenceBackend(), memory_maintenance_enabled=False)
    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(loop.run, item.objective, workdir=tmp_path)
        try:
            assert entered.wait(1)
            assert supervise(manager, tmp_path, {"type": EventType.LIFE_PLANNER_VERDICT})["status"] == "applied"
            assert not work.done() and artifact.read_text() == "in progress"
            release.set()
            outcome = work.result(timeout=1)
            assert outcome.status == "paused_operator"
            assert outcome.rounds == [], "the control boundary must not fabricate a Reviewer verdict"
            assert artifact.read_text() == "write complete"
            assert [label for label, _, _ in backend.history] == ["engineer-r1"]
        finally:
            release.set()


def test_issued_event_failure_does_not_discard_the_durable_decision(tmp_path, monkeypatch):
    from argus_skill.manager import supervision

    project(tmp_path)
    event = {"type": EventType.LIFE_PHASE_STARTED, "agent_layer": "engineer", "round_index": 2}
    backend = EvidenceBackend()
    manager = Manager(tmp_path, runner=backend, memory_maintenance_enabled=False)
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

    from argus_skill.manager import supervision

    class ProcessCrash(BaseException):
        pass

    project(tmp_path)
    event = {"type": EventType.LIFE_PHASE_STARTED, "agent_layer": "engineer", "round_index": 2}
    backend = EvidenceBackend()
    manager = Manager(tmp_path, runner=backend, memory_maintenance_enabled=False)
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
    from argus_skill.manager import supervision

    project(tmp_path)
    backend = EvidenceBackend()
    manager = Manager(tmp_path, runner=backend, memory_maintenance_enabled=False)
    original = supervision._apply
    attempts = []

    def briefly_busy(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("daemon control is busy")
        return original(*args, **kwargs)

    monkeypatch.setattr(supervision, "_apply", briefly_busy)
    supervision.start_supervision(tmp_path)
    assert supervision.schedule_supervision(manager, tmp_path, {"type": EventType.LIFE_MISSION_COMPLETED})
    try:
        assert wait_for_receipt(tmp_path, "applied")["status"] == "applied"
        assert len(attempts) == 2 and len(backend.calls) == 1
    finally:
        assert supervision.shutdown_supervision(tmp_path) == 0
