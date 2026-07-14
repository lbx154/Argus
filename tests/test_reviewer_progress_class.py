from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.reviewer import RESEARCH_SCHEMA_PATH, SCHEMA_PATH, Reviewer
from argus_skill.reviewer._parsing import parse_decision_text

PROGRESS_CLASSES = {
    "decision",
    "evidence",
    "setup_only",
    "artifact_sync_only",
    "none",
}


def _payload(*, progress_class: str | None, forward_progress: bool) -> str:
    value = {
        "status": "continue",
        "reason": "bounded increment reviewed",
        "next_action": "continue the mission",
        "round_summary_markdown": "# Review\n",
        "completion_summary_markdown": "",
        "planner_report": {
            "forward_progress": forward_progress,
            "headline": "bounded increment",
            "blocker": "",
            "recommended_next": "continue",
            "evidence_files": [],
        },
    }
    if progress_class is not None:
        value["progress_class"] = progress_class
    return json.dumps(value)


@pytest.mark.parametrize("progress_class", sorted(PROGRESS_CLASSES))
def test_parse_decision_preserves_progress_class(progress_class: str) -> None:
    decision = parse_decision_text(
        _payload(progress_class=progress_class, forward_progress=True)
    )

    assert decision is not None
    assert decision.progress_class == progress_class
    assert decision.to_event_payload()["progress_class"] == progress_class


@pytest.mark.parametrize(
    ("forward_progress", "expected"),
    [(True, "evidence"), (False, "none")],
)
def test_legacy_verdict_derives_progress_class_from_forward_progress(
    forward_progress: bool,
    expected: str,
) -> None:
    decision = parse_decision_text(
        _payload(progress_class=None, forward_progress=forward_progress)
    )

    assert decision is not None
    assert decision.progress_class == expected


def test_reviewer_schemas_require_only_the_compact_progress_enum() -> None:
    for path in (Path(SCHEMA_PATH), Path(RESEARCH_SCHEMA_PATH)):
        schema = json.loads(path.read_text(encoding="utf-8"))
        progress = schema["properties"]["progress_class"]
        assert set(progress["enum"]) == PROGRESS_CLASSES
        assert "progress_class" in schema["required"]


def test_reviewer_prompt_defines_progress_class_without_new_narrative() -> None:
    reviewer = Reviewer(runner=None, skill_store=None)
    prompt = reviewer._build_prompt(
        objective="Find one real verified instance.",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="Scaffold written; no search executed.",
        main_error=None,
        prior_checkpoint={},
    )

    assert "`progress_class`" in prompt
    for value in PROGRESS_CLASSES:
        assert f"`{value}`" in prompt
    assert "Do not add a separate explanation" in prompt
