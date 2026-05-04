"""Fake-runner integration test: SkillLoopRunner ⊕ LoopEngine.

This walks ArgusBot's ``LoopEngine.run()`` with our ``SkillLoopRunner``
plugged in as the main-agent runner, and a monkey-patched
``Reviewer.evaluate`` that scripts the verdict per round (continue,
continue, done). It catches integration regressions that pure unit
tests miss:

  * SkillLoopRunner's ``run_exec`` signature matches what LoopEngine
    actually invokes.
  * ``RunnerOptions`` field set is compatible (LoopEngine builds
    ArgusBot RunnerOptions; SkillLoopRunner reads them via getattr).
  * ``CodexRunResult`` shape returned by SkillLoopRunner is consumed
    by LoopEngine's round-completion path without raising.
  * The matcher is keyed against the mission objective even though
    LoopEngine's per-round prompt evolves with reviewer feedback.

We DO NOT spawn codex — the engineer runner is a fake. Reviewer is
real but its codex-bound ``evaluate`` is monkeypatched. Planner is
``None`` (plan_mode=off).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.adapters.skill_loop_runner import (
    EngineerCallConfig,
    SkillLoopRunner,
    SkillLoopRunnerConfig,
)
from argus_skill.core.models import RunnerOptions, RunnerResult
from argus_skill.scientist.distiller import DistillerConfig

# ---------------------------------------------------------------------------
# Fakes — kept minimal
# ---------------------------------------------------------------------------

class _FakeSkillStore:
    """No relevant skills → matcher returns nothing → engineer runs without skill block."""

    def __init__(self) -> None:
        self.match_calls: list[str] = []

    def find_relevant(self, task: str, *, on_event=None):
        self.match_calls.append(task)
        return [], None  # (skills, raw_response)

    def render_skill(self, skill):
        return ""

    def save_distilled(self, **kwargs):  # never called when distill_on_miss=False
        raise AssertionError("save_distilled should not be invoked in this test")


class _FakeDistiller:
    """Distill is never called because distill_on_miss=False below."""

    def distill(self, **kwargs):
        raise AssertionError("distill should not be invoked in this test")


class _ScriptedEngineer:
    """Returns one canned RunnerResult per call. Records every prompt seen."""

    def __init__(self) -> None:
        self.prompts_seen: list[str] = []
        self.options_seen: list[RunnerOptions] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.prompts_seen.append(prompt)
        self.options_seen.append(options)
        round_index = len(self.prompts_seen)
        return RunnerResult(
            exit_code=0,
            agent_messages=[
                f"Engineer round {round_index}: I made progress (prompt has {len(prompt)} chars).",
            ],
        )


class _FailingFallback:
    """Asserts it is never called for run_label='main'."""

    def run_exec(self, **kwargs):
        raise AssertionError(
            f"fallback_runner should not be invoked for run_label={kwargs.get('run_label')!r}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_skill_loop_runner(*, mission_objective: str, workdir: Path):
    return SkillLoopRunner(
        config=SkillLoopRunnerConfig(
            mission_objective=mission_objective,
            workdir=workdir,
            engineer=EngineerCallConfig(model="fake", reasoning_effort="medium"),
            distiller=DistillerConfig(model="fake", reasoning_effort="high"),
            distill_on_miss=False,
        ),
        skill_store=_FakeSkillStore(),
        distiller=_FakeDistiller(),
        engineer_runner=_ScriptedEngineer(),
        fallback_runner=_FailingFallback(),
    )


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_loop_engine_walks_two_rounds_then_done(monkeypatch, tmp_path):
    """LoopEngine drives 2 rounds; reviewer flips to ``done`` on round 2.

    Verifies the integration end-to-end:
      * Round 1 runs the engineer; matcher sees the *mission objective*,
        not LoopEngine's per-round prompt.
      * Reviewer says continue; LoopEngine extends to round 2.
      * Round 2 reviewer says done; LoopEngine returns success.
      * Each round's main_result has last_agent_message coming from
        our scripted engineer output.
    """
    pytest.importorskip("codex_autoloop.core.engine")

    from codex_autoloop.core.engine import LoopConfig, LoopEngine
    from codex_autoloop.core.state_store import LoopStateStore
    from codex_autoloop.models import ReviewDecision
    from codex_autoloop.reviewer import Reviewer

    mission_objective = "Set up a Python venv and write a hello-world script."
    skill_loop_runner = _make_skill_loop_runner(
        mission_objective=mission_objective,
        workdir=tmp_path,
    )

    # Script the reviewer: continue, then done.
    review_calls: list[dict] = []
    verdicts = iter([
        ReviewDecision(
            status="continue",
            confidence=0.7,
            reason="More work needed.",
            next_action="Implement remaining steps.",
            round_summary_markdown="# Round 1\n\n- partial.\n",
        ),
        ReviewDecision(
            status="done",
            confidence=0.95,
            reason="Engineer reported success.",
            next_action="Mission complete.",
            round_summary_markdown="# Round 2\n\n- done.\n",
            completion_summary_markdown="# Completion\n\nAll steps finished.",
        ),
    ])

    def fake_evaluate(self, **kwargs):
        review_calls.append(kwargs)
        return next(verdicts)

    monkeypatch.setattr(Reviewer, "evaluate", fake_evaluate)

    # Reviewer needs *some* runner; it's never invoked because we
    # patched evaluate. Pass a sentinel.
    sentinel_runner = object()
    reviewer = Reviewer.__new__(Reviewer)  # avoid the codex bind in __init__
    reviewer.runner = sentinel_runner

    state_store = LoopStateStore(objective=mission_objective)
    config = LoopConfig(
        objective=mission_objective,
        max_rounds=5,
        plan_mode="off",
        full_auto=True,
        skip_git_repo_check=True,
        main_model="fake-mini",
        reviewer_model="fake-mini",
        # Tighten check timeout so any accidental check call would fail fast
        check_timeout_seconds=1,
    )

    events: list[dict] = []

    class CollectorSink:
        def handle_event(self, event):
            events.append(event)

        def handle_stream_line(self, *args, **kwargs):
            pass

        def close(self):
            pass

    engine = LoopEngine(
        runner=skill_loop_runner,
        reviewer=reviewer,
        planner=None,
        config=config,
        state_store=state_store,
        event_sink=CollectorSink(),
    )

    result = engine.run()

    # --- Engine outcome ---------------------------------------------------
    assert result.success is True
    assert len(result.rounds) == 2

    # --- Reviewer was called twice with monotonically rising round_index --
    assert len(review_calls) == 2
    assert review_calls[0]["round_index"] == 1
    assert review_calls[1]["round_index"] == 2

    # --- Engineer was driven exactly twice (one per round) ----------------
    engineer = skill_loop_runner.engineer_runner
    assert len(engineer.prompts_seen) == 2

    # --- Matcher was queried with the mission objective, not round prompt -
    store = skill_loop_runner.skill_store
    assert store.match_calls, "matcher must have been called at least once"
    assert all(call == mission_objective for call in store.match_calls)
    # And matcher fires only ONCE across rounds (cached)
    assert len(store.match_calls) == 1

    # --- Engineer's prompt for round 1 contains the LoopEngine prompt ------
    # which itself embeds the mission objective.
    assert mission_objective in engineer.prompts_seen[0]
    # And our shim wrapped it under "## Engine prompt"
    assert "## Engine prompt" in engineer.prompts_seen[0]

    # --- Watchdog hooks were forwarded by LoopEngine into our shim --------
    # LoopEngine sets external_interrupt_reason_provider; our shim
    # translates it to argus-skill's RunnerOptions.
    assert engineer.options_seen[0].external_interrupt_reason_provider is not None
    assert engineer.options_seen[0].watchdog_soft_idle_seconds > 0

    # --- Mission events flowed to our sink ---------------------------------
    types = [e.get("type") for e in events]
    assert "loop.started" in types
    assert types.count("round.started") == 2
    assert types.count("round.main.completed") == 2
    assert "loop.completed" in types
