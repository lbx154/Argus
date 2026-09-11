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
        ("global daily budget exhausted", "budget_exhausted"),
        ("cost control unavailable", "backend_unavailable"),
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


# What each stop kind means to a reader, as a clause that completes a sentence
# such as "The task was paused because …". The kinds themselves stay machine
# codes; only these clauses are shown to people.
_STOP_KIND_CLAUSES: dict[str, tuple[str, str]] = {
    "budget_exhausted": (
        "the project reached its budget limit",
        "项目达到了预算上限",
    ),
    "provider_cooldown": (
        "the model service asked Argus to wait before calling again",
        "模型服务要求 Argus 稍后再调用",
    ),
    "provider_fence": (
        "the model provider is not accepting calls right now",
        "模型服务当前不接受调用",
    ),
    "daemon_shutdown": ("Argus was stopped", "Argus 被停止"),
    "operator_pause": ("the operator paused the work", "操作员暂停了工作"),
    "operator_abort": ("the operator cancelled the task", "操作员取消了任务"),
    "backend_unavailable": ("the model service was unavailable", "模型服务不可用"),
    "transient_error": (
        "a temporary error interrupted the call",
        "一次临时错误中断了调用",
    ),
    "permanent_error": (
        "an error that will not clear on its own stopped the call",
        "一个不会自行消失的错误中止了调用",
    ),
}

_PAUSE_STATUS_STOP_KINDS: dict[str, str] = {
    "paused_budget": "budget_exhausted",
    "paused_provider_cooldown": "provider_cooldown",
    "paused_provider_fence": "provider_fence",
    "paused_daemon_shutdown": "daemon_shutdown",
    "paused_operator": "operator_pause",
}

_PAUSE_STATUS_CLAUSES: dict[str, tuple[str, str]] = {
    "paused_external_work": (
        "work outside Argus had to finish first",
        "需要先等 Argus 之外的工作完成",
    ),
}


def stop_kind_clause(value: Any, *, chinese: bool = False) -> str:
    """The reason a call stopped, worded for a reader; empty for unknown kinds."""
    kind = normalize_stop_kind(value)
    if kind is None:
        return ""
    english, zh = _STOP_KIND_CLAUSES[kind]
    return zh if chinese else english


def pause_status_clause(value: Any, *, chinese: bool = False) -> str:
    """Why a task carrying a ``paused_*`` status paused, worded for a reader.

    Returns an empty string for statuses that do not describe a pause, so the
    caller can fall back to the stop kind or a generic sentence.
    """
    status = str(value or "").strip().lower()
    if status in _PAUSE_STATUS_STOP_KINDS:
        return stop_kind_clause(_PAUSE_STATUS_STOP_KINDS[status], chinese=chinese)
    if status in _PAUSE_STATUS_CLAUSES:
        english, zh = _PAUSE_STATUS_CLAUSES[status]
        return zh if chinese else english
    if status.startswith("paused_"):
        return "工作被暂停" if chinese else "the work was paused"
    return ""


__all__ = [
    "NON_FAILURE_STOP_KINDS",
    "RECOVERABLE_STOP_KINDS",
    "STOP_KINDS",
    "StopKind",
    "normalize_stop_kind",
    "pause_status_clause",
    "pause_status_for_stop_kind",
    "stop_kind_clause",
    "stop_kind_from_external_interrupt",
    "stop_kind_is_recoverable",
]
