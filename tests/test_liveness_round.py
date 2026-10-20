"""Round-loop liveness: the baton goes where the roles send it.

Each test pins one confirmed defect where a role's own word was misread, or
where the text a role reads disagreed with the decision the host took.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.models import RunnerResult
from argus.core.role_handoff import decision_engineer_handoff, parse_engineer_handoff
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


# --- 1. Self-review mode: the Engineer finishes, footers read tolerantly ---


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
def test_unfinished_self_reviewed_turns_go_on_until_the_engineer_says_done(
    tmp_path: Path, footer: str,
) -> None:
    backend = MemoryBackend()
    for index in (1, 2, 3):
        backend.queue(f"engineer-r{index}", CannedResponse(message=f"Step {index} written.\n{footer}"))
    backend.queue("engineer-r4", CannedResponse(message="All steps hold.\nMILESTONE_STATUS=done"))

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="low-risk repair",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=0, require_independent_review=False),
        workdir=tmp_path,
    )

    assert _labels(backend) == ["engineer-r1", "engineer-r2", "engineer-r3", "engineer-r4"]
    assert status == "done"
    assert [record.review.review_source for record in rounds] == ["engineer_self_review"]


@pytest.mark.parametrize("footer", ["MILESTONE_STATUS=continue", "NEXT_OWNER=engineer"])
def test_a_round_capped_self_reviewed_mission_adds_no_reviewer_call(
    tmp_path: Path, footer: str,
) -> None:
    """An explicit ``max_rounds`` is the operator's budget; it ends a
    self-reviewed mission without buying a Reviewer call."""
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



