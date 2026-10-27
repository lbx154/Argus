"""A settled task says who holds it only when a role handed it on.

A host stop names nobody, a question to the operator is a park on that
question, and an older release's research park stays a park.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus.apps._runtime_backends import _Outcome
from argus.apps.cli._follow import _format_follow_event
from argus.core.models import ReviewDecision
from argus.core.stop_kinds import park_reason_of
from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, EventJournal, LifeMemory
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from tests.manager_inbox import team_reports


class _Manager:
    def bind_execution_workdir(self, _workdir):
        return self


class _Runner:
    manager = _Manager()


def _settle(tmp_path: Path, outcome: _Outcome) -> tuple[Path, dict]:
    life = tmp_path / "life"
    supervisor = LifeSupervisor(
        memory=LifeMemory.open(life), runner=_Runner(),
        sink=JsonlEventSink(None, life_dir=life, verbosity="full"),
        config=LifeSupervisorConfig(budget=LifeBudget(max_missions=1), poll_interval_seconds=0.01),
    )
    supervisor._project_workdir().mkdir(parents=True, exist_ok=True)
    supervisor.memory.backlog.add(BacklogItem.new(
        title="Resolve boundary", objective="Resolve the boundary case", tags=["manager_direct"],
    ))
    supervisor.runner.execute = lambda **kwargs: outcome
    supervisor.tick()
    events = [json.loads(line) for line in (life / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    return life, next(event for event in events if event.get("type") == "life.mission.completed")


def test_a_reviewer_that_kept_failing_leaves_its_next_step_not_the_manager(tmp_path: Path) -> None:
    """The Reviewer's backend failed until the task stopped: nothing consulted
    the Manager, so the message gives the recorded next step."""
    unjudged = ReviewDecision(
        status="blocked", reason="Reviewer runner raised RuntimeError: boom",
        next_action="Resolve the reviewer runner failure before retrying.", backend_unavailable=True,
    )
    life, completed = _settle(tmp_path, _Outcome(
        success=False, status="error", stop_reason="The Reviewer could not reach a judgment.",
        final_review_status=unjudged.status, final_review_source="reviewer_unavailable",
        final_review_next_action=unjudged.next_action,
    ))

    assert completed["outcome"]["review_status"] == "not_assessed"
    [message] = [report["text"] for report in team_reports(life) if report.get("mission_result")]
    assert "Resolve the reviewer runner failure before retrying." in message
    assert "Manager is deciding" not in message


def test_a_question_to_the_operator_parks_and_never_reads_as_failed(tmp_path: Path) -> None:
    _, completed = _settle(tmp_path, _Outcome(
        success=False, status="operator", operator_question="Use the strict or the loose bound?",
        final_review_status="operator", final_review_source="reviewer",
    ))

    assert park_reason_of(completed["status"], completed.get("park_reason")) == "operator_question"
    assert _format_follow_event(completed, "engineer").startswith("⏸ Waiting: Resolve boundary")
    assert EventJournal._entry_from_event(completed).kind == "mission_parked"


def test_an_older_research_park_journals_as_a_research_pause() -> None:
    for status in ("paused_no_breakthrough", "exhausted_current_methods", "research_incomplete"):
        entry = EventJournal._entry_from_event({
            "type": "life.mission.completed", "item_id": "t", "title": "t",
            "status": status, "success": False,
        })
        assert entry.kind == "research_pause", status
