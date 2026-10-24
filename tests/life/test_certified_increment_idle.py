"""Certified increments report once without closing a standing campaign."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.planner_verdict_outbox import load_planner_verdict_outbox
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.life.supervisor._constants import (
    PLAN_AWAITING,
    PLAN_ERROR,
    PLAN_RETRY,
    PLAN_TERMINAL_IDLE,
)
from argus.planner import PlannerVerdict, TaskSpec
from argus.skills.stage_machine import completion_contract_fingerprint
from argus.skills.vertical_select import persist_vertical
from argus.verticals._base import load_vertical_contract


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "result.txt").write_text("accepted increment\n")
    life = tmp_path / "life"
    persist_vertical(project, "software", workflow_mode="direct")
    path = project / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(path.read_text())
    version = load_vertical_contract("software", project_root=project).completion_contract_version
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
    manager = SimpleNamespace()

    def unexpected_plan(*_args, **_kwargs):
        pytest.fail("Certified unchanged state must not call Planner")

    monkeypatch.setattr("argus.planner.Planner.plan_next", unexpected_plan)

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
        life=life, manager=manager,
    )


def _reports(life: Path):
    """The project completion messages the team put in the chat, oldest first."""
    from tests.manager_inbox import team_reports

    return [report for report in team_reports(life) if report.get("manager_project_report")]


@pytest.mark.parametrize("wake", ["evidence", "operator"])
def test_rejected_wait_consumes_feedback_budget_across_restarts(
    campaign, monkeypatch, wake,
):
    from argus.core.operator_context import append_directive

    calls = []

    def plan(_planner, **_kwargs):
        calls.append(True)
        return PlannerVerdict(
            project_done=False, handoff="engineer",
            summary=f"Waiting instead of fixing the rejection, wording {len(calls)}",
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan)
    monkeypatch.setattr(LifeSupervisor, "_journal_has_final_certification", lambda _self: False)
    supervisor = campaign.supervisor
    assert supervisor._persist_manager_planner_feedback(
        stage="delivery", reason="Required repair is still missing",
        diagnostic="stage_completion_gate_failed",
    )
    original = supervisor._load_manager_planner_feedback()
    original["repair"] = {"source": "retained independent rejection"}
    assert supervisor._write_manager_planner_feedback(original)
    for expected_attempts in (2, 3):
        supervisor = campaign.make()
        assert supervisor._plan_next_work() == PLAN_ERROR
        current = supervisor._load_manager_planner_feedback()
        assert current["attempts"] == expected_attempts
        for key in (
            "reason", "diagnostic", "evidence_signature", "created_at",
            "objective_fingerprint", "operator_context_revision", "repair",
        ):
            assert current[key] == original[key]
    for _ in range(3):
        assert campaign.make()._plan_next_work() == PLAN_TERMINAL_IDLE
    assert len(calls) == 2
    assert not supervisor.memory.backlog.active()
    if wake == "evidence":
        (campaign.project / "result.txt").write_text("new independently supplied evidence\n")
    else:
        append_directive(
            campaign.life, "New facts are available; reassess the preserved blocker.",
            lifetime="once", expected_revision=0,
        )
    monkeypatch.setattr(
        "argus.planner.Planner.plan_next",
        lambda *_args, **_kwargs: (
            calls.append(True) or PlannerVerdict(project_done=True, summary="New decision")
        ),
    )
    campaign.make()._plan_next_work()
    assert len(calls) == 3


def test_rejected_wait_reports_feedback_write_failure(campaign, monkeypatch):
    from argus.life.supervisor._planning_cycle_helpers import _PlanCycleState

    supervisor = campaign.supervisor
    assert supervisor._persist_manager_planner_feedback(
        stage="delivery", reason="Repair required", diagnostic="stage_completion_gate_failed",
    )
    before = supervisor._manager_planner_feedback_path().read_bytes()
    events = []
    monkeypatch.setattr(supervisor, "_emit", events.append)
    monkeypatch.setattr(supervisor, "_emit_status", lambda text: events.append({"status": text}))
    monkeypatch.setattr(supervisor, "_write_manager_planner_feedback", lambda _value: False)
    state = _PlanCycleState(None)
    state.verdict = PlannerVerdict(project_done=False, handoff="engineer", summary="Wait")
    assert supervisor._pc_handle_handoff(state) == PLAN_ERROR
    assert supervisor._manager_planner_feedback_path().read_bytes() == before
    assert any("failed to persist" in event.get("status", "") for event in events)


def _legacy_handoff(campaign):
    from argus.core.operator_context import OperatorContextStore

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
@pytest.mark.parametrize("fresh_inbox", [False, True])
def test_legacy_certified_wait_reports_once_without_recertification(
    campaign, monkeypatch, reason, fresh_inbox,
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
            project_done=False, summary=reason, handoff="operator",
            operator_question="What should the campaign take on next?",
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan)
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
    assert len(calls) == len(_reports(campaign.life)) == 1






@pytest.mark.parametrize("reason", [
    "Wait for the experiment result.",
    "Waiting for new experiment results.",
    "Waiting for operator credentials.",
    "No actionable work after the backend failure.",
    "Waiting for new operator direction while the experiment is still running.",
])
def test_a_turn_without_new_work_is_not_blindly_acknowledged(campaign, monkeypatch, reason):
    from argus.core.operator_context import OperatorContextStore, append_directive

    instruction = "Run the new experiment even though the previous attempt failed."
    append_directive(campaign.life, instruction, lifetime="once", expected_revision=0)
    calls = []

    def plan(_planner, **kwargs):
        calls.append(kwargs["runtime_change_summary"])
        return PlannerVerdict(project_done=False, handoff="engineer", summary=reason)

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan)
    for _ in range(2):
        assert campaign.make()._plan_next_work() == PLAN_AWAITING
    store = OperatorContextStore(campaign.life)
    assert store.acknowledged_revision("planner") == 0
    assert len(calls) == 2 and all(instruction in call for call in calls)
    assert not _reports(campaign.life)


@pytest.mark.parametrize("blocker", [
    "not_invoked", "error", "revision", "tasks", "retire", "advance", "pending",
    "paused", "nonterminal_stage", "stale_cert", "not_operator",
])
def test_certified_handoff_requires_current_final_idle_state(campaign, monkeypatch, blocker):
    from dataclasses import replace

    from argus.life.supervisor._planning_cycle_helpers import _PlanCycleState

    supervisor = campaign.supervisor
    state = _PlanCycleState(None)
    state.planner_invoked = True
    state.verdict = PlannerVerdict(
        project_done=False, handoff="operator", summary="Waiting for new operator direction.",
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
            supervisor.memory.backlog.update(item.id, park_reason="operator_pause", pending_question="Approve?")
    elif blocker == "nonterminal_stage":
        path = campaign.project / ".argus" / "PIPELINE_STATE.json"
        pipeline = json.loads(path.read_text())
        pipeline["stages"]["delivery"]["status"] = "active"
        path.write_text(json.dumps(pipeline))
    elif blocker == "stale_cert":
        (campaign.project / "result.txt").write_text("new uncertified result")
    else:
        state.verdict = replace(state.verdict, handoff="engineer")
    assert supervisor._pc_is_certified_operator_wait(state) is False
    assert not _reports(campaign.life)


@pytest.mark.parametrize("gate", ["external", "research"])
def test_certified_wait_cannot_ack_past_completion_gate(campaign, monkeypatch, gate):
    _, store = _legacy_handoff(campaign)
    monkeypatch.setattr(
        "argus.planner.Planner.plan_next",
        lambda *_args, **_kw: PlannerVerdict(
            project_done=False, handoff="operator", summary="Waiting for new operator direction.",
        ),
    )
    if gate == "external":
        monkeypatch.setattr(
            "argus.core.external_completion_gate.external_completion_gate_issue",
            lambda _root: "external acceptance remains unsatisfied",
        )
    else:
        monkeypatch.setattr(
            "argus.life.supervisor._planning_cycle_completion._research_project_done_issue",
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
    assert len(_reports(campaign.life)) == 1
    record = load_planner_verdict_outbox(campaign.life)
    assert record["delivered"] is True
    assert record["outcome"] == PLAN_TERMINAL_IDLE
    assert record["event"]["project_done"] is False
    assert record["event"]["completion_kind"] == "certified_increment"
    assert "the longer-term goal continues" in _reports(campaign.life)[0]["text"]
    assert supervisor._manager_project_completion_context()["stages"]["delivery"]["status"] == "done"
    assert supervisor.config.continuous and supervisor.config.open_ended
    assert supervisor.memory.backlog.active() == []

    bookkeeping = campaign.project / ".autors" / "bookkeeping.json"
    bookkeeping.parent.mkdir()
    bookkeeping.write_text('{"updated_at": 42}')
    restarted = campaign.make()
    for _ in range(3):
        assert restarted._plan_next_work() == PLAN_TERMINAL_IDLE
    assert len(_reports(campaign.life)) == 1






@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("failure", ["error", "raise", "done", "commit"])
def test_unhandled_input_blocks_old_certificate_until_tasks_committed(
    campaign, monkeypatch, restart, failure,
):
    from argus.core.operator_context import OperatorContextStore

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
            summary=(
                "planner backend failed before producing output; will retry later"
                if failure == "error" else "injected rejected decision"
            ),
            new_tasks=(
                [TaskSpec(title="Investigate next increment", objective=instruction)]
                if failure == "commit" else []
            ),
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", fail)
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
            summary="Handle the new operator instruction",
            new_tasks=[TaskSpec(
                title="Investigate next increment", objective=instruction,
                acceptance_check="New direction is evaluated.",
            )],
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", succeed)
    monkeypatch.setattr(backlog_type, "add_many", original_commit)
    if failure == "done":
        # A completion the Host declined is the Planner's answer to these
        # inputs; it is asked again only when something it reads changes.
        assert supervisor._plan_next_work() != PLAN_TERMINAL_IDLE
        assert len(calls) == 2
        messages.append(instruction)
        supervisor.config.user_inbox = lambda: messages.pop(0) if messages else None
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
    from argus.core.operator_context import OperatorContextStore

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
            project_done=False,
            summary="planner backend failed before producing output; will retry later",
            error="injected Planner failure",
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan)
    for _ in range(2):
        assert supervisor._plan_next_work() == PLAN_ERROR
    assert len(calls) == 2
    assert all(instruction in str(call) for call in calls)
    assert len(_reports(campaign.life)) == 1
    assert load_planner_verdict_outbox(campaign.life) is None


def test_a_handled_turn_without_new_work_keeps_report_dedup(campaign, monkeypatch):
    from argus.core.operator_context import OperatorContextStore

    supervisor = campaign.supervisor
    assert supervisor._plan_next_work() == PLAN_TERMINAL_IDLE
    messages = ["Wait for the new result before deciding the next increment."]
    supervisor.config.user_inbox = lambda: messages.pop(0) if messages else None
    calls = []

    def plan(_planner, **kwargs):
        calls.append(kwargs)
        return PlannerVerdict(
            project_done=False, summary="Wait for the result", handoff="engineer",
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan)
    for _ in range(3):
        assert supervisor._plan_next_work() == PLAN_AWAITING
    assert len(calls) == 2
    store = OperatorContextStore(campaign.life)
    assert store.acknowledged_revision("planner") < store.revision
    restarted = campaign.make()
    for _ in range(3):
        assert restarted._plan_next_work() == PLAN_AWAITING
    assert len(calls) == 3
    assert len(_reports(campaign.life)) == 1
    assert load_planner_verdict_outbox(campaign.life) is None


def test_failed_routing_still_persists_unhandled_input(campaign, monkeypatch):
    from argus.core.operator_context import OperatorContextStore

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
            project_done=False, summary="test failure", error="injected Planner failure",
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan)
    assert supervisor._plan_next_work() == PLAN_ERROR
    assert campaign.make()._plan_next_work() == PLAN_ERROR
    assert len(calls) == 2
    assert all("routing outage" in call for call in calls)
    store = OperatorContextStore(campaign.life)
    assert store.revision > store.acknowledged_revision("planner")


def test_input_arriving_during_planning_is_not_acknowledged(campaign, monkeypatch):
    from argus.core.operator_context import OperatorContextStore, append_directive

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
            project_done=False, summary="Handle the first instruction",
            new_tasks=[TaskSpec(title="First instruction", objective="Investigate the first instruction")],
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan)
    assert supervisor._plan_next_work() is True
    store = OperatorContextStore(campaign.life)
    assert store.acknowledged_revision("planner") == first.revision < store.revision
    item = supervisor.memory.backlog.active()[0]
    supervisor.memory.backlog.update(item.id, status="done")
    campaign.certify(supervisor)
    calls = []

    def fail(_planner, **kwargs):
        calls.append(kwargs["runtime_change_summary"])
        return PlannerVerdict(project_done=False, summary="test failure", error="injected failure")

    monkeypatch.setattr("argus.planner.Planner.plan_next", fail)
    assert campaign.make()._plan_next_work() == PLAN_ERROR
    assert len(calls) == 1
    assert "Investigate the newer instruction" in calls[0]
    assert store.acknowledged_revision("planner") == first.revision


def test_rejected_completion_terminal_idle_does_not_consume_once_input(campaign, monkeypatch):
    from argus.core.operator_context import OperatorContextStore, append_directive
    from argus.life.supervisor._planning_cycle_helpers import (
        load_completion_rejection_circuit,
    )

    instruction = "Investigate this unexecuted one-shot instruction."
    append_directive(campaign.life, instruction, lifetime="once", expected_revision=0)
    monkeypatch.setattr(
        "argus.core.external_completion_gate.external_completion_gate_issue",
        lambda _root: "external acceptance is not satisfied",
    )
    calls = []

    def plan(_planner, **kwargs):
        calls.append(kwargs["runtime_change_summary"])
        return PlannerVerdict(project_done=True, summary="Rejected completion", error="")

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan)
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
    from argus.core.operator_context import OperatorContextStore, append_directive
    from argus.life.supervisor._planning_cycle_helpers import (
        load_completion_rejection_circuit,
    )

    # Exercise each hold independently: the other stop-loss must not hide a
    # missed wake or a repeated reset in the hold being tested.
    if hold == "feedback":
        monkeypatch.setattr(
            "argus.life.supervisor._planning_cycle_completion."
            "COMPLETION_REJECTION_CIRCUIT_THRESHOLD", 100,
        )
    else:
        monkeypatch.setattr(
            "argus.life.supervisor._planning_cycle_intake."
            "MANAGER_FEEDBACK_REPLAN_LIMIT", 100,
        )
    monkeypatch.setattr(
        "argus.core.external_completion_gate.external_completion_gate_issue",
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
        return PlannerVerdict(project_done=True, summary="Rejected completion", error="")

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan)
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
    from argus.core.operator_context import OperatorContextStore, append_directive

    instruction = "Accept this certified result as the bounded deliverable."
    append_directive(campaign.life, instruction, lifetime="once", expected_revision=0)
    calls = []

    def plan(_planner, **kwargs):
        calls.append(kwargs["runtime_change_summary"])
        return PlannerVerdict(project_done=True, summary="Accepted bounded completion")

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan)
    assert campaign.make(open_ended=False)._plan_next_work() is False
    assert len(calls) == 1
    assert instruction in calls[0]
    store = OperatorContextStore(campaign.life)
    assert store.acknowledged_revision("planner") == store.revision
    assert store.project("planner", consume_once=False).directives == ()


def test_once_instruction_survives_failed_fresh_and_resumed_prompts(campaign, monkeypatch):
    from argus.core.operator_context import OperatorContextStore, append_directive

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
        return PlannerVerdict(project_done=False, summary="test failure", error="injected failure")

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan)
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
                item.id, park_reason="operator_pause", pending_question="Which direction?",
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

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan)
    result = restarted._plan_next_work()
    if change == "certificate":
        assert result == PLAN_TERMINAL_IDLE
        assert len(_reports(campaign.life)) == 2
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
            "argus.life.supervisor._planning_cycle_completion."
            "_research_project_done_issue"
        )
    elif gate == "external":
        target = "argus.core.external_completion_gate.external_completion_gate_issue"
    else:
        target = (
            "argus.life.supervisor._planning_cycle_completion."
            "_staged_goal_completion_issue"
        )
    monkeypatch.setattr(target, lambda *_args, **_kwargs: "required evidence missing")
    assert campaign.supervisor._plan_next_work() == PLAN_RETRY
    assert not _reports(campaign.life)
    assert load_planner_verdict_outbox(campaign.life) is None








@pytest.mark.parametrize("route", ["project_memory", "configured_project"])
def test_project_report_routes_and_deduplicates_in_project_conversation(
    campaign, monkeypatch, route,
):
    from argus.life.memory import GlobalMemory, MemoryBundle, ProjectMemory

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
    supervisor._manager_publish_project_report("accepted")
    supervisor._manager_publish_project_report("accepted")
    [message] = _reports(project_memory.root)
    assert not _reports(global_root)

    other = ProjectMemory.open("project-b", global_root=global_root)
    other.init()
    if route == "project_memory":
        supervisor.memory.project = other
    else:
        supervisor.config.project_state_dir = other.root
    supervisor._manager_publish_project_report("accepted")
    assert len(_reports(other.root)) == 1


def test_completion_evidence_is_bounded_and_confined(
    campaign, monkeypatch, require_symlink_support,
):
    from argus.life import delivery

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


@pytest.mark.parametrize(
    ("decision", "settles"),
    [
        ("commit-declared", True),
        ("commit-undeclared", False),
        ("commit-unoffered", False),
        # A handoff starts no work and is not checkpointed, so nothing settles.
        ("handoff-declared", False),
        ("error-declared", False),
    ],
)
def test_idle_instruction_settles_only_when_applied_decision_declares_it(
    campaign, monkeypatch, decision, settles,
):
    from argus.core.operator_context import OperatorContextStore, append_directive

    supervisor = campaign.supervisor
    store = OperatorContextStore(campaign.life)
    instruction = "Evaluate the faster parser in the next increment."
    idle = append_directive(
        campaign.life, instruction, scope="mission", lifetime="bounded_increment",
        expected_revision=store.revision,
    )
    declared = {
        "commit-declared": (idle.revision,),
        "commit-undeclared": (),
        "commit-unoffered": (idle.revision + 100,),
        "handoff-declared": (idle.revision,),
        "error-declared": (idle.revision,),
    }[decision]
    calls = []

    def plan(_planner, **kwargs):
        calls.append(kwargs["runtime_change_summary"])
        if decision.startswith("handoff"):
            return PlannerVerdict(
                project_done=False, summary="The parser run goes on", handoff="engineer",
                settled_instructions=declared,
            )
        if decision.startswith("error"):
            return PlannerVerdict(
                project_done=False, summary="test failure", error="injected Planner failure",
                settled_instructions=declared,
            )
        return PlannerVerdict(
            project_done=False,
            summary="Handle the new operator instruction",
            new_tasks=[TaskSpec(
                title="Evaluate faster parser", objective=instruction,
                acceptance_check="Parser comparison is recorded.",
            )],
            settled_instructions=declared,
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan)
    supervisor._plan_next_work()

    assert f"revision {idle.revision}]: {instruction}" in calls[0]
    for item in supervisor.memory.backlog.active():
        supervisor.memory.backlog.update(item.id, status="done")
    visible = [
        record.revision
        for record in store.project("planner", consume_once=False).directives
    ]
    assert (idle.revision in visible) is not settles


@pytest.mark.parametrize(
    ("dropped_as", "settles"),
    [("filtered", False), ("duplicate of finished work", False), ("duplicate of live work", True)],
)
def test_idle_instruction_settles_only_when_every_task_is_carried(
    campaign, monkeypatch, dropped_as, settles,
):
    from argus.core.operator_context import OperatorContextStore, append_directive

    supervisor = campaign.supervisor
    build_pending = LifeSupervisor._pc_build_pending_items

    def drop_first_task(self, state):
        # Stand-in for a Host filter that keeps one proposed task out of the
        # backlog; only a duplicate of live work still carries the instruction.
        result = build_pending(self, state)
        dropped, _item = state.pending_items[0]
        state.pending_items = state.pending_items[1:]
        if dropped_as.startswith("duplicate"):
            state.skipped_duplicate_titles.append(dropped.title)
        if dropped_as == "duplicate of live work":
            state.skipped_live_duplicate_titles.append(dropped.title)
        return result

    monkeypatch.setattr(LifeSupervisor, "_pc_build_pending_items", drop_first_task)
    store = OperatorContextStore(campaign.life)
    idle = append_directive(
        campaign.life, "Evaluate the faster parser next.", scope="mission",
        lifetime="bounded_increment", expected_revision=store.revision,
    )

    def plan(_planner, **_kwargs):
        return PlannerVerdict(
            project_done=False,
            summary="Handle the new operator instruction",
            new_tasks=[
                TaskSpec(
                    title="Evaluate faster parser", objective="Compare both parsers.",
                    acceptance_check="Parser comparison is recorded.",
                ),
                TaskSpec(
                    title="Profile the loader", objective="Profile loader startup.",
                    acceptance_check="Loader profile is recorded.",
                ),
            ],
            settled_instructions=(idle.revision,),
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan)
    assert supervisor._plan_next_work() is True

    titles = [item.title for item in supervisor.memory.backlog.active()]
    assert titles == ["Profile the loader"]
    visible = [
        record.revision
        for record in store.project("planner", consume_once=False).directives
    ]
    assert (idle.revision in visible) is not settles


def test_certified_handoff_carries_planner_settlements(campaign, monkeypatch):
    from argus.core.operator_context import OperatorContextStore, append_directive

    supervisor = campaign.supervisor
    store = OperatorContextStore(campaign.life)
    idle = append_directive(
        campaign.life, "Report the certified result; do not start new work.",
        scope="mission", lifetime="bounded_increment", expected_revision=store.revision,
    )
    calls = []

    def plan(_planner, **kwargs):
        calls.append(kwargs["runtime_change_summary"])
        return PlannerVerdict(
            project_done=False,
            summary="The current final certification is accepted. Waiting for new explicit operator direction.",
            handoff="operator",
            operator_question="What should the campaign take on next?",
            settled_instructions=(idle.revision,),
        )

    monkeypatch.setattr("argus.planner.Planner.plan_next", plan)
    assert supervisor._plan_next_work() == PLAN_TERMINAL_IDLE

    assert f"revision {idle.revision}]" in calls[0]
    record = load_planner_verdict_outbox(campaign.life)
    assert record["event"]["completion_kind"] == "certified_increment"
    assert record["event"]["settled_operator_instructions"] == [idle.revision]
    visible = [
        record.revision
        for record in store.project("planner", consume_once=False).directives
    ]
    assert idle.revision not in visible
