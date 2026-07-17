"""Structured reasons a backend call stopped without a usable result."""
from __future__ import annotations

from typing import Any, Literal, TypeAlias, cast

StopKind: TypeAlias = Literal[
    "budget_exhausted",
    "provider_cooldown",
    "provider_fence",
    "daemon_shutdown",
    "operator_pause",
    "operator_abort",
    "backend_unavailable",
    "transient_error",
    "permanent_error",
]

STOP_KINDS = frozenset({
    "budget_exhausted",
    "provider_cooldown",
    "provider_fence",
    "daemon_shutdown",
    "operator_pause",
    "operator_abort",
    "backend_unavailable",
    "transient_error",
    "permanent_error",
})
RECOVERABLE_STOP_KINDS = frozenset({
    "budget_exhausted",
    "provider_cooldown",
    "provider_fence",
    "daemon_shutdown",
    "operator_pause",
    "backend_unavailable",
    "transient_error",
})
NON_FAILURE_STOP_KINDS = frozenset({
    "budget_exhausted",
    "provider_cooldown",
    "provider_fence",
    "daemon_shutdown",
    "operator_pause",
    "operator_abort",
})


def normalize_stop_kind(value: Any) -> StopKind | None:
    normalized = str(value or "").strip().lower()
    return cast(StopKind, normalized) if normalized in STOP_KINDS else None


def stop_kind_from_external_interrupt(value: Any) -> StopKind | None:
    """Classify trusted control-plane interrupt prefixes, not model prose."""
    normalized = str(value or "").strip().casefold()
    if normalized.startswith("external interrupt:"):
        normalized = normalized.removeprefix("external interrupt:").lstrip()
    for prefix, kind in (
        ("daemon stop requested", "daemon_shutdown"),
        ("operator pause requested", "operator_pause"),
        ("operator abort requested", "operator_abort"),
    ):
        if normalized.startswith(prefix):
            return cast(StopKind, kind)
    return None


def pause_status_for_stop_kind(value: Any) -> str:
    kind = normalize_stop_kind(value)
    if kind is None:
        return ""
    return {
        "budget_exhausted": "paused_budget",
        "provider_cooldown": "paused_provider_cooldown",
        "provider_fence": "paused_provider_fence",
        "daemon_shutdown": "paused_daemon_shutdown",
        "operator_pause": "paused_operator",
    }.get(kind, "")


def stop_kind_is_recoverable(value: Any) -> bool:
    return normalize_stop_kind(value) in RECOVERABLE_STOP_KINDS


__all__ = [
    "NON_FAILURE_STOP_KINDS",
    "RECOVERABLE_STOP_KINDS",
    "STOP_KINDS",
    "StopKind",
    "normalize_stop_kind",
    "pause_status_for_stop_kind",
    "stop_kind_from_external_interrupt",
    "stop_kind_is_recoverable",
]
