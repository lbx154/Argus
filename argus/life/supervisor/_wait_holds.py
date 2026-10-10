"""Hold a challenged backlog item while the Planner's answer to it is a wait.

When a mission ends asking for a replan and the Planner answers by waiting,
the challenged item used to stay ``pending`` and the next tick ran it again
at once. In ab1009 that happened in freight-dispatch-shift on both arms D2
and E, even when the wait contract was persisted and the Planner said to keep
the task "without restarting work".

The item now keeps ``pending`` but carries ``wait_hold``, which the scheduler
honours (``memory.wait_hold_active``). Every hold has a release path:

- tied to a persisted wait contract (``wait_id``), it is released as soon as
  that contract stops being the active one: a watched input changed, the
  contract expired, the Manager resolved it, operator input arrived, or the
  Planner moved on to other work;
- every hold carries ``until``, the contract's timed recheck (or, with no
  contract, the degraded-poll interval), and lapses then whatever else
  happens. An event wait with no recheck time is bounded by the same ceiling
  that bounds how long the Planner may stay silent.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from ..memory import wait_hold_active
from ._constants import PLANNER_UNCHANGED_SKIP_MAX_SECONDS

if TYPE_CHECKING:
    from ..memory import LifeMemory

log = logging.getLogger(__name__)

#: Hold used when the Planner waited but no contract could be persisted: the
#: same bounded interval a degraded (unobservable) wait rechecks on.
FALLBACK_HOLD_SECONDS = 300.0
#: Longest a hold on an event wait with no timed recheck may last.
EVENT_HOLD_CEILING_SECONDS = PLANNER_UNCHANGED_SKIP_MAX_SECONDS

WAIT_HOLD_SET = "life.backlog.wait_hold.set"
WAIT_HOLD_RELEASED = "life.backlog.wait_hold.released"


class WaitHoldMixin:
    """Hold and release challenged items around a Planner wait."""

    if TYPE_CHECKING:
        memory: LifeMemory

        def _emit(self, event: dict[str, Any]) -> Any: ...
        def _load_planner_waiting_contract_state(self) -> dict[str, Any] | None: ...
        def _waiting_contract_key(self, contract: Any) -> tuple[str, str]: ...

    def _wait_hold_until(self, contract_state: dict[str, Any] | None, now: float) -> float:
        if not contract_state:
            return now + FALLBACK_HOLD_SECONDS
        bounds: list[float] = []
        recheck = float(contract_state.get("recheck_after_seconds") or 0.0)
        if recheck > 0:
            bounds.append(now + recheck)
        expires_at = float(contract_state.get("expires_at") or 0.0)
        if expires_at > now:
            bounds.append(expires_at)
        if str(contract_state.get("wait_mode") or "poll") != "event":
            # A poll wait is a timed recheck by definition.
            bounds.append(now + max(recheck, FALLBACK_HOLD_SECONDS))
        bounds.append(now + EVENT_HOLD_CEILING_SECONDS)
        return min(bounds)

    def _hold_challenged_item_for_wait(self, revision_request: Any, verdict: Any) -> None:
        """Keep the challenged item from re-running while the Planner waits."""
        item_id = str((revision_request or {}).get("item_id") or "").strip()
        if not item_id:
            return
        try:
            item = next(
                (row for row in self.memory.backlog.active() if row.id == item_id), None,
            )
            if item is None or item.status != "pending":
                return
            contract = getattr(verdict, "waiting_contract", None)
            state = self._load_planner_waiting_contract_state()
            persisted = (
                isinstance(state, dict)
                and bool(state.get("active"))
                and contract is not None
                and (state.get("blocker_fingerprint"), state.get("recheck_token"))
                == self._waiting_contract_key(contract)
            )
            now = time.time()
            hold = {
                "wait_id": str(state.get("wait_id") or "") if persisted else "",
                "blocker_fingerprint": (
                    str(state.get("blocker_fingerprint") or "") if persisted else ""
                ),
                "since": now,
                "until": self._wait_hold_until(state if persisted else None, now),
                "reason": " ".join(
                    str(
                        getattr(verdict, "waiting_reason", "")
                        or getattr(verdict, "reason", "")
                        or ""
                    ).split()
                )[:500],
            }
            self.memory.backlog.update(item_id, wait_hold=hold)
        except Exception:  # noqa: BLE001 - without a hold the item simply runs
            log.warning("failed to hold challenged item %s for a Planner wait", item_id, exc_info=True)
            return
        self._emit({
            "type": WAIT_HOLD_SET,
            "item_id": item_id,
            "wait_id": hold["wait_id"],
            "contract_persisted": persisted,
            "until": hold["until"],
            "reason": hold["reason"],
        })

    def _release_wait_holds(self) -> int:
        """Release every hold whose wait has ended. Returns how many were released."""
        try:
            held = [item for item in self.memory.backlog.active() if item.wait_hold]
        except Exception:  # noqa: BLE001 - an unreadable backlog holds nothing new
            return 0
        if not held:
            return 0
        try:
            state = self._load_planner_waiting_contract_state()
        except Exception:  # noqa: BLE001 - an unreadable contract is not a wait
            state = None
        active_wait_id = (
            str(state.get("wait_id") or "")
            if isinstance(state, dict) and bool(state.get("active"))
            else ""
        )
        now = time.time()
        released = 0
        for item in held:
            hold = item.wait_hold
            wait_id = str(hold.get("wait_id") or "")
            if item.status != "pending":
                reason = "item_no_longer_pending"
            elif not wait_hold_active(item, now=now):
                reason = "recheck_due"
            elif wait_id and wait_id != active_wait_id:
                reason = "wait_ended"
            else:
                continue
            try:
                self.memory.backlog.update(item.id, wait_hold={})
            except Exception:  # noqa: BLE001 - ``until`` still lapses on its own
                log.warning("failed to release wait hold on %s", item.id, exc_info=True)
                continue
            released += 1
            self._emit({
                "type": WAIT_HOLD_RELEASED,
                "item_id": item.id,
                "wait_id": wait_id,
                "release_reason": reason,
                "held_seconds": round(now - float(hold.get("since") or now), 1),
            })
        return released

    def _has_wait_held_items(self) -> bool:
        try:
            return any(wait_hold_active(item) for item in self.memory.backlog.active())
        except Exception:  # noqa: BLE001
            return False


__all__ = [
    "EVENT_HOLD_CEILING_SECONDS",
    "FALLBACK_HOLD_SECONDS",
    "WAIT_HOLD_RELEASED",
    "WAIT_HOLD_SET",
    "WaitHoldMixin",
]
