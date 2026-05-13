"""Shared operator-facing status helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


def _backlog_item_time(item: object, attr: str) -> float:
    value = getattr(item, attr, None)
    try:
        return float(value) if value is not None else float("-inf")
    except (TypeError, ValueError):
        return float("-inf")


def count_backlog_statuses(items: Sequence[object]) -> tuple[int, int, int, int, int]:
    pending = running = done = failed = skipped = 0
    for item in items:
        status = getattr(item, "status", "")
        if status == "pending":
            pending += 1
        elif status == "running":
            running += 1
        elif status == "done":
            done += 1
        elif status == "failed":
            failed += 1
        elif status == "skipped":
            skipped += 1
    return pending, running, done, failed, skipped


def select_current_running_item(items: Sequence[object]) -> object | None:
    running = [item for item in items if getattr(item, "status", "") == "running"]
    if not running:
        return None
    # Prefer the most recently started row so stale duplicates do not
    # make the current-task block bounce around between refreshes.
    return max(
        running,
        key=lambda item: (
            _backlog_item_time(item, "started_ts"),
            _backlog_item_time(item, "ts"),
            str(getattr(item, "id", "")),
        ),
    )


@dataclass(frozen=True)
class ContinuousStateInfo:
    enabled: bool
    objective: str
    done_reason: str
    done_at: str

    @property
    def is_completed(self) -> bool:
        return not self.enabled and bool(self.done_reason)


def describe_continuous_state(state: Any) -> ContinuousStateInfo:
    def _text(value: Any) -> str:
        return "" if value is None else str(value)

    return ContinuousStateInfo(
        enabled=bool(getattr(state, "enabled", False)),
        objective=_text(getattr(state, "objective", "")),
        done_reason=_text(getattr(state, "done_reason", "")),
        done_at=_text(getattr(state, "done_at", "")),
    )
