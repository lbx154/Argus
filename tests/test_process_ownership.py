from __future__ import annotations

import json

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus_skill.reviewer import Reviewer, ReviewerConfig


def test_runner_process_ownership_fact_reaches_reviewer(tmp_path) -> None:
    backend = MemoryBackend()
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message="Completed the bounded work.",
            orphan_process_group_id=4242,
            orphan_process_group_cleanup_succeeded=True,
        ),
    )
    backend.queue(
        "reviewer",
        CannedResponse(message=json.dumps({
            "status": "done",
            "reason": "The result is complete.",
            "next_action": "",
        })),
    )
    engineer = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )
    events: list[dict] = []

    status, _rounds, _final, _reason, _thread = engineer.run(
        objective="complete work",
        engineer_prompt_builder=lambda _next, _static=True: "Do the work.",
        supervised_config=SupervisedConfig(max_rounds=1),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "done"
    reviewer_prompt = next(
        prompt for label, prompt, _options in backend.history
        if label == "reviewer"
    )
    assert "private process group 4242" in reviewer_prompt
    assert "command" not in next(
        event["text"] for event in events
        if event.get("type") == "round.orphan_process_group"
    ).lower()
