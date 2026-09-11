"""Bounded comparisons with Pi 0.85.1's observed tool-schema transformations.

These helpers validate relationships between separately captured values. Their
outputs must never replace the provider schema or model arguments in a sample.
Unsupported coercions fail closed instead of manufacturing executed arguments.
"""
from __future__ import annotations

import json
import math

from jsonschema import Draft202012Validator, SchemaError

MAX_NODES = 100_000
MAX_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 32
_UNSUPPORTED_STRICT = frozenset({
    "$ref", "$defs", "definitions", "allOf", "oneOf", "patternProperties",
    "dependentSchemas", "dependencies", "unevaluatedProperties", "propertyNames",
    "contains", "prefixItems", "not", "if", "then", "else",
})


def _copy_json(value, error):
    nodes, budget = MAX_NODES, MAX_BYTES

    def copy(item, depth=0):
        nonlocal nodes, budget
        nodes -= 1
        budget -= 2
        if nodes < 0 or budget < 0 or depth > MAX_DEPTH:
            raise ValueError(error)
        if isinstance(item, dict):
            result = {}
            for key, val in item.items():
                if not isinstance(key, str):
                    raise ValueError(error)
                result[copy(key, depth + 1)] = copy(val, depth + 1)
            return result
        if isinstance(item, list):
            return [copy(val, depth + 1) for val in item]
        if item is not None and type(item) not in {str, int, float, bool}:
            raise ValueError(error)
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError(error)
        if isinstance(item, str) and len(item) > budget:
            raise ValueError(error)
        try:
            budget -= len(json.dumps(item, ensure_ascii=True, allow_nan=False))
        except (ValueError, OverflowError):
            raise ValueError(error) from None
        if budget < 0:
            raise ValueError(error)
        return item

    return copy(value)


def pi_strict_schema(runtime_schema):
    """Match makeStrictJsonSchema; compare the result to the captured schema.

    Source: Pi packages/ai/src/api/constrained-sampling.ts. Optional properties
    become required and nullable; object schemas forbid additional properties.
    Pi rejects references, structured unions and other unsupported constructs.
    """
    schema = _copy_json(runtime_schema, "invalid_tool_schema")

    def structured(node):
        if not isinstance(node, dict):
            return False
        types = node.get("type", [])
        types = [types] if isinstance(types, str) else types if isinstance(types, list) else []
        return "object" in types or "array" in types or "properties" in node or "items" in node

    def allows_null(node):
        if not isinstance(node, dict):
            return False
        types = node.get("type")
        return (types == "null" or isinstance(types, list) and "null" in types
                or "const" in node and node["const"] is None
                or isinstance(node.get("enum"), list) and None in node["enum"]
                or isinstance(node.get("anyOf"), list) and any(allows_null(part) for part in node["anyOf"]))

    def strict(node):
        if not isinstance(node, dict) or node.keys() & _UNSUPPORTED_STRICT:
            raise ValueError("invalid_tool_schema")
        if "anyOf" in node:
            variants = node["anyOf"]
            if not isinstance(variants, list) or not variants:
                raise ValueError("invalid_tool_schema")
            for variant in variants:
                if structured(variant):
                    raise ValueError("invalid_tool_schema")
                strict(variant)
        if "items" in node:
            strict(node["items"])
        if "properties" in node and node.get("type") != "object":
            raise ValueError("invalid_tool_schema")
        if node.get("type") != "object":
            return
        if "additionalProperties" in node and node["additionalProperties"] is not False:
            raise ValueError("invalid_tool_schema")
        properties = node.get("properties", {})
        required = node.get("required", [])
        if (not isinstance(properties, dict) or not isinstance(required, list)
                or any(not isinstance(key, str) for key in required)
                or any(key not in properties for key in required)):
            raise ValueError("invalid_tool_schema")
        for key, part in properties.items():
            strict(part)
            if key not in required and not allows_null(part):
                properties[key] = {"anyOf": [part, {"type": "null"}]}
        node["required"] = list(properties)
        node["additionalProperties"] = False

    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("invalid_tool_schema")
    strict(schema)
    return _copy_json(schema, "invalid_tool_schema")


def pi_execution_arguments(raw_args, runtime_schema):
    """Match Pi's optional-null removal, rejecting all other required coercion.

    Source: Pi packages/ai/src/utils/validation.ts normalizeOptionalNulls. Only
    optional properties whose original schema rejects null are removed. Required
    or explicitly nullable values are preserved. No external references resolve.
    """
    schema = _copy_json(runtime_schema, "invalid_tool_schema")
    args = _copy_json(raw_args, "invalid_tool_arguments")
    if not isinstance(schema, dict) or schema.get("type") != "object" or not isinstance(args, dict):
        raise ValueError("invalid_tool_arguments")
    pending = [schema]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if any(key in node for key in ("$ref", "$dynamicRef", "$recursiveRef")):
                raise ValueError("invalid_tool_schema")
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise ValueError("invalid_tool_schema") from None

    def normalize(value, node):
        if not isinstance(node, dict):
            return
        if isinstance(value, list):
            items = node.get("items")
            if isinstance(items, dict):
                for item in value:
                    normalize(item, items)
            elif isinstance(items, list):
                for item, item_schema in zip(value, items):
                    normalize(item, item_schema)
            return
        if not isinstance(value, dict) or not isinstance(node.get("properties"), dict):
            return
        required = node.get("required", [])
        for key, part in node["properties"].items():
            if key not in value:
                continue
            if value[key] is None and key not in required and not Draft202012Validator(part).is_valid(None):
                del value[key]
            else:
                normalize(value[key], part)

    normalize(args, schema)
    if not Draft202012Validator(schema).is_valid(args):
        raise ValueError("invalid_tool_arguments")
    return args
