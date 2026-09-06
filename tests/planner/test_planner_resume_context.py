"""``plan_next`` applies the caller's delta only when the session resumes.

The supervisor cannot know at render time whether the role session will resume
or rotate, so it hands ``plan_next`` both the full context and the delta. The
Planner applies the delta on a resumed thread — whose earlier turns already
hold the older material — and a fresh or rotated session always receives the
full journal and research plan, so a stale caller-side record can never starve
a new thread of context.
"""

from __future__ import annotations

from pathlib import Path

from argus_skill.core.models import RunnerResult
from argus_skill.planner.planner import Planner, PlannerConfig
from argus_skill.skills.vertical_select import persist_vertical

_TASK_REPLY = (
    "PROJECT_DONE=false\n"
    "REASON=queue the next decisive check\n"
    "TASK_KEY=k1\n"
    "TASK_TITLE=Run the next check\n"
    "TASK_OBJECTIVE=Execute the check the evidence calls for."
)


class _RecordingRunner:
    backend = "recorder"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run_exec(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return RunnerResult(
            exit_code=0,
            agent_messages=[_TASK_REPLY],
            thread_id="planner-thread",
        )


def _config(tmp_path: Path) -> PlannerConfig:
    return PlannerConfig(
        working_dir=str(tmp_path),
        state_root=str(tmp_path),
        role_session_policy="rolling",
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
