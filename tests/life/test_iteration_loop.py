"""LifeSupervisor iteration-loop integration test.

Drives the supervisor against a fake ``MissionExecutor`` runner AND a
fake ``RunnerBackend`` critic. The critic returns canned verdicts so
we can deterministically assert:

* a successful mission with a ``continue`` verdict re-arms the same
  backlog item as ``pending`` with the polished objective;
* a successful mission with a ``stop`` verdict finalizes the item;
* the ``cycles_done`` and ``iteration_cost_usd`` counters advance;
* the ``mission_iterated`` journal kind fires on requeue and
  ``mission_complete`` on the final cycle;
* the cycle ceiling caps further iteration even when the critic still
  wants more.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from argus_skill.core.models import RunnerResult
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
)


@dataclass
class _FakeOutcome:
    success: bool = True
    status: str = "success"
    stop_reason: str = ""
    rounds: int = 1
    matched_skill_name: str | None = None
    skill_distilled: bool = False
    had_follow_up: bool = False
    final_message: str = "engineer says: implemented base64 helper, all tests green"


class _FakeMissionRunner:
    """Fake ``MissionExecutor``-shaped runner. Records each call."""

    def __init__(
        self,
        *,
        engineer_in: int = 1_000,
        engineer_out: int = 500,
        reviewer_in: int = 200,
        reviewer_out: int = 100,
    ) -> None:
        self.calls: list[str] = []
        self.engineer_in = engineer_in
        self.engineer_out = engineer_out
        self.reviewer_in = reviewer_in
        self.reviewer_out = reviewer_out

    def execute(
        self,
        *,
        objective: str,
        sink: Any,
        preload_injects: list[str] | None = None,
        prelude_context: str = "",
    ) -> _FakeOutcome:
        self.calls.append(objective)
        sink.handle_event({
            "type": "round.main.completed",
            "input_tokens": self.engineer_in,
            "output_tokens": self.engineer_out,
        })
        sink.handle_event({
            "type": "round.review.completed",
            "input_tokens": self.reviewer_in,
            "output_tokens": self.reviewer_out,
        })
        return _FakeOutcome()


class _ScriptedCriticBackend:
    """Fake ``RunnerBackend``: feeds canned JSON responses to the Critic."""

    def __init__(
        self,
        scripted: list[str],
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        self._queue = list(scripted)
        self.calls = 0
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def run_exec(self, *, prompt, resume_thread_id, options, run_label=""):
        self.calls += 1
        if self._queue:
            msg = self._queue.pop(0)
        else:
            msg = '{"stop": true, "reason": "queue empty", "improvements": []}'
        return RunnerResult(
            exit_code=0,
            agent_messages=[msg],
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
        )


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def handle_stream_line(self, stream: str, line: str) -> None:  # noqa: ARG002
        return

    def close(self) -> None:
        return


def _events_of(sink: _RecordingSink, *types: str) -> list[dict[str, Any]]:
    return [e for e in sink.events if e.get("type") in types]


def _journal_kinds(mem: LifeMemory) -> list[str]:
    return [e.kind for e in mem.journal.all()]


def _continue_payload(
    title: str,
    *,
    reason: str = "high-impact follow-up",
    rationale: str = "operator-visible value",
    acceptance: str = "pytest -q",
    impact_score: int = 4,
    impact_area: str = "correctness",
    evidence: str = "missing high-value verification",
) -> str:
    return json.dumps({
        "stop": False,
        "reason": reason,
        "improvements": [
            {
                "title": title,
                "rationale": rationale,
                "acceptance": acceptance,
                "impact_score": impact_score,
                "impact_area": impact_area,
                "evidence": evidence,
            }
        ],
    })


def _build_supervisor(
    *,
    mem: LifeMemory,
    sink: _RecordingSink,
    critic: _ScriptedCriticBackend | None,
    max_missions: int = 5,
) -> tuple[LifeSupervisor, _FakeMissionRunner]:
    runner = _FakeMissionRunner()
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(
            per_mission_cap_usd=10.0,
            daily_cap_usd=100.0,
            max_missions=max_missions,
        ),
        poll_interval_seconds=0.0,
    )
    sup = LifeSupervisor(
        memory=mem,
        runner=runner,
        sink=sink,
        config=cfg,
        critic_runner=critic,
    )
    return sup, runner


# ---------------------------------------------------------------------------


def test_iteration_loop_continues_then_stops(tmp_path: Path):
    mem = LifeMemory.open(tmp_path)
    mem.init()
    item = mem.backlog.add(BacklogItem.new(
        title="b64 helper",
        objective="add base64 helper",
        iterate=True,
        iteration_max_cycles=3,
        iteration_budget_usd=10.0,
    ))
    sink = _RecordingSink()
    critic = _ScriptedCriticBackend([
        # Cycle 1: critic wants one more polish pass.
        _continue_payload(
            "add urlsafe_b64",
            reason="missing url-safe variant",
            rationale="URL-safe encoding is a core operator need",
            acceptance="new function urlsafe_b64encode covered by pytest",
            evidence="operator-facing base64 helper lacks URL-safe variant",
        ),
        # Cycle 2: now done.
        '{"stop": true, "reason": "objective fully satisfied", '
        '"improvements": []}',
    ])
    sup, runner = _build_supervisor(mem=mem, sink=sink, critic=critic)
    summary = sup.run()

    # Two engineer missions ran (cycle 1 + cycle 2).
    assert len(runner.calls) == 2
    # First call uses original objective; second uses polished objective.
    assert "add base64 helper" in runner.calls[0]
    assert "DO NOT rewrite from scratch" in runner.calls[1]
    assert "urlsafe_b64" in runner.calls[1]

    # Backlog item ended ``done`` with the iteration counters advanced.
    final = next(it for it in mem.backlog.all() if it.id == item.id)
    assert final.status == "done"
    assert final.iteration_cycles_done == 1  # incremented once on requeue
    assert final.iteration_cost_usd > 0.0
    # original_objective preserved across cycles.
    assert final.original_objective == "add base64 helper"

    # Journal: each mission starts with mission_started, then the
    # requeue and final completion rows.
    kinds = _journal_kinds(mem)
    assert kinds == [
        "mission_started",
        "mission_iterated",
        "mission_started",
        "mission_complete",
    ]

    # Critic events emitted with both verdicts.
    critic_events = _events_of(sink, "life.iteration.critic")
    assert len(critic_events) == 2
    assert critic_events[0]["stop"] is False
    assert critic_events[0]["improvement_count"] == 1
    assert critic_events[1]["stop"] is True

    # Continued event fired once.
    cont = _events_of(sink, "life.iteration.continued")
    assert len(cont) == 1
    assert cont[0]["cycles_done"] == 1
    assert cont[0]["improvements"][0]["title"] == "add urlsafe_b64"

    # Summary reports both mission attempts.
    assert summary["missions_started"] == 2


def test_iteration_loop_respects_cycle_ceiling(tmp_path: Path):
    mem = LifeMemory.open(tmp_path)
    mem.init()
    mem.backlog.add(BacklogItem.new(
        title="bottomless",
        objective="ship a calculator",
        iterate=True,
        iteration_max_cycles=1,  # 1 requeue then ceiling
        iteration_budget_usd=100.0,
    ))
    sink = _RecordingSink()
    # Critic ALWAYS wants one more polish pass.
    forever_continue = (
        _continue_payload(
            f"polish-{i}",
            reason="more high-impact polish",
            impact_score=4,
            evidence="simulated high-impact follow-up",
        )
        for i in range(10)
    )
    critic = _ScriptedCriticBackend(list(forever_continue))
    sup, runner = _build_supervisor(mem=mem, sink=sink, critic=critic)
    sup.run()

    # cycles_max=2 means: cycle 1 → critic continues → cycle 2 →
    # cycle ceiling reached, stop without consulting critic again.
    assert len(runner.calls) == 2
    final = mem.backlog.all()[0]
    assert final.status == "done"
    assert final.iteration_cycles_done == 1
    kinds = _journal_kinds(mem)
    assert kinds == [
        "mission_started",
        "mission_iterated",
        "mission_started",
        "mission_complete",
    ]


def test_iteration_loop_rejects_low_value_polish(tmp_path: Path):
    mem = LifeMemory.open(tmp_path)
    mem.init()
    mem.backlog.add(BacklogItem.new(
        title="small cleanup",
        objective="ship useful behavior",
        iterate=True,
        iteration_max_cycles=3,
        iteration_budget_usd=10.0,
    ))
    sink = _RecordingSink()
    critic = _ScriptedCriticBackend([
        _continue_payload(
            "rename helper for clarity",
            reason="minor cleanup",
            impact_score=2,
            evidence="would be cleaner",
        )
    ])
    sup, runner = _build_supervisor(mem=mem, sink=sink, critic=critic)
    sup.run()

    assert len(runner.calls) == 1
    final = mem.backlog.all()[0]
    assert final.status == "done"
    assert final.iteration_cycles_done == 0
    critic_events = _events_of(sink, "life.iteration.critic")
    assert critic_events[0]["stop"] is True
    assert "impact gate" in critic_events[0]["reason"]


def test_iteration_disabled_skips_critic_entirely(tmp_path: Path):
    mem = LifeMemory.open(tmp_path)
    mem.init()
    mem.backlog.add(BacklogItem.new(
        title="one-shot",
        objective="x",
        iterate=False,  # /add --once
    ))
    sink = _RecordingSink()
    critic = _ScriptedCriticBackend([
        _continue_payload("polish", reason="wants more")
    ])
    sup, runner = _build_supervisor(mem=mem, sink=sink, critic=critic)
    sup.run()

    assert len(runner.calls) == 1
    # Critic NOT consulted because iterate=False.
    assert critic.calls == 0
    assert _events_of(sink, "life.iteration.critic") == []
    assert mem.backlog.all()[0].status == "done"


def test_iteration_budget_counts_critic_tokens(tmp_path: Path) -> None:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    mem.backlog.add(BacklogItem.new(
        title="budgeted",
        objective="ship a tiny helper",
        iterate=True,
        iteration_max_cycles=3,
        iteration_budget_usd=10.0,
    ))
    sink = _RecordingSink()
    critic = _ScriptedCriticBackend(
        [
            _continue_payload(
                "tighten docs",
                reason="needs one more polish pass",
                rationale="operator-visible detail",
                acceptance=(
                    "pytest -q "
                    "tests/life/test_iteration_loop.py::"
                    "test_iteration_budget_counts_critic_tokens"
                ),
                evidence="documented behavior is part of the user-facing contract",
            ),
            '{"stop": true, "reason": "objective fully satisfied", "improvements": []}',
        ],
        input_tokens=1_000,
        output_tokens=500,
    )
    sup, runner = _build_supervisor(mem=mem, sink=sink, critic=critic)
    runner.engineer_in = 0
    runner.engineer_out = 0
    runner.reviewer_in = 0
    runner.reviewer_out = 0

    summary = sup.run()

    critic_cost = (1_000 * 1.25 + 500 * 10.0) / 1_000_000
    entries = mem.journal.all()
    assert len(entries) == 4
    assert [entry.kind for entry in entries] == [
        "mission_started",
        "mission_iterated",
        "mission_started",
        "mission_complete",
    ]
    assert entries[0].cost_usd == 0.0
    assert entries[1].cost_usd == pytest.approx(critic_cost)
    assert entries[2].cost_usd == 0.0
    assert entries[3].cost_usd == pytest.approx(critic_cost)
    assert mem.backlog.all()[0].iteration_cost_usd == pytest.approx(critic_cost)
    assert LifeBudget(daily_cap_usd=1.0).remaining_today(mem.journal) == pytest.approx(
        1.0 - (critic_cost * 2)
    )
    assert summary["missions_started"] == 2
    assert summary["stopped_by"] in {"backlog_empty", "__silent_stop__"}


def test_iteration_no_critic_runner_finalizes_immediately(tmp_path: Path):
    mem = LifeMemory.open(tmp_path)
    mem.init()
    mem.backlog.add(BacklogItem.new(
        title="memory-mode",
        objective="x",
        iterate=True,
    ))
    sink = _RecordingSink()
    sup, runner = _build_supervisor(mem=mem, sink=sink, critic=None)
    sup.run()
    # Without a critic_runner the supervisor does not iterate; the item
    # goes ``done`` after the first successful cycle.
    assert len(runner.calls) == 1
    assert mem.backlog.all()[0].status == "done"


def test_critic_unparseable_output_safely_finalizes(tmp_path: Path):
    mem = LifeMemory.open(tmp_path)
    mem.init()
    mem.backlog.add(BacklogItem.new(
        title="bad-critic",
        objective="x",
        iterate=True,
    ))
    sink = _RecordingSink()
    critic = _ScriptedCriticBackend(["not json at all"])
    sup, runner = _build_supervisor(mem=mem, sink=sink, critic=critic)
    sup.run()
    # Critic ran, returned garbage; supervisor must finalize as done
    # rather than loop forever or crash.
    assert len(runner.calls) == 1
    assert critic.calls == 1
    assert mem.backlog.all()[0].status == "done"
    assert _journal_kinds(mem) == ["mission_started", "mission_complete"]
