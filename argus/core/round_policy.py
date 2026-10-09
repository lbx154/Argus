"""Per-vertical round-guard policy for the supervised Engineer/Reviewer loop.

A mission has no fixed round ceiling (``ARGUS_SKILL_MAX_ROUNDS`` defaults to
0). What remains are four anti-livelock guards in the round loop; how much
patience each one should have depends on the kind of work, so a vertical may
declare them through ``ROUND_POLICY`` (Python vertical) or ``round_policy``
(data-domain JSON):

``stall_threshold``
    Consecutive ``continue`` verdicts in which the Reviewer explicitly reports
    ``FORWARD_PROGRESS=false``. This is the Reviewer's judgement and the
    primary stall mechanism; a missing signal never counts.
``no_progress_threshold``
    Consecutive Engineer turns that produced no output at all. It never ends a
    mission while the Reviewer's verdict for the round reports progress.
``soft_round_limit``
    After this round, two consecutive verdicts without
    ``forward_progress=true`` settle the mission as stalled. Any true verdict
    in that window lets the work continue.
``hard_escalate_rounds``
    From this round on, a ``continue`` verdict must carry an explicit progress
    judgement; a missing one ends the mission so the Planner can re-plan.

``0`` disables a guard. A field the vertical leaves out keeps the framework
default, which is exactly the pre-policy behaviour, so a vertical that
declares nothing never gets a tighter hidden cap than before.

Precedence, highest first: an operator knob set in the environment or the
cockpit (``ARGUS_SKILL_STALL_THRESHOLD`` and siblings), an explicit value on
the mission's loop configuration, the vertical's declaration, the default.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from typing import Any

ROUND_POLICY_FIELDS: tuple[str, ...] = (
    "stall_threshold",
    "no_progress_threshold",
    "soft_round_limit",
    "hard_escalate_rounds",
)

#: Operator knob for each guard. Set, it overrides every vertical.
ROUND_POLICY_KNOBS: dict[str, str] = {
    "stall_threshold": "ARGUS_SKILL_STALL_THRESHOLD",
    "no_progress_threshold": "ARGUS_SKILL_NO_PROGRESS_THRESHOLD",
    "soft_round_limit": "ARGUS_SKILL_SOFT_ROUND_LIMIT",
    "hard_escalate_rounds": "ARGUS_SKILL_HARD_ESCALATE_ROUNDS",
}


class RoundPolicyError(ValueError):
    """A declared round policy is malformed."""


@dataclass(frozen=True)
class RoundPolicy:
    """Round-guard thresholds; ``None`` means "not declared here"."""

    stall_threshold: int | None = None
    no_progress_threshold: int | None = None
    soft_round_limit: int | None = None
    hard_escalate_rounds: int | None = None

    def over(self, base: RoundPolicy) -> RoundPolicy:
        """Return ``base`` with every field this policy declares replaced."""
        changes = {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if getattr(self, item.name) is not None
        }
        return replace(base, **changes)

    def as_dict(self) -> dict[str, int | None]:
        return {name: getattr(self, name) for name in ROUND_POLICY_FIELDS}


#: Framework default: the guard values every mission ran with before verticals
#: could declare their own.
DEFAULT_ROUND_POLICY = RoundPolicy(
    stall_threshold=4,
    no_progress_threshold=2,
    soft_round_limit=12,
    hard_escalate_rounds=24,
)


def _guard_value(owner: str, name: str, raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise RoundPolicyError(f"{owner}: round_policy.{name} must be an integer")
    if raw < 0:
        raise RoundPolicyError(f"{owner}: round_policy.{name} must be >= 0")
    return raw


def parse_round_policy(owner: str, raw: object) -> RoundPolicy | None:
    """Validate one declared policy. ``None`` means nothing was declared."""
    if raw is None:
        return None
    if isinstance(raw, RoundPolicy):
        return raw
    if not isinstance(raw, Mapping):
        raise RoundPolicyError(f"{owner}: round_policy must be a mapping")
    unknown = sorted(str(key) for key in raw if key not in ROUND_POLICY_FIELDS)
    if unknown:
        raise RoundPolicyError(
            f"{owner}: round_policy has unknown fields: {', '.join(unknown)}"
        )
    return RoundPolicy(
        **{name: _guard_value(owner, name, value) for name, value in raw.items()}
    )


def operator_round_policy(
    env: Mapping[str, str] | None = None,
    persisted: Mapping[str, str] | None = None,
) -> RoundPolicy:
    """Read the operator's guard knobs; unset or unusable knobs declare nothing."""
    from .knobs import resolve_knob

    values: dict[str, Any] = {}
    for name, knob in ROUND_POLICY_KNOBS.items():
        resolved = resolve_knob(knob, "", env=env, persisted=persisted)
        if resolved.source == "default":
            continue
        try:
            value = int(resolved.value)
        except ValueError:
            continue
        if value >= 0:
            values[name] = value
    return RoundPolicy(**values)


def resolve_round_policy(
    vertical_policy: RoundPolicy | None = None,
    *,
    explicit: RoundPolicy | None = None,
    env: Mapping[str, str] | None = None,
    persisted: Mapping[str, str] | None = None,
) -> RoundPolicy:
    """Return the fully populated policy one mission runs with."""
    policy = DEFAULT_ROUND_POLICY
    if vertical_policy is not None:
        policy = vertical_policy.over(policy)
    if explicit is not None:
        policy = explicit.over(policy)
    return operator_round_policy(env=env, persisted=persisted).over(policy)


__all__ = [
    "DEFAULT_ROUND_POLICY",
    "ROUND_POLICY_FIELDS",
    "ROUND_POLICY_KNOBS",
    "RoundPolicy",
    "RoundPolicyError",
    "operator_round_policy",
    "parse_round_policy",
    "resolve_round_policy",
]
