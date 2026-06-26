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

from argus_skill.reviewer import SCHEMA_PATH


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
