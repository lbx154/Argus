"""``plan_next`` applies the caller's delta only when the session resumes.

The supervisor cannot know at render time whether the role session will resume
or rotate, so it hands ``plan_next`` both the full context and the delta. The
Planner applies the delta on a resumed thread — whose earlier turns already
hold the older material — and a fresh or rotated session always receives the
full journal and research plan, so a stale caller-side record can never starve
a new thread of context.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from argus.core.models import RunnerResult
from argus.planner.planner import Planner, PlannerConfig
from argus.skills.vertical_select import persist_vertical
from tests.hand_off import plan, submit

_TASK_REPLY = (
    plan(summary='queue the next decisive check', tasks=[{'key': 'k1', 'title': 'Run the next check', 'objective': 'Execute the check the evidence calls for.'}])
)


class _RecordingRunner:
    backend = "copilot"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run_exec(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        submit(kwargs["options"], _TASK_REPLY)
        return RunnerResult(
            exit_code=0,
            thread_id="planner-thread",
        )


def _config(tmp_path: Path) -> PlannerConfig:
    return PlannerConfig(
        working_dir=str(tmp_path),
        state_root=str(tmp_path),
        role_session_path=tmp_path / "role-sessions" / "planner.json",
    )


def test_resumed_session_receives_delta_and_plan_marker(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "software")
    runner = _RecordingRunner()
    planner = Planner(runner)

    first = planner.plan_next(
        continuous_objective="improve the solver",
        journal_tail="FULL JOURNAL BODY ALPHA",
        research_plan="PLAN BODY ALPHA",
        journal_delta=None,
        config=_config(tmp_path),
    )
    assert first.error == ""
    assert runner.calls[0]["resume_thread_id"] is None
    assert "FULL JOURNAL BODY ALPHA" in runner.calls[0]["prompt"]
    assert "PLAN BODY ALPHA" in runner.calls[0]["prompt"]

    second = planner.plan_next(
        continuous_objective="improve the solver",
        journal_tail="FULL JOURNAL BODY ALPHA BETA",
        research_plan="PLAN BODY ALPHA",
        journal_delta="- [09-06 10:00] mission_complete: DELTA ONLY BETA",
        research_plan_unchanged=True,
        config=_config(tmp_path),
    )
    assert second.error == ""
    assert runner.calls[1]["resume_thread_id"] == "planner-thread"
    prompt = runner.calls[1]["prompt"]
    assert "DELTA ONLY BETA" in prompt
    assert "Newly settled work since your previous planning turn" in prompt
    assert "FULL JOURNAL BODY ALPHA BETA" not in prompt
    assert "PLAN BODY ALPHA" not in prompt
    assert "unchanged since your previous planning turn" in prompt


def test_fresh_session_ignores_the_delta(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "software")
    runner = _RecordingRunner()
    planner = Planner(runner)

    verdict = planner.plan_next(
        continuous_objective="improve the solver",
        journal_tail="FULL JOURNAL BODY ALPHA",
        research_plan="PLAN BODY ALPHA",
        journal_delta="DELTA THAT MUST NOT APPEAR ALONE",
        research_plan_unchanged=True,
        config=_config(tmp_path),
    )

    assert verdict.error == ""
    prompt = runner.calls[0]["prompt"]
    assert runner.calls[0]["resume_thread_id"] is None
    assert "FULL JOURNAL BODY ALPHA" in prompt
    assert "PLAN BODY ALPHA" in prompt
    assert "Newly settled work" not in prompt


def test_resumed_session_is_not_resent_unchanged_sections(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "software")
    runner = _RecordingRunner()
    planner = Planner(runner)
    for cycle in range(3):
        planner.plan_next(
            continuous_objective="improve the solver",
            journal_tail="FULL JOURNAL BODY ALPHA",
            research_plan="PLAN BODY ALPHA",
            journal_delta=f"- mission_complete: cycle {cycle}",
            research_plan_unchanged=True,
            planning_cycle=cycle,
            config=_config(tmp_path),
        )

    third = runner.calls[2]["prompt"]
    assert runner.calls[2]["resume_thread_id"] == "planner-thread"
    assert "## Unchanged since your previous turn" in third
    assert "PLAN BODY ALPHA" not in third
    assert "mission_complete: cycle 2" in third, "what changed is always sent"


class _PrunedThreadRunner(_RecordingRunner):
    """The provider has lost ``planner-thread``: resuming it fails at startup."""

    def __init__(self) -> None:
        super().__init__()
        self.fresh_threads = 0

    def run_exec(self, **kwargs):  # noqa: ANN003
        if kwargs.get("resume_thread_id") == "planner-thread":
            self.calls.append(kwargs)
            return RunnerResult(
                exit_code=1,
                fatal_error="Error: No session, task, or name matched 'planner-thread'.",
            )
        result = super().run_exec(**kwargs)
        if kwargs.get("resume_thread_id") is None:
            self.fresh_threads += 1
            result.thread_id = f"fresh-thread-{self.fresh_threads}"
        else:
            result.thread_id = kwargs["resume_thread_id"]
        return result


def test_lost_provider_thread_restarts_with_the_full_prompt(tmp_path: Path) -> None:
    """A pruned Copilot session must not leave the Planner on a delta-only thread.

    Copilot sessions idle past the retention window are deleted. Resuming one
    used to continue in a fresh thread with only the resume delta, so that
    thread never received the Planner contract or the older journal.
    """
    persist_vertical(tmp_path, "software")
    Planner(_RecordingRunner()).plan_next(
        continuous_objective="improve the solver",
        journal_tail="FULL JOURNAL BODY ALPHA",
        research_plan="PLAN BODY ALPHA",
        config=_config(tmp_path),
    )

    runner = _PrunedThreadRunner()
    planner = Planner(runner)
    events: list[dict] = []
    verdict = planner.plan_next(
        continuous_objective="improve the solver",
        journal_tail="FULL JOURNAL BODY ALPHA BETA",
        research_plan="PLAN BODY ALPHA",
        journal_delta="- mission_complete: DELTA ONLY BETA",
        research_plan_unchanged=True,
        config=replace(_config(tmp_path), on_event=events.append),
    )

    assert verdict.error == ""
    assert [call["resume_thread_id"] for call in runner.calls] == ["planner-thread", None]
    fresh = runner.calls[1]["prompt"]
    assert "You are the Argus Planner" in fresh and "## Rules:" in fresh
    assert "FULL JOURNAL BODY ALPHA BETA" in fresh
    assert "PLAN BODY ALPHA" in fresh
    assert "Continued Planner cycle" not in fresh
    turn = events[-1]
    assert (turn["action"], turn["rotation_reason"]) == ("rotated", "missing_provider_session")
    assert turn["session_id"] == "fresh-thread-1"

    planner.plan_next(
        continuous_objective="improve the solver",
        journal_tail="FULL JOURNAL BODY ALPHA BETA GAMMA",
        research_plan="PLAN BODY ALPHA",
        journal_delta="- mission_complete: DELTA ONLY GAMMA",
        research_plan_unchanged=True,
        config=_config(tmp_path),
    )
    assert runner.calls[2]["resume_thread_id"] == "fresh-thread-1"
    resumed = runner.calls[2]["prompt"]
    assert "DELTA ONLY GAMMA" in resumed
    # The fresh thread's full prompt is the record the next delta elides against.
    assert "## Unchanged since your previous turn" in resumed
