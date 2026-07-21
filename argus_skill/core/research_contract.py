"""Vertical-agnostic research target and reviewer-assessment contract."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RESEARCH_TARGET_LEVELS = ("exploratory", "publishable", "doctoral")
RESULT_CLASSES = (
    "known_result",
    "finite_verification",
    "counterexample",
    "partial_result",
    "new_candidate",
    "novelty_unverified",
    "verified_new_result",
    "structured_failure_report",
    "honest_final_report",
    "literature_review",
    "lean_local_verification",
    "complete_solution",
    "new_theorem",
    "improved_bound",
    "new_infinite_family",
    "new_reduction",
    "exact_counterexample",
    "exhausted_current_methods",
)
CORRECTNESS_STATUSES = ("verified", "incorrect", "uncertain")
NOVELTY_STATUSES = ("known", "unverified", "verified_new", "not_applicable")
SIGNIFICANCE_STATUSES = (
    "exploratory",
    "publishable",
    "doctoral",
    "unverified",
    "not_applicable",
)
STATEMENT_FIDELITY_STATUSES = ("verified", "failed", "uncertain", "not_applicable")

_BREAKTHROUGH_CLASSES = frozenset({
    "verified_new_result",
    "complete_solution",
    "new_theorem",
    "improved_bound",
    "new_infinite_family",
    "new_reduction",
    "exact_counterexample",
})
_EXPLORATORY_TERMINAL_CLASSES = frozenset({
    "known_result",
    "finite_verification",
    "counterexample",
    "structured_failure_report",
    "honest_final_report",
    "complete_solution",
    "verified_new_result",
    "new_theorem",
    "improved_bound",
    "new_infinite_family",
    "new_reduction",
    "exact_counterexample",
    "lean_local_verification",
})
_STATE_RELPATH = ("research", "PIPELINE_STATE.json")


@dataclass(frozen=True)
class ResearchTargetContract:
    supported_levels: tuple[str, ...]
    selected_level: str | None

    @property
    def required(self) -> bool:
        return bool(self.supported_levels)


def normalize_research_target_level(value: Any) -> str | None:
    level = str(value or "").strip().lower()
    return level if level in RESEARCH_TARGET_LEVELS else None


def resolve_research_target_level(project_root: object) -> str | None:
    path = Path(str(project_root)).joinpath(*_STATE_RELPATH)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return normalize_research_target_level(payload.get("research_target_level"))


def resolve_research_target_contract(
    project_root: object,
) -> ResearchTargetContract:
    selected = resolve_research_target_level(project_root)
    try:
        from ..skills.vertical_select import resolve_checklist_vertical
        from ..verticals._base import (
            load_vertical,
            vertical_research_target_levels,
        )

        vertical = resolve_checklist_vertical(project_root)
        supported = (
            tuple(vertical_research_target_levels(
                load_vertical(vertical, project_root=project_root)
            ))
            if vertical is not None
            else ()
        )
    except Exception:  # noqa: BLE001
        supported = RESEARCH_TARGET_LEVELS if selected is not None else ()
    return ResearchTargetContract(
        supported_levels=supported,
        selected_level=selected,
    )


def research_target_env_override() -> str | None:
    """Read the generic target override, with one legacy env-name adapter."""
    raw = os.environ.get("ARGUS_SKILL_RESEARCH_TARGET_LEVEL")
    if raw is None:
        raw = os.environ.get("ARGUS_SKILL_MATH_RESEARCH_TARGET_LEVEL")
    if raw is None:
        return None
    level = normalize_research_target_level(raw)
    if level is None:
        raise ValueError(
            "research target override must be exploratory, publishable, or doctoral"
        )
    return level


def resolve_research_target_set_at(project_root: object) -> float | None:
    path = Path(str(project_root)).joinpath(*_STATE_RELPATH)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw_value = payload.get("research_target_set_at")
    if raw_value is None:
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def normalize_research_result(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result_class = str(value.get("result_class") or "").strip()
    correctness = str(
        value.get("correctness_status") or value.get("correctness") or ""
    ).strip()
    novelty = str(
        value.get("novelty_status") or value.get("novelty") or ""
    ).strip()
    legacy_shape = (
        "correctness_status" not in value
        and ("correctness" in value or "novelty" in value)
    )
    significance = str(
        value.get("significance_status")
        or ("exploratory" if legacy_shape else "")
    ).strip()
    fidelity = str(
        value.get("statement_fidelity_status")
        or value.get("statement_fidelity")
        or "not_applicable"
    ).strip()
    if (
        result_class not in RESULT_CLASSES
        or correctness not in CORRECTNESS_STATUSES
        or novelty not in NOVELTY_STATUSES
        or significance not in SIGNIFICANCE_STATUSES
        or fidelity not in STATEMENT_FIDELITY_STATUSES
    ):
        return None
    evidence = (
        [
            str(item or "").strip()[:500]
            for item in value.get("evidence", [])[:12]
            if str(item or "").strip()
        ]
        if isinstance(value.get("evidence"), list)
        else []
    )
    limitations = (
        [
            str(item or "").strip()[:500]
            for item in value.get("limitations", [])[:12]
            if str(item or "").strip()
        ]
        if isinstance(value.get("limitations"), list)
        else []
    )
    return {
        "result_class": result_class,
        "correctness_status": correctness,
        "novelty_status": novelty,
        "significance_status": significance,
        "statement_fidelity_status": fidelity,
        "evidence": evidence,
        "limitations": limitations,
    }


def adapt_legacy_research_result_payload(
    payload: Any,
) -> dict[str, Any] | None:
    """Read the canonical result or a historical field through one adapter."""
    if not isinstance(payload, dict):
        return None
    raw = payload.get("research_result")
    if not isinstance(raw, dict):
        raw = payload.get("math_result")
    return normalize_research_result(raw)


def research_completion_issue(
    value: Any,
    *,
    research_target_level: str | None,
    scope: str = "",
) -> str:
    target = normalize_research_target_level(research_target_level)
    if target is None:
        return ""
    result = normalize_research_result(value)
    if result is None:
        return "missing_or_invalid_research_result"
    result_class = result["result_class"]
    correctness = result["correctness_status"]
    novelty = result["novelty_status"]
    significance = result["significance_status"]
    fidelity = result["statement_fidelity_status"]
    if not result["evidence"]:
        return "missing_research_evidence"
    if correctness != "verified":
        return "correctness_not_verified"
    if fidelity == "failed":
        return "statement_fidelity_failed"
    if str(scope or "").strip().lower() == "bounded":
        # A bounded backlog item certifies only its own acceptance criteria, not
        # the persisted project-level research target.  Keep the structured
        # result and evidence checks above, but leave terminal novelty and
        # significance to final-submission missions.
        return ""
    if target == "exploratory":
        if result_class not in _EXPLORATORY_TERMINAL_CLASSES:
            return f"result_class_not_exploratory_terminal:{result_class}"
        if novelty == "unverified" and result_class not in {
            "structured_failure_report",
            "honest_final_report",
        }:
            return "novelty_not_verified"
        return ""
    if result_class not in _BREAKTHROUGH_CLASSES:
        return f"result_class_below_{target}:{result_class}"
    if novelty != "verified_new":
        return "novelty_not_verified_new"
    if significance not in {"publishable", "doctoral"}:
        return f"significance_below_{target}:{significance}"
    return ""


def research_pause_status(value: Any) -> str:
    result = normalize_research_result(value)
    if result is None:
        return "research_incomplete"
    if result["result_class"] == "exhausted_current_methods":
        return "exhausted_current_methods"
    if result["result_class"] in {
        "structured_failure_report",
        "honest_final_report",
    }:
        return "paused_no_breakthrough"
    return "research_incomplete"


__all__ = [
    "CORRECTNESS_STATUSES",
    "NOVELTY_STATUSES",
    "RESEARCH_TARGET_LEVELS",
    "RESULT_CLASSES",
    "SIGNIFICANCE_STATUSES",
    "STATEMENT_FIDELITY_STATUSES",
    "ResearchTargetContract",
    "adapt_legacy_research_result_payload",
    "normalize_research_result",
    "normalize_research_target_level",
    "research_completion_issue",
    "research_pause_status",
    "research_target_env_override",
    "resolve_research_target_contract",
    "resolve_research_target_level",
    "resolve_research_target_set_at",
]
