"""Vertical-agnostic research target and reviewer-assessment contract."""
from __future__ import annotations

import json
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
    if target == "exploratory":
        if result_class not in _EXPLORATORY_TERMINAL_CLASSES:
            return f"result_class_not_exploratory_terminal:{result_class}"
        if novelty == "unverified" and result_class not in {
            "structured_failure_report",
            "honest_final_report",
        }:
            return "novelty_not_verified"
        return ""
    if str(scope or "").strip().lower() == "bounded":
        return f"bounded_cycle_cannot_complete_{target}"
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
    "normalize_research_result",
    "normalize_research_target_level",
    "research_completion_issue",
    "research_pause_status",
    "resolve_research_target_level",
    "resolve_research_target_set_at",
]
