"""The reviewer output-schema MUST be a valid OpenAI/codex strict structured-output
schema, or EVERY reviewer call fails with ``invalid_json_schema`` (exit 1) — which
on 2026-06-26 took down a whole teammate fleet (the reviewer's ``--output-schema``
was rejected by the API: ``skill_ops.items`` listed only ``["op"]`` in ``required``
and the root ``required`` omitted ``skill_ops``).

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

from argus_skill.reviewer import RESEARCH_SCHEMA_PATH, SCHEMA_PATH


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


def test_skill_ops_items_require_all_keys():
    # Pin the exact spot that broke on 2026-06-26 so a revert is caught by name.
    schema = json.loads(Path(SCHEMA_PATH).read_text(encoding="utf-8"))
    items = schema["properties"]["skill_ops"]["items"]
    assert set(items["required"]) == set(items["properties"]) == {
        "op",
        "name",
        "content",
        "why",
    }
    assert "skill_ops" in schema["required"]


def test_operator_question_parsing_blocked_only():
    """blocked uses the reviewer's question (or falls back to next_action's first
    sentence); done/continue never carry one; a blocked verdict still parses with
    the field present (strict-mode required + nullable)."""
    from argus_skill.reviewer._parsing import parse_decision_text

    common = ('"round_summary_markdown":"x","completion_summary_markdown":"",'
              '"failure_cause":"","scope":"",'
              '"planner_report":{},"checklist":[],"checkpoint":{},"skill_ops":[],'
              '"checklist_feedback":null,"step_back":null')
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
        "round_summary_markdown": "# Review\n",
        "completion_summary_markdown": "",
    })

    assert parse_decision_text(payload) is None
    decision = parse_decision_text(payload, allow_research_pause=True)
    assert decision is not None
    assert decision.status == "research_incomplete"
