from __future__ import annotations

PLANNER_MODE_OFF = "off"
PLANNER_MODE_AUTO = "auto"
PLANNER_MODE_RECORD = "record"

PLANNER_MODE_CHOICES = (
    PLANNER_MODE_OFF,
    PLANNER_MODE_AUTO,
    PLANNER_MODE_RECORD,
)


def planner_mode_label(mode: str) -> str:
    if mode == PLANNER_MODE_OFF:
        return "No Planner"
    if mode == PLANNER_MODE_RECORD:
        return "Record-Only Planner"
    return "Auto Planner"


def planner_mode_description(mode: str) -> str:
    if mode == PLANNER_MODE_OFF:
        return "Disable the planner agent entirely."
    if mode == PLANNER_MODE_RECORD:
        return "Planner records architecture and TODO only. No autonomous follow-up execution."
    return (
        "Planner explores, maintains architecture, and proposes next sessions. "
        "Daemon auto follow-up still requires explicit /plan confirmation of the session goal."
    )


def resolve_planner_mode(*, planner_enabled_flag: bool, planner_mode: str) -> str:
    if not planner_enabled_flag:
        return PLANNER_MODE_OFF
    if planner_mode in PLANNER_MODE_CHOICES:
        return planner_mode
    return PLANNER_MODE_AUTO


def planner_mode_enabled(mode: str) -> bool:
    return mode != PLANNER_MODE_OFF


def planner_mode_allows_follow_up(mode: str) -> bool:
    return mode == PLANNER_MODE_AUTO
