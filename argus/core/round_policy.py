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

Precedence, highest first: an operator knob (``ARGUS_SKILL_STALL_THRESHOLD``
and siblings) from the process environment, then the same knob saved from the
cockpit config view; an explicit value on the mission's loop configuration;
the vertical's declaration; the default. A knob set to ``vertical`` (the value
the cockpit saves to hand control back) declares nothing. An unusable knob
value is ignored with a warning.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace

log = logging.getLogger(__name__)

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


#: Knob value meaning "no operator override; let the vertical decide".
ROUND_POLICY_KNOB_INHERIT = "vertical"
_INHERIT_SPELLINGS = frozenset({"vertical", "(vertical)", "default", "inherit"})

_warned: set[tuple[str, str, str]] = set()


def _warn_once(owner: str, name: str, detail: str) -> None:
    key = (owner, name, detail)
    if key in _warned:
        return
    _warned.add(key)
    log.warning("%s: ignoring round_policy.%s: %s", owner, name, detail)


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


def parse_round_policy_lenient(owner: str, raw: object) -> RoundPolicy | None:
    """Like ``parse_round_policy`` but keep valid fields and drop bad ones.

    For hand-edited or agent-authored JSON: one typo must not silently throw
    away the rest of the block. Each dropped field is warned about once.
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        _warn_once(owner, "*", f"must be a mapping, got {type(raw).__name__}")
        return None
    values: dict[str, int] = {}
    for key, value in raw.items():
        if key not in ROUND_POLICY_FIELDS:
            _warn_once(owner, str(key), "unknown field")
            continue
        try:
            values[key] = _guard_value(owner, key, value)
        except RoundPolicyError as exc:
            _warn_once(owner, str(key), str(exc).rsplit(f"round_policy.{key} ", 1)[-1])
    return RoundPolicy(**values)


def _operator_values(
    env: Mapping[str, str] | None,
    persisted: Mapping[str, str] | None,
) -> dict[str, tuple[int, str]]:
    from .knobs import resolve_knob

    if persisted is None:
        from .knob_store import read_persisted_knobs

        persisted = read_persisted_knobs()
    values: dict[str, tuple[int, str]] = {}
    for name, knob in ROUND_POLICY_KNOBS.items():
        resolved = resolve_knob(knob, "", env=env, persisted=persisted)
        if resolved.source == "default":
            continue
        raw = resolved.value.strip()
        if raw.lower() in _INHERIT_SPELLINGS:
            continue
        try:
            value = int(raw)
        except ValueError:
            value = -1
        if value < 0:
            _warn_once(
                f"{knob} ({resolved.source})",
                name,
                f"{raw!r} is not a non-negative integer",
            )
            continue
        values[name] = (value, f"operator knob {knob} ({resolved.source})")
    return values


def operator_round_policy(
    env: Mapping[str, str] | None = None,
    persisted: Mapping[str, str] | None = None,
) -> RoundPolicy:
    """Read the operator's guard knobs; unset or unusable knobs declare nothing."""
    return RoundPolicy(
        **{name: value for name, (value, _src) in _operator_values(env, persisted).items()}
    )


def explain_round_policy(
    vertical_policy: RoundPolicy | None = None,
    *,
    explicit: RoundPolicy | None = None,
    vertical: str = "",
    env: Mapping[str, str] | None = None,
    persisted: Mapping[str, str] | None = None,
) -> tuple[RoundPolicy, dict[str, str]]:
    """Return the mission's policy and where each value came from."""
    values: dict[str, int] = {}
    sources: dict[str, str] = {}
    layers = (
        (DEFAULT_ROUND_POLICY, "framework default"),
        (vertical_policy, f"vertical {vertical!r} round_policy" if vertical else "vertical round_policy"),
        (explicit, "mission loop config"),
    )
    for layer, label in layers:
        if layer is None:
            continue
        for name in ROUND_POLICY_FIELDS:
            value = getattr(layer, name)
            if value is not None:
                values[name] = value
                sources[name] = label
    for name, (value, label) in _operator_values(env, persisted).items():
        values[name] = value
        sources[name] = label
    return RoundPolicy(**values), sources


def resolve_round_policy(
    vertical_policy: RoundPolicy | None = None,
    *,
    explicit: RoundPolicy | None = None,
    env: Mapping[str, str] | None = None,
    persisted: Mapping[str, str] | None = None,
) -> RoundPolicy:
    """Return the fully populated policy one mission runs with."""
    return explain_round_policy(
        vertical_policy, explicit=explicit, env=env, persisted=persisted
    )[0]


__all__ = [
    "DEFAULT_ROUND_POLICY",
    "ROUND_POLICY_FIELDS",
    "ROUND_POLICY_KNOB_INHERIT",
    "ROUND_POLICY_KNOBS",
    "RoundPolicy",
    "RoundPolicyError",
    "explain_round_policy",
    "operator_round_policy",
    "parse_round_policy",
    "parse_round_policy_lenient",
    "resolve_round_policy",
]
