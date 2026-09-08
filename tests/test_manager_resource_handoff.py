"""A mission unable to acquire its prerequisites must yield to plan revision."""

from __future__ import annotations

import json

import pytest

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.apps._runtime_backends import _Outcome
from argus_skill.core.role_decision import encode_role_decision
from argus_skill.core.role_handoff import decision_engineer_handoff, parse_engineer_handoff
from argus_skill.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus_skill.manager.plan_challenge import adjudicate_plan_challenge
from argus_skill.planner import PlannerConfig
from argus_skill.reviewer import Reviewer, ReviewerConfig
from argus_skill.skills.vertical_select import persist_vertical

BLOCKER = "Locks remain 0/12; work/coordination/locks is outside writable_paths."
REPAIR = "Assign an authorized lock-acquisition prerequisite before dynamic validation."


def _run(backend, tmp_path, *, require_independent_review=True, context_packet_path=""):
    # A redispatch on the broken path must produce a valid verdict, so the
    # regression fails on routing rather than spending time in parser retries.
    backend.default = CannedResponse(message=json.dumps({
        "status": "continue", "reason": BLOCKER, "next_action": REPAIR,
        "forward_progress": False, "plan_signal": "continue",
    }))
    events = []
    result = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    ).run(
        objective="Validate dynamic timing after acquiring all 12 resource locks.",
        engineer_prompt_builder=lambda _next, _static=True: "Do the assigned task.",
        supervised_config=SupervisedConfig(
            max_rounds=3,
            require_independent_review=require_independent_review,
            decision_progress_timeout_seconds=0,
            context_packet_path=context_packet_path,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )
    return result, events


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("require_independent_review", [False, True])
def test_explicit_manager_handoff_exits_before_redispatch(
    tmp_path, structured, require_independent_review,
):
    message = (
        encode_role_decision("engineer", {
            "status": "blocked",
            "result": BLOCKER,
            "next_owner": "manager",
        })
        if structured
        else f"{BLOCKER}\nDecision:\nMILESTONE_STATUS=blocked\nNEXT_OWNER=manager"
    )
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message=message))

    (status, rounds, final, _reason, _thread), events = _run(
        backend, tmp_path, require_independent_review=require_independent_review,
    )

    assert status == "replan_requested"
    assert [label for label, *_ in backend.history] == ["engineer-r1"]
    assert len(rounds) == 1
    assert BLOCKER in final
    review = rounds[0].review
    assert review.review_source == "engineer_manager_handoff"
    assert not review.operator_question
    assert adjudicate_plan_challenge(
        review.planner_report, reviewer_status=review.status,
    ).action == "revise"
    settled = [e for e in events if e["type"] == "round.review.completed"]
    assert settled[-1]["status"] == "replan_requested"


@pytest.mark.parametrize("structured", [False, True])
def test_no_progress_reconsider_reaches_manager_without_another_round(tmp_path, structured):
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message=BLOCKER))
    review = (
        json.dumps({
            "status": "continue",
            "reason": BLOCKER,
            "next_action": REPAIR,
            "forward_progress": False,
            "plan_signal": "reconsider",
            "plan_challenge": BLOCKER,
            "plan_alternative": REPAIR,
            "authority_impact": "technical",
        })
        if structured
        else (
            f"Decision:\nSTATUS=continue\nREASON={BLOCKER}\nNEXT_ACTION={REPAIR}\n"
            f"FORWARD_PROGRESS=false\nPLAN_SIGNAL=reconsider\nPLAN_CHALLENGE={BLOCKER}\n"
            f"PLAN_ALTERNATIVE={REPAIR}\nAUTHORITY_IMPACT=technical"
        )
    )
    backend.queue("reviewer", CannedResponse(message=review))

    (status, rounds, _final, _reason, _thread), events = _run(backend, tmp_path)

    assert status == "replan_requested"
    assert [label for label, *_ in backend.history] == ["engineer-r1", "reviewer"]
    assert len(rounds) == 1
    review = rounds[0].review
    assert review.status == "replan_requested"
    assert review.planner_report["forward_progress"] is False
    assert adjudicate_plan_challenge(
        review.planner_report, reviewer_status=review.status,
    ).action == "replace"
    settled = [e for e in events if e["type"] == "round.review.completed"]
    assert settled[-1]["status"] == "replan_requested"


@pytest.mark.parametrize("progress,signal", [(True, "reconsider"), (None, "reconsider"), (False, "continue")])
def test_plan_advice_and_local_repairs_can_still_continue(tmp_path, progress, signal):
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="Inspected the failing timing test."))
    backend.queue("reviewer", CannedResponse(message=json.dumps({
        "status": "continue", "reason": "One in-scope repair remains.",
        "next_action": "Repair the test.",
        "planner_report": {"forward_progress": progress, "plan_signal": signal},
    })))
    backend.queue("engineer-r2", CannedResponse(message="The timing test passes."))
    backend.queue("reviewer", CannedResponse(message=json.dumps({
        "status": "done", "reason": "Verified.", "next_action": "",
    })))

    (status, rounds, *_), _events = _run(backend, tmp_path)

    assert status == "done"
    assert len(rounds) == 2


@pytest.mark.parametrize("status", ["done", "blocked"])
def test_terminal_verdict_is_not_reopened_by_plan_advice(tmp_path, status):
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="Examined the existing evidence."))
    backend.queue("reviewer", CannedResponse(message=json.dumps({
        "status": status, "reason": "The current evidence settles this verdict.",
        "next_action": "", "forward_progress": False, "plan_signal": "reconsider",
    })))

    (settled, rounds, *_), _events = _run(backend, tmp_path)

    assert settled == status
    assert len(rounds) == 1


def test_manager_handoff_cannot_discard_an_explicit_operator_question():
    question = "May I publish this production release?"
    payload = {"next_owner": "manager", "operator_question": question}
    handoff = decision_engineer_handoff(payload)

    assert handoff.waits_for_operator
    assert handoff == parse_engineer_handoff(
        f"NEXT_OWNER=manager\nOPERATOR_QUESTION={question}"
    )


@pytest.mark.parametrize("engineer_handoff", [False, True])
def test_supervisor_routes_blocker_to_manager_and_replaces_invalid_plan(
    tmp_path, monkeypatch, engineer_handoff,
):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    persist_vertical(project, "software", workflow_mode="direct")
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message=(
        f"{BLOCKER}\nDecision:\nMILESTONE_STATUS=blocked\n"
        f"NEXT_OWNER={'manager' if engineer_handoff else 'reviewer'}"
    )))
    backend.queue("reviewer", CannedResponse(message=json.dumps({
        "status": "continue", "reason": BLOCKER, "next_action": REPAIR,
        "forward_progress": False, "plan_signal": "reconsider",
        "plan_challenge": BLOCKER, "plan_alternative": REPAIR,
    })))

    class MissionRunner:
        def execute(self, **kwargs):
            (status, rounds, final, reason, thread), events = _run(
                backend, project, context_packet_path=kwargs.get("context_packet_path"),
            )
            for event in events:
                kwargs["sink"].handle_event(event)
            review = rounds[-1].review
            return _Outcome(
                success=status == "done", status=status, stop_reason=reason,
                rounds=len(rounds), last_thread_id=thread, final_message=final,
                final_review_status=review.status, final_review_source=review.review_source,
                final_review_reason=review.reason, final_review_next_action=review.next_action,
                final_planner_report=review.planner_report,
            )

    planner = MemoryBackend(default=CannedResponse(message="\n".join([
        "PROJECT_DONE=false", "REASON=Assign lock acquisition before runtime validation.",
        "TASK_KEY=locks", "TASK_TITLE=Acquire runtime resource locks",
        "TASK_OBJECTIVE=Verify availability and acquire all 12 resource locks.",
        "TASK_OWNS_PATHS=work/coordination/locks/**",
        "TASK_KEY=runtime", "TASK_DEPS=locks", "TASK_TITLE=Validate dynamic timing",
        "TASK_OBJECTIVE=Reuse the completed offline repair and run dynamic timing with held locks.",
        "TASK_OWNS_PATHS=work/m4/**",
    ])))
    memory = LifeMemory.open(tmp_path / "life")
    supervisor = LifeSupervisor(
        memory=memory, runner=MissionRunner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            budget=LifeBudget(global_daily_cap_usd=0, max_missions=1),
            continuous=False, continuous_objective="Complete dynamic timing validation.",
            project_worktree=project, artifact_root=project,
        ),
        planner_runner=planner,
    )
    supervisor._vertical_resolved = True
    supervisor._planner_config = lambda: PlannerConfig(working_dir=str(project))
    original = memory.backlog.add(BacklogItem.new(
        title="Validate timing under incomplete permissions",
        objective="Complete dynamic timing validation after acquiring locks.",
        plan_id="invalid-plan", plan_version=1, node_key="runtime",
        owns_paths=["work/m4/**"],
        tags=["planner", "scope:bounded", "review:required"],
        manager_decision={"routed": True, "vertical": "software", "route_source": "planner"},
    ))

    supervisor.run()

    assert [label for label, *_ in backend.history] == (
        ["engineer-r1"] if engineer_handoff else ["engineer-r1", "reviewer"]
    )
    assert len(planner.history) == 1
    assert BLOCKER in planner.history[0][1]
    items = {item.id: item for item in memory.backlog.all()}
    assert items[original.id].status == "superseded"
    locks = next(item for item in items.values() if item.node_key == "locks")
    runtime = next(item for item in items.values() if item.node_key == "runtime" and item.id != original.id)
    assert locks.owns_paths == ["work/coordination/locks/**"]
    assert runtime.deps == [locks.id]
    assert runtime.owns_paths == original.owns_paths
    assert locks.status == runtime.status == "pending"
    events = [json.loads(line) for line in (memory.root / "events.jsonl").read_text().splitlines()]
    assert any(event["type"] == "life.manager.plan_challenge.decided" for event in events)
    assert any(event["type"] == "life.plan.revision.committed" for event in events)
    # A request for plan-owner intervention must not become independent
    # Reviewer evidence merely because it uses the common settlement path.
    reviewed_packet = memory.root / "handoffs" / original.id / "round-0001.json"
    assert reviewed_packet.exists() is not engineer_handoff
    if reviewed_packet.exists():
        packet = json.loads(reviewed_packet.read_text())
        assert packet["producer_role"] == "reviewer"
        assert packet["review"]["status"] == "replan_requested"
    assert not (project / "work/coordination/locks").exists()
