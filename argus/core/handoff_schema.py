"""Model-facing action variants; the host still validates the original contract.

Keep mutually exclusive fields out of the same model-visible object. In
particular, a provider that samples every property must not invent an answer
or control command just to submit a team ticket. The envelope leaves a root
object for JSON-schema tool APIs that disallow a root union.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

_TEAM_FIELDS = frozenset({
    "goal", "acceptance", "constraints", "context", "plan", "lifetime", "vertical", "domain",
    "workflow_mode", "start_stage", "workflow_profile", "workflow_stages", "research_target_level",
    "research_direction_mode", "target_venue", "require_independent_review", "operator_decides_stages",
    "waypoint_id",
})
_MANAGEMENT_FIELDS = frozenset({"answer", "control", "config", "authorization", "workflow_setup"})


def nullable_optionals(schema: dict[str, Any]) -> dict[str, Any]:
    """Give optional fields an honest absent representation on strict transports."""
    result = deepcopy(schema)
    if result.get("type") == "object":
        required = result.get("required", ())
        result["properties"] = {
            name: nullable_optionals(value) if name in required else {
                "anyOf": [nullable_optionals(value), {"type": "null"}],
                "description": value.get("description", "") + " Omit or use null when not applicable.",
            }
            for name, value in result.get("properties", {}).items()
        }
    elif result.get("type") == "array" and isinstance(result.get("items"), dict):
        result["items"] = nullable_optionals(result["items"])
    return result


def action_schema(form: str, schema: dict[str, Any]) -> dict[str, Any]:
    destinations = schema.get("properties", {}).get("to", {}).get("enum", ())
    if not destinations:
        return nullable_optionals(schema)
    variants = []
    for destination in destinations:
        variant = deepcopy(schema)
        properties = variant["properties"]
        properties["to"]["enum"] = [destination]
        removed = {"ask"} if destination != "operator" else set()
        if form == "manager":
            if destination == "operator":
                removed |= set(properties) - {"to", "summary", "assumptions", "ask"}
            elif destination == "team":
                removed |= _MANAGEMENT_FIELDS
            else:
                removed |= _TEAM_FIELDS - {"lifetime"}
        for name in removed:
            properties.pop(name, None)
        required = [name for name in variant["required"] if name in properties]
        if destination == "operator":
            required.append("ask")
        if form == "manager" and destination == "team":
            required.extend(["goal", "acceptance", "vertical"])
        variant["required"] = list(dict.fromkeys(required))
        variants.append(nullable_optionals(variant))
    return {
        "type": "object", "additionalProperties": False, "required": ["decision"],
        "properties": {"decision": {"anyOf": variants,
            "description": "One hand-off only. Choose its destination and fill only that variant."}},
    }


def omit_optional_nulls(value: Any, schema: dict[str, Any]) -> Any:
    """Only optional nulls mean absence; never discard non-null conflicting intent."""
    if isinstance(value, dict) and schema.get("type") == "object":
        properties, required = schema.get("properties", {}), schema.get("required", ())
        return {
            key: omit_optional_nulls(item, properties.get(key, {}))
            for key, item in value.items()
            if not (item is None and key in properties and key not in required)
        }
    if isinstance(value, list) and schema.get("type") == "array":
        return [omit_optional_nulls(item, schema.get("items", {})) for item in value]
    return value
