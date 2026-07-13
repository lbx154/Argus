from __future__ import annotations

import json
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from argus_skill.core.pricing import usd_for_tokens
from argus_skill.life.memory import BacklogItem, Journal, JournalEntry, LifeMemory
from argus_skill.life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
    global_daily_spend,
)
from argus_skill.life.supervisor._config import reserve_global_daily_budget
from argus_skill.life.supervisor._constants import PLANNER_DEDUP_STATUSES
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
    failure = SimpleNamespace(
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
    assert _research_project_done_issue(tmp_path, [failure]) == (
        "missing_doctoral_reviewer_certification"
    )
    assert _research_project_done_issue(tmp_path, [bounded_breakthrough]) == (
        "missing_doctoral_reviewer_certification"
    )
    assert _research_project_done_issue(tmp_path, [failure, breakthrough]) == ""


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


def test_skill_miss_scientist_spend_is_journaled_and_budgeted(
    tmp_path,
) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    runner = _ScientistSpendRunner()
    sink = _RecordingSink(mem.root)
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(
            per_mission_cap_usd=1.0,
            daily_cap_usd=0.20,
            max_missions=2,
        ),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(memory=mem, runner=runner, sink=sink, config=cfg)

    first = mem.backlog.add(BacklogItem.new(
        title="skill miss",
        objective="force a skill miss and distill",
        max_cost_usd=0.01,
    ))
    second = mem.backlog.add(BacklogItem.new(
        title="next mission",
        objective="should be held by daily budget",
        max_cost_usd=0.01,
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

    blocked = sup.tick()

    assert blocked is not None
    assert blocked["status"] == "paused_budget"
    assert blocked["item_id"] == second.id
    assert "daily budget remaining" in blocked["reason"]


# ---- F3: effective per-mission cap + mid-mission budget_exhausted pause -------


def test_effective_per_mission_cap_clamps_to_smaller_of_item_and_global() -> None:
    """The enforced cap is the smaller of the operator's per-item budget and the
    global per-mission cap — one number for both preflight and the breaker."""
    budget = LifeBudget(per_mission_cap_usd=30.0, daily_cap_usd=180.0)
    cheap = BacklogItem.new(title="t", objective="o", max_cost_usd=10.0)
    pricey = BacklogItem.new(title="t", objective="o", max_cost_usd=50.0)
    assert budget.effective_per_mission_cap(cheap) == 10.0   # item budget binds
    assert budget.effective_per_mission_cap(pricey) == 30.0  # global cap binds


def _write_journal_rows(path, rows: list[dict[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_global_daily_spend_sums_across_projects_and_rollover(tmp_path) -> None:
    now = time.time()
    local = time.localtime(now)
    day_start = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
    root = tmp_path / "root"
    _write_journal_rows(
        root / "projects" / "p1" / "journal.jsonl",
        [
            {"ts": day_start - 1, "cost_usd": 99.0},
            {"ts": day_start + 10, "cost_usd": 1.25},
        ],
    )
    _write_journal_rows(
        root / "projects" / "p2" / "journal.jsonl.1",
        [
            {"ts": day_start + 20, "cost_usd": 2.5},
            {"ts": day_start - 20, "cost_usd": 7.0},
        ],
    )

    assert global_daily_spend(global_root=root, now=now) == pytest.approx(3.75)


def test_global_daily_spend_reads_canonical_events_and_all_rollovers(tmp_path) -> None:
    now = time.time()
    local = time.localtime(now)
    day_start = time.mktime(
        (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
    )
    root = tmp_path / "root"
    project = root / "projects" / "p1"
    project.mkdir(parents=True)
    for name, cost, offset in (
        ("events.jsonl.2", 1.25, 10),
        ("events.jsonl.1", 2.5, 20),
        ("events.jsonl", 3.75, 30),
    ):
        (project / name).write_text(
            json.dumps({
                "type": "life.mission.completed",
                "ts": day_start + offset,
                "cost_usd": cost,
                "success": True,
            }) + "\n",
            encoding="utf-8",
        )

    assert global_daily_spend(global_root=root, now=now) == pytest.approx(7.5)


def test_global_daily_spend_observes_new_cost_without_ttl_staleness(tmp_path) -> None:
    now = time.time()
    root = tmp_path / "root"
    path = root / "projects" / "p1" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({
            "type": "life.mission.completed",
            "ts": now,
            "cost_usd": 1.0,
            "success": True,
        }) + "\n",
        encoding="utf-8",
    )
    assert global_daily_spend(global_root=root, now=now) == pytest.approx(1.0)

    from argus_skill.core.usage import UsageLedger, UsageRecord

    UsageLedger(path.parent, migrate_legacy=False).append(
        UsageRecord(
            call_id="new-call",
            project_id="p1",
            mission_id=None,
            provider="legacy",
            model="",
            run_label="test.aggregate",
            started_at=now + 1,
            completed_at=now + 1,
            status="completed",
            input_tokens=None,
            cached_input_tokens=None,
            output_tokens=None,
            reasoning_output_tokens=None,
            premium_requests=None,
            pricing_status="priced",
            pricing_tier="test",
            cost_usd=2.0,
            cost_basis="legacy_aggregate",
            source="legacy.events",
        )
    )

    assert global_daily_spend(global_root=root, now=now) == pytest.approx(3.0)


def test_global_budget_reservations_serialize_concurrent_mission_envelopes(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    first, reason = reserve_global_daily_budget(
        cap_usd=10.0, amount_usd=6.0, global_root=tmp_path, owner="p1",
    )
    assert first is not None and reason == ""

    blocked, reason = reserve_global_daily_budget(
        cap_usd=10.0, amount_usd=6.0, global_root=tmp_path, owner="p2",
    )
    assert blocked is None
    assert "active reservations $6.00" in reason

    first.release()
    second, reason = reserve_global_daily_budget(
        cap_usd=10.0, amount_usd=6.0, global_root=tmp_path, owner="p2",
    )
    assert second is not None and reason == ""
    second.release()


def test_supervisor_releases_global_reservation_after_runner_failure(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingRunner:
        def execute(self, **kwargs: Any) -> _Outcome:
            raise RuntimeError("boom")

    root = tmp_path / "root"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    mem = LifeMemory.open(root / "projects" / "p1")
    mem.backlog.add(BacklogItem.new(title="task", objective="x", max_cost_usd=3.0))
    supervisor = LifeSupervisor(
        memory=mem,
        runner=_FailingRunner(),
        sink=_RecordingSink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(
                per_mission_cap_usd=3.0,
                daily_cap_usd=20.0,
                global_daily_cap_usd=10.0,
            ),
        ),
    )

    supervisor.tick()

    payload = json.loads((root / "budget-reservations.json").read_text())
    assert payload["reservations"] == []


def test_can_start_blocks_on_global_daily_cap_even_when_project_daily_allows(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    local = time.localtime(now)
    day_start = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
    root = tmp_path / "root"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    _write_journal_rows(
        root / "projects" / "p1" / "journal.jsonl",
        [{"ts": day_start + 1, "cost_usd": 6.0}],
    )
    _write_journal_rows(
        root / "projects" / "p2" / "journal.jsonl.1",
        [{"ts": day_start + 2, "cost_usd": 5.0}],
    )
    local_journal = Journal(root / "projects" / "p3" / "journal.jsonl")
    entry = JournalEntry.new(kind="mission_complete", title="local", summary="local", cost_usd=1.0)
    entry.ts = day_start + 3
    local_journal.append(entry)
    budget = LifeBudget(
        per_mission_cap_usd=3.0,
        daily_cap_usd=20.0,
        global_daily_cap_usd=12.0,
    )
    item = BacklogItem.new(title="t", objective="o", max_cost_usd=3.0)

    allowed, reason = budget.can_start(item=item, journal=local_journal, now=now)

    assert allowed is False
    assert "global daily spend" in reason
    assert "global daily cap" in reason


def test_global_daily_cap_zero_is_backward_compatible(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = Journal(tmp_path / "journal.jsonl")
    item = BacklogItem.new(title="t", objective="o", max_cost_usd=3.0)
    budget = LifeBudget(
        per_mission_cap_usd=3.0,
        daily_cap_usd=20.0,
        global_daily_cap_usd=0.0,
    )
    calls = {"n": 0}

    def fake_global_daily_spend(**kwargs: Any) -> float:
        calls["n"] += 1
        return 999.0

    monkeypatch.setattr(
        "argus_skill.life.supervisor._config.global_daily_spend",
        fake_global_daily_spend,
    )

    allowed, reason = budget.can_start(item=item, journal=journal, now=time.time())

    assert allowed is True
    assert reason == ""
    assert calls["n"] == 0


class _BudgetExhaustedRunner:
    """A runner whose mission trips the mid-mission cost breaker — it returns a
    ``budget_exhausted`` outcome (success=False), as ``LifeRuntime.execute`` does
    when ``SupervisedEngineer.run`` stops on the per-mission cap."""

    def execute(self, **kwargs: Any) -> _Outcome:
        # The supervisor must hand us a live per-mission budget probe.
        assert "per_mission_budget" in kwargs and kwargs["per_mission_budget"] is not None
        return _Outcome(success=False, status="budget_exhausted", final_message="paused")


def test_budget_exhausted_outcome_pauses_item_and_journals_budget_pause(
    tmp_path,
) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(mem.root)
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(per_mission_cap_usd=30.0, daily_cap_usd=180.0, max_missions=2),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(
        memory=mem, runner=_BudgetExhaustedRunner(), sink=sink, config=cfg,
    )

    item = mem.backlog.add(BacklogItem.new(
        title="long mission",
        objective="something that overruns the per-mission cap",
        max_cost_usd=30.0,
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
    assert pauses[0].extra["cap_usd"] == 30.0
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
        budget=LifeBudget(per_mission_cap_usd=30.0, daily_cap_usd=180.0, max_missions=2),
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


def test_non_blocked_failure_does_not_set_pending_question(tmp_path) -> None:
    """A plain error/crash (status != "blocked") must never populate
    pending_question — it is specifically for "the reviewer needs YOU to
    decide something", not every failure."""

    class _CrashRunner:
        def execute(self, **kwargs: Any) -> _Outcome:
            return _Outcome(success=False, status="error", final_message="boom")

    mem = LifeMemory.open(tmp_path / "life")
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(per_mission_cap_usd=30.0, daily_cap_usd=180.0, max_missions=2),
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
