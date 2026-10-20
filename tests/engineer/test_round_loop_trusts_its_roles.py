"""The round loop ends only on a role's own word, never on a count of rounds.

Whether work is progressing is a judgment, and it belongs to the roles: the
Engineer keeps the work until it hands it over, and the Reviewer's verdict ends
or continues the mission however many rounds that takes.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.event_catalog import EventType
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import Reviewer, ReviewerConfig


def _engineer(backend: MemoryBackend) -> SupervisedEngineer:
    return SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )


def test_an_engineer_keeps_the_work_until_it_hands_it_over(tmp_path: Path) -> None:
    backend = MemoryBackend()
    for index in range(1, 8):
        backend.queue(
            f"engineer-r{index}",
            CannedResponse(message=f"round {index}: more to do\n\nNEXT_OWNER=engineer"),
        )
    backend.queue("engineer-r8", CannedResponse(message="Finished and verified."))
    backend.queue("reviewer", CannedResponse(review_action=(
        "approve_review", {"review": "The objective is verified complete."},
    )))

    status, _rounds, _final, _reason, _thread = _engineer(backend).run(
        objective="Finish the task.",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(),
        workdir=tmp_path,
    )

    labels = [label for label, _prompt, _options in backend.history]
    assert status == "done"
    assert [label for label in labels if label.startswith("engineer-r")] == [
        f"engineer-r{index}" for index in range(1, 9)
    ]
    assert labels.count("reviewer") == 1


def test_continue_verdicts_never_end_the_mission_by_count(tmp_path: Path) -> None:
    backend = MemoryBackend()
    rounds = 30
    for index in range(1, rounds):
        backend.queue(f"engineer-r{index}", CannedResponse(message=f"round {index} changed nothing"))
        backend.queue("reviewer", CannedResponse(review_action=(
            "revise_review", {"review": "The residual did not move.\nTry another route."},
        )))
    backend.queue(f"engineer-r{rounds}", CannedResponse(message="Found the bug."))
    backend.queue("reviewer", CannedResponse(review_action=(
        "approve_review", {"review": "The residual is gone."},
    )))
    events: list[dict] = []

    status, records, _final, _reason, _thread = _engineer(backend).run(
        objective="Fix the residual.",
        engineer_prompt_builder=lambda _next, _static=True: "Fix it.",
        supervised_config=SupervisedConfig(),
        workdir=tmp_path,
        on_event=events.append,
    )

    assert status == "done"
    assert len(records) == rounds
    assert not [event for event in events if event.get("type") == EventType.ROUND_STALL]


def test_a_supervised_review_names_the_ledger_its_periodic_look_reads(tmp_path: Path) -> None:
    packet = tmp_path / "handoffs" / "m1" / "context.json"
    packet.parent.mkdir(parents=True)
    packet.write_text(json.dumps({"mission_id": "m1"}), encoding="utf-8")
    seen: dict[bool, list[dict]] = {}
    for supervised in (True, False):
        backend = MemoryBackend()
        backend.queue("engineer-r1", CannedResponse(message="Done."))
        backend.queue("reviewer", CannedResponse(review_action=(
            "approve_review", {"review": "Verified."},
        )))
        events: list[dict] = []
        _engineer(backend).run(
            objective="Finish the task.",
            engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
            supervised_config=SupervisedConfig(
                manager_supervised=supervised, context_packet_path=str(packet),
            ),
            workdir=tmp_path,
            on_event=events.append,
        )
        seen[supervised] = [
            event for event in events if event.get("type") == EventType.ROUND_REVIEW_COMPLETED
        ]

    assert seen[True][0]["holder_ledger"].endswith("lineage-m1/holders.json")
    assert "holder_ledger" not in seen[False][0]
