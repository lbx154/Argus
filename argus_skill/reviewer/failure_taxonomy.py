"""Typed failure layers separating research infrastructure from science."""

from __future__ import annotations

from typing import Final

VALID_FAILURE_LAYERS: Final[frozenset[str]] = frozenset({
    "platform",
    "orchestration",
    "evaluator",
    "evidence_packaging",
    "scientific",
    "operator",
    "unknown",
})
REPAIRABLE_FAILURE_LAYERS: Final[frozenset[str]] = frozenset({
    "platform",
    "orchestration",
    "evaluator",
    "evidence_packaging",
})

_LEGACY_LAYER = {
    "environmental": "platform",
    "execution_mistake": "orchestration",
    "skill_gap": "platform",
    "method_failure": "scientific",
    "ambiguous_objective": "operator",
    "operator_interrupt": "operator",
    "unknown": "unknown",
}


def normalize_failure_layer(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in VALID_FAILURE_LAYERS:
            return normalized
    return ""


def resolve_failure_layer(*, failure_layer: object, failure_cause: object) -> str:
    explicit = normalize_failure_layer(failure_layer)
    if explicit:
        return explicit
    cause = str(failure_cause or "").strip().lower()
    return _LEGACY_LAYER.get(cause, "")


def is_repairable_failure_layer(value: object) -> bool:
    return normalize_failure_layer(value) in REPAIRABLE_FAILURE_LAYERS


__all__ = [
    "REPAIRABLE_FAILURE_LAYERS",
    "VALID_FAILURE_LAYERS",
    "is_repairable_failure_layer",
    "normalize_failure_layer",
    "resolve_failure_layer",
]
