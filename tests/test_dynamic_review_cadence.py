"""Every Engineer turn is reviewed; the old deferral sentinel is inert."""
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
        "reason": "reviewed",
        "next_action": "",
        "round_summary_markdown": "# done\n",
        "completion_summary_markdown": "Done.",
    })


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
    backend.queue("reviewer", CannedResponse(message=_done_review(), thread_id="v1"))

    events: list[dict] = []
    status, rounds, _final, _reason, tid = _engineer(backend).run(
        objective="always review",
        engineer_prompt_builder=lambda _na, _include_static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=2,
            review_deferral_limit=99,
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
