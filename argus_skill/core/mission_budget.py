"""Live per-mission budget guard shared by runtime and provider reservations."""

from __future__ import annotations

from typing import Any


class MissionBudgetGuard:
    """Callable source gate with machine-readable cap/spend metadata.

    ``AgentCliRunner`` only needs the callable reason. ``AgentCliBackend`` also
    reads ``cap_usd`` so the atomic reservation and provider-side fence use the
    exact effective backlog-item cap instead of the process-wide default.
    """

    def __init__(self, budget: Any) -> None:
        self._budget = budget

    @property
    def cap_usd(self) -> float:
        try:
            return max(0.0, float(self._budget.cap_usd))
        except (AttributeError, TypeError, ValueError):
            return 0.0

    def spent_usd(self) -> float:
        try:
            return max(0.0, float(self._budget.spent()))
        except (AttributeError, TypeError, ValueError):
            return 0.0

    def remaining_usd(self) -> float:
        cap = self.cap_usd
        return max(0.0, cap - self.spent_usd()) if cap > 0 else 0.0

    def __call__(self) -> str | None:
        try:
            if self._budget.exceeded():
                return (
                    f"per-mission budget ${self.cap_usd:.2f} exhausted "
                    f"(spent ${self.spent_usd():.2f})"
                )
        except Exception:  # noqa: BLE001 - a budget probe must never wedge a call
            return None
        return None


def build_mission_budget_guard(
    budget: Any,
) -> MissionBudgetGuard | None:
    """Return a live guard for a MissionBudget-shaped object, else ``None``."""
    if budget is None or not hasattr(budget, "exceeded"):
        return None
    return MissionBudgetGuard(budget)


def mission_cap_from_guard(provider: Any) -> float | None:
    """Extract an explicit cap without changing legacy callable providers."""
    if provider is None or not hasattr(provider, "cap_usd"):
        return None
    try:
        return max(0.0, float(provider.cap_usd))
    except (TypeError, ValueError):
        return None


__all__ = [
    "MissionBudgetGuard",
    "build_mission_budget_guard",
    "mission_cap_from_guard",
]
