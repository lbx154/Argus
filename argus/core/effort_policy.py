"""How hard each role thinks, chosen by what the work needs.

Small, bounded work should feel fast (design/ux-principles.md, rule 3: no
waiting in the dark), so it runs at ``medium``; research, papers and work a
Reviewer sent back run at ``high``. Nothing runs at ``xhigh`` unless the
operator asks for it.

An operator's explicit effort always wins: the role's knob set in the
environment or switched with ``/config`` ("engineer 用 xhigh"), and a hosted
deployment's fixed effort.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

#: Verticals whose work is open-ended reading and writing, not a small change.
_DEEP_VERTICAL_MARKERS = ("research", "paper", "survey", "academic")


def explicit_effort(*knobs: str, env: Mapping[str, str] | None = None) -> str:
    """The first effort the operator set on ``knobs`` (env, then /config), or ""."""
    from .knobs import resolve_role_reasoning_effort

    for knob in knobs:
        value = resolve_role_reasoning_effort(knob, env=env, default="").strip()
        if value:
            return value
    return ""


def manager_chat_effort(*, deciding: bool = False, env: Mapping[str, str] | None = None) -> str:
    """One Manager chat turn: ``medium``; ``high`` while it rules on a team stage question.

    A chat turn answers, asks or hands work off; the team's own roles do the
    deep work. A stage ruling decides where the whole project goes next, so
    it keeps the careful setting.
    """
    return explicit_effort(
        "ARGUS_SKILL_SELF_REASONING_EFFORT", "ARGUS_SKILL_MANAGER_REASONING_EFFORT", env=env,
    ) or ("high" if deciding else "medium")


def is_bounded_mission(*, workflow_mode: str, paper_mission: bool, vertical: str) -> bool:
    """A direct, non-research, non-paper mission: one small piece of work."""
    if paper_mission or str(workflow_mode or "").strip().lower() != "direct":
        return False
    name = str(vertical or "").strip().lower()
    return not any(marker in name for marker in _DEEP_VERTICAL_MARKERS)


@dataclass(frozen=True)
class MissionEfforts:
    engineer_initial: str  # the Engineer's first round
    engineer: str  # every later round (the Reviewer asked for more)
    reviewer: str


def mission_efforts(*, bounded: bool, env: Mapping[str, str] | None = None) -> MissionEfforts:
    """Engineer and Reviewer effort for one mission.

    Bounded work: Engineer round one and the Reviewer at ``medium``. A second
    round, research, papers and staged work: ``high``. An explicit Engineer
    effort applies to every round unless the first-round knob says otherwise.
    """
    engineer = explicit_effort("ARGUS_SKILL_ENGINEER_REASONING_EFFORT", env=env)
    initial = explicit_effort("ARGUS_SKILL_ENGINEER_INITIAL_REASONING_EFFORT", env=env) or engineer
    reviewer = explicit_effort("ARGUS_SKILL_REVIEWER_REASONING_EFFORT", env=env)
    default = "medium" if bounded else "high"
    return MissionEfforts(
        engineer_initial=initial or default,
        engineer=engineer or "high",
        reviewer=reviewer or default,
    )


__all__ = [
    "MissionEfforts",
    "explicit_effort",
    "is_bounded_mission",
    "manager_chat_effort",
    "mission_efforts",
]
