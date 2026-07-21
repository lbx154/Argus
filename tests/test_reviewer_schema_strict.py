"""The reviewer output-schema MUST be a valid OpenAI/codex strict structured-output
schema, or every reviewer call fails with ``invalid_json_schema`` (exit 1).

The strict-mode contract the API enforces: for EVERY object that declares
``properties``, the ``required`` array must list EVERY property key, and
``additionalProperties`` must be ``false``. Optional fields are expressed by making
the type nullable (``["string", "null"]``), NOT by omitting them from ``required``.

This test walks the whole schema and fails on any object that violates that — so a
future edit (or a revert) that re-breaks it is caught offline, before it reaches the
model and silently/loudly kills missions.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.reviewer import (
    LEGACY_RESEARCH_SCHEMA_PATH,
    RESEARCH_SCHEMA_PATH,
    SCHEMA_PATH,
)


def _strict_violations(node, path="root"):
    """Yield (json_path, problem) for every strict-mode violation in the schema."""
    if isinstance(node, dict):
        if isinstance(node.get("properties"), dict):
            props = set(node["properties"])
            required = set(node.get("required", []) or [])
            missing = props - required
            if missing:
                yield (path, f"required missing keys: {sorted(missing)}")
            if node.get("additionalProperties", True) is not False:
                yield (path, "additionalProperties must be false")
        for key, value in node.items():
            yield from _strict_violations(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _strict_violations(value, f"{path}[{i}]")


def test_reviewer_schema_is_strict_structured_output_compliant():
    schema = json.loads(Path(SCHEMA_PATH).read_text(encoding="utf-8"))
    violations = list(_strict_violations(schema))
    assert not violations, (
        "reviewer_schema.json is NOT valid for OpenAI strict structured output — "
        "the codex reviewer call will be rejected with invalid_json_schema and every "
        "reviewer round will exit 1:\n  "
        + "\n  ".join(f"{p}: {msg}" for p, msg in violations)
    )


def test_research_reviewer_schema_is_strict_and_isolated() -> None:
    base = json.loads(Path(SCHEMA_PATH).read_text(encoding="utf-8"))
    research = json.loads(Path(RESEARCH_SCHEMA_PATH).read_text(encoding="utf-8"))

    assert "research_result" not in base["properties"]
    assert "research_result" not in base["required"]
    assert "research_result" in research["properties"]
    assert "research_result" in research["required"]
    assert not list(_strict_violations(research))


def test_legacy_research_schema_remains_compatible_with_old_daemons() -> None:
    legacy = json.loads(
        Path(LEGACY_RESEARCH_SCHEMA_PATH).read_text(encoding="utf-8")
    )

    assert "research_result" not in legacy["properties"]
    assert "math_result" in legacy["properties"]
    assert "math_result" in legacy["required"]
    assert set(legacy["properties"]["math_result"]["required"]) == {
        "result_class",
        "correctness",
        "novelty",
        "statement_fidelity",
        "evidence",
        "limitations",
    }
    assert not list(_strict_violations(legacy))


def test_reviewer_schemas_stay_token_efficient() -> None:
    assert Path(SCHEMA_PATH).stat().st_size < 11_500
    assert Path(RESEARCH_SCHEMA_PATH).stat().st_size < 14_000
    assert Path(LEGACY_RESEARCH_SCHEMA_PATH).stat().st_size < 13_000


def test_reviewer_schema_has_no_memory_proposal_fields() -> None:
    schema = json.loads(Path(SCHEMA_PATH).read_text(encoding="utf-8"))
    assert "skill_ops" not in schema["properties"]
    assert "wiki_ops" not in schema["properties"]
    assert "skill_ops" not in schema["required"]
    assert "wiki_ops" not in schema["required"]


def test_reviewer_schema_has_one_to_one_handoff_fields() -> None:
    for schema_path in (SCHEMA_PATH, RESEARCH_SCHEMA_PATH):
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        for duplicate in (
            "round_summary_markdown",
            "completion_summary_markdown",
            "step_back",
        ):
            assert duplicate not in schema["properties"]
            assert duplicate not in schema["required"]
        report = schema["properties"]["planner_report"]
        assert set(report["properties"]) == {
            "forward_progress",
            "plan_signal",
            "evidence_files",
        }
        assert set(report["required"]) == set(report["properties"])


def test_active_schemas_expose_framework_certification_payload() -> None:
    for schema_path in (SCHEMA_PATH, RESEARCH_SCHEMA_PATH):
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        cert = schema["properties"]["certification_payload"]
        assert "certification_payload" in schema["required"]
        assert cert["type"] == ["object", "null"]
        review = cert["properties"]["review"]
        assert set(review["required"]) == set(review["properties"])
        assert review["properties"]["status"]["enum"] == ["done"]
        assert review["properties"]["scope"]["enum"] == [
            "bounded",
            "final_submission",
        ]
        item = review["properties"]["checklist"]["items"]
        assert set(item["required"]) == {
            "checklist_id",
            "verdict",
            "evidence_refs",
            "reviewer_notes",
        }
        assert item["properties"]["verdict"]["enum"] == [
            "supported",
            "not_applicable",
        ]


def test_control_object_requires_all_keys_in_active_schemas() -> None:
    for schema_path in (SCHEMA_PATH, RESEARCH_SCHEMA_PATH):
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        control = schema["properties"]["control"]
        assert set(control["required"]) == set(control["properties"]) == {
            "action",
            "task_id",
        }
        assert control["additionalProperties"] is False
        assert "control" in schema["required"]


def test_failure_source_is_strict_evidence_backed_and_required_nullable() -> None:
    for schema_path in (SCHEMA_PATH, RESEARCH_SCHEMA_PATH):
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        source = schema["properties"]["failure_source"]
        assert source["additionalProperties"] is False
        assert set(source["required"]) == {
            "kind", "validator_id", "repair_paths", "evidence",
        }
        assert "failure_source" in schema["required"]
        assert "scientific_decision" in schema["required"]


def test_operator_question_parsing_blocked_only():
    """blocked uses the reviewer's question (or falls back to next_action's first
    sentence); done/continue never carry one; a blocked verdict still parses with
    the field present (strict-mode required + nullable)."""
    from argus_skill.reviewer._parsing import parse_decision_text

    common = ('"failure_cause":"","scope":"",'
              '"planner_report":{},"checklist":[],'
              '"checklist_feedback":null')
    blk = parse_decision_text(
        '{"status":"blocked","reason":"r","next_action":"n",'
        '"operator_question":"刷哪两道题？",' + common + '}')
    assert blk is not None and blk.operator_question == "刷哪两道题？"
    fb = parse_decision_text(
        '{"status":"blocked","reason":"r","next_action":"先选路线。再跑题。",' + common + '}')
    assert fb is not None and fb.operator_question == "先选路线"
    cont = parse_decision_text(
        '{"status":"continue","reason":"r","next_action":"keep going",' + common + '}')
    assert cont is not None and cont.operator_question == ""


def test_research_pause_status_parses_only_when_targeted() -> None:
    from argus_skill.reviewer._parsing import parse_decision_text

    payload = json.dumps({
        "status": "research_incomplete",
        "reason": "No original theorem was verified.",
        "next_action": "Resume with a distinct method.",
    })

    assert parse_decision_text(payload) is None
    decision = parse_decision_text(payload, allow_research_pause=True)
    assert decision is not None
    assert decision.status == "research_incomplete"
