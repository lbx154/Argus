from __future__ import annotations

import json
from pathlib import Path

import argus_skill.reviewer._core as reviewer_core
from argus_skill.reviewer import REVIEWER_SCHEMA_PATHS, SCHEMA_PATH, parse_decision_text


def test_reviewer_schema_is_minimal_and_strict() -> None:
    schema = json.loads(Path(SCHEMA_PATH).read_text(encoding="utf-8"))

    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {
        "status",
        "reason",
        "next_action",
        "operator_question",
    }
    assert set(schema["required"]) == set(schema["properties"])


def test_reviewer_schema_stays_small() -> None:
    assert Path(SCHEMA_PATH).stat().st_size < 2_000


def test_all_reviewer_schema_assets_resolve_next_to_loaded_module() -> None:
    paths = {Path(path).name: Path(path) for path in REVIEWER_SCHEMA_PATHS}

    assert set(paths) == {
        "reviewer_schema.json",
        "reviewer_research_schema.json",
        "reviewer_legacy_research_schema.json",
    }
    for path in paths.values():
        assert path.parent == Path(reviewer_core.__file__).resolve().parent
        assert path.is_file()


def test_minimal_verdict_parses() -> None:
    decision = parse_decision_text(
        '{"status":"blocked","reason":"Need operator input.",'
        '"next_action":"","operator_question":"Which route?"}'
    )

    assert decision is not None
    assert decision.status == "blocked"
    assert decision.operator_question == "Which route?"


def test_extra_control_fields_are_rejected_by_schema() -> None:
    schema = json.loads(Path(SCHEMA_PATH).read_text(encoding="utf-8"))

    for field in (
        "scientific_decision",
        "planner_report",
        "progress_class",
        "failure_layer",
        "failure_source",
        "control",
        "checklist",
        "certification_payload",
        "checklist_feedback",
    ):
        assert field not in schema["properties"]
