from __future__ import annotations

import json
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from argus_skill.core.event_catalog import EventType
from argus_skill.core.pricing import usd_for_tokens
from argus_skill.core.transcript import read_turns
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
    global_daily_spend,
)
from argus_skill.life.supervisor._constants import PLANNER_DEDUP_STATUSES
from argus_skill.life.supervisor._mission_execution_settlement import (
    _final_submission_research_value_issue,
)
from argus_skill.life.supervisor._planning_cycle import _research_project_done_issue
from argus_skill.skills.vertical_select import persist_vertical


class _RecordingSink:
    """Captures events in memory. When ``life_dir`` is given it ALSO tees every
    event to ``<life_dir>/events.jsonl`` (verbosity="full") exactly like the
    daemon's ``JsonlEventSink`` — so a ``LifeMemory`` whose journal is an
    ``EventJournal`` (derived from that file) sees the events, matching how the
    real daemon persists them and mirroring the sibling life tests."""

    def __init__(self, life_dir: Any = None) -> None:
        self.events: list[dict[str, Any]] = []
        self._tee = None
        if life_dir is not None:
            from argus_skill.life.event_log import JsonlEventSink

            self._tee = JsonlEventSink(None, life_dir=life_dir, verbosity="full")

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        if self._tee is not None:
            self._tee.handle_event(event)


@dataclass
class _Outcome:
    success: bool = True
    status: str = "done"
    stop_reason: str = ""
    rounds: int = 1
    matched_skill_name: str = ""
    skill_distilled: bool = True
    had_follow_up: bool = False
    final_message: str = "done"
    operator_question: str = ""
    research_result: dict[str, Any] | None = None


class _ScientistSpendRunner:
    def execute(
        self,
        *,
        objective: str,
        sink: Any,
        prelude_context: str = "",
        scope: str = "",
        original_objective: str = "",
    ) -> _Outcome:
        sink.handle_event({
            "type": "skill.cost.completed",
            "agent_layer": "scientist",
            "matcher_model": "gpt-5.5",
            "distiller_model": "gpt-5.5-mini",
            "matcher": {
                "model": "gpt-5.5",
                "input_tokens": 200_000,
                "cached_input_tokens": 0,
                "output_tokens": 1_000,
            },
            "distiller": {
                "model": "gpt-5.5-mini",
                "input_tokens": 100_000,
                "cached_input_tokens": 50_000,
                "output_tokens": 2_000,
            },
            "usage_scope": "delta",
        })
        return _Outcome()


class _ResearchIncompleteRunner:
    def execute(self, **kwargs) -> _Outcome:
        return _Outcome(
            success=False,
            status="research_incomplete",
            stop_reason="doctoral target not reached",
        )


class _ResearchBreakthroughRunner:
    def execute(self, **kwargs) -> _Outcome:
        return _Outcome(
            research_result=_certified_research_result("verified_new_result"),
        )


class _MaintenanceRunner:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def execute(self, **kwargs) -> _Outcome:
        self.kwargs = kwargs
        outcome = _Outcome()
        outcome.final_review_status = "done"
        return outcome


def test_framework_maintenance_uses_private_worktree_and_review(
    tmp_path,
) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(memory.root)
    runner = _MaintenanceRunner()
    project = tmp_path / "project"
    project.mkdir()
    private = tmp_path / "private-framework"
    private.mkdir()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            project_worktree=project,
            artifact_root=project,
        ),
    )
    memory.backlog.add(BacklogItem.new(
        title="repair framework",
        objective="fix observed defect",
        tags=["framework_maintenance", "review:required", "scope:bounded"],
        execution_workdir=str(private),
    ))

    result = supervisor.tick()

    assert result is not None and result["status"] == "done"
    assert result["review_status"] == "done"
    assert runner.kwargs["working_dir_override"] == str(private)
    assert runner.kwargs["maintenance_mission"] is True
    assert runner.kwargs["require_independent_review"] is True


def _certified_research_result(result_class: str) -> dict[str, Any]:
    return {
        "result_class": result_class,
        "correctness_status": "verified",
        "novelty_status": (
            "verified_new"
            if result_class == "verified_new_result"
            else "not_applicable"
        ),
        "statement_fidelity_status": "verified",
        "significance_status": (
            "doctoral"
            if result_class == "verified_new_result"
            else "exploratory"
        ),
        "evidence": ["independently checked evidence"],
        "limitations": [],
    }


def test_doctoral_planner_done_requires_current_reviewer_certification(
    tmp_path,
) -> None:
    persist_vertical(
        tmp_path,
        "math",
        research_target_level="doctoral",
    )
    state = json.loads(
        (tmp_path / "research" / "PIPELINE_STATE.json").read_text()
    )
    target_set_at = state["research_target_set_at"]
    failed_mission = SimpleNamespace(
        kind="mission_failed",
        ts=target_set_at + 1,
        extra={"research_result": _certified_research_result("honest_final_report")},
    )
    reviewer_completed_negative = SimpleNamespace(
        kind="mission_complete",
        ts=target_set_at + 1,
        extra={"research_result": _certified_research_result("honest_final_report")},
    )
    breakthrough = SimpleNamespace(
        kind="mission_complete",
        ts=target_set_at + 2,
        extra={"research_result": _certified_research_result("verified_new_result")},
    )
    bounded_breakthrough = SimpleNamespace(
        kind="mission_complete",
        ts=target_set_at + 2,
        extra={
            "scope": "bounded",
            "research_result": _certified_research_result("verified_new_result"),
        },
    )

    assert _research_project_done_issue(tmp_path, []) == (
        "missing_doctoral_reviewer_certification"
    )
    assert _research_project_done_issue(tmp_path, [failed_mission]) == (
        "missing_doctoral_reviewer_certification"
    )
    assert _research_project_done_issue(tmp_path, [bounded_breakthrough]) == (
        "missing_doctoral_reviewer_certification"
    )
    assert _research_project_done_issue(
        tmp_path, [reviewer_completed_negative]
    ) == (
        "research_target_not_met:"
        "result_class_below_doctoral:honest_final_report"
    )
    assert _research_project_done_issue(
        tmp_path, [failed_mission, breakthrough]
    ) == ""


def test_research_project_done_fails_closed_without_success_bar(tmp_path) -> None:
    persist_vertical(tmp_path, "research")

    assert _research_project_done_issue(tmp_path, []) == (
        "missing_research_target_level"
    )


def test_planner_done_rejected_after_reviewer_no_go_without_target(tmp_path) -> None:
    no_go = SimpleNamespace(
        kind="mission_replan_requested",
        ts=1,
        extra={
            "scope": "bounded",
            "outcome": {"scientific_decision": "no_go"},
        },
    )

    assert _research_project_done_issue(tmp_path, [no_go]) == (
        "latest_scientific_decision_stop"
    )

    later_go = SimpleNamespace(
        kind="mission_complete",
        ts=2,
        extra={"outcome": {"scientific_decision": "continue"}},
    )
    assert _research_project_done_issue(tmp_path, [no_go, later_go]) == ""


def test_final_certificate_requires_research_value_not_honesty(tmp_path) -> None:
    persist_vertical(
        tmp_path,
        "math",
        research_target_level="exploratory",
    )

    assert _final_submission_research_value_issue(
        tmp_path,
        _certified_research_result("honest_final_report"),
    ) == "result_class_not_exploratory_terminal:honest_final_report"
    assert _final_submission_research_value_issue(
        tmp_path,
        _certified_research_result("counterexample"),
    ) == ""


def test_persisted_research_campaign_restores_target_via_manager(
    tmp_path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    persist_vertical(project, "research")
    state_path = project / "research" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["current_stage"] = "analysis"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(
        "argus_skill.manager.Manager._decide_research_target",
        lambda self, task, **kwargs: "publishable",
    )
    memory = LifeMemory.open(tmp_path / "life")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_ResearchBreakthroughRunner(),
        sink=_RecordingSink(memory.root),
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="develop a submission-quality paper",
            project_worktree=project,
            artifact_root=project,
        ),
        planner_runner=object(),
    )

    supervisor._resolve_vertical_once()

    restored = json.loads(state_path.read_text(encoding="utf-8"))
    assert restored["research_target_level"] == "publishable"
    assert restored["current_stage"] == "analysis"


def test_successful_research_result_is_journaled_for_planner_gate(tmp_path) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(mem.root)
    sup = LifeSupervisor(
        memory=mem,
        runner=_ResearchBreakthroughRunner(),
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=1),
            poll_interval_seconds=0.01,
        ),
    )
    mem.backlog.add(
        BacklogItem.new(title="breakthrough", objective="prove a new theorem")
    )

    result = sup.tick()

    assert result is not None and result["success"] is True
    event = next(
        event
        for event in sink.events
        if event.get("type") == "life.mission.completed"
    )
    assert event["research_result"]["result_class"] == "verified_new_result"


def test_budget_pause_is_published_once_in_operator_chat(tmp_path) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(mem.root)
    sup = LifeSupervisor(
        memory=mem,
        runner=_ResearchBreakthroughRunner(),
        sink=sink,
    )
    event = {
        "type": EventType.LIFE_BUDGET_PAUSE,
        "item_id": "task-1",
        "title": "Long experiment",
        "reason": "project daily budget exhausted",
    }

    assert sup._emit(event)
    assert sup._emit(event)

    (turn,) = read_turns(mem.root)
    assert "预算不足" in turn["text"]
    assert "Long experiment" in turn["text"]
    ui_events = [
        event
        for line in (mem.root / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if (event := json.loads(line)).get("type") == "ui.argus"
    ]
    assert len(ui_events) == 1


def test_research_incomplete_mission_is_paused_and_resumable(tmp_path) -> None:
    assert "paused" in PLANNER_DEDUP_STATUSES
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(mem.root)
    sup = LifeSupervisor(
        memory=mem,
        runner=_ResearchIncompleteRunner(),
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=1),
            poll_interval_seconds=0.01,
        ),
    )
    item = mem.backlog.add(
        BacklogItem.new(title="doctoral research", objective="prove a new theorem")
    )

    result = sup.tick()

    assert result is not None
    assert result["success"] is False
    assert result["status"] == "research_incomplete"
    paused = next(
        (candidate for candidate in mem.backlog.all() if candidate.id == item.id),
        None,
    )
    assert paused is not None
    assert paused.status == "research_incomplete"
    assert paused.last_error == "doctoral target not reached"
    event = next(
        event
        for event in sink.events
        if event.get("type") == "life.mission.completed"
    )
    assert event["success"] is False
    assert event["resumable"] is True

    resumed = mem.backlog.resume_paused(item.id)
    assert resumed is not None
    assert resumed.status == "pending"
    assert resumed.attempt == 2


def test_skill_miss_scientist_spend_is_journaled(
    tmp_path,
) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    runner = _ScientistSpendRunner()
    sink = _RecordingSink(mem.root)
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(
            global_daily_cap_usd=0.0,
            max_missions=2,
        ),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(memory=mem, runner=runner, sink=sink, config=cfg)

    first = mem.backlog.add(BacklogItem.new(
        title="skill miss",
        objective="force a skill miss and distill",
    ))

    result = sup.tick()

    expected_scientist_usd = usd_for_tokens(
        "gpt-5.5",
        200_000,
        0,
        1_000,
    ) + usd_for_tokens("gpt-5.5-mini", 100_000, 50_000, 2_000)
    assert result is not None
    assert result["success"] is True
    completed = [entry for entry in mem.journal.all() if entry.kind == "mission_complete"]
    assert len(completed) == 1
    entry = completed[0]
    assert entry.cost_usd == pytest.approx(expected_scientist_usd)
    assert entry.extra["scientist_cost_usd"] == pytest.approx(expected_scientist_usd)
    assert entry.extra["scientist_input_tokens"] == 300_000
    assert entry.extra["input_tokens"] == 300_000
    assert mem.backlog.all()[0].id == first.id
    assert mem.backlog.all()[0].status == "done"


def _append_usage(project, call_id: str, completed_at: float, cost_usd: float) -> None:
    from argus_skill.core.usage import UsageLedger, UsageRecord

    project.mkdir(parents=True, exist_ok=True)
    UsageLedger(project, migrate_legacy=False).append(
        UsageRecord(
            call_id=call_id,
            project_id=project.name,
            mission_id=None,
            provider="test",
            model="",
            run_label="test.aggregate",
            started_at=completed_at,
            completed_at=completed_at,
            status="completed",
            input_tokens=None,
            cached_input_tokens=None,
            output_tokens=None,
            reasoning_output_tokens=None,
            premium_requests=None,
            pricing_status="priced",
            pricing_tier="test",
            cost_usd=cost_usd,
            cost_basis="test",
        )
    )


def test_global_daily_spend_sums_across_projects_and_rollover(tmp_path) -> None:
    now = time.time()
    local = time.localtime(now)
    day_start = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)    )
    root = tmp_path / "root"
    _append_usage(root / "projects" / "p1", "old-p1", day_start - 1, 99.0)
    _append_usage(root / "projects" / "p1", "new-p1", day_start + 10, 1.25)
    _append_usage(root / "projects" / "p2", "new-p2", day_start + 20, 2.5)
    _append_usage(root / "projects" / "p2", "old-p2", day_start - 20, 7.0)

    assert global_daily_spend(global_root=root, now=now) == pytest.approx(3.75)


def test_global_daily_spend_reads_canonical_usage_across_projects(tmp_path) -> None:
    now = time.time()
    local = time.localtime(now)
    day_start = time.mktime(
        (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
    )
    root = tmp_path / "root"
    for project_id, call_id, cost, offset in (
        ("p1", "call-1", 1.25, 10),
        ("p2", "call-2", 2.5, 20),
        ("p3", "call-3", 3.75, 30),
    ):
        _append_usage(
            root / "projects" / project_id,
            call_id,
            day_start + offset,
            cost,
        )

    assert global_daily_spend(global_root=root, now=now) == pytest.approx(7.5)


def test_global_daily_spend_observes_new_cost_without_ttl_staleness(tmp_path) -> None:
    now = time.time()
    root = tmp_path / "root"
    project = root / "projects" / "p1"
    _append_usage(project, "first-call", now, 1.0)
    assert global_daily_spend(global_root=root, now=now) == pytest.approx(1.0)

    _append_usage(project, "new-call", now + 1, 2.0)

    assert global_daily_spend(global_root=root, now=now) == pytest.approx(3.0)


def test_can_start_blocks_on_global_daily_cap(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "argus_skill.life.supervisor._config.global_daily_spend",
        lambda **_kwargs: 12.0,
    )
    budget = LifeBudget(global_daily_cap_usd=12.0)

    allowed, reason = budget.can_start(global_root=tmp_path, now=time.time())

    assert allowed is False
    assert "global daily budget exhausted" in reason


def test_global_daily_cap_zero_is_backward_compatible(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = LifeBudget(global_daily_cap_usd=0.0)
    calls = {"n": 0}

    def fake_global_daily_spend(**kwargs: Any) -> float:
        calls["n"] += 1
        return 999.0

    monkeypatch.setattr(
        "argus_skill.life.supervisor._config.global_daily_spend",
        fake_global_daily_spend,
    )

    allowed, reason = budget.can_start(global_root=tmp_path, now=time.time())

    assert allowed is True
    assert reason == ""
    assert calls["n"] == 0


class _BudgetExhaustedRunner:
    """A runner whose provider call was denied by the host-global budget."""

    def execute(self, **kwargs: Any) -> _Outcome:
        return _Outcome(success=False, status="budget_exhausted", final_message="paused")


def test_budget_exhausted_outcome_pauses_item_and_journals_budget_pause(
    tmp_path,
) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(mem.root)
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=2),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(
        memory=mem, runner=_BudgetExhaustedRunner(), sink=sink, config=cfg,
    )

    item = mem.backlog.add(BacklogItem.new(
        title="long mission",
        objective="something that reaches the host-global cap",
    ))

    result = sup.tick()

    # Hard pause, NOT a completion — reviewer stays the sole done-ness authority.
    assert result is not None
    assert result["status"] == "paused_budget"
    assert result["item_id"] == item.id
    assert result.get("success") is not True
    # Item is recoverably paused until an explicit resume starts a fresh attempt.
    rows = {row.id: row for row in mem.backlog.all()}
    assert rows[item.id].status == "paused_budget"
    # Exactly one budget_pause journal entry; no mission_complete.
    pauses = [e for e in mem.journal.all() if e.kind == "budget_pause"]
    assert len(pauses) == 1
    assert pauses[0].extra["item_id"] == item.id
    assert not [e for e in mem.journal.all() if e.kind == "mission_complete"]
    # A life.mission.completed event marks it as a non-success budget_pause.
    completed = [e for e in sink.events if e.get("type") == "life.mission.completed"]
    assert completed and completed[-1]["status"] == "paused_budget"
    assert completed[-1]["success"] is False


class _BlockedQuestionRunner:
    """A runner whose mission stops with a reviewer 'blocked' verdict carrying
    an operator_question — the shape apps/_runtime.py's real execute()
    produces (``_Outcome.operator_question``, extracted from the final
    round's ReviewDecision when ``status == "blocked"``)."""

    def execute(self, **kwargs: Any) -> _Outcome:
        return _Outcome(
            success=False, status="blocked", final_message="needs a decision",
            operator_question="fp16 精度损失可以接受吗，还是必须 fp32？",
        )


def test_blocked_verdict_persists_operator_question_onto_backlog_item(
    tmp_path,
) -> None:
    """Point 11 of the 11-point CLI directive: the reviewer's operator_question
    must be durably visible, not just live in whatever cockpit process
    happened to be tailing events.jsonl at that instant. The supervisor is the
    ONE place every daemon mission outcome flows through,
    so this is where the question gets persisted onto the (now-terminal)
    backlog item for status views to read later."""
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink()
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=2),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(
        memory=mem, runner=_BlockedQuestionRunner(), sink=sink, config=cfg,
    )

    item = mem.backlog.add(BacklogItem.new(
        title="Optimize matmul kernel", objective="make it 2x faster",
    ))

    sup.tick()

    rows = {row.id: row for row in mem.backlog.all()}
    # Terminal like any other non-success outcome — "blocked" is not a
    # separate backlog status; pending_question is what distinguishes
    # "reviewer needs a decision" from a genuine crash/error.
    assert rows[item.id].status == "failed"
    assert rows[item.id].pending_question == "fp16 精度损失可以接受吗，还是必须 fp32？"
    pending_events = [
        event
        for event in sink.events
        if event["type"] == "life.operator_question.pending"
    ]
    assert pending_events[-1]["item_id"] == item.id
    assert pending_events[-1]["question"] == "fp16 精度损失可以接受吗，还是必须 fp32？"


def test_non_blocked_failure_does_not_set_pending_question(tmp_path) -> None:
    """A plain error/crash (status != "blocked") must never populate
    pending_question — it is specifically for "the reviewer needs YOU to
    decide something", not every failure."""

    class _CrashRunner:
        def execute(self, **kwargs: Any) -> _Outcome:
            return _Outcome(success=False, status="error", final_message="boom")

    mem = LifeMemory.open(tmp_path / "life")
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(global_daily_cap_usd=0.0, max_missions=2),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(
        memory=mem, runner=_CrashRunner(), sink=_RecordingSink(), config=cfg,
    )
    item = mem.backlog.add(BacklogItem.new(title="task", objective="x"))

    sup.tick()

    rows = {row.id: row for row in mem.backlog.all()}
    assert rows[item.id].status == "failed"
    assert rows[item.id].pending_question == ""
