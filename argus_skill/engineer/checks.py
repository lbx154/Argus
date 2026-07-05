from __future__ import annotations

from ..core.models import CheckResult


def all_checks_passed(results: list[CheckResult]) -> bool:
    return all(item.passed for item in results)


__all__ = ["all_checks_passed"]
