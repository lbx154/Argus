"""Bounded, engineer-requested continuation before an intermediate review."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
    parse_continue_work_request,
)
from argus_skill.reviewer import Reviewer, ReviewerConfig


def _done_review() -> str:
    return json.dumps({
        "status": "done",
        "reason": "The accumulated work meets the task.",
        "next_action": "",
        "round_summary_markdown": "# done\n",
        "completion_summary_markdown": "Done.",
    })


def _work(next_step: str) -> str:
    return (
        "## Verification (verbatim)\n```\n1 passed\n```\n\n"
        "## Summary\n- Landed a concrete increment.\n"
        f"CONTINUE_WORK: {next_step}"
    )


def _engineer(backend: MemoryBackend) -> SupervisedEngineer:
    return SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )


def test_parse_continue_work_request_requires_substantive_final_line() -> None:
    assert parse_continue_work_request(
        "Changed the parser.\nCONTINUE_WORK: wire it into the runner"
    ) == "wire it into the runner"
    assert parse_continue_work_request("CONTINUE_WORK: wire it in") is None
    assert parse_continue_work_request(
        "CONTINUE_WORK: wire it in\nBut first I should ask for review."
    ) is None
    assert parse_continue_work_request(
        "Changed the parser.\n`CONTINUE_WORK: wire it into the runner`"
    ) == "wire it into the runner"


def test_engineer_can_defer_one_intermediate_review(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(message=_work("wire the parser into the runner"), thread_id="t1"),
    )
    backend.queue(
        "engineer-r2",
        CannedResponse(message="Wired it in and ran the tests.", thread_id="t1"),
    )
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    events: list[dict] = []
    status, rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="add dynamic review cadence",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=3),
        workdir=tmp_path,
        on_event=events.append,
    )

    labels = [label for label, _prompt, _options in backend.history]
    assert labels[:3] == ["engineer-r1", "engineer-r2", "reviewer"]
    round_two_prompt = next(
        prompt for label, prompt, _options in backend.history
        if label == "engineer-r2"
    )
    assert "## Engineer-selected next step" in round_two_prompt
    assert "wire the parser into the runner" in round_two_prompt
    assert [event["type"] for event in events].count("round.review.deferred") == 1
    assert status == "done"
    assert len(rounds) == 1


def test_consecutive_deferral_is_forced_back_to_reviewer(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(message=_work("implement the runner path"), thread_id="t1"),
    )
    backend.queue(
        "engineer-r2",
        CannedResponse(message=_work("keep changing unrelated pieces"), thread_id="t1"),
    )
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    events: list[dict] = []
    status, _rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="add dynamic review cadence",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=3,
            review_deferral_limit=99,
        ),
        workdir=tmp_path,
        on_event=events.append,
    )

    labels = [label for label, _prompt, _options in backend.history]
    assert labels[:3] == ["engineer-r1", "engineer-r2", "reviewer"]
    assert [event["type"] for event in events].count("round.review.deferred") == 1
    assert status == "done"


def test_failed_engineer_turn_cannot_defer_review(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message=_work("pretend the failed turn succeeded"),
            exit_code=1,
            thread_id="t1",
        ),
    )
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    events: list[dict] = []
    status, _rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="review failed work",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=2),
        workdir=tmp_path,
        on_event=events.append,
    )

    labels = [label for label, _prompt, _options in backend.history]
    assert labels[:2] == ["engineer-r1", "reviewer"]
    assert not any(event["type"] == "round.review.deferred" for event in events)
    assert status == "done"


def test_selected_next_step_survives_backend_retry(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(message=_work("wire the parser into the runner"), thread_id="t1"),
    )
    backend.queue(
        "engineer-r2",
        CannedResponse(exit_code=1, fatal_error="connection reset", thread_id="t1"),
    )
    backend.queue(
        "engineer-r3",
        CannedResponse(message="Retry succeeded.", thread_id="t2"),
    )
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    status, _rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="add dynamic review cadence",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=4,
            backend_failure_backoff_seconds=0,
        ),
        workdir=tmp_path,
    )

    retry_prompt = next(
        prompt for label, prompt, _options in backend.history
        if label == "engineer-r3"
    )
    assert "wire the parser into the runner" in retry_prompt
    assert status == "done"


def test_successful_deferral_breaks_backend_failure_streak(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(exit_code=1, fatal_error="connection reset", thread_id="t1"),
    )
    backend.queue(
        "engineer-r2",
        CannedResponse(message=_work("finish the runner path"), thread_id="t2"),
    )
    backend.queue(
        "engineer-r3",
        CannedResponse(exit_code=1, fatal_error="connection reset", thread_id="t2"),
    )
    backend.queue(
        "engineer-r4",
        CannedResponse(message="Finished after retry.", thread_id="t3"),
    )
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    status, _rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="survive separated backend failures",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=5,
            backend_failure_threshold=2,
            backend_failure_backoff_seconds=0,
        ),
        workdir=tmp_path,
    )

    labels = [label for label, _prompt, _options in backend.history]
    assert labels[:5] == [
        "engineer-r1",
        "engineer-r2",
        "engineer-r3",
        "engineer-r4",
        "reviewer",
    ]
    assert status == "done"


def test_final_round_cannot_defer_review(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(message=_work("take another turn"), thread_id="t1"),
    )
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    events: list[dict] = []
    status, _rounds, _final, _reason, _tid = _engineer(backend).run(
        objective="finish within one round",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=1),
        workdir=tmp_path,
        on_event=events.append,
    )

    labels = [label for label, _prompt, _options in backend.history]
    assert labels[:2] == ["engineer-r1", "reviewer"]
    assert not any(event["type"] == "round.review.deferred" for event in events)
    assert status == "done"
