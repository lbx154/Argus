"""Tests for ``argus_skill.adapters.skill_loop_runner.SkillLoopRunner``.

The shim sits in ``LoopEngine.runner`` and on each ``run_exec`` call:

  1. matcher (once per mission) → distill (on miss) → engineer
  2. wraps the engineer result back into ArgusBot ``CodexRunResult``

These tests verify the contract for each of those steps with light
fakes — no real codex / matcher LLM calls.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Skip the entire module if ArgusBot isn't importable (the shim
# imports CodexRunResult lazily; without the dep there is nothing to test).
pytest.importorskip("codex_autoloop.codex_runner")
pytest.importorskip("codex_autoloop.models")

from codex_autoloop.codex_runner import RunnerOptions as ArgusRunnerOptions  # noqa: E402
from codex_autoloop.models import CodexRunResult  # noqa: E402

from argus_skill.adapters.skill_loop_runner import (  # noqa: E402
    EngineerCallConfig,
    SkillLoopRunner,
    SkillLoopRunnerConfig,
)
from argus_skill.core.models import RunnerResult  # noqa: E402
from argus_skill.scientist.distiller import DistillerConfig  # noqa: E402
from argus_skill.skills.store import Skill  # noqa: E402

# ---------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------


class FakeSkillStore:
    """Capture-and-replay skill store for shim tests."""

    def __init__(self, matched: list[Skill] | None = None):
        self._matched = matched
        self.find_relevant_calls: list[str] = []
        self.save_distilled_calls: list[dict] = []

    def find_relevant(self, task_description, on_event=None):  # noqa: ARG002
        self.find_relevant_calls.append(task_description)
        return (self._matched, 0)

    def render_skill(self, skill: Skill) -> str:  # type: ignore[override]
        return skill.content.strip()

    def save_distilled(self, *, task_description, raw_distill_output, scientist_model):
        self.save_distilled_calls.append(
            {
                "task_description": task_description,
                "raw_distill_output": raw_distill_output,
                "scientist_model": scientist_model,
            }
        )
        return Skill(
            name="fresh-skill",
            description="distilled in test",
            category="test",
            content="# fresh\n\nplaybook contents",
            version=1,
            scientist_model=scientist_model,
            created_at="2026-01-01T00:00:00Z",
            task_history=[task_description],
        )


class FakeDistiller:
    def __init__(self, *, last_agent_message: str = "", raise_exc: Exception | None = None):
        self._msg = last_agent_message
        self._raise = raise_exc
        self.calls: list[dict] = []

    def distill(self, *, task_description, config, workdir_context="", on_event=None):  # noqa: ARG002
        self.calls.append({"task_description": task_description, "config": config})
        if self._raise is not None:
            raise self._raise
        return RunnerResult(
            exit_code=0,
            agent_messages=[self._msg] if self._msg else [],
        )


class FakeEngineerRunner:
    """argus-skill RunnerBackend stub that captures the call."""

    def __init__(self, result: RunnerResult | None = None):
        self.result = result or RunnerResult(
            exit_code=0,
            agent_messages=["engineer wrote a file"],
        )
        self.calls: list[dict] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.calls.append(
            {
                "prompt": prompt,
                "options": options,
                "run_label": run_label,
                "resume_thread_id": resume_thread_id,
            }
        )
        return self.result


class FakeFallbackRunner:
    """ArgusBot CodexRunner-shaped fallback for non-main labels."""

    def __init__(self):
        self.calls: list[dict] = []

    def run_exec(self, *, prompt, resume_thread_id, options, run_label):
        self.calls.append(
            {
                "prompt": prompt,
                "resume_thread_id": resume_thread_id,
                "options": options,
                "run_label": run_label,
            }
        )
        return CodexRunResult(
            command=["fallback"],
            exit_code=0,
            agent_messages=["fallback agent message"],
            turn_completed=True,
        )


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _make_runner(
    *,
    mission_objective: str = "make a small CLI",
    matched_skill: Skill | None = None,
    distill_on_miss: bool = True,
    distill_message: str = "",
    engineer_result: RunnerResult | None = None,
    workdir: Path | None = None,
) -> tuple[SkillLoopRunner, FakeSkillStore, FakeDistiller, FakeEngineerRunner, FakeFallbackRunner]:
    store = FakeSkillStore(matched=[matched_skill] if matched_skill else None)
    distiller = FakeDistiller(last_agent_message=distill_message)
    engineer = FakeEngineerRunner(result=engineer_result)
    fallback = FakeFallbackRunner()
    runner = SkillLoopRunner(
        config=SkillLoopRunnerConfig(
            mission_objective=mission_objective,
            workdir=workdir or Path("/tmp/mission-wd"),
            engineer=EngineerCallConfig(model="gpt-5.4-mini"),
            distiller=DistillerConfig(model="gpt-5.4"),
            distill_on_miss=distill_on_miss,
        ),
        skill_store=store,
        distiller=distiller,
        engineer_runner=engineer,
        fallback_runner=fallback,
    )
    return runner, store, distiller, engineer, fallback


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_main_label_matches_against_mission_objective_not_round_prompt():
    """Matcher must see the stable mission objective, NOT the
    LoopEngine prompt that grows with reviewer/planner instructions
    every round. Otherwise the cache key drifts and we constantly
    distill new skills.
    """
    skill = Skill(
        name="cli-builder",
        description="build a small CLI",
        category="cli",
        content="# CLI playbook",
    )
    runner, store, _, engineer, _ = _make_runner(
        mission_objective="Build a tiny CLI that says hi",
        matched_skill=skill,
    )

    loop_engine_prompt = (
        "Continue with prior objective. Reviewer next action: please add tests. "
        "Round 5 of 50."
    )
    runner.run_exec(
        prompt=loop_engine_prompt,
        resume_thread_id=None,
        options=ArgusRunnerOptions(),
        run_label="main",
    )

    # Matcher saw the mission objective, NOT the engine prompt.
    assert store.find_relevant_calls == ["Build a tiny CLI that says hi"]
    assert loop_engine_prompt not in store.find_relevant_calls
    # Engineer received the engine prompt (under "## Engine prompt").
    assert engineer.calls
    eng_prompt = engineer.calls[0]["prompt"]
    assert "## Engine prompt" in eng_prompt
    assert loop_engine_prompt in eng_prompt
    # Skill block went in too.
    assert "## Skill playbook" in eng_prompt
    assert "# CLI playbook" in eng_prompt


def test_skill_resolved_only_once_across_rounds():
    """Mission objective is stable; matcher should not re-fire each
    round. The shim caches the skill resolution and reuses it.
    """
    skill = Skill(
        name="cached", description="x", category="x", content="cached body"
    )
    runner, store, distiller, engineer, _ = _make_runner(matched_skill=skill)

    for round_idx in range(1, 4):
        runner.run_exec(
            prompt=f"round {round_idx} prompt",
            resume_thread_id=None,
            options=ArgusRunnerOptions(),
            run_label="main",
        )

    assert len(store.find_relevant_calls) == 1, "matcher must fire once per mission"
    assert distiller.calls == []
    assert len(engineer.calls) == 3


def test_outcome_done_maps_to_turn_completed():
    """Successful engineer call → CodexRunResult with turn_completed=True
    and the agent message readable via .last_agent_message."""
    runner, _, _, _, _ = _make_runner(
        engineer_result=RunnerResult(
            exit_code=0,
            agent_messages=["wrote /tmp/x.txt", "verified contents"],
        ),
    )
    result = runner.run_exec(
        prompt="do the thing",
        resume_thread_id=None,
        options=ArgusRunnerOptions(),
        run_label="main",
    )
    assert isinstance(result, CodexRunResult)
    assert result.turn_completed is True
    assert result.turn_failed is False
    assert result.fatal_error is None
    assert result.last_agent_message == "verified contents"
    assert result.thread_id is None  # we don't fake codex thread continuity


def test_engineer_runner_failure_maps_to_turn_failed():
    """Transport failure (FileNotFoundError → exit_code 127 + fatal_error)
    must become turn_failed=True so LoopEngine sees the failure and
    can react (e.g. mark round as no_progress)."""
    runner, _, _, _, _ = _make_runner(
        engineer_result=RunnerResult(
            exit_code=127,
            agent_messages=[],
            fatal_error="runner binary not found: codex",
        ),
    )
    result = runner.run_exec(
        prompt="anything",
        resume_thread_id=None,
        options=ArgusRunnerOptions(),
        run_label="main",
    )
    assert result.turn_failed is True
    assert result.turn_completed is False
    assert result.fatal_error == "runner binary not found: codex"


def test_external_interrupt_preserves_fatal_error_prefix():
    """When the engineer subprocess is killed by the watchdog with
    fatal_error='External interrupt: <reason>', LoopEngine relies on
    the 'External interrupt:' prefix at engine.py:166-168 to take the
    interrupted-branch. The shim must preserve that prefix verbatim.
    """
    runner, _, _, _, _ = _make_runner(
        engineer_result=RunnerResult(
            exit_code=-15,
            agent_messages=["partial output before interrupt"],
            fatal_error="External interrupt: operator stop requested",
        ),
    )
    result = runner.run_exec(
        prompt="long task",
        resume_thread_id=None,
        options=ArgusRunnerOptions(),
        run_label="main",
    )
    assert result.fatal_error is not None
    assert result.fatal_error.startswith("External interrupt:")
    assert result.turn_failed is True


def test_pptx_run_label_routes_to_fallback_runner():
    """Non-main labels (final-report, pptx-report) bypass skill matching
    and go straight to the real codex runner."""
    runner, store, distiller, engineer, fallback = _make_runner()

    runner.run_exec(
        prompt="generate pptx report",
        resume_thread_id=None,
        options=ArgusRunnerOptions(),
        run_label="main-pptx-report",
    )
    assert fallback.calls and fallback.calls[0]["run_label"] == "main-pptx-report"
    # And we did NOT fire matcher / distiller / engineer.
    assert store.find_relevant_calls == []
    assert distiller.calls == []
    assert engineer.calls == []


def test_watchdog_options_forwarded_from_loopengine_to_engineer():
    """LoopEngine passes external_interrupt_reason_provider /
    inactivity_callback / watchdog_*_idle_seconds in its RunnerOptions
    so the operator's /inject and /stop can interrupt a long round.
    The shim must forward these to the engineer call so the codex
    subprocess actually polls for them.
    """
    runner, _, _, engineer, _ = _make_runner()

    interrupt_reasons: list[str | None] = [None, None, "operator stopped"]

    def provider() -> str | None:
        return interrupt_reasons.pop(0) if interrupt_reasons else None

    def inactivity(snapshot) -> str | None:  # noqa: ARG001
        return None

    outer_options = ArgusRunnerOptions(
        external_interrupt_reason_provider=provider,
        inactivity_callback=inactivity,
        watchdog_soft_idle_seconds=120,
        watchdog_hard_idle_seconds=600,
    )
    runner.run_exec(
        prompt="x",
        resume_thread_id=None,
        options=outer_options,
        run_label="main",
    )

    forwarded = engineer.calls[0]["options"]
    assert forwarded.external_interrupt_reason_provider is provider
    assert forwarded.inactivity_callback is inactivity
    assert forwarded.watchdog_soft_idle_seconds == 120
    assert forwarded.watchdog_hard_idle_seconds == 600


def test_distill_on_miss_creates_and_uses_new_skill():
    """When matcher returns no skill and distill_on_miss=True, distiller
    is called against the mission objective and the saved skill is used
    in the engineer prompt for this and subsequent rounds.
    """
    runner, store, distiller, engineer, _ = _make_runner(
        mission_objective="set up a venv and install requests",
        matched_skill=None,
        distill_message="# Distilled\n\nrun python -m venv .venv etc.",
    )

    runner.run_exec(
        prompt="round 1",
        resume_thread_id=None,
        options=ArgusRunnerOptions(),
        run_label="main",
    )
    runner.run_exec(
        prompt="round 2",
        resume_thread_id=None,
        options=ArgusRunnerOptions(),
        run_label="main",
    )

    # Matcher fired once, distiller fired once, engineer twice.
    assert len(store.find_relevant_calls) == 1
    assert len(distiller.calls) == 1
    assert distiller.calls[0]["task_description"] == "set up a venv and install requests"
    assert len(store.save_distilled_calls) == 1
    assert len(engineer.calls) == 2

    # The freshly distilled skill body is in the engineer prompt for
    # both rounds (skill cache reused — same content used in both calls).
    for call in engineer.calls:
        assert "## Skill playbook" in call["prompt"]
        # FakeSkillStore returns a Skill whose content is "# fresh\n\nplaybook contents".
        assert "playbook contents" in call["prompt"]


def test_distill_disabled_means_no_skill_block():
    """When distill_on_miss=False, a matcher miss produces no skill —
    the engineer prompt has no playbook section."""
    runner, _, distiller, engineer, _ = _make_runner(
        matched_skill=None,
        distill_on_miss=False,
    )
    runner.run_exec(
        prompt="bare task",
        resume_thread_id=None,
        options=ArgusRunnerOptions(),
        run_label="main",
    )
    assert distiller.calls == []
    assert engineer.calls
    eng_prompt = engineer.calls[0]["prompt"]
    assert "## Skill playbook" not in eng_prompt
    assert "## Engine prompt" in eng_prompt


def test_engineer_options_inherit_runner_config_when_outer_unset():
    """If the LoopEngine RunnerOptions don't set model / reasoning,
    the shim falls back to the SkillLoopRunner's EngineerCallConfig."""
    runner, _, _, engineer, _ = _make_runner()
    runner.run_exec(
        prompt="x",
        resume_thread_id=None,
        options=ArgusRunnerOptions(),  # no model / reasoning_effort
        run_label="main",
    )
    forwarded = engineer.calls[0]["options"]
    # Falls back to EngineerCallConfig.model="gpt-5.4-mini".
    assert forwarded.model == "gpt-5.4-mini"


def test_workdir_used_when_outer_options_dont_provide_one():
    runner, _, _, engineer, _ = _make_runner(workdir=Path("/tmp/argus-mission-xyz"))
    runner.run_exec(
        prompt="x",
        resume_thread_id=None,
        options=ArgusRunnerOptions(),  # working_dir=None
        run_label="main",
    )
    forwarded = engineer.calls[0]["options"]
    assert forwarded.working_dir == "/tmp/argus-mission-xyz"
