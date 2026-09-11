"""Synthetic fixtures checked against Pi 0.85.1's actual conversion helpers."""
from __future__ import annotations

import copy

import pytest

from argus_skill.trial.training_schema import pi_execution_arguments, pi_strict_schema


@pytest.fixture
def bash_schema():
    # Actual createBashTool().parameters and makeStrictJsonSchema observations.
    return {
        "type": "object", "properties": {
            "command": {"type": "string", "description": "Bash command to execute"},
            "timeout": {"type": "number", "description": "Timeout in seconds (optional, no default timeout)"},
        }, "required": ["command"],
    }


def test_actual_bash_strict_schema_and_execution_arguments(bash_schema):
    before = copy.deepcopy(bash_schema)
    actual = pi_strict_schema(bash_schema)
    assert actual == {
        "type": "object", "properties": {
            "command": before["properties"]["command"],
            "timeout": {"anyOf": [before["properties"]["timeout"], {"type": "null"}]},
        }, "required": ["command", "timeout"], "additionalProperties": False,
    }
    model_arguments = {"command": "true", "timeout": None}
    assert pi_execution_arguments(model_arguments, bash_schema) == {"command": "true"}
    assert model_arguments == {"command": "true", "timeout": None}
    assert bash_schema == before


def test_nested_objects_arrays_and_existing_nullable_fields():
    schema = {"type": "object", "properties": {
        "rows": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "count": {"type": "integer"},
            "comment": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        }, "required": ["name"]}},
    }, "required": ["rows"]}
    strict = pi_strict_schema(schema)
    nested = strict["properties"]["rows"]["items"]
    assert nested["required"] == ["name", "count", "comment"]
    assert nested["additionalProperties"] is False
    assert nested["properties"]["comment"] == schema["properties"]["rows"]["items"]["properties"]["comment"]
    args = {"rows": [{"name": "Synthetic", "count": None, "comment": None}]}
    assert pi_execution_arguments(args, schema) == {"rows": [{"name": "Synthetic", "comment": None}]}


@pytest.mark.parametrize("nullable", [
    {"type": ["string", "null"]}, {"enum": ["x", None]}, {"const": None},
    {"anyOf": [{"type": "string"}, {"type": "null"}]},
])
def test_explicit_nullability_is_preserved(nullable):
    schema = {"type": "object", "properties": {"value": nullable}}
    assert pi_strict_schema(schema)["properties"]["value"] == nullable
    assert pi_execution_arguments({"value": None}, schema) == {"value": None}


@pytest.mark.parametrize("schema", [
    True, {"type": "array"}, {"type": "object", "additionalProperties": True},
    {"type": "object", "required": ["absent"]},
    {"type": "object", "properties": {"x": {"$ref": "https://example.invalid/schema"}}},
    {"type": "object", "properties": {"x": {"oneOf": [{"type": "string"}]}}},
    {"type": "object", "properties": {"x": {"anyOf": [{"type": "object"}, {"type": "null"}]}}},
    {"type": "object", "properties": {"x": {"type": "array", "items": [{"type": "string"}]}}},
    {"type": "object", "properties": {"x": {"anyOf": []}}},
    {"type": "object", "properties": {"x": False}},
])
def test_pi_unsupported_strict_schemas_fail(schema):
    with pytest.raises(ValueError, match="invalid_tool_schema"):
        pi_strict_schema(schema)


@pytest.mark.parametrize("args", [
    {"command": "true", "timeout": "5"},  # Pi can coerce this; this profile does not claim equivalence.
    {"command": None}, {"command": 1}, {"timeout": None},
])
def test_unverified_coercions_and_invalid_arguments_fail(bash_schema, args):
    with pytest.raises(ValueError, match="invalid_tool_arguments"):
        pi_execution_arguments(args, bash_schema)


def test_explicit_required_null_is_not_deleted():
    schema = {"type": "object", "properties": {"value": {"type": ["string", "null"]}}, "required": ["value"]}
    assert pi_execution_arguments({"value": None}, schema) == {"value": None}


def test_schema_references_never_resolve(bash_schema):
    bash_schema["properties"]["timeout"] = {"$ref": "https://example.invalid/schema"}
    with pytest.raises(ValueError, match="invalid_tool_schema"):
        pi_execution_arguments({"command": "true", "timeout": None}, bash_schema)


def test_inputs_are_bounded_and_non_json_values_rejected(bash_schema):
    cycle = {"type": "object"}
    cycle["properties"] = {"recursive": cycle}
    for value in (cycle, {"type": "object", "description": "x" * (4 * 1024 * 1024)}, {"type": "object", "default": float("nan")}):
        with pytest.raises(ValueError, match="invalid_tool_schema"):
            pi_strict_schema(value)
    with pytest.raises(ValueError, match="invalid_tool_arguments"):
        pi_execution_arguments({"command": object()}, bash_schema)
def test_required_semantic_comparison_keeps_captured_order_and_rejects_missing_or_duplicate():
    import copy

    from argus_skill.trial.training_schema import pi_schema_equal

    actual = {"type": "object", "properties": {"second": {"type": "string"}, "first": {"type": "string"}},
              "required": ["second", "first"], "additionalProperties": False}
    original = copy.deepcopy(actual)
    assert pi_schema_equal(actual, {**actual, "required": ["first", "second"]})
    assert actual == original
    assert not pi_schema_equal(actual, {**actual, "required": ["first"]})
    assert not pi_schema_equal(actual, {**actual, "required": ["first", "first", "second"]})
    assert not pi_schema_equal(actual, {**actual, "default": {"required": ["first", "second"]}})
    left = {**actual, "default": {"required": ["second", "first"]}}
    assert not pi_schema_equal(left, {**left, "default": {"required": ["first", "second"]}})
