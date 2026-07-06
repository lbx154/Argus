from __future__ import annotations

import json
import time
from dataclasses import dataclass
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


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


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


def test_skill_miss_scientist_spend_is_journaled_and_budgeted(
    tmp_path,
) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    runner = _ScientistSpendRunner()
    sink = _RecordingSink()
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
    assert blocked["status"] == "budget_pause"
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


def test_budget_exhausted_outcome_leaves_item_pending_and_journals_budget_pause(
    tmp_path,
) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink()
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
    assert result["status"] == "budget_pause"
    assert result["item_id"] == item.id
    assert result.get("success") is not True
    # Item rolled back to pending so the next tick resumes it from checkpoint.
    rows = {row.id: row for row in mem.backlog.all()}
    assert rows[item.id].status == "pending"
    # Exactly one budget_pause journal entry; no mission_complete.
    pauses = [e for e in mem.journal.all() if e.kind == "budget_pause"]
    assert len(pauses) == 1
    assert pauses[0].extra["item_id"] == item.id
    assert pauses[0].extra["cap_usd"] == 30.0
    assert not [e for e in mem.journal.all() if e.kind == "mission_complete"]
    # A life.mission.completed event marks it as a non-success budget_pause.
    completed = [e for e in sink.events if e.get("type") == "life.mission.completed"]
    assert completed and completed[-1]["status"] == "budget_pause"
    assert completed[-1]["success"] is False
