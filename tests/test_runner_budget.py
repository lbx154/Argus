"""F3 PART A: the mid-mission per-mission cost circuit-breaker.

``SupervisedEngineer.run`` takes an optional ``per_mission_budget``. Before each
round *after the first* it checks live spend against the cap; once reached it
stops the mission with status ``budget_exhausted`` WITHOUT calling the reviewer
again. This is a hard pause, never a completion — the reviewer stays the sole
authority on done-ness (anti-cheat), and the supervisor leaves the item pending.

Pinned here:
  * round 1 always runs (the cap is only consulted for round_index > 1);
  * once the cap is reached the next round does NOT run (no engineer-r2 call);
  * a ``round.budget_exhausted`` event is emitted and status is propagated;
  * a cap <= 0 disables the breaker entirely.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill import SkillLoop, SkillLoopConfig
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.life.supervisor._config import MissionBudget

SKILL_MD = (
    "## Title\nDemo skill\n\n"
    "## Description\nA fixed playbook for the budget test.\n\n"
    "## Category\ndemo\n\n"
    "## When to use\n- demo task\n\n"
    "## When NOT to use\n- production code\n\n"
    "## How to solve\n- Do the thing.\n\n"
    "## Examples\n- demo → done\n\n"
    "## Response shape\n- Reply inline.\n"
)


def _continue_review() -> str:
    return json.dumps({
        "status": "continue",
        "reason": "More work needed.",
        "next_action": "Finish the work.",
        "round_summary_markdown": "# r\n",
        "completion_summary_markdown": "",
    })


def _done_review() -> str:
    return json.dumps({
        "status": "done",
        "reason": "Met criterion.",
        "next_action": "—",
        "round_summary_markdown": "# done\n",
        "completion_summary_markdown": "Done.",
    })


def _build_loop(backend: MemoryBackend, skills_dir: Path, events: list) -> SkillLoop:
    config = SkillLoopConfig(
        engineer_model="m",
        reviewer_model="m",
        max_rounds=5,
        backend_failure_backoff_seconds=0,
    )
    return SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=config,
        on_event=events.append,
    )


def test_per_mission_cap_breaks_before_round_two(tmp_path: Path) -> None:
    """Cap already exceeded by the time round 2 is entered → the mission stops
    as ``paused_budget`` and round 2's engineer turn never runs."""
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="r1 work", thread_id="tid-1"))
    backend.queue("reviewer", CannedResponse(message=_continue_review()))
    # NOTE: deliberately no engineer-r2 / second reviewer queued — the breaker
    # must stop the loop before round 2 consumes them.

    events: list = []
    loop = _build_loop(backend, tmp_path / "skills", events)
    budget = MissionBudget(cap_usd=0.50, spent=lambda: 1.00)  # already over
    out = loop.run("task", workdir=tmp_path, per_mission_budget=budget)

    assert out.status == "paused_budget", out.status
    assert out.stop_kind == "budget_exhausted"
    assert out.recoverable is True
    assert not out.successful
    # Round 1 ran; round 2 (and its reviewer) did NOT.
    labels = [label for label, _, _ in backend.history]
    assert "engineer-r1" in labels
    assert "engineer-r2" not in labels
    assert labels.count("reviewer") == 1
    # The breaker emitted its event with the cap/spend.
    breaks = [e for e in events if e.get("type") == "round.budget_exhausted"]
    assert len(breaks) == 1
    assert breaks[0]["cap_usd"] == 0.50
    assert breaks[0]["spent_usd"] == 1.00


def test_round_one_always_runs_even_when_over_cap(tmp_path: Path) -> None:
    """Even if spend is already over the cap at entry, round 1 is never skipped —
    the breaker only consults the cap for round_index > 1."""
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="r1 work", thread_id="tid-1"))
    backend.queue("reviewer", CannedResponse(message=_continue_review()))

    events: list = []
    loop = _build_loop(backend, tmp_path / "skills", events)
    budget = MissionBudget(cap_usd=0.01, spent=lambda: 999.0)
    out = loop.run("task", workdir=tmp_path, per_mission_budget=budget)

    assert out.status == "paused_budget"
    assert out.stop_kind == "budget_exhausted"
    assert [label for label, _, _ in backend.history].count("engineer-r1") == 1


def test_cap_zero_disables_breaker(tmp_path: Path) -> None:
    """A cap of 0 is a no-op: the mission runs to its normal reviewer-owned
    completion even with astronomically reported spend."""
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="r1 work", thread_id="tid-1"))
    backend.queue("reviewer", CannedResponse(message=_continue_review()))
    backend.queue("engineer-r2", CannedResponse(message="r2 work", thread_id="tid-2"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    events: list = []
    loop = _build_loop(backend, tmp_path / "skills", events)
    budget = MissionBudget(cap_usd=0.0, spent=lambda: 10_000.0)
    out = loop.run("task", workdir=tmp_path, per_mission_budget=budget)

    assert out.successful  # reviewer said done; budget never interfered
    assert out.status == "done"
    assert not [e for e in events if e.get("type") == "round.budget_exhausted"]


def test_no_budget_passed_is_unchanged_behaviour(tmp_path: Path) -> None:
    """When no ``per_mission_budget`` is supplied the loop behaves exactly as
    before (the breaker is entirely inert)."""
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="r1 work", thread_id="tid-1"))
    backend.queue("reviewer", CannedResponse(message=_continue_review()))
    backend.queue("engineer-r2", CannedResponse(message="r2 work", thread_id="tid-2"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    events: list = []
    loop = _build_loop(backend, tmp_path / "skills", events)
    out = loop.run("task", workdir=tmp_path)

    assert out.successful
    assert not [e for e in events if e.get("type") == "round.budget_exhausted"]
