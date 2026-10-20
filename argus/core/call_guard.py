"""Backstop: background model calls need progress between them.

Every call site that runs on its own (a timer, a retry, a wait) is gated on
new input or capped. This guard holds that line for one that slips through:
in a daemon, at most ``BACKGROUND_CALL_BUDGET`` background calls are admitted
between two progress events. Further background calls are refused before they
reach a provider, so they cost nothing, until progress arrives; the operator
is told once.

Mission work (Engineer and Reviewer rounds and the tools their sessions call)
and calls that each answer one input (an operator turn, a peer message) are
not background calls: the round or the input pays for them.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from .event_catalog import EventType

log = logging.getLogger(__name__)

BACKGROUND_CALL_BUDGET = 30
_FOREGROUND_LABELS = (
    "engineer-",
    "reviewer",
    "advisor.",
    "research.",
    "subagent:",
    "peer-message-response",
    "manager-frontdoor",
    "manager-classify",
    "manager-chat",
    "manager-self",
    "manager-ask",
    "map-",
)
# New input or settled work: what background calls are allowed to react to.
# Nothing here can be produced by time passing alone.
PROGRESS_EVENTS = frozenset({
    EventType.LIFE_INBOX_DRAINED,
    EventType.LIFE_OPERATOR_QUESTION_ANSWERED,
    EventType.LIFE_MISSION_COMPLETED,
    EventType.LIFE_MISSION_FAILED,
    EventType.LIFE_MISSION_SKIPPED,
    EventType.ROUND_MAIN_COMPLETED,
    EventType.ROUND_REVIEW_COMPLETED,
    EventType.LIFE_PLANNER_TASK_ADDED,
    EventType.LIFE_PLAN_REVISION_COMMITTED,
    EventType.LIFE_PLANNER_WAITING_WOKEN,
    EventType.LIFE_PEER_MESSAGE_PROCESSED,
})

_lock = threading.Lock()
_enabled = False
_calls: list[str] = []
_notified = False
_on_trip: Callable[[list[str]], None] | None = None


def enable(on_trip: Callable[[list[str]], None] | None = None) -> None:
    """Guard this process's background calls; ``on_trip`` tells the operator."""
    global _enabled, _notified, _on_trip
    with _lock:
        _enabled, _notified, _on_trip = True, False, on_trip
        _calls.clear()


def disable() -> None:
    global _enabled, _notified, _on_trip
    with _lock:
        _enabled, _notified, _on_trip = False, False, None
        _calls.clear()


def observe(event: dict[str, Any]) -> None:
    """A progress event restores the budget."""
    kind = event.get("type")
    if kind not in PROGRESS_EVENTS:
        return
    # A wait that merely ran out of time saw nothing new.
    if kind == EventType.LIFE_PLANNER_WAITING_WOKEN and event.get("wake_reason") == "expired":
        return
    global _notified
    with _lock:
        _notified = False
        _calls.clear()


def refusal(run_label: str) -> str:
    """Why this call may not run now; empty when it is admitted."""
    label = str(run_label or "")
    if label.startswith(_FOREGROUND_LABELS):
        return ""
    global _notified
    with _lock:
        if not _enabled:
            return ""
        if len(_calls) < BACKGROUND_CALL_BUDGET:
            _calls.append(label)
            return ""
        notify = None if _notified else _on_trip
        _notified = True
        recent = list(_calls)
    if notify is not None:
        try:
            notify(recent)
        except Exception:  # noqa: BLE001 - the refusal stands without the note
            log.exception("could not tell the operator background calls stopped")
    return (
        f"background model calls paused: {BACKGROUND_CALL_BUDGET} calls without "
        "progress; they resume when work settles, the plan changes, or the operator replies"
    )
