"""Reviewer cadence follows the explicit independent-review contract."""
from __future__ import annotations

from pathlib import Path

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.engineer.round_routing import engineer_actions
from argus.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
    parse_continue_work_request,
)
from argus.reviewer import Reviewer, ReviewerConfig
from tests.hand_off import hand_off


def _done_review() -> tuple[str, dict]:
    return hand_off("done", "reviewed")


def _engineer(backend: MemoryBackend) -> SupervisedEngineer:
    return SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )


def test_legacy_continue_work_parser_remains_compatible() -> None:
    assert parse_continue_work_request(
        "Changed the parser.\nCONTINUE_WORK: wire it into the runner"
    ) == "wire it into the runner"
    assert parse_continue_work_request("CONTINUE_WORK: wire it in") is None


def test_a_question_nobody_may_be_asked_is_refused_to_the_engineer() -> None:
    """With no one to ask, the Engineer decides the reading itself or hands the work to the Manager."""
    actions = engineer_actions(SupervisedConfig(operator_questions_allowed=False))
    with pytest.raises(ValueError, match="cannot be asked"):
        actions.dispatch(*hand_off("operator", "Git is missing.", ask={"question": "Can you provide Git?", "options": []}))


def test_without_review_the_engineers_manager_handoff_settles_immediately(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(action=hand_off("manager", "Only unavailable credentials remain; state preserved.")),
    )

    status, rounds, _final, reason, _tid = _engineer(backend).run(
        objective="perform credential-gated work",
        engineer_prompt_builder=lambda _next_action, _include_static=True: "Do it.",
        supervised_config=SupervisedConfig(
            max_rounds=4,
            require_independent_review=False,
        ),
        workdir=tmp_path,
    )

    assert status == "blocked"
    assert [label for label, _prompt, _options in backend.history] == ["engineer-r1"]
    assert len(rounds) == 1
    assert rounds[0].review.operator_question == ""
    assert reason == "Only unavailable credentials remain; state preserved."


def _freight_question_round(tmp_path: Path):
    """The Engineer round from the freight trial: an explicit operator
    handoff whose question mentions credentials only to say they are not
    needed."""
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(action=hand_off("operator", "Blocked on packet access.", ask={
            "question": "Provide the Reviewer with original packet access and bounded local "
                        "execution; production credentials are unnecessary.",
            "options": [],
        }), thread_id="t1"),
    )
    backend.queue("reviewer", CannedResponse(action=_done_review(), thread_id="v1"))
    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="build the dispatch tool",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=2, require_independent_review=True),
        workdir=tmp_path,
    )
    return backend, status, rounds


def test_an_explicit_operator_handoff_parks_the_round(tmp_path: Path) -> None:
    # No word in the question routes it: the handoff named the operator.
    backend, status, rounds = _freight_question_round(tmp_path)
    assert [label for label, _prompt, _options in backend.history] == ["engineer-r1"]
    assert status == "blocked"
    assert rounds[0].review.review_source == "engineer_handoff"
