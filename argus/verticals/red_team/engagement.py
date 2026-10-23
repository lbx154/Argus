"""The engagement gate: machine-readable authorization for offensive work.

Offensive security is the one capability whose first pipeline stage is a
permission check. ``authorize`` cannot close without
``<project>/SECURITY_ENGAGEMENT.json`` naming the operator, a current window,
every target under audit with its authorization reference, the allowed method
categories, and the impact flags the operator has explicitly enabled.

The gate is deliberately closed-vocabulary on methods and targets: whether a
declaration *authorizes* a mission is only decidable when each category's
impact requirement is known, so an unknown method category is rejected with the
known list, not waved through. Evidence-by-canary (proving access with a
harmless demonstrated action) is the default expectation the checklists carry;
nothing here makes a destructive demonstration legitimate — the impact flags
exist so the operator has to say so explicitly first.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Iterable

ENGAGEMENT_FILE = "SECURITY_ENGAGEMENT.json"

#: What a declared target entry may be audited as. ``source_audit`` covers the
#: public repository source; ``host`` covers a running instance the operator
#: owns or is authorized to test.
TARGET_KINDS = frozenset({"source_audit", "host"})

#: Impact dimensions an operator must explicitly enable before methods that
#: carry them are authorized. Default expectation: all false.
IMPACT_FLAGS: tuple[str, ...] = ("destructive", "dos", "data_modification")

#: Allowed method categories mapped to the impact flags each requires the
#: operator to have explicitly enabled in the declaration. Canary-safe audit
#: and reproduction methods require none; anything that can deny service or
#: modify data requires the matching flag.
METHOD_IMPACT_REQUIREMENTS: dict[str, frozenset[str]] = {
    "source_audit": frozenset(),
    "local_reproduction": frozenset(),
    "network_probe": frozenset(),
    "dos_proof": frozenset({"dos"}),
    "destructive_proof": frozenset({"destructive"}),
    "data_modification_proof": frozenset({"data_modification"}),
}


def _date_issues(window: object, today: date) -> list[str]:
    if not isinstance(window, dict):
        return ["window must be an object with start and end dates"]
    issues: list[str] = []
    parsed: dict[str, date] = {}
    for key in ("start", "end"):
        value = window.get(key)
        if not isinstance(value, str) or not value.strip():
            issues.append(f"window.{key} must be an ISO date (YYYY-MM-DD)")
            continue
        try:
            parsed[key] = date.fromisoformat(value.strip())
        except ValueError:
            issues.append(f"window.{key} {value!r} is not an ISO date (YYYY-MM-DD)")
    if len(parsed) != 2:
        return issues
    if parsed["end"] < parsed["start"]:
        issues.append("window ends before it starts")
    elif not parsed["start"] <= today <= parsed["end"]:
        issues.append(
            f"window is not current: {parsed['start']}..{parsed['end']} does not "
            f"cover {today.isoformat()}"
        )
    return issues


def _target_issues(declaration: dict[str, Any], mission_targets: Iterable[str]) -> list[str]:
    targets = declaration.get("targets")
    if not isinstance(targets, list) or not targets:
        return ["targets must be a non-empty list of declared entries"]
    issues: list[str] = []
    refs: list[str] = []
    for index, entry in enumerate(targets):
        label = f"targets[{index}]"
        if not isinstance(entry, dict):
            issues.append(f"{label} must be an object")
            continue
        kind = entry.get("kind")
        if kind not in TARGET_KINDS:
            issues.append(
                f"{label}: kind {kind!r} must be one of "
                f"{', '.join(sorted(TARGET_KINDS))}"
            )
        ref = entry.get("ref")
        if not isinstance(ref, str) or not ref.strip():
            issues.append(f"{label}: ref must be a non-empty string")
        else:
            refs.append(ref.strip())
        authorization = entry.get("authorization")
        if not isinstance(authorization, str) or not authorization.strip():
            issues.append(f"{label}: authorization must be a non-empty reference")
    for wanted in mission_targets:
        if not isinstance(wanted, str):
            continue
        if wanted.strip() and wanted.strip() not in refs:
            issues.append(
                f"mission target {wanted.strip()!r} is not covered by any declared "
                "target entry"
            )
    return issues


def _method_issues(declaration: dict[str, Any]) -> list[str]:
    methods = declaration.get("methods")
    if not isinstance(methods, list) or not methods:
        return ["methods must be a non-empty list of allowed method categories"]
    issues: list[str] = []
    declared: list[str] = []
    for index, method in enumerate(methods):
        if not isinstance(method, str) or not method.strip():
            issues.append(f"methods[{index}] must be a non-empty method category")
            continue
        if method.strip() not in METHOD_IMPACT_REQUIREMENTS:
            issues.append(
                f"methods[{index}]: unknown method category {method.strip()!r}; "
                f"known categories: {', '.join(sorted(METHOD_IMPACT_REQUIREMENTS))}"
            )
        elif method.strip() not in declared:
            declared.append(method.strip())
    if issues:
        return issues
    impact = declaration.get("impact")
    if not isinstance(impact, dict):
        return [
            "impact must be an object with the flags "
            f"{', '.join(IMPACT_FLAGS)} (all default false)"
        ]
    issues = []
    values: dict[str, bool] = {}
    for flag in IMPACT_FLAGS:
        if flag not in impact:
            issues.append(f"impact.{flag} is missing; declare it explicitly (false unless the operator enables it)")
            continue
        value = impact[flag]
        if not isinstance(value, bool):
            issues.append(f"impact.{flag} must be a boolean")
            continue
        values[flag] = value
    if issues:
        return issues
    for method in declared:
        required = METHOD_IMPACT_REQUIREMENTS[method]
        missing = sorted(flag for flag in required if not values.get(flag, False))
        if missing:
            issues.append(
                f"method {method!r} requires impact flag(s) {', '.join(missing)} "
                "to be explicitly enabled by the operator"
            )
    return issues


def engagement_issues(
    project_root: Path | str,
    *,
    mission_targets: Iterable[str] = (),
    today: date | None = None,
) -> tuple[str, ...]:
    """Validate the engagement declaration; an empty tuple authorizes.

    ``mission_targets`` are the target refs this specific mission will touch:
    every one must be covered by a declared entry before the gate accepts.
    ``today`` is injectable so window-currency is testable; it defaults to the
    real current date.
    """
    path = Path(str(project_root)) / ENGAGEMENT_FILE
    if not path.is_file():
        return (
            f"{ENGAGEMENT_FILE} is missing at the project root: the authorize "
            "stage cannot close without a machine-readable engagement "
            "declaration (operator, window, targets, methods, impact)",
        )
    try:
        declaration = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return (f"{ENGAGEMENT_FILE} is unreadable or not valid JSON: {exc}",)
    if not isinstance(declaration, dict):
        return (f"{ENGAGEMENT_FILE} must be a JSON object",)
    issues: list[str] = []
    operator = declaration.get("operator")
    if not isinstance(operator, str) or not operator.strip():
        issues.append("operator must be a non-empty string")
    issues.extend(_date_issues(declaration.get("window"), today or date.today()))
    issues.extend(_target_issues(declaration, mission_targets))
    issues.extend(_method_issues(declaration))
    return tuple(issues)


__all__ = [
    "ENGAGEMENT_FILE",
    "IMPACT_FLAGS",
    "METHOD_IMPACT_REQUIREMENTS",
    "TARGET_KINDS",
    "engagement_issues",
]
