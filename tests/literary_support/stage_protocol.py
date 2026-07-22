"""Test-only literary STAGE PROTOCOL conformance helper, extracted
from the real verticals, not designed in a vacuum.

By loop 11 there are five real literary verticals (fiction, classical_poetry,
modern_poetry, prose, literary_editor). They deliberately have DIFFERENT stage
flows (fiction: intake→plan→draft→state_update→review→revise; editor:
intake→diagnose→revision_plan→edit→verify). This module captures only what they
already share and validates conformance — it does NOT impose one STAGE_ORDER.

The shared shape every literary ``stages`` module exposes:

* ``STAGE_ORDER``      — the ordered stage names (each vertical's own);
* ``completion_gate``  — how the mission ends;
* ``STAGE_CHECKS``     — per-stage runtime gates ``{stage: [(desc, cmd), ...]}``;
* ``REVIEWER_CHECKLISTS`` — per-stage ``{stage: (skill, instructions, files)}``;
* ``role_banner(role)`` — role framing for planner/engineer/reviewer.

:func:`extract_protocol` reads a stage module into a per-stage :class:`StageSpec`
(name / required_inputs / produced_artifacts / validations / next) by parsing what
the vertical ALREADY declares; :func:`validate_stage_module` raises if a module
does not conform (missing attribute, a check for a stage not in STAGE_ORDER, a
stage with no gate, a role_banner that returns nothing).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Attributes every conforming literary stage module must expose.
REQUIRED_ATTRS: tuple[str, ...] = (
    "STAGE_ORDER", "completion_gate", "STAGE_CHECKS", "REVIEWER_CHECKLISTS",
    "role_banner",
)

#: The roles role_banner must answer with a non-empty framing.
BANNER_ROLES: tuple[str, ...] = ("planner", "engineer", "reviewer")

_ARTIFACT_RE = re.compile(r"test\s+-[sf]\s+(\S+)")


class StageProtocolError(ValueError):
    """Raised when a stage module does not conform to the shared protocol."""


@dataclass
class StageSpec:
    """The extracted contract of a single stage."""
    name: str
    required_inputs: list[str] = field(default_factory=list)
    produced_artifacts: list[str] = field(default_factory=list)
    validations: list[str] = field(default_factory=list)
    next: str | None = None


def _produced_from_cmds(cmds: list[str]) -> list[str]:
    seen: list[str] = []
    for cmd in cmds:
        for m in _ARTIFACT_RE.findall(cmd):
            if m not in seen:
                seen.append(m)
    return seen


def validate_stage_module(module: Any) -> None:
    """Raise :class:`StageProtocolError` unless ``module`` conforms to the protocol.

    Checks the shared shape only; each vertical keeps its own stage names/order.
    """
    for attr in REQUIRED_ATTRS:
        if not hasattr(module, attr):
            raise StageProtocolError(f"missing required attribute {attr!r}")

    order = module.STAGE_ORDER
    if not isinstance(order, (list, tuple)) or not order:
        raise StageProtocolError("STAGE_ORDER must be a non-empty sequence")
    if any(not isinstance(s, str) or not s for s in order):
        raise StageProtocolError("STAGE_ORDER entries must be non-empty strings")
    if len(set(order)) != len(order):
        raise StageProtocolError("STAGE_ORDER has duplicate stage names")
    order_set = set(order)

    if not isinstance(module.completion_gate, str):
        raise StageProtocolError("completion_gate must be a string")

    checks = module.STAGE_CHECKS
    if not isinstance(checks, dict):
        raise StageProtocolError("STAGE_CHECKS must be a dict")
    stray = set(checks) - order_set
    if stray:
        raise StageProtocolError(
            f"STAGE_CHECKS names stages not in STAGE_ORDER: {sorted(stray)}")
    for stage in order:
        if stage not in checks:
            raise StageProtocolError(f"stage {stage!r} has no STAGE_CHECKS gate")
        entries = checks[stage]
        if not isinstance(entries, list) or not entries:
            raise StageProtocolError(f"stage {stage!r} checks must be a non-empty list")
        for e in entries:
            if (not isinstance(e, tuple) or len(e) != 2
                    or not all(isinstance(x, str) for x in e)):
                raise StageProtocolError(
                    f"stage {stage!r} check must be a (desc, cmd) string pair, got {e!r}")

    rc = module.REVIEWER_CHECKLISTS
    if not isinstance(rc, dict):
        raise StageProtocolError("REVIEWER_CHECKLISTS must be a dict")
    stray = set(rc) - order_set
    if stray:
        raise StageProtocolError(
            f"REVIEWER_CHECKLISTS names stages not in STAGE_ORDER: {sorted(stray)}")
    for stage, entry in rc.items():
        if (not isinstance(entry, tuple) or len(entry) != 3
                or not isinstance(entry[0], str) or not isinstance(entry[1], str)
                or not isinstance(entry[2], list)):
            raise StageProtocolError(
                f"REVIEWER_CHECKLISTS[{stage!r}] must be (skill, instructions, files[])")

    if not callable(module.role_banner):
        raise StageProtocolError("role_banner must be callable")
    for role in BANNER_ROLES:
        banner = module.role_banner(role)
        if not isinstance(banner, str) or not banner.strip():
            raise StageProtocolError(
                f"role_banner({role!r}) must return a non-empty string")


def extract_protocol(module: Any) -> dict[str, StageSpec]:
    """Extract a per-stage :class:`StageSpec` from a conforming stage module.

    Validates first, then reads what the vertical already declares: required_inputs
    from REVIEWER_CHECKLISTS files, produced_artifacts from the ``test -s/-f``
    targets in STAGE_CHECKS, validations from the remaining gate commands, and
    ``next`` from STAGE_ORDER.
    """
    validate_stage_module(module)
    order = list(module.STAGE_ORDER)
    checks = module.STAGE_CHECKS
    rc = module.REVIEWER_CHECKLISTS
    specs: dict[str, StageSpec] = {}
    for i, stage in enumerate(order):
        cmds = [cmd for _desc, cmd in checks[stage]]
        produced = _produced_from_cmds(cmds)
        validations = [c for c in cmds if "argus_skill" in c]
        inputs = list(rc[stage][2]) if stage in rc else []
        specs[stage] = StageSpec(
            name=stage,
            required_inputs=inputs,
            produced_artifacts=produced,
            validations=validations,
            next=order[i + 1] if i + 1 < len(order) else None,
        )
    return specs


__all__ = [
    "REQUIRED_ATTRS",
    "BANNER_ROLES",
    "StageProtocolError",
    "StageSpec",
    "validate_stage_module",
    "extract_protocol",
]
