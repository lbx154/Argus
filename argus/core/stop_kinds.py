"""Structured reasons a backend call stopped without a usable result."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias, cast

StopKind: TypeAlias = Literal[
    "budget_exhausted",
    "cost_unreconciled",
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
    "cost_unreconciled",
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
    "cost_unreconciled",
    "provider_cooldown",
    "provider_fence",
    "daemon_shutdown",
    "operator_pause",
    "backend_unavailable",
    "transient_error",
})
NON_FAILURE_STOP_KINDS = frozenset({
    "budget_exhausted",
    "cost_unreconciled",
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
        ("global daily token budget exhausted", "budget_exhausted"),
        ("unresolved provider cost", "cost_unreconciled"),
        ("cost control unavailable", "backend_unavailable"),
        ("daemon stop requested", "daemon_shutdown"),
        ("operator pause requested", "operator_pause"),
        ("operator abort requested", "operator_abort"),
    ):
        if normalized.startswith(prefix):
            return cast(StopKind, kind)
    return None


def stop_park_reason(value: Any) -> StopKind | None:
    """The park a stop leaves its work in, or ``None`` when the stop does not park it.

    Every stop that is not a failure parks, except an abort, which ends the work.
    """
    kind = normalize_stop_kind(value)
    return kind if kind in NON_FAILURE_STOP_KINDS and kind != "operator_abort" else None


def interrupt_outcome(reason: Any) -> tuple[Literal["aborted", "parked"], StopKind]:
    """How an interrupt ends a run, with its stop kind: an operator abort aborts
    it; any other parks it under its own park, or the operator's pause."""
    kind = stop_kind_from_external_interrupt(reason)
    if kind == "operator_abort":
        return "aborted", kind
    return "parked", stop_park_reason(kind) or "operator_pause"


def run_park_reason(value: Any) -> str:
    """Where a stopped run waits: a non-failure stop under its own kind, a host
    that could not start (``backend_unavailable``) on ``infra``; else ""."""
    kind = normalize_stop_kind(value)
    return "infra" if kind == "backend_unavailable" else stop_park_reason(kind) or ""


def stop_kind_is_recoverable(value: Any) -> bool:
    return normalize_stop_kind(value) in RECOVERABLE_STOP_KINDS


# Why work stopped or waits, as a clause that completes a sentence such as
# "The task was paused because …": every stop kind, and the parks no backend
# stop explains. The codes stay machine codes; only these clauses are shown.
_CLAUSES: dict[str, tuple[str, str]] = {
    "budget_exhausted": (
        "the project reached its budget limit",
        "项目达到了预算上限",
    ),
    "cost_unreconciled": (
        "provider charges are awaiting reconciliation or explicit risk approval",
        "模型费用尚待核对或明确的风险确认",
    ),
    "provider_cooldown": (
        "the model service asked Argus to wait before calling again",
        "模型服务要求 Argus 稍后再调用",
    ),
    "provider_fence": (
        "the model provider is not accepting calls right now",
        "模型服务当前不接受调用",
    ),
    "daemon_shutdown": (
        "the Argus background service stopped or restarted",
        "Argus 后台服务停止或重启了",
    ),
    "operator_pause": ("you paused the work", "你暂停了工作"),
    "operator_abort": ("you cancelled the task", "你取消了这项任务"),
    "backend_unavailable": ("the model service was unavailable", "模型服务不可用"),
    "transient_error": (
        "a temporary error interrupted the call",
        "一次临时错误中断了调用",
    ),
    "permanent_error": (
        "an error that will not clear on its own stopped the call",
        "一个不会自行消失的错误中止了调用",
    ),
    "external_work": (
        "work outside Argus had to finish first",
        "需要先等 Argus 之外的工作完成",
    ),
    "operator_question": (
        "a question is waiting for your answer",
        "有个问题在等你回答",
    ),
    "infra": (
        "the program that runs the work could not start",
        "运行环境无法启动",
    ),
}
_UNSAID = ("the work was interrupted", "工作被打断了")


def stop_kind_clause(value: Any, *, chinese: bool = False, neutral: bool = False) -> str:
    """Why work stopped or waits, by its stop kind or park reason.

    Empty when the record does not say why, or with ``neutral`` a clause that
    blames no one.
    """
    english, zh = _CLAUSES.get(
        str(value or "").strip().lower(), _UNSAID if neutral else ("", ""),
    )
    return zh if chinese else english


@dataclass(frozen=True)
class ParkRule:
    """Who ends a park."""

    holder: Literal["operator", "planner", "runtime", "world"]
    auto_resume: bool = False  # the runtime rechecks the condition by itself


# Why a backlog item is parked: a stop kind, or a park no backend stop
# explains. A ``parked`` row records its reason in ``outcome["park"]["reason"]``
# and a ``parked`` event in ``park_reason``; a row or event an older release
# wrote names it in its status (_LEGACY_PARK_REASONS).
_PARK_RULES: dict[str, ParkRule] = {
    "budget_exhausted": ParkRule("runtime", auto_resume=True),
    "cost_unreconciled": ParkRule("runtime", auto_resume=True),
    "daemon_shutdown": ParkRule("runtime", auto_resume=True),
    "provider_cooldown": ParkRule("world", auto_resume=True),
    # An older release parked work on a job it watched; it resumes at once.
    "external_work": ParkRule("runtime", auto_resume=True),
    # The operator's Run ends it, and so does the model service answering
    # again (the outage circuit it opened closing: life/runtime_failure_circuit.py).
    "provider_fence": ParkRule("operator"),
    "operator_pause": ParkRule("operator"),
    "operator_question": ParkRule("operator"),
    "infra": ParkRule("world"),
    "research_open": ParkRule("planner"),
}
# A park whose reason is unknown (an old ``paused`` row, a reason a newer
# release added) is the Planner's to retire, so nothing waits on it forever.
_UNKNOWN_PARK = ParkRule("planner")

_LEGACY_PARK_REASONS: dict[str, str] = {
    "paused": "",
    "paused_budget": "budget_exhausted",
    "paused_cost": "cost_unreconciled",
    "paused_provider_cooldown": "provider_cooldown",
    "paused_provider_fence": "provider_fence",
    "paused_daemon_shutdown": "daemon_shutdown",
    "paused_operator": "operator_pause",
    "paused_external_work": "external_work",
    "research_incomplete": "research_open",
    "paused_no_breakthrough": "research_open",
    "exhausted_current_methods": "research_open",
    "infra_blocked": "infra",
}
PARK_STATUSES = frozenset({"parked", *_LEGACY_PARK_REASONS})
#: Parks the runtime ends on its next pass: the condition was its own to recheck.
RUNTIME_RESUMED_PARKS = frozenset(
    reason for reason, rule in _PARK_RULES.items() if rule.auto_resume
)
#: Parks an explicit operator Run ends: every one the Planner does not own.
OPERATOR_RESUMED_PARKS = frozenset(
    reason for reason, rule in _PARK_RULES.items() if rule.holder != "planner"
)

def park_reason_of(status: Any, recorded: Any = "") -> str | None:
    """Why a row or event with ``status`` is parked; ``None`` when it is not.

    ``recorded`` is the reason a ``parked`` row or event carries. An older
    status names its own, and a ``paused_*`` status this table does not know
    is a park whose reason is unknown (``""``).
    """
    status = str(status or "").strip().lower()
    if status == "parked":
        return str(recorded or "").strip().lower()
    if status in _LEGACY_PARK_REASONS:
        return _LEGACY_PARK_REASONS[status]
    return "" if status.startswith("paused_") else None


def park_rule(reason: Any) -> ParkRule:
    return _PARK_RULES.get(str(reason or ""), _UNKNOWN_PARK)


def is_pause_status(status: Any) -> bool:
    """Whether an event's ``status`` reads as a pause: ``parked`` or ``paused_*``."""
    status = str(status or "").strip().lower()
    return status == "parked" or status.startswith("paused_")


def park_clause(status: Any, recorded: Any = "", *, chinese: bool = False) -> str:
    """Why a parked row or event paused, worded for a reader.

    Returns an empty string for a status that is not a park, so the caller can
    fall back to the stop kind or a generic sentence.
    """
    reason = park_reason_of(status, recorded)
    if reason is None:
        return ""
    return stop_kind_clause(reason, chinese=chinese) or (
        "工作被暂停" if chinese else "the work was paused"
    )


__all__ = [
    "NON_FAILURE_STOP_KINDS",
    "OPERATOR_RESUMED_PARKS",
    "PARK_STATUSES",
    "RECOVERABLE_STOP_KINDS",
    "RUNTIME_RESUMED_PARKS",
    "STOP_KINDS",
    "ParkRule",
    "StopKind",
    "interrupt_outcome",
    "is_pause_status",
    "normalize_stop_kind",
    "park_clause",
    "park_reason_of",
    "park_rule",
    "run_park_reason",
    "stop_kind_clause",
    "stop_kind_from_external_interrupt",
    "stop_kind_is_recoverable",
    "stop_park_reason",
]
