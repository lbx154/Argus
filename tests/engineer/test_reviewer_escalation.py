"""A Reviewer that cannot judge hands the work to the Manager, not back to the Engineer (HO-3).

Access or a tool neither the Reviewer nor the Engineer has is not the
Engineer's to fix. Handed back, the Engineer ran again with nothing it could
change: 17 of 59 ``continue`` verdicts in two instances were this. With
``to=manager`` the round loop ends, no Engineer turn follows, and the Manager
is told the Reviewer handed it the work.
"""
from __future__ import annotations

from pathlib import Path

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.event_catalog import EventType
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.manager.supervision import _review_consult_reason
from argus.reviewer import Reviewer, ReviewerConfig
from tests.hand_off import hand_off

_BLOCKER = (
    "results/metrics.csv sits outside the sandbox for both of us, so the reported "
    "accuracy cannot be checked; neither the Engineer nor I can grant that access."
)


def test_a_blocked_reviewer_hands_the_work_to_the_manager(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(action=hand_off("reviewer", "Wrote the metrics.")))
    backend.queue("reviewer", CannedResponse(action=hand_off("manager", _BLOCKER)))
    # Queued only to be left unused: the Engineer must not run again.
    backend.queue("engineer-r2", CannedResponse(action=hand_off("reviewer", "Nothing I can change.")))
    events: list[dict] = []

    status, records, _final, reason, _thread = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    ).run(
        objective="Report the model's accuracy.",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(),
        workdir=tmp_path,
        on_event=events.append,
    )

    labels = [label for label, _prompt, _options in backend.history]
    assert labels == ["engineer-r1", "reviewer"]
    assert status == "manager"
    assert len(records) == 1
    assert _BLOCKER in reason
    completed = [event for event in events if event.get("type") == EventType.ROUND_REVIEW_COMPLETED]
    assert [(event["status"], event["review_source"]) for event in completed] == [("manager", "reviewer")]
    assert _review_consult_reason(tmp_path, completed[0]).startswith(
        "the Reviewer handed the work to you"
    )
