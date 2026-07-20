"""Regression tests for planner verdict outbox replay correctness.

Finding 1: PLANNED verdict with all tasks filtered persists
resume_outcome=False; on restart replay this is interpreted as project
completion, disabling the continuous objective.

Finding 2: A corrupt/unreadable outbox emits an error but does NOT retire
the invalid record, creating permanent no-progress where every cycle
bypasses Planner.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from argus_skill.core.models import RunnerResult
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.planner_verdict_outbox import (
    OUTBOX_FILE,
    load_planner_verdict_outbox,
)
from argus_skill.life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
)
from argus_skill.life.supervisor._constants import PLAN_RETRY

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FailingSink:
    """Sink that rejects event delivery (simulates first-delivery failure)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> bool:
        self.events.append(event)
        # Reject planner verdict delivery to simulate transport failure.
        if event.get("type") == "life.planner.verdict":
            return False
        return True


class _RecordingSink:
    """Sink that accepts all events."""

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


class _NeverCalledRunner:
    """Runner that should never be invoked."""

    called: bool = False

    def execute(self, **kwargs: Any) -> SimpleNamespace:
        self.called = True
        return SimpleNamespace(
            success=True, status="done", stop_reason="", rounds=1,
            matched_skill_name="", skill_distilled=True,
            had_follow_up=False, final_message="done",
            operator_question="", research_result=None,
        )


# ---------------------------------------------------------------------------
# Test 1: All-filtered PLANNED verdict + first-delivery failure + restart
# must replay PLAN_RETRY, not False.
# ---------------------------------------------------------------------------


def test_all_filtered_planned_verdict_replays_plan_retry_not_false(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Reproduce: Planner PLANNED verdict with 0 enqueued tasks (all filtered as
    duplicates) has its first delivery fail. On restart, the persisted
    resume_outcome must be PLAN_RETRY — not False — to avoid misinterpreting the
    replay as project completion and disabling the continuous objective.

    This test exercises the real ``_plan_next_work`` production path so the
    failing assertion depends on line ``_planning_cycle.py:758``; a hardcoded
    expression in the test body would pass regardless of whether that line was
    reverted to ``resume_outcome=bool(added_titles)``.
    """
    # Three tasks the planner will emit.  All three are pre-seeded in the
    # backlog (status="pending" ∈ PLANNER_DEDUP_STATUSES) so every task is
    # filtered as a duplicate and ``added_titles`` stays empty.
    tasks = [
        ("run baseline experiment",
         "run the baseline and record results in results.csv"),
        ("run ablation study",
         "run ablations over key hyperparams; write ablation.csv"),
        ("analyse results",
         "read results.csv and ablation.csv; write analysis/summary.md"),
    ]

    mem = LifeMemory.open(tmp_path / "life")
    for title, objective in tasks:
        mem.backlog.add(BacklogItem.new(title=title, objective=objective))

    verdict_json = json.dumps({
        "project_done": False,
        "reason": "continue improving the pipeline",
        "restart_daemon": False,
        "restart_reason": "",
        "waiting": False,
        "waiting_reason": "",
        "new_tasks": [
            {
                "title": title,
                "objective": objective,
                "impact_score": 5,
                "impact_area": "reliability",
                "evidence": "still needed",
                "scope": "bounded",
                "key": "",
                "deps": [],
            }
            for title, objective in tasks
        ],
    })

    class _CountingPlannerRunner:
        def __init__(self) -> None:
            self.call_count = 0

        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            self.call_count += 1
            return RunnerResult(
                exit_code=0,
                agent_messages=[verdict_json],
                stdout_lines=[],
                stderr_lines=[],
                thread_id=None,
                fatal_error=None,
                input_tokens=0,
                cached_input_tokens=0,
                output_tokens=0,
            )

    config = LifeSupervisorConfig(
        continuous=True,
        continuous_objective="keep improving the project",
        paper_mission=False,
        full_paper_gate=False,
        open_ended=False,
    )
    failing_sink = _FailingSink()
    planner_runner_1 = _CountingPlannerRunner()
    sup = LifeSupervisor(
        memory=mem,
        runner=_NeverCalledRunner(),
        sink=failing_sink,
        config=config,
        planner_runner=planner_runner_1,
    )

    # Stub pre-loop gates (same pattern as test_planner_dag_enqueue.py).
    monkeypatch.setattr(sup, "_maybe_idle_after_unchanged_open_ended_done", lambda: None)
    monkeypatch.setattr(sup, "_resolve_vertical_once", lambda: None)
    monkeypatch.setattr(sup, "_wiki_collect_task_if_due_under_blocker", lambda: None)
    monkeypatch.setattr(sup, "_render_journal_for_planner", lambda: "")
    monkeypatch.setattr(sup, "_recent_no_progress_failures", lambda: {})
    monkeypatch.setattr(sup, "_recent_subagent_family_failures", lambda: {})
    monkeypatch.setattr(sup, "_effective_full_paper_gate", lambda *_a, **_k: False)
    monkeypatch.setattr(sup, "_planner_runtime_with_idle_note", lambda: "")

    # ── first pass ──────────────────────────────────────────────────────────
    # Planner runs, all 3 tasks deduplicated, verdict delivery fails → PLAN_RETRY.
    result = sup._plan_next_work()

    assert planner_runner_1.call_count == 1, (
        "Planner must be called exactly once on the first _plan_next_work"
    )
    assert result == PLAN_RETRY, (
        f"_plan_next_work returned {result!r}; expected PLAN_RETRY for "
        "all-filtered verdict with delivery failure"
    )

    # Outbox must persist PLAN_RETRY, NOT False — False would signal project
    # completion on the next restart and disable the continuous objective.
    outbox = load_planner_verdict_outbox(mem.root)
    assert outbox is not None, "Outbox must hold a pending record after delivery failure"
    assert outbox["outcome"] == PLAN_RETRY, (
        f"Outbox stored outcome={outbox['outcome']!r}; must be PLAN_RETRY. "
        "This assertion catches the bool(added_titles) regression at "
        "_planning_cycle.py:758."
    )

    # ── restart (replay) ────────────────────────────────────────────────────
    # New supervisor on the same memory, working sink.  The outbox replay must
    # return PLAN_RETRY without invoking the Planner again.
    class _PlannerThatMustNotBeCalled:
        def run_exec(self, **_kwargs):  # pragma: no cover — proves no call
            raise AssertionError("Planner must not be called during outbox replay")

    working_sink = _RecordingSink()
    sup2 = LifeSupervisor(
        memory=mem,
        runner=_NeverCalledRunner(),
        sink=working_sink,
        config=config,
        planner_runner=_PlannerThatMustNotBeCalled(),
    )

    replay_result = sup2._plan_next_work()

    assert replay_result == PLAN_RETRY, (
        f"Replay returned {replay_result!r}; False would disable the continuous "
        "objective by signaling project completion — the exact Finding 1 regression"
    )


# ---------------------------------------------------------------------------
# Test 2: Corrupt outbox creates permanent no-progress by blocking Planner
# every cycle without clearing the invalid record.
# ---------------------------------------------------------------------------


def test_corrupt_outbox_is_retired_and_planning_resumes(
    tmp_path: Path,
) -> None:
    """A corrupt/unreadable outbox must be quarantined so it cannot block
    every future planning cycle. After retirement, load_planner_verdict_outbox
    must return None (no pending record) so the next _plan_next_work proceeds.
    """
    mem = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink(mem.root)

    # Write a corrupt outbox (invalid JSON).
    outbox_path = Path(mem.root) / OUTBOX_FILE
    outbox_path.write_text("{{not valid json at all", encoding="utf-8")

    sup = LifeSupervisor(
        memory=mem,
        runner=_NeverCalledRunner(),
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=1),
            poll_interval_seconds=0.01,
            continuous_objective="test objective",
        ),
    )

    # First call: detects corrupt outbox, emits diagnostic, retires it.
    retried_1, outcome_1 = sup._retry_pending_planner_verdict()
    assert retried_1 is True
    assert outcome_1 == PLAN_RETRY

    # Verify a structured life.planner.error diagnostic was emitted.
    error_events = [
        e for e in sink.events if e.get("type") == "life.planner.error"
    ]
    assert len(error_events) >= 1
    assert "corrupt" in error_events[0].get("error", "").lower() or \
           "unreadable" in error_events[0].get("error", "").lower()

    # Critical: The corrupt file must be retired. A second call must NOT
    # hit the corrupt-outbox branch again — (False, None) means no pending
    # outbox, so Planner can proceed normally.
    retried_2, outcome_2 = sup._retry_pending_planner_verdict()
    assert retried_2 is False, (
        "Corrupt outbox was NOT retired; the system will loop forever "
        "without invoking Planner"
    )
    assert outcome_2 is None
