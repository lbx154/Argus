"""End-to-end: a Planner task that requests independent review must run a real
Reviewer round, not self-settle with "independent review was not required".

Regression for the enqueue bug where a planner-emitted
``TASK_REQUIRE_INDEPENDENT_REVIEW=true`` was dropped at the enqueue boundary, so
the pending item lacked ``review:required`` and the mission closed on the
Engineer's own self-review. This test drives the fresh-life-dir supervisor
through the real ``SkillLoop`` with a memory engineer+reviewer backend and
asserts the ``round.review.completed`` event records ``review_source=reviewer``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.core.models import RunnerResult
from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus_skill.life.supervisor._planning_context import PlanningContextMixin
from argus_skill.loop import SkillLoop, SkillLoopConfig
from argus_skill.planner import PlannerConfig
from argus_skill.skills.vertical_select import persist_vertical


class _PlannerBackend:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def run_exec(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return RunnerResult(exit_code=0, agent_messages=[self.replies.pop(0)])


class _RecordingSink:
    """Captures events in memory and tees them to the fresh life dir, matching
    how the daemon's ``JsonlEventSink`` persists them."""

    def __init__(self, life_dir: Path) -> None:
        self.events: list[dict] = []
        self._tee = JsonlEventSink(None, life_dir=life_dir, verbosity="full")

    def handle_event(self, event: dict) -> None:
        self.events.append(event)
        self._tee.handle_event(event)


class _RealLoopRunner:
    """Mission runner that executes the real ``SkillLoop`` with memory backends,
    mirroring the production ``_SkillLoopRunner`` contract the supervisor
    drives (``require_independent_review`` and friends flow via ``execute``)."""

    def __init__(self, skills_dir: Path, workdir: Path) -> None:
        self.skills_dir = skills_dir
        self.workdir = workdir
        self.engineer_backend = MemoryBackend()
        self.reviewer_backend = MemoryBackend()
        self.kwargs: dict = {}

    def _queue_loop_replies(self) -> None:
        self.engineer_backend.queue(
            "engineer-r1",
            CannedResponse(
                message=(
                    "Implemented the bounded fix.\n"
                    "## Verification\n3 tests passed\n"
                    "`MILESTONE_STATUS=done`"
                ),
                thread_id="t1",
            ),
        )
        self.reviewer_backend.queue(
            "reviewer",
            CannedResponse(
                message=json.dumps({
                    "status": "done",
                    "reason": "The submitted artifact satisfies its checklist.",
                    "next_action": "",
                    "round_summary_markdown": "# done\n",
                    "completion_summary_markdown": "Done.",
                }),
                thread_id="v1",
            ),
        )

    def execute(self, **kwargs):  # noqa: ANN003
        self.kwargs = kwargs
        self._queue_loop_replies()
        sink = kwargs["sink"]
        config = SkillLoopConfig(
            engineer_model="m",
            reviewer_model="m",
            require_independent_review=bool(
                kwargs.get("require_independent_review", False)
            ),
            wiki_enabled=False,
            auto_init_wiki=False,
            max_rounds=2,
            workflow_mode="direct",
            active_vertical=str(kwargs.get("vertical_override") or ""),
            vertical_state_root=self.workdir,
        )
        loop = SkillLoop(
            skills_dir=self.skills_dir,
            engineer_runner=self.engineer_backend,
            reviewer_runner=self.reviewer_backend,
            config=config,
            on_event=sink.handle_event,
        )
        outcome = loop.run(
            kwargs["objective"],
            workdir=self.workdir,
            objective_for_skill=kwargs.get("review_objective") or kwargs["objective"],
            review_objective=kwargs.get("review_objective") or kwargs["objective"],
            original_objective=kwargs.get("original_objective") or kwargs["objective"],
            scope=kwargs.get("scope", ""),
        )
        rounds = outcome.rounds
        last_review = rounds[-1].review if rounds else None
        return SimpleNamespace(
            success=outcome.successful,
            status=(
                outcome.status.value
                if hasattr(outcome.status, "value")
                else str(outcome.status)
            ),
            stop_reason=outcome.reason,
            rounds=outcome.round_count,
            final_review_status=(
                str(getattr(last_review, "status", "") or "")
                if last_review else ""
            ),
            final_review_source=(
                str(getattr(last_review, "review_source", "") or "")
                if last_review else ""
            ),
            final_review_reason=(
                str(getattr(last_review, "reason", "") or "")
                if last_review else ""
            ),
            final_message=outcome.final_message,
            summary="",
            stage_transition={},
        )


def test_planner_requested_review_runs_real_reviewer_round(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    planner = _PlannerBackend([
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=delegate the reviewed bounded repair",
            "TASK_KEY=reviewed",
            "TASK_TITLE=Adopt the reviewed bounded candidate",
            "TASK_OBJECTIVE=Implement the candidate and close it through "
            "the independent Reviewer.",
            "TASK_REQUIRE_INDEPENDENT_REVIEW=true",
        ])
    ])
    runner = _RealLoopRunner(skills_dir, project)
    memory = LifeMemory.open(life)
    sink = _RecordingSink(memory.root)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            continuous=True,
            continuous_objective="keep optimizing Argus",
            open_ended=True,
            project_worktree=project,
            artifact_root=project,
        ),
        planner_runner=planner,
    )
    persist_vertical(project, "software", workflow_mode="direct")
    supervisor._vertical_resolved = True
    supervisor._planner_config = lambda: PlannerConfig(  # type: ignore[method-assign]
        working_dir=str(project),
        open_ended=True,
    )

    # Planning cycle: the structured review request must survive enqueue.
    assert supervisor._plan_next_work() is True
    pending = supervisor.memory.backlog.pending()
    assert len(pending) == 1
    assert "review:required" in pending[0].tags
    assert PlanningContextMixin._item_requires_independent_review(pending[0]) is True

    # Mission execution through the real loop with a reviewer backend.
    result = supervisor.tick()
    assert result is not None
    assert result["status"] == "done"
    assert result["success"] is True
    assert runner.kwargs["require_independent_review"] is True

    review_events = [
        event for event in sink.events
        if event.get("type") == "round.review.completed"
    ]
    assert review_events, "expected a reviewer round"
    assert review_events[0]["review_source"] == "reviewer"
    assert review_events[0]["status"] == "done"
    assert "independent review was not required" not in str(
        review_events[0].get("reason", "")
    )

    # The same evidence is persisted in the fresh life dir's event journal.
    persisted = [
        json.loads(line)
        for line in (life / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    persisted_reviews = [
        event for event in persisted
        if event.get("type") == "round.review.completed"
    ]
    assert persisted_reviews
    assert persisted_reviews[0]["review_source"] == "reviewer"
