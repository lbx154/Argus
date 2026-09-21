"""Accounting permission is not a work result, a retry transition or unpause."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class AccountingAdmission:
    allowed: bool
    reservation: Any = None
    reason: str = ""
    before_dispatch: Callable[[], None] | None = None
    execution_failed: Callable[[str], None] | None = None
    report_budget_events: bool = True

    def validate(self) -> None:
        """Validate even duck-typed adapter results again at consumption."""
        if type(self.allowed) is not bool:
            raise TypeError("accounting permission must be a literal bool")
        if not callable(self.before_dispatch) or not callable(self.execution_failed):
            raise TypeError("accounting admission requires both protocol hooks")

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise TypeError("accounting permission must be a literal bool")
