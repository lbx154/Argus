"""A Reviewer that cannot judge hands the work to the Manager, not back to the Engineer (HO-3).

A file the Reviewer cannot read, a tool or access it lacks, or acceptance
criteria that contradict each other are not the Engineer's to fix. Handed back,
the Engineer ran again with nothing it could change: 17 of 59 ``continue``
verdicts in two instances were this. With `→ manager` the round loop ends,
no Engineer turn follows, and the Manager is told the Reviewer handed it the work.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.event_catalog import EventType
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.manager.supervision import _review_consult_reason
from argus.reviewer import Reviewer, ReviewerConfig
from argus.reviewer.tools import HANDOFF_LINE

_BLOCKERS = {
    "unreadable_file": (
        "results/metrics.csv cannot be read here: permission denied for my session, "
        "so the reported accuracy cannot be checked. The Engineer cannot grant my access."
    ),
    "contradictory_criteria": (
        "The criteria require both keeping the public API unchanged and renaming "
        "parse() to read(); no candidate can meet both, and choosing is not the Engineer's."
    ),
}


@pytest.mark.parametrize("blocker", sorted(_BLOCKERS))
def test_a_blocked_reviewer_hands_the_work_to_the_manager(tmp_path: Path, blocker: str) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="Wrote the metrics.\n→ reviewer"))
    backend.queue("reviewer", CannedResponse(review_action=(
        "submit_review", {"review": f"{_BLOCKERS[blocker]}\n→ manager"},
    )))
    # Queued only to be left unused: the Engineer must not run again.
    backend.queue("engineer-r2", CannedResponse(message="Nothing I can change.\n→ reviewer"))
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
    assert status == "blocked"
    assert len(records) == 1
    assert _BLOCKERS[blocker] in reason
    completed = [event for event in events if event.get("type") == EventType.ROUND_REVIEW_COMPLETED]
    assert [(event["status"], event["review_source"]) for event in completed] == [("blocked", "reviewer")]
    assert _review_consult_reason(tmp_path, completed[0]).startswith(
        "the Reviewer handed the work to you: "
    )


def test_the_reviewer_is_told_what_goes_to_the_manager() -> None:
    prompt = Reviewer(runner=None, skill_store=None)._build_static_preamble(
        objective="Report the model's accuracy.",
        operator_messages=[], planner_review_instruction="", round_index=1,
        session_id=None, main_summary="Wrote the metrics.", main_error=None,
        prior_checkpoint={},
    )
    for text in (prompt, HANDOFF_LINE):
        rule = " ".join(text.split())
        assert "`→ manager` for an obstacle the Engineer cannot remove" in rule or (
            "`→ manager` an obstacle the Engineer cannot remove" in rule
        )
        assert "a file you cannot read, a tool or access you lack" in rule
    assert "criteria that contradict each other" in " ".join(prompt.split())
