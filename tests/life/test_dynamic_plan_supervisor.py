from __future__ import annotations

import json
from types import SimpleNamespace

from argus_skill.core.models import RunnerResult
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus_skill.life.supervisor._constants import (
    PLAN_ERROR,
    PLAN_RETRY,
    PLAN_TERMINAL_IDLE,
)
from argus_skill.planner import Planner


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def handle_event(self, event: dict) -> None:
        self.events.append(event)


class _ReplanRunner:
    def execute(self, **_kwargs):
        return SimpleNamespace(
            success=False,
            status="replan_requested",
            stop_reason="new evidence invalidated the current plan",
            stop_kind=None,
            recoverable=False,
            rounds=2,
            matched_skill_name="",
            skill_distilled=False,
            final_submission_certified=False,
            completion_evidence="",
            planner_report={
                "forward_progress": False,
                "headline": "route falsified",
                "blocker": "the remaining plan assumes a falsified mechanism",
                "recommended_next": "replace the remaining plan",
                "plan_signal": "reconsider",
                "plan_signal_reason": "new evidence invalidated the current plan",
                "evidence_files": [
                    {"path": "research/NO_GO.md", "why": "records the falsifier"},
                ],
            },
            checklist_feedback={},
            step_back=None,
            stage_transition={},
            operator_question="",
            research_result={},
        )


class _StageReplanRunner(_ReplanRunner):
    def execute(self, **_kwargs):
        outcome = super().execute(**_kwargs)
        outcome.stage_transition = {
            "action": "rollback",
            "target_stage": "benchmark",
            "reason": "Reviewer found an upstream benchmark defect",
        }
        outcome.planner_report["stage_reconciliation_required"] = True
        outcome.planner_report["earliest_broken_stage"] = "benchmark"
        return outcome


class _PlannerRunner:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_prompt = ""

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.last_prompt = prompt
        return RunnerResult(exit_code=0, agent_messages=[self.response])


class _RaisingPlannerRunner:
    def run_exec(self, **_kwargs):
        raise RuntimeError("planner backend down")


def _replacement_verdict() -> str:
    def task(key: str, deps: list[str], title: str) -> dict:
        return {
            "key": key,
            "deps": deps,
            "title": title,
            "impact_score": 5,
            "impact_area": "correctness",
            "evidence": "the prior route was falsified",
            "scope": "bounded",
            "objective": f"execute {title}",
        }

    return json.dumps(
        {
            "project_done": False,
            "reason": "replace the falsified route",
            "restart_daemon": False,
            "restart_reason": "",
            "waiting": False,
            "waiting_reason": "",
            "new_tasks": [
                task("discover", [], "discover replacement"),
                task("verify", ["discover"], "verify replacement"),
            ],
        }
    )


def _empty_replacement_verdict() -> str:
    return json.dumps(
        {
            "project_done": False,
            "reason": "no replacement produced",
            "restart_daemon": False,
            "restart_reason": "",
            "waiting": False,
            "waiting_reason": "",
            "new_tasks": [],
        }
    )


def _single_replacement_verdict() -> str:
    return json.dumps(
        {
            "project_done": False,
            "reason": "one replacement",
            "restart_daemon": False,
            "restart_reason": "",
            "waiting": False,
            "waiting_reason": "",
            "new_tasks": [
                {
                    "key": "discover",
                    "deps": [],
                    "title": "discover replacement",
                    "impact_score": 5,
                    "impact_area": "correctness",
                    "evidence": "the prior route was falsified",
                    "scope": "bounded",
                    "objective": "execute discover replacement",
                }
            ],
        }
    )


def _supervisor(tmp_path, *, runner=None, planner_response: str | None = None):
    memory = LifeMemory.open(tmp_path / "life")
    sink = _Sink()
    config = LifeSupervisorConfig(
        continuous=True,
        continuous_objective="keep solving the project",
        open_ended=False,
        full_paper_gate=False,
        budget=LifeBudget(max_missions=1),
        poll_interval_seconds=0.01,
    )
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner or _ReplanRunner(),
        sink=sink,
        config=config,
        planner_runner=(
            _PlannerRunner(planner_response)
            if planner_response is not None
            else None
        ),
    )
    return supervisor, sink


def _stage_reconciliation_waiting_verdict() -> str:
    return json.dumps(
        {
            "project_done": False,
            "reason": "current stage blocks prerequisite repair",
            "restart_daemon": False,
            "restart_reason": "",
            "waiting": True,
            "waiting_reason": "Manager must reconcile the stage",
            "waiting_contract": {
                "blocker_fingerprint": "stage-blocker",
                "recheck_condition": "Manager resolves the stage",
                "recheck_token": "stage-blocker-v1",
                "stage_reconciliation_required": True,
            },
            "new_tasks": [],
        }
    )


def _seed_plan(supervisor: LifeSupervisor):
    current = supervisor.memory.backlog.add(
        BacklogItem.new(
            item_id="current",
            title="current route",
            objective="work the current route",
            plan_id="plan-a",
            plan_version=1,
            node_key="current",
        )
    )
    stale = supervisor.memory.backlog.add(
        BacklogItem.new(
            item_id="stale",
            title="stale follow-up",
            objective="this should be replaced",
            plan_id="plan-a",
            plan_version=1,
            node_key="stale",
            deps=[current.id],
        )
    )
    return current, stale


def _revision_request() -> dict:
    return {
        "status": "replan_requested",
        "item_id": "current",
        "expected_plan_id": "plan-a",
        "expected_plan_version": 1,
        "planner_report": {
            "plan_signal": "reconsider",
            "plan_signal_reason": "new evidence invalidated the current plan",
            "evidence_files": [
                {"path": "research/NO_GO.md", "why": "records the falsifier"},
            ],
        },
    }


def _isolate_planning(supervisor: LifeSupervisor, monkeypatch) -> None:
    monkeypatch.setattr(
        supervisor, "_maybe_idle_after_unchanged_open_ended_done", lambda: None
    )
    monkeypatch.setattr(supervisor, "_resolve_vertical_once", lambda: None)
    monkeypatch.setattr(
        supervisor, "_wiki_collect_task_if_due_under_blocker", lambda: None
    )
    monkeypatch.setattr(supervisor, "_render_journal_for_planner", lambda: "")
    monkeypatch.setattr(supervisor, "_recent_no_progress_failures", lambda: {})
    monkeypatch.setattr(
        supervisor, "_recent_subagent_family_failures", lambda: {}
    )
    monkeypatch.setattr(
        supervisor, "_effective_full_paper_gate", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(supervisor, "_planner_runtime_with_idle_note", lambda: "")


def test_replan_outcome_requeues_current_item_instead_of_failing(tmp_path) -> None:
    supervisor, _sink = _supervisor(tmp_path)
    current, _stale = _seed_plan(supervisor)

    outcome = supervisor.tick()

    assert outcome is not None
    assert outcome["status"] == "replan_requested"
    assert outcome["expected_plan_id"] == "plan-a"
    assert outcome["expected_plan_version"] == 1
    assert outcome["planner_report"]["plan_signal"] == "reconsider"
    rows = {item.id: item for item in supervisor.memory.backlog.all()}
    assert rows[current.id].status == "pending"


def test_stage_reconciled_replan_retires_invalid_current_item(tmp_path) -> None:
    supervisor, _sink = _supervisor(tmp_path, runner=_StageReplanRunner())
    current, _stale = _seed_plan(supervisor)

    outcome = supervisor.tick()

    assert outcome is not None
    assert outcome["status"] == "replan_requested"
    rows = {item.id: item for item in supervisor.memory.backlog.all()}
    assert rows[current.id].status == "failed"
    assert "manager rollback to benchmark" in rows[current.id].last_error


def test_stage_reconciliation_hold_keeps_plan_and_calls_manager_once(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor, _sink = _supervisor(
        tmp_path,
        planner_response=_stage_reconciliation_waiting_verdict(),
    )
    current, stale = _seed_plan(supervisor)
    _isolate_planning(supervisor, monkeypatch)
    reconciliations: list[object] = []

    def reconcile(verdict):
        reconciliations.append(verdict)
        return "hold"

    monkeypatch.setattr(
        supervisor,
        "_reconcile_open_ended_planner_waiting",
        reconcile,
    )

    result = supervisor._plan_next_work(revision_request=_revision_request())

    assert result == "planner_retry"
    assert len(reconciliations) == 1
    rows = {item.id: item for item in supervisor.memory.backlog.all()}
    assert rows[current.id].status == "pending"
    assert rows[stale.id].status == "pending"
    assert not any(
        event["type"] == "life.plan.revision.rolled_back"
        for event in _sink.events
    )


def test_revision_wait_without_explicit_request_still_checks_reconciliation(
    tmp_path,
    monkeypatch,
) -> None:
    planner_response = _stage_reconciliation_waiting_verdict().replace(
        '"stage_reconciliation_required": true',
        '"stage_reconciliation_required": false',
    )
    supervisor, _sink = _supervisor(
        tmp_path,
        planner_response=planner_response,
    )
    _seed_plan(supervisor)
    _isolate_planning(supervisor, monkeypatch)
    reconciliations: list[object] = []

    def reconcile(verdict):
        reconciliations.append(verdict)
        return ""

    monkeypatch.setattr(
        supervisor,
        "_reconcile_open_ended_planner_waiting",
        reconcile,
    )

    supervisor._plan_next_work(revision_request=_revision_request())

    assert len(reconciliations) == 1


def test_planning_cycle_drains_operator_input_while_waiting(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor, sink = _supervisor(
        tmp_path,
        planner_response=_single_replacement_verdict(),
    )
    messages = iter(["GPU 1 is now allocated", None])
    supervisor.config.user_inbox = lambda: next(messages)
    deactivated: list[bool] = []
    monkeypatch.setattr(
        supervisor,
        "_deactivate_planner_waiting_contract",
        lambda: deactivated.append(True),
    )
    _isolate_planning(supervisor, monkeypatch)

    assert supervisor._plan_next_work() is True

    drained = [
        event for event in sink.events if event["type"] == "life.inbox.drained"
    ]
    assert drained[-1]["messages"] == ["GPU 1 is now allocated"]
    assert deactivated
    assert "LIVE OPERATOR GUIDANCE" in supervisor.planner_runner.last_prompt
    assert "GPU 1 is now allocated" in supervisor.planner_runner.last_prompt


def test_pending_operator_question_defers_planner_without_calling_it(tmp_path) -> None:
    supervisor, sink = _supervisor(
        tmp_path,
        planner_response=_single_replacement_verdict(),
    )
    blocked = BacklogItem.new(title="Need GPU", objective="Run the matrix")
    blocked.status = "failed"
    blocked.pending_question = "Which GPU is approved?"
    supervisor.memory.backlog.add(blocked)

    summary = supervisor.run()

    assert summary["stopped_by"] == "pending_operator_question"
    assert supervisor.planner_runner.last_prompt == ""
    deferred = [
        event
        for event in sink.events
        if event["type"] == "life.planner.deferred"
    ]
    assert deferred[-1]["reason"] == "waiting for operator answer"
    assert deferred[-1]["item_ids"] == [blocked.id]


def test_operator_inbox_resolves_single_pending_question_through_manager(
    tmp_path,
) -> None:
    supervisor, sink = _supervisor(
        tmp_path,
        planner_response=_single_replacement_verdict(),
    )
    blocked = BacklogItem.new(
        title="Need GPU",
        objective="Run the matrix",
        acceptance_check="Run only on GPU 0.",
        non_goals=["Do not use GPU 6."],
    )
    blocked.status = "failed"
    blocked.pending_question = "Which GPU is approved?"
    supervisor.memory.backlog.add(blocked)
    messages = iter(["GPU 6 is approved", None])
    supervisor.config.user_inbox = lambda: next(messages)

    def resolve(item, message):
        original, continuation = supervisor.memory.backlog.continue_with_operator_reply(
            item.id,
            message,
            manager_decision="Use physical GPU 6 exclusively.",
        )
        return {
            "resolved": original is not None and continuation is not None,
            "item": continuation.to_jsonable() if continuation else None,
        }

    supervisor.config.pending_question_resolver = resolve

    assert supervisor._resolve_pending_question_from_inbox([blocked]) is True
    rows = supervisor.memory.backlog.all()
    original = next(item for item in rows if item.id == blocked.id)
    continuation = next(item for item in rows if item.id != blocked.id)
    assert original.pending_question == ""
    assert continuation.status == "pending"
    assert "GPU 6 is approved" in continuation.objective
    assert "Use physical GPU 6 exclusively." in continuation.objective
    assert "Manager operator-answer decision" in continuation.acceptance_check
    assert "Run only on GPU 0." in continuation.acceptance_check
    assert continuation.non_goals == [
        (
            "Subject to the authoritative Manager operator-answer decision, "
            "preserve this inherited non-goal only where it does not conflict: "
            "Do not use GPU 6."
        )
    ]
    drained = [
        event for event in sink.events if event["type"] == "life.inbox.drained"
    ]
    assert drained[-1]["messages"] == ["GPU 6 is approved"]


def test_pending_question_resolution_drains_only_one_inbox_message(
    tmp_path,
) -> None:
    supervisor, _sink = _supervisor(
        tmp_path,
        planner_response=_single_replacement_verdict(),
    )
    blocked = BacklogItem.new(title="Need GPU", objective="Run the matrix")
    blocked.status = "failed"
    blocked.pending_question = "Which GPU is approved?"
    supervisor.memory.backlog.add(blocked)
    messages = iter(["GPU 6 is approved", "Keep batch size unchanged", None])
    supervisor.config.user_inbox = lambda: next(messages)
    supervisor.config.pending_question_resolver = lambda _item, _message: {
        "resolved": True
    }

    assert supervisor._resolve_pending_question_from_inbox([blocked]) is True
    assert next(messages) == "Keep batch size unchanged"


def test_non_answer_inbox_message_is_still_routed_through_manager(
    tmp_path,
) -> None:
    supervisor, _sink = _supervisor(
        tmp_path,
        planner_response=_single_replacement_verdict(),
    )
    blocked = BacklogItem.new(title="Need GPU", objective="Run the matrix")
    blocked.status = "failed"
    blocked.pending_question = "Which GPU is approved?"
    supervisor.memory.backlog.add(blocked)
    supervisor.config.user_inbox = lambda: "Show current status"
    routed: list[str] = []

    def route(_item, message):
        routed.append(message)
        return {"kind": "chat", "resolved": False}

    supervisor.config.pending_question_resolver = route

    assert supervisor._resolve_pending_question_from_inbox([blocked]) is False
    assert routed == ["Show current status"]
    current = next(
        item for item in supervisor.memory.backlog.all() if item.id == blocked.id
    )
    assert current.pending_question == "Which GPU is approved?"


def test_replan_planner_atomically_replaces_active_revision(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor, _sink = _supervisor(
        tmp_path,
        planner_response=_replacement_verdict(),
    )
    current, stale = _seed_plan(supervisor)
    _isolate_planning(supervisor, monkeypatch)

    assert supervisor._plan_next_work(revision_request=_revision_request()) is True

    rows = {item.id: item for item in supervisor.memory.backlog.all()}
    assert rows[current.id].status == "superseded"
    assert rows[stale.id].status == "superseded"
    replacement = [
        item for item in rows.values() if item.plan_id not in {"plan-a", ""}
    ]
    assert {item.title for item in replacement} == {
        "discover replacement",
        "verify replacement",
    }
    assert {item.plan_version for item in replacement} == {2}
    assert {item.node_key for item in replacement} == {"discover", "verify"}
    discover = next(item for item in replacement if item.node_key == "discover")
    verify = next(item for item in replacement if item.node_key == "verify")
    assert verify.deps == [discover.id]
    assert discover.context_refs == [
        {
            "kind": "artifact",
            "ref": "research/NO_GO.md",
            "why": "records the falsifier",
            "content_hash": "",
        }
    ]


def test_replan_planner_failure_keeps_old_plan_pending(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor, _sink = _supervisor(tmp_path, planner_response="not json")
    _seed_plan(supervisor)
    _isolate_planning(supervisor, monkeypatch)
    before = supervisor.memory.backlog.path.read_bytes()

    assert supervisor._plan_next_work(revision_request=_revision_request()) is not True
    assert supervisor.memory.backlog.path.read_bytes() == before


def test_replan_does_not_replay_an_unrelated_prior_planner_outbox(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor, _sink = _supervisor(
        tmp_path,
        planner_response=_replacement_verdict(),
    )
    _seed_plan(supervisor)
    _isolate_planning(supervisor, monkeypatch)

    def fail_if_replayed():
        raise AssertionError("revision must not replay a prior planner outcome")

    monkeypatch.setattr(
        supervisor,
        "_retry_pending_planner_verdict",
        fail_if_replayed,
    )

    assert supervisor._plan_next_work(revision_request=_revision_request()) is True


def test_empty_replacement_emits_correlated_rejection(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor, sink = _supervisor(
        tmp_path,
        planner_response=_empty_replacement_verdict(),
    )
    _seed_plan(supervisor)
    _isolate_planning(supervisor, monkeypatch)

    assert supervisor._plan_next_work(revision_request=_revision_request()) is not True

    rejected = [
        event
        for event in sink.events
        if event["type"] == "life.plan.revision.rejected"
    ]
    assert rejected[-1]["expected_plan_id"] == "plan-a"
    assert rejected[-1]["expected_plan_version"] == 1
    assert "no concrete tasks" in rejected[-1]["reason"]


def test_fully_filtered_replacement_emits_correlated_rejection(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor, sink = _supervisor(
        tmp_path,
        planner_response=_single_replacement_verdict(),
    )
    _seed_plan(supervisor)
    duplicate = supervisor.memory.backlog.add(
        BacklogItem.new(
            title="discover replacement",
            objective="execute discover replacement",
            tags=["scope:bounded"],
            acceptance_check="the prior route was falsified",
        )
    )
    supervisor.memory.backlog.mark_done(duplicate.id)
    _isolate_planning(supervisor, monkeypatch)

    assert supervisor._plan_next_work(revision_request=_revision_request()) is not True

    rejected = [
        event
        for event in sink.events
        if event["type"] == "life.plan.revision.rejected"
    ]
    assert rejected[-1]["expected_plan_id"] == "plan-a"
    assert rejected[-1]["expected_plan_version"] == 1
    assert rejected[-1]["reason"] == "all replacement tasks were filtered"


def test_fully_filtered_replacement_stops_after_bounded_retries(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor, sink = _supervisor(
        tmp_path,
        planner_response=_single_replacement_verdict(),
    )
    _seed_plan(supervisor)
    duplicate = supervisor.memory.backlog.add(
        BacklogItem.new(
            title="discover replacement",
            objective="execute discover replacement",
            tags=["scope:bounded"],
            acceptance_check="the prior route was falsified",
        )
    )
    supervisor.memory.backlog.mark_done(duplicate.id)
    _isolate_planning(supervisor, monkeypatch)

    assert supervisor._plan_next_work(revision_request=_revision_request()) == PLAN_RETRY
    assert supervisor._plan_next_work(revision_request=_revision_request()) == PLAN_RETRY
    assert (
        supervisor._plan_next_work(revision_request=_revision_request())
        == PLAN_TERMINAL_IDLE
    )

    current = next(
        item for item in supervisor.memory.backlog.all() if item.id == "current"
    )
    assert current.status == "failed"
    assert current.replan_rejections == 3
    assert "filtered replacement" in current.last_error
    rejected = [
        event
        for event in sink.events
        if event["type"] == "life.plan.revision.rejected"
    ]
    assert rejected[-1]["terminal"] is True
    assert rejected[-1]["attempts"] == 3


def test_replan_request_uses_existing_planner_gate(tmp_path, monkeypatch) -> None:
    supervisor, _sink = _supervisor(tmp_path)
    _seed_plan(supervisor)
    supervisor.config.planner_cycle_gate = lambda: "planner rate limited"
    calls: list[dict] = []
    monkeypatch.setattr(
        supervisor,
        "_plan_next_work",
        lambda **kwargs: calls.append(kwargs) or True,
    )

    summary = supervisor.run()

    assert calls == []
    assert summary["stopped_by"] == "planner rate limited"
    rows = {item.id: item for item in supervisor.memory.backlog.all()}
    assert rows["current"].status == "pending"


def test_backlog_metadata_discloses_context_references_not_payloads(tmp_path) -> None:
    supervisor, _sink = _supervisor(tmp_path)
    item = BacklogItem.new(
        title="replacement node",
        objective="continue from grounded evidence",
        plan_id="plan-b",
        plan_version=2,
        node_key="discover",
        acceptance_check="research/replacement.json records a reviewed decision",
        non_goals=["do not rerun the obsolete route"],
        context_refs=[
            {
                "kind": "artifact",
                "ref": "research/NO_GO.md",
                "why": "records why the prior route failed",
                "content_hash": "abc123",
            }
        ],
    )

    rendered = supervisor._render_backlog_item_metadata(item)

    assert "plan-b v2" in rendered
    assert "node_key: discover" in rendered
    assert "decisive_acceptance_check" in rendered
    assert "research/replacement.json records a reviewed decision" in rendered
    assert "do not rerun the obsolete route" in rendered
    assert "Open only as needed" in rendered
    assert "research/NO_GO.md" in rendered
    assert "records why the prior route failed" in rendered
    assert "abc123" in rendered


def test_unversioned_replan_request_preserves_unrelated_manual_items(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor, sink = _supervisor(
        tmp_path,
        planner_response=_single_replacement_verdict(),
    )
    supervisor.memory.backlog.add(
        BacklogItem.new(item_id="manual-a", title="A", objective="A")
    )
    supervisor.memory.backlog.add(
        BacklogItem.new(item_id="manual-b", title="B", objective="B")
    )
    _isolate_planning(supervisor, monkeypatch)
    request = _revision_request()
    request.update(
        item_id="manual-a",
        expected_plan_id="",
        expected_plan_version=0,
    )

    assert supervisor._plan_next_work(revision_request=request) is not True

    rows = {item.id: item for item in supervisor.memory.backlog.all()}
    assert rows["manual-a"].status == "pending"
    assert rows["manual-b"].status == "pending"
    assert not any(
        event["type"] == "life.plan.revision.proposed" for event in sink.events
    )
    rejected = next(
        event
        for event in sink.events
        if event["type"] == "life.plan.revision.rejected"
    )
    assert rejected["reason"] == "unversioned backlog items cannot be revised"


def test_revision_without_planner_resolves_proposal_as_rejected(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor, sink = _supervisor(tmp_path)
    _seed_plan(supervisor)
    _isolate_planning(supervisor, monkeypatch)

    assert supervisor._plan_next_work(revision_request=_revision_request()) == PLAN_ERROR
    assert supervisor._suggested_sleep_s > 0

    types = [event["type"] for event in sink.events]
    assert types.count("life.plan.revision.proposed") == 1
    assert types.count("life.plan.revision.rejected") == 1


def test_revision_planner_exception_resolves_proposal_as_rejected(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor, sink = _supervisor(tmp_path)
    supervisor.planner_runner = _RaisingPlannerRunner()
    _seed_plan(supervisor)
    _isolate_planning(supervisor, monkeypatch)

    assert supervisor._plan_next_work(revision_request=_revision_request()) is not True

    rejected = [
        event
        for event in sink.events
        if event["type"] == "life.plan.revision.rejected"
    ]
    assert "planner backend down" in rejected[-1]["reason"]


def test_revision_uncaught_planner_exception_resolves_proposal_as_rejected(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor, sink = _supervisor(
        tmp_path,
        planner_response=_single_replacement_verdict(),
    )
    _seed_plan(supervisor)
    _isolate_planning(supervisor, monkeypatch)

    def raise_uncaught(_self, **_kwargs):
        raise RuntimeError("uncaught planner fault")

    monkeypatch.setattr(Planner, "plan_next", raise_uncaught)

    assert supervisor._plan_next_work(revision_request=_revision_request()) == PLAN_ERROR
    assert supervisor._suggested_sleep_s > 0

    rejected = [
        event
        for event in sink.events
        if event["type"] == "life.plan.revision.rejected"
    ]
    assert rejected[-1]["reason"] == (
        "planner raised: RuntimeError: uncaught planner fault"
    )


def test_no_runner_and_planner_exception_use_exponential_backoff(
    tmp_path,
    monkeypatch,
) -> None:
    no_runner, _sink = _supervisor(tmp_path)
    _isolate_planning(no_runner, monkeypatch)
    assert no_runner._plan_next_work() == PLAN_ERROR
    first_sleep = no_runner._suggested_sleep_s
    assert first_sleep > 0

    failing, _sink = _supervisor(tmp_path / "failing")
    failing.planner_runner = _RaisingPlannerRunner()
    _isolate_planning(failing, monkeypatch)
    assert failing._plan_next_work() == PLAN_ERROR
    assert failing._suggested_sleep_s > 0


def test_repeated_manager_feedback_enters_terminal_idle_circuit_breaker(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor, sink = _supervisor(
        tmp_path,
        planner_response=_single_replacement_verdict(),
    )
    project = tmp_path / "project"
    project.mkdir()
    supervisor.config.project_worktree = project
    supervisor.config.artifact_root = project
    _isolate_planning(supervisor, monkeypatch)
    for _ in range(3):
        assert supervisor._persist_manager_planner_feedback(
            stage="scope",
            reason="reviewer evidence is still missing",
            diagnostic="planner_wait_advance_rejected",
        )

    assert supervisor._plan_next_work() == PLAN_TERMINAL_IDLE
    assert supervisor._suggested_sleep_s > 0
    assert any(
        event.get("type") == "life.manager.feedback.exhausted"
        for event in sink.events
    )


def test_new_project_evidence_wakes_manager_feedback_circuit_breaker(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor, _sink = _supervisor(
        tmp_path,
        planner_response=_single_replacement_verdict(),
    )
    project = tmp_path / "project"
    project.mkdir()
    supervisor.config.project_worktree = project
    supervisor.config.artifact_root = project
    _isolate_planning(supervisor, monkeypatch)
    for _ in range(3):
        assert supervisor._persist_manager_planner_feedback(
            stage="scope",
            reason="reviewer evidence is still missing",
            diagnostic="planner_wait_advance_rejected",
        )

    (project / "new-evidence.txt").write_text("certified\n", encoding="utf-8")

    assert supervisor._plan_next_work() is True
    assert supervisor._load_manager_planner_feedback() is None


def test_revision_restart_without_tasks_resolves_proposal_as_rejected(
    tmp_path,
    monkeypatch,
) -> None:
    response = json.dumps(
        {
            "project_done": False,
            "reason": "runtime upgrade required",
            "restart_daemon": True,
            "restart_reason": "load upgraded code",
            "waiting": False,
            "waiting_reason": "",
            "new_tasks": [],
        }
    )
    supervisor, sink = _supervisor(tmp_path, planner_response=response)
    _seed_plan(supervisor)
    _isolate_planning(supervisor, monkeypatch)
    monkeypatch.setattr(supervisor, "_handle_planner_restart", lambda _reason: False)

    assert supervisor._plan_next_work(revision_request=_revision_request()) is not True

    rejected = [
        event
        for event in sink.events
        if event["type"] == "life.plan.revision.rejected"
    ]
    assert rejected[-1]["reason"] == "replacement planner requested daemon restart"


def test_revision_bypasses_stale_terminal_idle_short_circuit(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor, _sink = _supervisor(
        tmp_path,
        planner_response=_replacement_verdict(),
    )
    _seed_plan(supervisor)
    _isolate_planning(supervisor, monkeypatch)
    monkeypatch.setattr(
        supervisor,
        "_maybe_idle_after_unchanged_open_ended_done",
        lambda: "planner_terminal_idle",
    )

    assert supervisor._plan_next_work(revision_request=_revision_request()) is True


def test_revision_bypasses_external_blocker_short_circuit(
    tmp_path,
    monkeypatch,
) -> None:
    supervisor, _sink = _supervisor(
        tmp_path,
        planner_response=_replacement_verdict(),
    )
    _seed_plan(supervisor)
    _isolate_planning(supervisor, monkeypatch)
    monkeypatch.setattr(
        supervisor,
        "_effective_full_paper_gate",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        supervisor,
        "_operator_external_blocker_short_circuit_decision",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        supervisor,
        "_record_planner_waiting",
        lambda *_args, **_kwargs: "awaiting_external",
    )

    assert supervisor._plan_next_work(revision_request=_revision_request()) is True
