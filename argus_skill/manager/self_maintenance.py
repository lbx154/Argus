"""Manager-owned decisions for evidence-bound daemon self-maintenance."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from ..roles.prompts.manager import build_maintenance_prompt


@dataclass(frozen=True)
class MaintenanceDecision:
    action: str
    reason: str
    problem: str = ""
    title: str = ""
    objective: str = ""
    acceptance_check: str = ""
    evidence_ids: tuple[str, ...] = ()
    affected_paths: tuple[str, ...] = ()
    error: str = ""


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    candidates = [raw]
    if "```" in raw:
        candidates.extend(
            block.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            for block in raw.split("```")[1::2]
        )
    start = raw.find("{")
    end = raw.rfind("}")
    if 0 <= start < end:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def parse_maintenance_decision(
    text: str,
    *,
    valid_evidence_ids: Iterable[str],
) -> MaintenanceDecision:
    payload = _extract_json(text)
    if payload is None:
        return MaintenanceDecision(
            action="no_action",
            reason="Manager returned no valid maintenance decision",
            error="invalid_json",
        )
    action = str(payload.get("action") or "").strip().lower()
    reason = str(payload.get("reason") or "").strip()
    if action == "no_action":
        return MaintenanceDecision(action=action, reason=reason or "no repair justified")
    valid = {str(value) for value in valid_evidence_ids if str(value)}
    if action == "adopt":
        evidence_ids = tuple(
            dict.fromkeys(
                str(value).strip()
                for value in (payload.get("evidence_ids") or [])
                if str(value).strip() in valid
            )
        )
        if not evidence_ids:
            return MaintenanceDecision(
                action="no_action",
                reason="Manager adoption decision lacked bound update evidence",
                error="incomplete_adoption",
            )
        return MaintenanceDecision(
            action="adopt",
            reason=reason or "adopt reviewed upstream change",
            acceptance_check=str(payload.get("acceptance_check") or "").strip(),
            evidence_ids=evidence_ids,
        )
    if action != "repair":
        return MaintenanceDecision(
            action="no_action",
            reason="Manager maintenance action was invalid",
            error="invalid_action",
        )

    evidence_ids = tuple(
        dict.fromkeys(
            str(value).strip()
            for value in (payload.get("evidence_ids") or [])
            if str(value).strip() in valid
        )
    )
    problem = str(payload.get("problem") or "").strip()
    title = str(payload.get("title") or "").strip()
    objective = str(payload.get("objective") or "").strip()
    acceptance = str(payload.get("acceptance_check") or "").strip()
    affected_paths = tuple(
        dict.fromkeys(
            str(value).strip()
            for value in (payload.get("affected_paths") or [])
            if str(value).strip()
        )
    )
    missing = [
        name
        for name, value in (
            ("evidence_ids", evidence_ids),
            ("problem", problem),
            ("title", title),
            ("objective", objective),
            ("acceptance_check", acceptance),
            ("affected_paths", affected_paths),
        )
        if not value
    ]
    if missing:
        return MaintenanceDecision(
            action="no_action",
            reason=(
                "Manager repair decision lacked evidence-bound fields: "
                + ", ".join(missing)
            ),
            error="incomplete_repair",
        )
    return MaintenanceDecision(
        action="repair",
        reason=reason or problem,
        problem=problem,
        title=title[:160],
        objective=objective,
        acceptance_check=acceptance,
        evidence_ids=evidence_ids,
        affected_paths=affected_paths,
    )


__all__ = [
    "MaintenanceDecision",
    "build_maintenance_prompt",
    "parse_maintenance_decision",
]
