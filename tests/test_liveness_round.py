"""Round-loop liveness: every unjudged continuation is bounded.

Each test pins one confirmed defect where the baton could circle without the
round guards ever being consulted, or where the text a role reads disagreed
with the decision the host took.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.models import ReviewDecision, RunnerResult
from argus.core.role_handoff import decision_engineer_handoff, parse_engineer_handoff
from argus.engineer import round_settlement
from argus.engineer.round_execution import _engineer_decision_message
from argus.engineer.round_self_review import _milestone_is_blocked, _milestone_is_done
from argus.engineer.round_state import EngineerTurnOutcome
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import Reviewer, ReviewerConfig


def _outcome(message: str, decision: dict | None = None) -> EngineerTurnOutcome:
    return EngineerTurnOutcome(
        engineer_result=RunnerResult(exit_code=0, agent_messages=[message]),
        round_thread_id=None, fatal_error=None, safe_fatal_error=None, stop_kind=None,
        raw_engineer_message=message, engineer_message=message,
        process_ownership_note="", round_started_at=0.0, decision=decision,
    )


def _engineer(backend: MemoryBackend) -> SupervisedEngineer:
    return SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )


def _labels(backend: MemoryBackend) -> list[str]:
    return [label for label, _prompt, _options in backend.history]


# --- 1. Self-review mode: unjudged turns are bounded, footers read tolerantly ---


@pytest.mark.parametrize("footer", [
    "MILESTONE_STATUS=done",
    "MILESTONE_STATUS: done",
    "- MILESTONE_STATUS=done",
    "**MILESTONE_STATUS**: done",
    "**MILESTONE_STATUS=done**",
    "`MILESTONE_STATUS=done`",
    "MILESTONE_STATUS = Done.",
    "Decision:\nMILESTONE_STATUS: done\nNEXT_OWNER=reviewer",
])
def test_a_decorated_milestone_footer_still_says_done(footer: str) -> None:
    outcome = _outcome("Implemented the bounded fix; 3 tests pass.\n" + footer)
    assert _milestone_is_done(outcome) is True
    assert _milestone_is_blocked(outcome) is False


def test_the_milestone_reader_keeps_blocked_apart_and_ignores_quoted_examples() -> None:
    assert _milestone_is_blocked(_outcome("Credentials are missing.\n- MILESTONE_STATUS: blocked"))
    # A quoted instruction mid-sentence is not a verdict.
    quoted = _outcome("The task says to end with `MILESTONE_STATUS=done|continue` later.")
    assert _milestone_is_done(quoted) is False
    assert _milestone_is_done(_outcome("Half done.\nMILESTONE_STATUS=done|continue")) is False


def test_a_decorated_done_finishes_a_self_reviewed_mission(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="Fixed it; tests pass.\nMILESTONE_STATUS: done"))

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="low-risk repair",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=4, require_independent_review=False),
        workdir=tmp_path,
    )

    assert status == "done"
    assert _labels(backend) == ["engineer-r1"]
    assert rounds[0].review.review_source == "engineer_self_review"


@pytest.mark.parametrize("footer", ["MILESTONE_STATUS=continue", "NEXT_OWNER=engineer"])
def test_unfinished_self_reviewed_turns_reach_the_reviewer_after_the_limit(
    tmp_path: Path, footer: str,
) -> None:
    """Without independent review or a round cap, a turn that never wrote
    ``done`` used to continue forever: no Reviewer, no settlement, so no round
    guard ran."""
    backend = MemoryBackend()
    for index in (1, 2, 3):
        backend.queue(f"engineer-r{index}", CannedResponse(message=f"Step {index} written.\n{footer}"))
    backend.queue("reviewer", CannedResponse(review_action=("approve_review", {"review": "Holds."})))

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="low-risk repair",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=0, require_independent_review=False),
        workdir=tmp_path,
    )

    assert _labels(backend) == ["engineer-r1", "engineer-r2", "engineer-r3", "reviewer"]
    assert status == "done"
    assert [record.review.review_source for record in rounds] == ["reviewer"]


@pytest.mark.parametrize("footer", ["MILESTONE_STATUS=continue", "NEXT_OWNER=engineer"])
def test_a_round_capped_self_reviewed_mission_adds_no_reviewer_call(
    tmp_path: Path, footer: str,
) -> None:
    """``max_rounds`` already ends a self-reviewed mission, so the self-handoff
    limit must not buy a Reviewer call every third turn there."""
    backend = MemoryBackend()
    for index in (1, 2, 3, 4):
        backend.queue(f"engineer-r{index}", CannedResponse(message=f"Step {index} written.\n{footer}"))

    status, _rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="low-risk repair",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=4, require_independent_review=False),
        workdir=tmp_path,
    )

    assert _labels(backend) == ["engineer-r1", "engineer-r2", "engineer-r3", "engineer-r4"]
    assert status == "max_rounds"


class _CappedEngineer:
    """Writes work forever but never ``done``; fails loud past a call budget so
    a regression ends the test instead of hanging it."""

    backend = "stub"

    def __init__(self, budget: int = 30) -> None:
        self.calls = 0
        self.budget = budget

    def run_exec(self, **_kwargs: Any) -> RunnerResult:
        self.calls += 1
        if self.calls > self.budget:
            return RunnerResult(exit_code=1, fatal_error="test call budget exhausted",
                                stop_kind="permanent_error")
        return RunnerResult(exit_code=0, agent_messages=[f"Changed module {self.calls}."])


class _NoProgressReviewer:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, **_kwargs: Any) -> ReviewDecision:
        self.calls += 1
        return ReviewDecision(
            status="continue", reason="Same failure as before.", next_action="Fix the failing check.",
            planner_report={"forward_progress": False},
        )


def _stub_engine(engineer: Any, reviewer: Any) -> SupervisedEngineer:
    engine = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    engine.engineer_runner = engineer
    engine.engineer_config = EngineerConfig(model="stub")
    engine.reviewer = reviewer
    engine.reviewer_config = ReviewerConfig(model="stub")
    return engine


def test_an_unbounded_self_reviewed_mission_now_ends_on_the_stall_guard(tmp_path: Path) -> None:
    engineer, reviewer = _CappedEngineer(), _NoProgressReviewer()

    status, _rounds, _final, reason, _tid = _stub_engine(engineer, reviewer).run(
        objective="low-risk repair",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=0, require_independent_review=False, stall_threshold=2,
        ),
        workdir=tmp_path,
    )

    assert status == "no_progress", reason
    assert (engineer.calls, reviewer.calls) == (6, 2)


# --- 3. The rendered handoff names the owner control acts on --------------


@pytest.mark.parametrize("payload", [
    {"status": "continue", "result": "Partial fix."},
    {"status": "continue", "result": "Partial fix.", "next_owner": "engineer"},
    {"status": "continue", "result": "Need a choice.", "next_owner": "engineer",
     "operator_question": "Publish route A or B?"},
    {"status": "continue", "result": "Mis-scoped.", "next_owner": "planner"},
    {"status": "blocked", "result": "Credentials are missing."},
])
def test_a_rendered_decision_names_the_owner_the_round_loop_uses(payload: dict) -> None:
    message = _engineer_decision_message(payload)
    assert parse_engineer_handoff(message).next_owner == decision_engineer_handoff(payload).next_owner


def test_a_continue_decision_without_an_owner_renders_the_reviewer() -> None:
    message = _engineer_decision_message({"status": "continue", "result": "Partial fix."})
    assert message.splitlines()[-1] == "→ reviewer"


@pytest.mark.parametrize("status", ["done", "continue", "blocked"])
def test_a_rendered_decision_is_done_only_when_the_decision_is(status: str) -> None:
    """The Reviewer reads ``→ done`` exactly when the round loop settles done."""
    decision = {"status": status, "result": "Credentials are missing."}
    rendered = _outcome(_engineer_decision_message(decision))
    assert _milestone_is_done(rendered) is _milestone_is_done(_outcome("", decision))


# --- 4. Only this mission's healthy work excuses its stall -----------------


def _subagent(workdir: Path, task_id: str, owner: str) -> None:
    root = workdir / ".argus_subagents"
    root.mkdir(exist_ok=True)
    (root / f"{task_id}.json").write_text(json.dumps({
        "task_id": task_id, "run_id": f"{task_id}-run", "state": "running",
        "mode": "direct", "worker_pid": os.getpid(), "heartbeat_at": 100,
        "owner_mission_id": owner,
    }))


@pytest.fixture
def host_process_env(monkeypatch):
    # The host process carries no mission id of its own; only the Engineer's does.
    monkeypatch.delenv("ARGUS_PLUGIN_PARENT_MISSION_ID", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TEAM_TASK_ID", raising=False)


def test_a_neighbours_healthy_subagent_does_not_count_as_this_missions_wait(
    tmp_path: Path, host_process_env,
) -> None:
    _subagent(tmp_path, "neighbour-grid", owner="mission-b")
    assert round_settlement._blocked_on_healthy_work(tmp_path, "mission-a") is False
    assert round_settlement._blocked_on_healthy_work(tmp_path, "mission-b") is True
    # Without a mission id nothing can be attributed: unchanged behaviour.
    assert round_settlement._blocked_on_healthy_work(tmp_path) is True

    _subagent(tmp_path, "own-grid", owner="mission-a")
    assert round_settlement._blocked_on_healthy_work(tmp_path, "mission-a") is True


def test_an_unattributed_healthy_subagent_still_counts(tmp_path: Path, host_process_env) -> None:
    _subagent(tmp_path, "legacy-grid", owner="")
    assert round_settlement._blocked_on_healthy_work(tmp_path, "mission-a") is True


def test_the_stall_guard_fires_while_only_a_neighbour_is_busy(tmp_path: Path, host_process_env) -> None:
    _subagent(tmp_path, "neighbour-grid", owner="mission-b")
    engineer, reviewer = _CappedEngineer(), _NoProgressReviewer()

    status, _rounds, _final, reason, _tid = _stub_engine(engineer, reviewer).run(
        objective="repair the failing check",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=6, stall_threshold=2, session_id="mission-a"),
        workdir=tmp_path,
    )

    assert status == "no_progress", reason
    assert (engineer.calls, reviewer.calls) == (2, 2)
