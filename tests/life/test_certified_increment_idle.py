"""Certified increments report once without closing a standing campaign."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.transcript import read_turns
from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.planner_verdict_outbox import load_planner_verdict_outbox
from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus_skill.life.supervisor._constants import (
    PLAN_AWAITING,
    PLAN_ERROR,
    PLAN_RETRY,
    PLAN_TERMINAL_IDLE,
)
from argus_skill.planner import PlannerVerdict, TaskSpec, WaitingContract
from argus_skill.skills.stage_machine import completion_contract_fingerprint
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals._base import load_vertical, vertical_completion_contract_version


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "result.txt").write_text("accepted increment\n")
    life = tmp_path / "life"
    persist_vertical(project, "software", workflow_mode="direct")
    path = project / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(path.read_text())
    version = vertical_completion_contract_version(load_vertical("software", project_root=project))
    state["current_stage"] = "delivery"
    state["stages"] = {
        "delivery": {
            "status": "done",
            "completion_contract_version": version,
            "completion_contract_sha256": completion_contract_fingerprint(
                project, "delivery", version=version,
            ),
        },
    }
    path.write_text(json.dumps(state))
    reports = []

    def report(**kwargs):
        reports.append(kwargs)
        return "Manager: certified increment complete; standing campaign remains active."

    manager = SimpleNamespace(report_project_completion=report)

    def unexpected_plan(*_args, **_kwargs):
        pytest.fail("Certified unchanged state must not call Planner")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", unexpected_plan)

    def make(*, open_ended=True):
        memory = LifeMemory.open(life)
        supervisor = LifeSupervisor(
            memory=memory,
            runner=SimpleNamespace(),
            sink=JsonlEventSink(None, life_dir=life, verbosity="full"),
            config=LifeSupervisorConfig(
                continuous=True,
                continuous_objective="keep improving",
                open_ended=open_ended,
                final_certification_gate=True,
                project_worktree=project,
                artifact_root=project,
            ),
            planner_runner=object(),
        )
        monkeypatch.setattr(supervisor, "_bound_manager", lambda: manager)
        supervisor._vertical_resolved = True
        monkeypatch.setattr(supervisor, "_manager_intent_context", lambda: {})
        monkeypatch.setattr(supervisor, "_effective_final_certification_gate", lambda _root: True)
        return supervisor

    def certify(supervisor):
        # Use the real journal projection and current-signature gate, not a
        # mocked certification boolean. No historical production event is read.
        supervisor.sink.handle_event({
            "type": "life.mission.completed",
            "item_id": "certified-increment",
            "title": "Independent final review",
            "summary": "Certified",
            "success": True,
            "status": "done",
            "scope": "final_submission",
            "final_submission_certified": True,
            "final_submission_signature": supervisor._final_submission_signature(),
        })
        assert supervisor._journal_has_final_certification()

    supervisor = make()
    certify(supervisor)
    return SimpleNamespace(
        supervisor=supervisor, make=make, certify=certify, project=project,
        life=life, reports=reports, manager=manager,
    )


def _reports(life: Path):
    return [
        turn for turn in read_turns(life)
        if str(turn.get("message_id") or "").startswith("manager-project-report-")
    ]


def _legacy_handoff(campaign):
    from argus_skill.core.operator_context import OperatorContextStore

    instruction = "Keep the certified artifact unchanged; report and await new explicit direction."
    (campaign.life / "STEERING.jsonl").write_text(json.dumps({
        "id": "legacy-handoff", "kind": "directive", "source": "operator.inbox",
        "text": instruction, "timestamp": "2026-01-01T00:00:00Z", "version": 1,
    }) + "\n")
    store = OperatorContextStore(campaign.life)
    assert store.revision == 1
    assert store.acknowledged_revision("planner") == 0
    assert not store.ledger_path.exists()
    return instruction, store


@pytest.mark.parametrize("reason", [
    "The current final certification is accepted. Waiting for new explicit operator direction.",
    "当前稿件已完成终局独立认证，现保留全部成果并等待新的明确研究指示。",
])
@pytest.mark.parametrize("contracted", [False, True])
@pytest.mark.parametrize("fresh_inbox", [False, True])
def test_legacy_certified_wait_reports_once_without_recertification(
    campaign, monkeypatch, reason, contracted, fresh_inbox,
):
    instruction, store = _legacy_handoff(campaign)
    fresh_instruction = "Publish the current final report; do not repeat certification."
    if fresh_inbox:
        inbox = [fresh_instruction]
        campaign.supervisor.config.user_inbox = lambda: inbox.pop(0) if inbox else None
    calls = []

    def plan(_planner, **kwargs):
        calls.append(kwargs["runtime_change_summary"])
        return PlannerVerdict(
            project_done=False, reason=reason, waiting=True,
            waiting_contract=(
                WaitingContract(
                    blocker_fingerprint="new-direction", recheck_token="certified",
                    recheck_condition="New operator instruction arrives",
                    operator_action_required=True, wait_mode="event",
                    wake_on=("authorization",),
                ) if contracted else None
            ),
        )

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", plan)
    assert campaign.supervisor._plan_next_work() == PLAN_TERMINAL_IDLE
    assert len(calls) == 1 and instruction in calls[0]
    if fresh_inbox:
        assert fresh_instruction in calls[0]
    assert store.acknowledged_revision("planner") == store.revision
    record = load_planner_verdict_outbox(campaign.life)
    assert record["event"]["handled_operator_context_revision"] == store.revision
    assert record["event"]["completion_kind"] == "certified_increment"
    assert record["event"]["project_done"] is False
    for _ in range(3):
        restarted = campaign.make()
        assert restarted._plan_next_work() == PLAN_TERMINAL_IDLE
        assert not restarted.memory.backlog.active()
        assert restarted.config.continuous and restarted.config.open_ended
    assert len(calls) == len(campaign.reports) == len(_reports(campaign.life)) == 1


@pytest.mark.parametrize("once", [False, True])
@pytest.mark.parametrize("failure", ["report", "publish", "verdict", "outbox", "input_ack"])
def test_certified_handoff_delivery_failure_does_not_repeat_planning(
    campaign, monkeypatch, failure, once,
):
    from argus_skill.core import operator_messages
    from argus_skill.core.operator_context import OperatorContextStore, append_directive
    from argus_skill.life.supervisor import _core

    if once:
        append_directive(
            campaign.life, "Preserve the certified result and wait for new direction.",
            lifetime="once", expected_revision=0,
        )
        store = OperatorContextStore(campaign.life)
    else:
        _, store = _legacy_handoff(campaign)
    calls = []

    def plan(_planner, **_kwargs):
        calls.append(True)
        return PlannerVerdict(
            project_done=False, waiting=True,
            reason="Certified artifact preserved; waiting for new operator instruction.",
        )

    def fail(*_args, **_kwargs):
        raise OSError("injected handoff delivery failure")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", plan)
    with monkeypatch.context() as delivery:
        if failure == "report":
            delivery.setattr(campaign.manager, "report_project_completion", lambda **_kw: "")
        elif failure == "publish":
            delivery.setattr(operator_messages, "publish_operator_message", fail)
        elif failure == "outbox":
            delivery.setattr(_core, "write_planner_verdict_outbox", fail)
        elif failure == "input_ack":
            delivery.setattr(OperatorContextStore, "acknowledge", fail)
        else:
            original_emit = campaign.supervisor._emit
            delivery.setattr(
                campaign.supervisor, "_emit",
                lambda event: False if event["type"] == "life.planner.verdict" else original_emit(event),
            )
        assert campaign.supervisor._plan_next_work() == PLAN_RETRY
    # Only a durably recorded and checkpointed handoff can skip Planner.
    needs_replan = failure in {"outbox", "input_ack"}
    assert store.acknowledged_revision("planner") == (0 if needs_replan else 1)
    assert campaign.make()._plan_next_work() == PLAN_TERMINAL_IDLE
    assert campaign.make()._plan_next_work() == PLAN_TERMINAL_IDLE
    assert len(calls) == (2 if needs_replan else 1)
    assert len(_reports(campaign.life)) == 1
    if once:
        assert not store.project("planner", consume_once=False).directives


@pytest.mark.parametrize("arrival", ["planning", "report", "after_report_failure"])
def test_certified_handoff_never_consumes_newer_work_after_failure(
    campaign, monkeypatch, arrival,
):
    from argus_skill.core.operator_context import append_directive

    _, store = _legacy_handoff(campaign)
    newer_instruction = "Evaluate the newly requested experiment, not the old increment."

    def append_newer():
        append_directive(
            campaign.life, newer_instruction, lifetime="once", expected_revision=1,
        )

    def plan(_planner, **_kwargs):
        if arrival == "planning":
            append_newer()
        return PlannerVerdict(
            project_done=False, waiting=True,
            reason="Certified increment accepted; waiting for new operator direction.",
        )

    original_report = campaign.manager.report_project_completion

    def report(**kwargs):
        if arrival == "report":
            append_newer()
        if arrival == "after_report_failure":
            return ""
        return original_report(**kwargs)

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", plan)
    campaign.manager.report_project_completion = report
    campaign.supervisor._plan_next_work()
    if arrival == "after_report_failure":
        append_newer()
    assert store.acknowledged_revision("planner") == 1 < store.revision
    calls = []

    def failed_new_work(_planner, **kwargs):
        calls.append(kwargs["runtime_change_summary"])
        return PlannerVerdict(project_done=False, reason="Backend failed", error="injected failure")

    campaign.manager.report_project_completion = original_report
    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", failed_new_work)
    for _ in range(2):
        assert campaign.make()._plan_next_work() == PLAN_ERROR
    assert len(calls) == 2 and all(newer_instruction in call for call in calls)
    assert store.acknowledged_revision("planner") == 1
    assert newer_instruction in [
        row.text for row in store.project("planner", consume_once=False).directives
    ]
    assert load_planner_verdict_outbox(campaign.life) is None
    assert len(_reports(campaign.life)) == (0 if arrival == "after_report_failure" else 1)


@pytest.mark.parametrize("reason", [
    "Wait for the experiment result.",
    "Waiting for new experiment results.",
    "Waiting for operator credentials.",
    "No actionable work after the backend failure.",
    "Waiting for new operator direction while the experiment is still running.",
])
def test_uncontracted_wait_is_not_blindly_acknowledged(campaign, monkeypatch, reason):
    from argus_skill.core.operator_context import OperatorContextStore, append_directive

    instruction = "Run the new experiment even though the previous attempt failed."
    append_directive(campaign.life, instruction, lifetime="once", expected_revision=0)
    monkeypatch.setattr(
        LifeSupervisor, "_reconcile_open_ended_planner_waiting", lambda *_args: False,
    )
    monkeypatch.setattr(
        LifeSupervisor, "_planner_turn_available_during_wait", lambda *_args: False,
    )
    calls = []

    def plan(_planner, **kwargs):
        calls.append(kwargs["runtime_change_summary"])
        return PlannerVerdict(project_done=False, waiting=True, reason=reason)

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", plan)
    for _ in range(2):
        assert campaign.make()._plan_next_work() == PLAN_AWAITING
    store = OperatorContextStore(campaign.life)
    assert store.acknowledged_revision("planner") == 0
    assert len(calls) == 2 and all(instruction in call for call in calls)
    assert not _reports(campaign.life)


@pytest.mark.parametrize("blocker", [
    "not_invoked", "error", "revision", "tasks", "retire", "advance", "pending",
    "paused", "job", "nonterminal_stage", "stale_cert", "external_contract",
])
def test_certified_handoff_requires_current_final_idle_state(campaign, monkeypatch, blocker):
    from dataclasses import replace

    from argus_skill.life.supervisor._planning_cycle_helpers import _PlanCycleState

    supervisor = campaign.supervisor
    state = _PlanCycleState(None)
    state.planner_invoked = True
    state.verdict = PlannerVerdict(
        project_done=False, waiting=True, reason="Waiting for new operator direction.",
    )
    if blocker == "not_invoked":
        state.planner_invoked = False
    elif blocker == "error":
        state.verdict = replace(state.verdict, error="failed")
    elif blocker == "revision":
        state.revision_request = {"expected_plan_id": "old-plan"}
    elif blocker == "tasks":
        state.verdict = replace(state.verdict, new_tasks=[TaskSpec(title="Next", objective="New work")])
    elif blocker == "retire":
        state.verdict = replace(state.verdict, retire_tasks=(("old", "retire"),))
    elif blocker == "advance":
        state.verdict = replace(state.verdict, advance_to_stage="delivery")
    elif blocker in {"pending", "paused"}:
        item = supervisor.memory.backlog.add(BacklogItem.new(title="New work", objective="New work"))
        if blocker == "paused":
            supervisor.memory.backlog.update(item.id, status="paused_operator", pending_question="Approve?")
    elif blocker == "job":
        monkeypatch.setattr(supervisor, "_waitable_subagent_jobs", lambda: [object()])
    elif blocker == "nonterminal_stage":
        path = campaign.project / ".argus" / "PIPELINE_STATE.json"
        pipeline = json.loads(path.read_text())
        pipeline["stages"]["delivery"]["status"] = "active"
        path.write_text(json.dumps(pipeline))
    elif blocker == "stale_cert":
        (campaign.project / "result.txt").write_text("new uncertified result")
    else:
        state.verdict = replace(state.verdict, waiting_contract=WaitingContract(
            blocker_fingerprint="experiment", recheck_token="1", recheck_condition="Result arrives",
            operator_action_required=True, wake_on=("filesystem",), watched_paths=("result.txt",),
        ))
    assert supervisor._pc_is_certified_operator_wait(state) is False
    assert not _reports(campaign.life)


@pytest.mark.parametrize("gate", ["external", "research"])
def test_certified_wait_cannot_ack_past_completion_gate(campaign, monkeypatch, gate):
    _, store = _legacy_handoff(campaign)
    monkeypatch.setattr(
        "argus_skill.planner.Planner.plan_next",
        lambda *_args, **_kw: PlannerVerdict(
            project_done=False, waiting=True, reason="Waiting for new operator direction.",
        ),
    )
    if gate == "external":
        monkeypatch.setattr(
            "argus_skill.core.external_completion_gate.external_completion_gate_issue",
            lambda _root: "external acceptance remains unsatisfied",
        )
    else:
        monkeypatch.setattr(
            "argus_skill.life.supervisor._planning_cycle_completion._research_project_done_issue",
            lambda *_args, **_kw: "research target remains unsatisfied",
        )
    assert campaign.supervisor._plan_next_work() == PLAN_RETRY
    assert store.acknowledged_revision("planner") == 0
    assert load_planner_verdict_outbox(campaign.life) is None
    assert not _reports(campaign.life)


def test_certified_increment_reports_once_and_survives_restart(campaign):
    supervisor = campaign.supervisor
    for _ in range(4):
        assert supervisor._plan_next_work() == PLAN_TERMINAL_IDLE
    assert len(campaign.reports) == len(_reports(campaign.life)) == 1
    record = load_planner_verdict_outbox(campaign.life)
    assert record["delivered"] is True
    assert record["outcome"] == PLAN_TERMINAL_IDLE
    assert record["event"]["project_done"] is False
    assert record["event"]["completion_kind"] == "certified_increment"
    context = campaign.reports[0]["completion_context"]
    assert context["standing_objective_active"] is True
    assert context["completion_scope"] == "certified_increment"
    assert context["stages"]["delivery"]["status"] == "done"
    assert supervisor.config.continuous and supervisor.config.open_ended
    assert supervisor.memory.backlog.active() == []

    bookkeeping = campaign.project / ".autors" / "bookkeeping.json"
    bookkeeping.parent.mkdir()
    bookkeeping.write_text('{"updated_at": 42}')
    restarted = campaign.make()
    for _ in range(3):
        assert restarted._plan_next_work() == PLAN_TERMINAL_IDLE
    assert len(campaign.reports) == len(_reports(campaign.life)) == 1


@pytest.mark.parametrize("failure", ["empty", "raise", "publish", "verdict", "ack"])
def test_report_and_verdict_failures_retry_durably(campaign, monkeypatch, failure):
    from argus_skill.core import operator_messages
    from argus_skill.life.supervisor import _core

    original_report = campaign.manager.report_project_completion
    original_publish = operator_messages.publish_operator_message
    original_ack = _core.mark_planner_verdict_delivered
    original_emit = campaign.supervisor._emit

    def fail(*_args, **_kwargs):
        raise OSError("injected delivery failure")

    if failure == "empty":
        campaign.manager.report_project_completion = lambda **_kwargs: ""
    elif failure == "raise":
        campaign.manager.report_project_completion = fail
    elif failure == "publish":
        monkeypatch.setattr(operator_messages, "publish_operator_message", fail)
    elif failure == "ack":
        monkeypatch.setattr(_core, "mark_planner_verdict_delivered", fail)
    else:
        monkeypatch.setattr(
            campaign.supervisor, "_emit",
            lambda event: False if event["type"] == "life.planner.verdict" else original_emit(event),
        )
    assert campaign.supervisor._plan_next_work() == PLAN_RETRY
    assert not load_planner_verdict_outbox(campaign.life)["delivered"]
    assert not campaign.supervisor._last_open_ended_project_done_signature

    campaign.manager.report_project_completion = original_report
    monkeypatch.setattr(operator_messages, "publish_operator_message", original_publish)
    monkeypatch.setattr(_core, "mark_planner_verdict_delivered", original_ack)
    restarted = campaign.make()
    assert restarted._plan_next_work() == PLAN_TERMINAL_IDLE
    assert restarted._plan_next_work() == PLAN_TERMINAL_IDLE
    assert len(_reports(campaign.life)) == 1
    assert len(campaign.reports) == (2 if failure == "publish" else 1)
    verdicts = [
        json.loads(line) for line in (campaign.life / "events.jsonl").read_text().splitlines()
        if json.loads(line).get("type") == "life.planner.verdict"
    ]
    assert len(verdicts) == 1


@pytest.mark.parametrize("pending_report", [False, True])
def test_new_operator_input_wakes_even_after_restart(campaign, monkeypatch, pending_report):
    if pending_report:
        campaign.manager.report_project_completion = lambda **_kwargs: ""
        assert campaign.supervisor._plan_next_work() == PLAN_RETRY
    else:
        assert campaign.supervisor._plan_next_work() == PLAN_TERMINAL_IDLE
    restarted = campaign.make()
    messages = ["Investigate a genuinely different next increment."]
    restarted.config.user_inbox = lambda: messages.pop(0) if messages else None
    calls = []

    def plan(_planner, **kwargs):
        calls.append(kwargs)
        return PlannerVerdict(project_done=True, reason="injected planner completion")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", plan)
    assert restarted._plan_next_work() == PLAN_RETRY
    assert len(calls) == 1
    assert load_planner_verdict_outbox(campaign.life) is None
    assert len(_reports(campaign.life)) == (0 if pending_report else 1)


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("failure", ["error", "raise", "done", "commit"])
def test_unhandled_input_blocks_old_certificate_until_tasks_committed(
    campaign, monkeypatch, restart, failure,
):
    from argus_skill.core.operator_context import OperatorContextStore

    supervisor = campaign.supervisor
    assert supervisor._plan_next_work() == PLAN_TERMINAL_IDLE
    instruction = "Investigate a genuinely different next increment."
    messages = [instruction]
    supervisor.config.user_inbox = lambda: messages.pop(0) if messages else None
    calls = []
    backlog_type = type(supervisor.memory.backlog)
    original_commit = backlog_type.add_many
    if failure == "commit":
        def reject_commit(*_args, **_kwargs):
            raise OSError("injected DAG commit failure")

        monkeypatch.setattr(backlog_type, "add_many", reject_commit)

    def fail(_planner, **kwargs):
        calls.append(kwargs["runtime_change_summary"])
        if failure == "raise":
            raise RuntimeError("injected Planner failure")
        return PlannerVerdict(
            project_done=failure == "done",
            error="injected Planner failure" if failure == "error" else "",
            reason="injected rejected decision",
            new_tasks=(
                [TaskSpec(title="Investigate next increment", objective=instruction)]
                if failure == "commit" else []
            ),
        )

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", fail)
    assert supervisor._plan_next_work() != PLAN_TERMINAL_IDLE
    assert supervisor._journal_has_final_certification()
    store = OperatorContextStore(campaign.life)
    assert store.revision > store.acknowledged_revision("planner")
    supervisor = campaign.make() if restart else supervisor
    assert supervisor._plan_next_work() != PLAN_TERMINAL_IDLE
    assert len(calls) == 2
    assert instruction in str(calls[-1])
    assert load_planner_verdict_outbox(campaign.life) is None
    assert len(_reports(campaign.life)) == 1

    def succeed(_planner, **kwargs):
        calls.append(kwargs["runtime_change_summary"])
        return PlannerVerdict(
            project_done=False,
            reason="Handle the new operator instruction",
            new_tasks=[TaskSpec(
                title="Investigate next increment", objective=instruction,
                acceptance_check="New direction is evaluated.",
            )],
        )

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", succeed)
    monkeypatch.setattr(backlog_type, "add_many", original_commit)
    assert supervisor._plan_next_work() is True
    assert store.acknowledged_revision("planner") == store.revision
    item = supervisor.memory.backlog.active()[0]
    supervisor.memory.backlog.update(item.id, status="done")
    campaign.certify(supervisor)
    assert campaign.make()._plan_next_work() == PLAN_TERMINAL_IDLE
    assert len(calls) == 3
    assert campaign.make()._plan_next_work() == PLAN_TERMINAL_IDLE


@pytest.mark.parametrize("restart", [False, True])
def test_second_inbox_drain_reenters_intake_before_certification(
    campaign, monkeypatch, restart,
):
    from argus_skill.core.operator_context import OperatorContextStore

    supervisor = campaign.supervisor
    assert supervisor._plan_next_work() == PLAN_TERMINAL_IDLE
    instruction = "Investigate the late operator instruction."
    inbox_calls = []
    replies = iter([None, instruction, None])

    def inbox():
        inbox_calls.append(True)
        return next(replies, None)

    supervisor.config.user_inbox = inbox
    assert supervisor._plan_next_work() == PLAN_RETRY
    assert len(inbox_calls) == 3  # Empty intake, late idle drain, drain terminator.
    assert supervisor._operator_guidance_carryover == [instruction]
    store = OperatorContextStore(campaign.life)
    assert store.revision > store.acknowledged_revision("planner")
    supervisor = campaign.make() if restart else supervisor
    calls = []

    def plan(_planner, **kwargs):
        calls.append(kwargs["runtime_change_summary"])
        return PlannerVerdict(
            project_done=False, reason="test failure", error="injected Planner failure",
        )

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", plan)
    for _ in range(2):
        assert supervisor._plan_next_work() == PLAN_ERROR
    assert len(calls) == 2
    assert all(instruction in str(call) for call in calls)
    assert len(_reports(campaign.life)) == 1
    assert load_planner_verdict_outbox(campaign.life) is None


@pytest.mark.parametrize("event_wait", [False, True])
def test_handled_wait_preserves_waiting_and_report_dedup(campaign, monkeypatch, event_wait):
    from argus_skill.core.operator_context import OperatorContextStore

    supervisor = campaign.supervisor
    assert supervisor._plan_next_work() == PLAN_TERMINAL_IDLE
    messages = ["Wait for the new result before deciding the next increment."]
    supervisor.config.user_inbox = lambda: messages.pop(0) if messages else None
    monkeypatch.setattr(
        LifeSupervisor, "_reconcile_open_ended_planner_waiting", lambda *_args: False,
    )
    monkeypatch.setattr(
        LifeSupervisor, "_planner_turn_available_during_wait", lambda *_args: False,
    )
    calls = []

    def plan(_planner, **kwargs):
        calls.append(kwargs)
        return PlannerVerdict(
            project_done=False, reason="Wait for the result", waiting=True,
            waiting_contract=(
                WaitingContract(
                    blocker_fingerprint="new-result",
                    recheck_condition="The new result arrives",
                    recheck_token="new-result-1",
                    wait_mode="event", wake_on=("filesystem",),
                    watched_paths=("new-result.txt",),
                ) if event_wait else None
            ),
        )

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", plan)
    for _ in range(3):
        assert supervisor._plan_next_work() == PLAN_AWAITING
    assert len(calls) == (1 if event_wait else 2)
    store = OperatorContextStore(campaign.life)
    assert (store.acknowledged_revision("planner") == store.revision) is event_wait
    restarted = campaign.make()
    for _ in range(3):
        assert restarted._plan_next_work() == PLAN_AWAITING
    assert len(calls) == (1 if event_wait else 3)
    assert len(_reports(campaign.life)) == 1
    assert load_planner_verdict_outbox(campaign.life) is None


def test_failed_routing_still_persists_unhandled_input(campaign, monkeypatch):
    from argus_skill.core.operator_context import OperatorContextStore

    supervisor = campaign.supervisor
    assert supervisor._plan_next_work() == PLAN_TERMINAL_IDLE

    def classify(*_args, **_kwargs):
        raise RuntimeError("injected routing failure")

    campaign.manager.classify_front_door = classify
    messages = ["Investigate the next increment despite the routing outage."]
    supervisor.config.user_inbox = lambda: messages.pop(0) if messages else None
    calls = []

    def plan(_planner, **kwargs):
        calls.append(kwargs["runtime_change_summary"])
        return PlannerVerdict(
            project_done=False, reason="test failure", error="injected Planner failure",
        )

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", plan)
    assert supervisor._plan_next_work() == PLAN_ERROR
    assert campaign.make()._plan_next_work() == PLAN_ERROR
    assert len(calls) == 2
    assert all("routing outage" in call for call in calls)
    store = OperatorContextStore(campaign.life)
    assert store.revision > store.acknowledged_revision("planner")


def test_input_arriving_during_planning_is_not_acknowledged(campaign, monkeypatch):
    from argus_skill.core.operator_context import OperatorContextStore, append_directive

    supervisor = campaign.supervisor
    assert supervisor._plan_next_work() == PLAN_TERMINAL_IDLE
    first = append_directive(
        campaign.life, "Investigate the first instruction", expected_revision=0,
    )

    def plan(_planner, **_kwargs):
        append_directive(
            campaign.life, "Investigate the newer instruction", lifetime="once",
            expected_revision=first.revision,
        )
        return PlannerVerdict(
            project_done=False, reason="Handle the first instruction",
            new_tasks=[TaskSpec(title="First instruction", objective="Investigate the first instruction")],
        )

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", plan)
    assert supervisor._plan_next_work() is True
    store = OperatorContextStore(campaign.life)
    assert store.acknowledged_revision("planner") == first.revision < store.revision
    item = supervisor.memory.backlog.active()[0]
    supervisor.memory.backlog.update(item.id, status="done")
    campaign.certify(supervisor)
    calls = []

    def fail(_planner, **kwargs):
        calls.append(kwargs["runtime_change_summary"])
        return PlannerVerdict(project_done=False, reason="test failure", error="injected failure")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", fail)
    assert campaign.make()._plan_next_work() == PLAN_ERROR
    assert len(calls) == 1
    assert "Investigate the newer instruction" in calls[0]
    assert store.acknowledged_revision("planner") == first.revision


def test_rejected_completion_terminal_idle_does_not_consume_once_input(campaign, monkeypatch):
    from argus_skill.core.operator_context import OperatorContextStore, append_directive
    from argus_skill.life.supervisor._planning_cycle_helpers import (
        load_completion_rejection_circuit,
    )

    instruction = "Investigate this unexecuted one-shot instruction."
    append_directive(campaign.life, instruction, lifetime="once", expected_revision=0)
    monkeypatch.setattr(
        "argus_skill.core.external_completion_gate.external_completion_gate_issue",
        lambda _root: "external acceptance is not satisfied",
    )
    calls = []

    def plan(_planner, **kwargs):
        calls.append(kwargs["runtime_change_summary"])
        return PlannerVerdict(project_done=True, reason="Rejected completion", error="")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", plan)
    supervisor = campaign.supervisor
    assert supervisor._plan_next_work() == PLAN_RETRY
    assert supervisor._plan_next_work() == PLAN_RETRY
    assert supervisor._plan_next_work() == PLAN_TERMINAL_IDLE
    circuit = load_completion_rejection_circuit(supervisor._completion_rejection_circuit_file())
    assert circuit["paused"] is True
    assert circuit["consecutive_rejections"] == 3
    assert len(calls) == 3
    assert supervisor.memory.backlog.active() == []
    assert load_planner_verdict_outbox(campaign.life) is None
    store = OperatorContextStore(campaign.life)
    assert store.acknowledged_revision("planner") == 0
    assert instruction in [
        row.text for row in store.project("planner", consume_once=False).directives
    ]
    for _ in range(2):
        assert campaign.make()._plan_next_work() == PLAN_TERMINAL_IDLE
    assert len(calls) == 3
    assert store.acknowledged_revision("planner") == 0


@pytest.mark.parametrize("hold", ["feedback", "circuit"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("arrives_during_planning", [False, True])
def test_durable_input_wakes_holds_once_per_revision_after_restart(
    campaign, monkeypatch, hold, legacy, arrives_during_planning,
):
    from argus_skill.core.operator_context import OperatorContextStore, append_directive
    from argus_skill.life.supervisor._planning_cycle_helpers import (
        load_completion_rejection_circuit,
    )

    # Exercise each hold independently: the other stop-loss must not hide a
    # missed wake or a repeated reset in the hold being tested.
    if hold == "feedback":
        monkeypatch.setattr(
            "argus_skill.life.supervisor._planning_cycle_completion."
            "COMPLETION_REJECTION_CIRCUIT_THRESHOLD", 100,
        )
    else:
        monkeypatch.setattr(
            "argus_skill.life.supervisor._planning_cycle_intake."
            "MANAGER_FEEDBACK_REPLAN_LIMIT", 100,
        )
    monkeypatch.setattr(
        "argus_skill.core.external_completion_gate.external_completion_gate_issue",
        lambda _root: "external acceptance is not satisfied",
    )
    calls = []
    instruction = "Investigate the newer durable instruction after restart."
    newer = None

    def plan(_planner, **kwargs):
        nonlocal newer
        calls.append(kwargs["runtime_change_summary"])
        if arrives_during_planning and len(calls) == 3:
            newer = append_directive(
                campaign.life, instruction, lifetime="once", expected_revision=first.revision,
            )
        return PlannerVerdict(project_done=True, reason="Rejected completion", error="")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", plan)
    supervisor = campaign.supervisor
    first = append_directive(
        campaign.life, "Investigate the first instruction.", lifetime="once", expected_revision=0,
    )
    for _ in range(3):
        supervisor._plan_next_work()
    assert len(calls) == 3
    if not arrives_during_planning:
        assert supervisor._plan_next_work() == PLAN_TERMINAL_IDLE
        assert len(calls) == 3

    def read_hold(supervisor):
        if hold == "feedback":
            return supervisor._load_manager_planner_feedback()
        return load_completion_rejection_circuit(supervisor._completion_rejection_circuit_file())

    assert read_hold(supervisor)["operator_context_revision"] == first.revision
    if legacy:
        path = (
            supervisor._manager_planner_feedback_path() if hold == "feedback"
            else supervisor._completion_rejection_circuit_file()
        )
        payload = json.loads(path.read_text())
        payload.pop("operator_context_revision")
        path.write_text(json.dumps(payload))

    if newer is None:
        newer = append_directive(
            campaign.life, instruction, lifetime="once", expected_revision=first.revision,
        )
    # There is no inbox callback or carryover on this fresh supervisor.
    supervisor = campaign.make()
    assert not getattr(supervisor, "_operator_guidance_carryover", [])
    expected_new_calls = 3 if hold == "feedback" else 1
    for _ in range(expected_new_calls):
        supervisor._plan_next_work()
    assert len(calls) == 3 + expected_new_calls
    assert all(instruction in call for call in calls[3:])
    record = read_hold(supervisor)
    assert record["operator_context_revision"] == newer.revision
    count_key = "attempts" if hold == "feedback" else "consecutive_rejections"
    assert record[count_key] == (3 if hold == "feedback" else 4)
    for restart in (False, True, True):
        if restart:
            supervisor = campaign.make()
        assert supervisor._plan_next_work() == PLAN_TERMINAL_IDLE
        assert read_hold(supervisor)[count_key] == record[count_key]
    assert len(calls) == 3 + expected_new_calls
    store = OperatorContextStore(campaign.life)
    assert store.acknowledged_revision("planner") == 0
    assert instruction in [
        row.text for row in store.project("planner", consume_once=False).directives
    ]
    events = [
        json.loads(line) for line in (campaign.life / "events.jsonl").read_text().splitlines()
    ]
    expected_event = (
        "life.manager.feedback.exhausted" if hold == "feedback"
        else "life.planner.completion_circuit_holding"
    )
    assert any(event["type"] == expected_event for event in events)


def test_accepted_bounded_completion_acknowledges_once_input(campaign, monkeypatch):
    from argus_skill.core.operator_context import OperatorContextStore, append_directive

    instruction = "Accept this certified result as the bounded deliverable."
    append_directive(campaign.life, instruction, lifetime="once", expected_revision=0)
    calls = []

    def plan(_planner, **kwargs):
        calls.append(kwargs["runtime_change_summary"])
        return PlannerVerdict(project_done=True, reason="Accepted bounded completion")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", plan)
    assert campaign.make(open_ended=False)._plan_next_work() is False
    assert len(calls) == 1
    assert instruction in calls[0]
    store = OperatorContextStore(campaign.life)
    assert store.acknowledged_revision("planner") == store.revision
    assert store.project("planner", consume_once=False).directives == ()


def test_once_instruction_survives_failed_fresh_and_resumed_prompts(campaign, monkeypatch):
    from argus_skill.core.operator_context import OperatorContextStore, append_directive

    assert campaign.supervisor._plan_next_work() == PLAN_TERMINAL_IDLE
    instruction = "Investigate this one-shot instruction."
    append_directive(campaign.life, instruction, lifetime="once", expected_revision=0)
    prompts = []

    def plan(planner, **kwargs):
        config = kwargs.pop("config")
        builder = (
            planner._build_resumed_planner_prompt if prompts else planner._build_planner_prompt
        )
        prompts.append(builder(
            **kwargs, state_root=campaign.life, project_root=config.working_dir,
            open_ended=config.open_ended,
        ))
        return PlannerVerdict(project_done=False, reason="test failure", error="injected failure")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", plan)
    assert campaign.supervisor._plan_next_work() == PLAN_ERROR
    assert campaign.make()._plan_next_work() == PLAN_ERROR
    assert len(prompts) == 2
    assert all(instruction in prompt for prompt in prompts)
    store = OperatorContextStore(campaign.life)
    assert store.acknowledged_revision("planner") == 0
    assert [row.text for row in store.project("planner", consume_once=False).directives] == [
        instruction,
    ]


@pytest.mark.parametrize("change", ["backlog", "paused_operator", "artifact", "certificate"])
def test_meaningful_state_change_invalidates_idle(campaign, monkeypatch, change):
    assert campaign.supervisor._plan_next_work() == PLAN_TERMINAL_IDLE
    if change in {"backlog", "paused_operator"}:
        item = campaign.supervisor.memory.backlog.add(
            BacklogItem.new(title="new work", objective="new operator-authorized work"),
        )
        if change == "paused_operator":
            campaign.supervisor.memory.backlog.update(
                item.id, status="paused_operator", pending_question="Which direction?",
            )
    else:
        (campaign.project / "result.txt").write_text("new increment\n")
        if change == "certificate":
            campaign.certify(campaign.supervisor)
    restarted = campaign.make()
    calls = []

    def plan(_planner, **kwargs):
        calls.append(kwargs)
        return PlannerVerdict(project_done=False, error="test stops after genuine replanning")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", plan)
    result = restarted._plan_next_work()
    if change == "certificate":
        assert result == PLAN_TERMINAL_IDLE
        assert len(campaign.reports) == len(_reports(campaign.life)) == 2
        assert not calls
    else:
        assert result != PLAN_TERMINAL_IDLE
        assert len(calls) == 1
        assert len(_reports(campaign.life)) == 1


def test_bounded_certification_still_completes_project(campaign):
    supervisor = campaign.make(open_ended=False)
    assert supervisor._plan_next_work() is False
    record = load_planner_verdict_outbox(campaign.life)
    assert record["event"]["project_done"] is True
    assert record["event"]["completion_kind"] == "project_completed"
    assert record["outcome"] is False
    assert len(_reports(campaign.life)) == 1


def test_manager_completion_authority_still_required(campaign, monkeypatch):
    monkeypatch.setattr(campaign.supervisor, "_manager_final_stage_is_completed", lambda: False)
    assert campaign.supervisor._plan_next_work() == PLAN_RETRY
    assert not _reports(campaign.life)
    assert load_planner_verdict_outbox(campaign.life) is None


@pytest.mark.parametrize("gate", ["research", "external", "staged"])
def test_certified_increment_does_not_bypass_completion_gates(campaign, monkeypatch, gate):
    if gate == "research":
        target = (
            "argus_skill.life.supervisor._planning_cycle_completion."
            "_research_project_done_issue"
        )
    elif gate == "external":
        target = "argus_skill.core.external_completion_gate.external_completion_gate_issue"
    else:
        target = (
            "argus_skill.life.supervisor._planning_cycle_completion."
            "_staged_goal_completion_issue"
        )
    monkeypatch.setattr(target, lambda *_args, **_kwargs: "required evidence missing")
    assert campaign.supervisor._plan_next_work() == PLAN_RETRY
    assert not _reports(campaign.life)
    assert load_planner_verdict_outbox(campaign.life) is None


def test_changed_state_discards_pending_report_before_replanning(campaign, monkeypatch):
    campaign.manager.report_project_completion = lambda **_kwargs: ""
    assert campaign.supervisor._plan_next_work() == PLAN_RETRY
    (campaign.project / "result.txt").write_text("unreviewed change\n")
    calls = []

    def plan(_planner, **kwargs):
        calls.append(kwargs)
        return PlannerVerdict(project_done=False, error="test stops at replanning")

    monkeypatch.setattr("argus_skill.planner.Planner.plan_next", plan)
    assert campaign.make()._plan_next_work() != PLAN_TERMINAL_IDLE
    assert len(calls) == 1
    assert not _reports(campaign.life)
    assert load_planner_verdict_outbox(campaign.life) is None


@pytest.mark.parametrize("objective", ["keep improving", "继续改进"])
def test_increment_report_fallback_preserves_standing_campaign(objective):
    from argus_skill.manager._stage_ops import _StageDecisionMixin

    manager = SimpleNamespace(_build_stage_run_exec=lambda *_args: (None, None))
    text = _StageDecisionMixin.report_project_completion(
        manager,
        completion_context={"completion_scope": "certified_increment", "stages": {}},
        continuous_objective=objective,
        completion_reason="Historical manuscript had 12 pages and 99% accuracy.",
    )
    assert "Project completed." not in text
    assert "项目已完成。" not in text
    assert "standing objective remains active" in text or "长期目标仍然有效" in text
    assert "12 pages" not in text and "99%" not in text
    assert "not inspected" in text or "未检查" in text


def test_increment_prompt_does_not_close_campaign():
    from argus_skill.roles.prompts.manager import build_project_completion_report_prompt

    prompt = build_project_completion_report_prompt(
        objective="keep improving",
        completion_reason="current certification",
        completion_context={"completion_scope": "certified_increment"},
    )
    assert "not campaign completion" in prompt
    assert "Do not invent further work" in prompt
    assert "already-completed project" not in prompt


@pytest.mark.parametrize("route", ["project_memory", "configured_project"])
def test_project_report_routes_and_deduplicates_in_project_conversation(
    campaign, monkeypatch, route,
):
    from argus_skill.core.transcript import append_turn
    from argus_skill.life.memory import GlobalMemory, MemoryBundle, ProjectMemory

    supervisor = campaign.supervisor
    global_root = campaign.life
    project_memory = ProjectMemory.open("project-a", global_root=global_root)
    project_memory.init()
    if route == "project_memory":
        supervisor.memory = MemoryBundle(GlobalMemory.open(global_root), project_memory)
        # Project memory is authoritative, as for mission-result messages.
        supervisor.config.project_state_dir = global_root / "wrong-configured-root"
    else:
        supervisor.config.project_state_dir = project_memory.root
    # Identical content across projects must not share delivery/dedup state.
    monkeypatch.setattr(supervisor, "_manager_project_completion_context", lambda: {
        "stages": {}, "stage_history": [], "rollback_history": [],
    })
    assert supervisor._manager_publish_project_report("accepted") == "reported"
    message = _reports(project_memory.root)[0]
    assert not _reports(global_root)
    events = [
        json.loads(line)
        for line in (project_memory.root / "events.jsonl").read_text().splitlines()
    ]
    assert any(
        event["type"] == "ui.argus" and event["message_id"] == message["message_id"]
        for event in events
    )
    # An old globally misrouted message cannot suppress project publication.
    append_turn(global_root, "argus", "old global report", message_id=message["message_id"])
    (project_memory.root / "transcript.jsonl").unlink()
    assert supervisor._manager_publish_project_report("accepted") == "reported"
    assert len(_reports(project_memory.root)) == 1
    assert supervisor._manager_publish_project_report("accepted") == "reported"
    assert len(campaign.reports) == 2

    other = ProjectMemory.open("project-b", global_root=global_root)
    other.init()
    if route == "project_memory":
        supervisor.memory.project = other
    else:
        supervisor.config.project_state_dir = other.root
    assert supervisor._manager_publish_project_report("accepted") == "reported"
    assert len(_reports(other.root)) == 1
    assert _reports(other.root)[0]["message_id"] != message["message_id"]
    assert len(campaign.reports) == 3


def test_completion_context_separates_current_artifacts_from_historical_reviews(
    campaign, monkeypatch,
):
    import hashlib

    from argus_skill.core.pipeline_state import read_pipeline_state, write_pipeline_state
    from argus_skill.roles.prompts.manager import build_project_completion_report_prompt

    supervisor = campaign.supervisor
    state_root = campaign.life / "projects" / "project-a"
    supervisor.config.project_state_dir = state_root
    write_pipeline_state(state_root, read_pipeline_state(campaign.project))
    supervisor.config.artifact_root = state_root
    paper = campaign.project / "paper"
    paper.mkdir()
    manuscript = (
        r"\title{May Means More Than Possibility}"
        "\nCurrent balanced confirmation: structured SFT 32/32, orbit-minimax 30/32.\n"
    )
    (paper / "main.tex").write_bytes(manuscript.encode("utf-8"))
    (paper / "main.pdf").write_bytes(b"current PDF bytes")
    (paper / "REVIEW.md").write_text(
        "# Authoritative review\nJudgment: done\nCurrent PDF: 10 pages.\n",
    )
    legacy = {
        "review": {
            "project_root": str(state_root),
            "review_status": "done",
            "certified": True,
            "manager_reason": "Old manuscript: 12 pages, 99% accuracy.",
            "manuscript_snapshot": {"sha256": "old-manuscript"},
        },
        "paper": {
            "project_root": str(campaign.life / "projects" / "unrelated"),
            "manager_reason": "OTHER PROJECT SECRET",
        },
        "idea": {"manager_reason": "UNOWNED GLOBAL RECEIPT"},
    }
    (campaign.life / "stage-certificates.json").write_text(json.dumps({"stages": legacy}))
    (state_root / "stage-certificates.json").write_text(json.dumps({"stages": {
        "delivery": {"review_status": "done", "manager_reason": "project-local history"},
    }}))

    context = supervisor._manager_project_completion_context()
    assert context["project_workdir"] == str(campaign.project)
    assert context["project_state_dir"] == str(state_root)
    assert set(context["stage_reviews"]) == {"review", "delivery"}
    old_review = context["stage_reviews"]["review"]
    assert old_review["reporting_use"] == "historical_stage_progression_only"
    assert old_review["manuscript_freshness"]["status"] == "stale"
    artifacts = {row["path"]: row for row in context["current_artifact_evidence"]}
    assert artifacts["paper/main.tex"]["excerpt"] == manuscript
    assert artifacts["paper/main.tex"]["sha256"] == hashlib.sha256(manuscript.encode()).hexdigest()
    assert "10 pages" in artifacts["paper/REVIEW.md"]["excerpt"]
    assert "alone do not prove" in artifacts["paper/REVIEW.md"]["reporting_use"]
    # The fixture's old certificate cannot certify this changed manuscript.
    assert context["current_final_certification"]["certified"] is False
    prompt = build_project_completion_report_prompt(
        objective="keep improving", completion_reason="done", completion_context=context,
    )
    assert "12 pages" in prompt and "10 pages" in prompt
    assert "OTHER PROJECT SECRET" not in prompt and "UNOWNED GLOBAL RECEIPT" not in prompt
    assert "Never present their old manuscript titles, page counts, or numerical results" in prompt
    assert "pdfinfo" in prompt and "Do not scan raw transcripts" in prompt

    assert supervisor._manager_publish_project_report("accepted") == "reported"
    assert supervisor._manager_publish_project_report("accepted") == "reported"
    assert len(campaign.reports) == 1
    (paper / "main.tex").write_bytes(
        (manuscript + "A corrected current result.\n").encode("utf-8")
    )
    assert supervisor._manager_publish_project_report("accepted") == "reported"
    assert len(campaign.reports) == 2


def test_completion_evidence_is_bounded_and_confined(
    campaign, monkeypatch, require_symlink_support,
):
    from argus_skill.life import delivery

    paper = campaign.project / "paper"
    paper.mkdir()
    outside = campaign.life / "private.txt"
    outside.write_text("outside workspace evidence must not be imported")
    (paper / "REVIEW.md").symlink_to(outside)
    (paper / "main.tex").write_text("x" * 15000)
    monkeypatch.setattr(delivery, "_vertical_primary_targets", lambda *_args: [
        {"path": "../life/private.txt"}, {"path": ".env"}, {"path": "result.txt"},
    ])
    context = campaign.supervisor._manager_project_completion_context()
    evidence = {row["path"]: row for row in context["current_artifact_evidence"]}
    assert set(evidence) == {"paper/main.tex", "result.txt"}
    assert len(evidence["paper/main.tex"]["excerpt"]) == 12000
    assert evidence["paper/main.tex"]["excerpt_truncated"] is True


def test_failed_project_report_publish_cannot_use_global_dedup(campaign, monkeypatch):
    from argus_skill.core import operator_messages
    from argus_skill.core.transcript import append_turn

    supervisor = campaign.supervisor
    project_root = campaign.life / "projects" / "project-a"
    supervisor.config.project_state_dir = project_root

    def global_only(root, *, text, message_id, **_kwargs):
        assert root == project_root
        append_turn(campaign.life, "argus", text, message_id=message_id)
        return False

    with monkeypatch.context() as failure:
        failure.setattr(operator_messages, "publish_operator_message", global_only)
        assert supervisor._manager_publish_project_report("accepted") == PLAN_ERROR
    assert not _reports(project_root)
    assert supervisor._manager_publish_project_report("accepted") == "reported"
    assert len(_reports(project_root)) == 1
    assert supervisor._manager_publish_project_report("accepted") == "reported"
    assert len(campaign.reports) == 2
