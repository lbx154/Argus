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
_UNSET = object()


def _payload(
    *,
    progress_class: str | None,
    forward_progress: bool,
    control: object = _UNSET,
) -> str:
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
    if control is not _UNSET:
        value["control"] = control
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


@pytest.mark.parametrize("progress_class", ["", "bogus"])
def test_explicit_invalid_progress_class_is_none(progress_class: str) -> None:
    decision = parse_decision_text(
        _payload(progress_class=progress_class, forward_progress=True)
    )

    assert decision is not None
    assert decision.progress_class == "none"


def test_parse_decision_reads_structured_wait_control() -> None:
    decision = parse_decision_text(
        _payload(
            progress_class="none",
            forward_progress=False,
            control={"action": "wait_for_subagent", "task_id": "train-1"},
        )
    )

    assert decision is not None
    assert decision.control_action == "wait_for_subagent"
    assert decision.control_task_id == "train-1"


@pytest.mark.parametrize(
    "control",
    [
        None,
        {},
        {"action": "wait_for_subagent"},
        {"task_id": "train-1"},
        {"action": "wait_for_subagent", "task_id": ""},
        {"action": "other", "task_id": "train-1"},
        "WAIT_FOR_SUBAGENT: train-1",
    ],
)
def test_invalid_or_missing_control_drops_wait_request(control: object) -> None:
    decision = parse_decision_text(
        _payload(
            progress_class="none",
            forward_progress=False,
            control=control,
        )
    )

    assert decision is not None
    assert decision.control_action == ""
    assert decision.control_task_id == ""


def test_parse_failure_source_requires_structured_evidence() -> None:
    payload = json.loads(_payload(progress_class="none", forward_progress=False))
    payload["failure_source"] = {
        "kind": "validator_defect",
        "validator_id": "terminal-contract",
        "repair_paths": ["tests/test_terminal_contract.py"],
        "evidence": [{
            "artifact": "tests/test_terminal_contract.py",
            "observation": "historical hash was compared with the current file",
        }],
    }
    payload["scientific_decision"] = "stop"

    decision = parse_decision_text(json.dumps(payload))

    assert decision is not None
    assert decision.failure_source == "validator_defect"
    assert decision.validator_id == "terminal-contract"
    assert decision.repair_paths == ["tests/test_terminal_contract.py"]
    assert decision.failure_source_evidence[0]["artifact"].startswith("tests/")
    assert decision.scientific_decision == "stop"
    event = decision.to_event_payload()
    assert event["failure_source"] == "validator_defect"
    assert event["scientific_decision"] == "stop"


def test_validator_defect_without_evidence_fails_closed() -> None:
    payload = json.loads(_payload(progress_class="none", forward_progress=False))
    payload["failure_source"] = {
        "kind": "validator_defect",
        "validator_id": "terminal-contract",
        "repair_paths": [],
        "evidence": [],
    }

    decision = parse_decision_text(json.dumps(payload))

    assert decision is not None
    assert decision.failure_source == ""
    assert decision.validator_id == ""
    assert decision.repair_paths == []


def test_reviewer_schemas_require_only_the_compact_progress_enum() -> None:
    for path in (Path(SCHEMA_PATH), Path(RESEARCH_SCHEMA_PATH)):
        schema = json.loads(path.read_text(encoding="utf-8"))
        progress = schema["properties"]["progress_class"]
        assert set(progress["enum"]) == PROGRESS_CLASSES
        assert "progress_class" in schema["required"]


def test_reviewer_schemas_define_structured_wait_control() -> None:
    for path in (Path(SCHEMA_PATH), Path(RESEARCH_SCHEMA_PATH)):
        schema = json.loads(path.read_text(encoding="utf-8"))
        control = schema["properties"]["control"]
        assert "control" in schema["required"]
        assert control["additionalProperties"] is False
        assert set(control["required"]) == {"action", "task_id"}
        assert set(control["properties"]["action"]["enum"]) == {
            "wait_for_subagent",
            None,
        }


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


def test_reviewer_prompt_requires_structured_wait_control_not_prose() -> None:
    reviewer = Reviewer(runner=None, skill_store=None)
    prompt = reviewer._build_prompt(
        objective="Wait for the supervised run only when nothing else is actionable.",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="The engineer only re-polled train-1 this round.",
        main_error=None,
        prior_checkpoint={},
        background_context=(
            "## Background subagents in flight\n"
            "Self-watched and healthy (do NOT spend a round polling these):\n"
            "- `train-1`: state=running, health=healthy.\n"
        ),
    )

    assert "`control`" in prompt
    assert "`wait_for_subagent`" in prompt
    assert "Do NOT encode this wait in prose" in prompt
