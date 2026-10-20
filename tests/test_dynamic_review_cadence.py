"""Reviewer cadence follows the explicit independent-review contract."""
from __future__ import annotations

from pathlib import Path

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
    parse_continue_work_request,
)
from argus.reviewer import Reviewer, ReviewerConfig


def _done_review() -> tuple[str, dict]:
    return "submit_review", {"review": "reviewed\n→ done"}


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


def test_continue_work_text_does_not_skip_reviewer(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message=(
                "## Verification (verbatim)\n```\n1 passed\n```\n"
                "CONTINUE_WORK: wire it into the runner"
            ),
            thread_id="t1",
        ),
    )
    backend.queue("reviewer", CannedResponse(review_action=_done_review(), thread_id="v1"))

    events: list[dict] = []
    status, rounds, _final, _reason, tid = _engineer(backend).run(
        objective="always review",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=2,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    labels = [label for label, _prompt, _options in backend.history]
    assert labels[:2] == ["engineer-r1", "reviewer"]
    assert not any(event["type"] == "round.review.deferred" for event in events)
    assert status == "done"
    assert len(rounds) == 1
    assert tid is None
    assert [
        resume for label, resume in backend.resume_history
        if label in {"engineer-r1", "reviewer"}
    ] == [None, None]


def test_low_risk_task_can_finish_with_engineer_self_review(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message=(
                "Implemented the bounded fix.\n## Verification\n3 tests passed\n"
                "`→ done`"
            ),
            thread_id="t1",
        ),
    )

    events: list[dict] = []
    status, rounds, _final, reason, tid = _engineer(backend).run(
        objective="low-risk repair with decisive tests",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=1,
            require_independent_review=False,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert [label for label, _prompt, _options in backend.history] == ["engineer-r1"]
    assert status == "done"
    assert len(rounds) == 1
    assert rounds[0].review.review_source == "engineer_self_review"
    review_events = [event for event in events if event["type"] == "round.review.completed"]
    assert review_events[0]["review_source"] == "engineer_self_review"
    assert reason == "Implemented the bounded fix.\n## Verification\n3 tests passed"
    assert tid is None


def test_engineer_continues_milestone_without_reviewer(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message="Captured the signal.\n→ engineer",
            thread_id="t1",
        ),
    )
    backend.queue(
        "engineer-r2",
        CannedResponse(
            message="Made the keep/reject decision.\n→ done",
            thread_id="t2",
        ),
    )

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="complete one decision-sized milestone",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=2,
            require_independent_review=False,
        ),
        workdir=tmp_path,
    )

    assert status == "done"
    assert [label for label, _prompt, _options in backend.history] == [
        "engineer-r1",
        "engineer-r2",
    ]
    assert len(rounds) == 1


def test_engineer_operator_question_parks_without_reviewer(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message=(
                "The required choice belongs to the operator.\n"
                "→ operator: Is publishing route A or B acceptable?"
            ),
            thread_id="t1",
        ),
    )

    events: list[dict] = []
    status, rounds, _final, reason, _tid = _engineer(backend).run(
        objective="write the operator-selected value",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=3,
            require_independent_review=True,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert [label for label, _prompt, _options in backend.history] == ["engineer-r1"]
    assert status == "blocked"
    assert len(rounds) == 1
    assert rounds[0].review.review_source == "engineer_handoff"
    assert rounds[0].review.operator_question == "Is publishing route A or B acceptable?"
    assert reason == "The required choice belongs to the operator."
    review_events = [event for event in events if event["type"] == "round.review.completed"]
    assert review_events[0]["operator_question"] == (
        "Is publishing route A or B acceptable?"
    )


def test_a_question_nobody_may_be_asked_parks_for_the_manager(tmp_path: Path) -> None:
    """The Engineer named the operator; with none to ask, the Manager decides the card."""
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message="Git is not available in the current shell.\n→ operator: Can you provide Git?",
            thread_id="t1",
        ),
    )

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="complete and review the work",
        engineer_prompt_builder=lambda next_action, _include_static=True: (
            next_action or "Do the task."
        ),
        supervised_config=SupervisedConfig(
            max_rounds=3,
            operator_questions_allowed=False,
        ),
        workdir=tmp_path,
    )

    assert status == "blocked"
    assert [label for label, _prompt, _options in backend.history] == ["engineer-r1"]
    assert [record.review.operator_question for record in rounds] == ["Can you provide Git?"]


def test_a_reviewer_question_nobody_may_be_asked_parks_with_its_telemetry(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(message="Prepared the change.\n→ reviewer", thread_id="t1"),
    )
    backend.queue(
        "reviewer",
        CannedResponse(
            review_action=("submit_review", {
                "review": "The environment needs repair.\n→ operator: Can you repair the environment?",
            }),
            input_tokens=17,
            output_tokens=9,
            thread_id="v1",
        ),
    )

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="complete and review the work",
        engineer_prompt_builder=lambda next_action, _include_static=True: (
            next_action or "Do the task."
        ),
        supervised_config=SupervisedConfig(
            max_rounds=3,
            operator_questions_allowed=False,
        ),
        workdir=tmp_path,
    )

    assert status == "blocked"
    kept = rounds[0].review
    assert (kept.status, kept.operator_question, kept.interventions) == (
        "blocked", "Can you repair the environment?", [],
    )
    assert (kept.input_tokens, kept.output_tokens) == (17, 9)
    assert [label for label, _prompt, _options in backend.history] == ["engineer-r1", "reviewer"]


def test_without_review_the_engineers_manager_handoff_settles_immediately(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(message="Only unavailable credentials remain; state preserved.\n→ manager"),
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


def test_structured_reviewer_handoff_is_authoritative_over_summary_vocabulary(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message=(
                "Should the operator authorize the budget and external publication "
                "before review?\n"
                "→ reviewer"
            ),
            thread_id="t1",
        ),
    )
    backend.queue("reviewer", CannedResponse(review_action=_done_review(), thread_id="v1"))

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="publish an artifact",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=2, require_independent_review=True),
        workdir=tmp_path,
    )

    assert [label for label, _prompt, _options in backend.history] == [
        "engineer-r1",
        "reviewer",
    ]
    assert status == "done"
    assert rounds[0].review.review_source == "reviewer"
    assert rounds[0].review.operator_question == ""


def test_structured_engineer_handoff_continues_without_early_review(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message=(
                "Completed the first internal step.\n"
                "→ engineer"
            ),
            thread_id="t1",
        ),
    )
    backend.queue(
        "engineer-r2",
        CannedResponse(
            message=(
                "Completed the artifact.\n"
                "→ done"
            ),
            thread_id="t2",
        ),
    )
    backend.queue("reviewer", CannedResponse(review_action=_done_review(), thread_id="v1"))

    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="complete a two-step artifact",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=2, require_independent_review=True),
        workdir=tmp_path,
    )

    assert [label for label, _prompt, _options in backend.history] == [
        "engineer-r1",
        "engineer-r2",
        "reviewer",
    ]
    assert status == "done"
    assert len(rounds) == 1


def _freight_question_round(tmp_path: Path):
    """The Engineer round from the freight trial: an explicit operator
    handoff whose question mentions credentials only to say they are not
    needed."""
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message=(
                "Blocked on packet access.\n"
                "→ operator: Provide the Reviewer with original packet access "
                "and bounded local execution; production credentials are unnecessary."
            ),
            thread_id="t1",
        ),
    )
    backend.queue("reviewer", CannedResponse(review_action=_done_review(), thread_id="v1"))
    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="build the dispatch tool",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=2, require_independent_review=True),
        workdir=tmp_path,
    )
    return backend, status, rounds


@pytest.mark.parametrize("available", ["true", "false"])
def test_an_explicit_operator_handoff_parks_the_round(
    tmp_path: Path, monkeypatch, available: str,
) -> None:
    # No word in the question routes it: the line named the operator.
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", available)
    backend, status, rounds = _freight_question_round(tmp_path)
    assert [label for label, _prompt, _options in backend.history] == ["engineer-r1"]
    assert status == "blocked"
    assert rounds[0].review.review_source == "engineer_handoff"
