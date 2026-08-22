"""Explicit Planner work-kind contract shared with mission persistence."""

from __future__ import annotations

WORK_KINDS = (
    "scope",
    "algorithm_discovery",
    "environment_setup",
    "engineering_optimization",
    "validation",
    "delivery",
)
DEFAULT_WORK_KIND = "scope"
INVALID_WORK_KIND_ERROR = "work_kind must be one of: " + ", ".join(WORK_KINDS)


def parse_work_kind(raw: object) -> str:
    """Validate an explicit work kind, defaulting only an absent legacy value."""
    value = str(raw or "").strip()
    if not value:
        return DEFAULT_WORK_KIND
    if value not in WORK_KINDS:
        raise ValueError(INVALID_WORK_KIND_ERROR)
    return value


def planner_work_kind_guidance() -> str:
    """Return the model-visible structured task field contract."""
    return (
        "`work_kind` (validated; no prose inference): "
        + "|".join(WORK_KINDS)
        + "; absent=`scope`."
    )
