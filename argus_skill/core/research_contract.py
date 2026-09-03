"""Vertical-agnostic research target and reviewer-assessment contract."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .pipeline_state import read_pipeline_state

RESEARCH_TARGET_LEVELS = ("exploratory", "publishable", "doctoral")
RESEARCH_DIRECTION_MODES = ("broad", "locked")
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

#: Every enum-valued field of a research result, paired with its legal set.
#:
#: One table, read by the validator, by the diagnostic that explains a
#: rejection, and by the Reviewer prompt that asks for the block in the first
#: place — so the three cannot describe three different contracts. The prompt
#: renders this; it does not restate it.
RESULT_FIELD_CHOICES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("result_class", RESULT_CLASSES),
    ("correctness_status", CORRECTNESS_STATUSES),
    ("novelty_status", NOVELTY_STATUSES),
    ("significance_status", SIGNIFICANCE_STATUSES),
    ("statement_fidelity_status", STATEMENT_FIDELITY_STATUSES),
)

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
    "complete_solution",
    "verified_new_result",
    "new_theorem",
    "improved_bound",
    "new_infinite_family",
    "new_reduction",
    "exact_counterexample",
    "lean_local_verification",
})
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
    try:
        payload = read_pipeline_state(project_root)
    except (OSError, ValueError):
        return None
    return normalize_research_target_level(payload.get("research_target_level"))


def normalize_research_direction_mode(value: Any) -> str | None:
    mode = str(value or "").strip().lower()
    return mode if mode in RESEARCH_DIRECTION_MODES else None


def resolve_research_direction_mode(project_root: object) -> str | None:
    try:
        payload = read_pipeline_state(project_root)
    except (OSError, ValueError):
        return None
    return normalize_research_direction_mode(payload.get("research_direction_mode"))


def research_target_contract(
    *,
    supported_levels: tuple[str, ...],
    selected_level: str | None,
) -> ResearchTargetContract:
    """Build the generic target contract from a vertical-owned declaration."""
    return ResearchTargetContract(
        supported_levels=tuple(
            level
            for value in supported_levels
            if (level := normalize_research_target_level(value)) is not None
        ),
        selected_level=normalize_research_target_level(selected_level),
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
    try:
        payload = read_pipeline_state(project_root)
    except (OSError, ValueError):
        return None
    raw_value = payload.get("research_target_set_at")
    if raw_value is None:
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _resolve_research_result_fields(value: dict[str, Any]) -> dict[str, str]:
    """Read the five enum fields, honouring the historical field names.

    Split out so the validator and the diagnostic that explains its refusal
    read the payload exactly once, the same way. A diagnostic that re-derives
    the fields is a diagnostic that can disagree with the check it explains.
    """
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
    return {
        "result_class": str(value.get("result_class") or "").strip(),
        "correctness_status": correctness,
        "novelty_status": novelty,
        "significance_status": significance,
        "statement_fidelity_status": fidelity,
    }


def research_result_rejection(value: Any) -> str:
    """Name the fields that make a research result unreadable, or ``""``.

    ``research_completion_issue`` answered every unreadable result with a flat
    ``missing_or_invalid_research_result`` while every check *after* it named
    its offender (``significance_below_publishable:exploratory``). The one
    that fires most often was the one that said least.

    Testbed run 15 (``s-f0dbba19``) is what that costs. Six Reviewer rounds
    each emitted a ``RESEARCH_RESULT`` block and all six were discarded whole:
    the prompt listed the five field *names* and never their legal values, so
    the model supplied fluent inventions — ``result_class=proof``,
    ``correctness_status=proved``, ``significance_status=bounded_complete``,
    ``statement_fidelity_status=supported``. The operator was then told the
    research result was missing, against a reviewer message that visibly
    contained one, and the project could not close at its final stage.

    The prompt now renders ``RESULT_FIELD_CHOICES``, which is the actual fix.
    This is the second line: when a value still misses, the trace says which
    field and which value rather than leaving an operator to diff two enums by
    eye.
    """
    if not isinstance(value, dict):
        return "missing_or_invalid_research_result"
    fields = _resolve_research_result_fields(value)
    offenders = [
        f"{name}={fields[name] or '(empty)'}"
        for name, choices in RESULT_FIELD_CHOICES
        if fields[name] not in choices
    ]
    if not offenders:
        return ""
    return "invalid_research_result_fields:" + ",".join(offenders)


def normalize_research_result(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    fields = _resolve_research_result_fields(value)
    if any(fields[name] not in choices for name, choices in RESULT_FIELD_CHOICES):
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
    return {**fields, "evidence": evidence, "limitations": limitations}


#: Which significance ratings clear each research target.
#:
#: A target level is a bar, and two bars that accept the same set are one bar
#: with two names. That is what ``publishable`` and ``doctoral`` were for every
#: result class except surveys: the survey branch below has always indexed the
#: accepted set by target, while the original-result branch asked only for
#: ``significance in {publishable, doctoral}`` regardless of which of the two
#: was requested — so an operator who asked for doctoral work and received a
#: result its own author graded ``publishable`` got a completion. The distinction
#: survived in the vocabulary, in the prompts that ask a Reviewer to grade
#: against it, and in the operator's expectation; it did not survive in the
#: check. One table, read by both branches, is the whole fix.
#:
#: Monotone by construction: a higher rating always clears a lower target, so
#: nothing is refused for being too good.
ACCEPTED_SIGNIFICANCE: dict[str, frozenset[str]] = {
    "exploratory": frozenset({"exploratory", "publishable", "doctoral"}),
    "publishable": frozenset({"publishable", "doctoral"}),
    "doctoral": frozenset({"doctoral"}),
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
        return research_result_rejection(value)
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
    if str(scope or "").strip().lower() == "idea_only" and result_class == "new_candidate":
        if novelty != "verified_new":
            return "novelty_not_verified_new"
        if significance not in ACCEPTED_SIGNIFICANCE[target]:
            return f"significance_below_{target}:{significance}"
        return ""
    if result_class == "literature_review":
        if novelty not in {"known", "not_applicable"}:
            return "survey_novelty_must_be_not_applicable"
        if significance in ACCEPTED_SIGNIFICANCE[target]:
            return ""
        return f"survey_significance_below_{target}:{significance}"
    if target == "exploratory":
        if result_class not in _EXPLORATORY_TERMINAL_CLASSES:
            return f"result_class_not_exploratory_terminal:{result_class}"
        if novelty == "unverified":
            return "novelty_not_verified"
        return ""
    if result_class not in _BREAKTHROUGH_CLASSES:
        return f"result_class_below_{target}:{result_class}"
    if novelty != "verified_new":
        return "novelty_not_verified_new"
    if significance not in ACCEPTED_SIGNIFICANCE[target]:
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
    "ACCEPTED_SIGNIFICANCE",
    "CORRECTNESS_STATUSES",
    "NOVELTY_STATUSES",
    "RESEARCH_DIRECTION_MODES",
    "RESEARCH_TARGET_LEVELS",
    "RESULT_CLASSES",
    "RESULT_FIELD_CHOICES",
    "SIGNIFICANCE_STATUSES",
    "STATEMENT_FIDELITY_STATUSES",
    "ResearchTargetContract",
    "adapt_legacy_research_result_payload",
    "normalize_research_result",
    "normalize_research_direction_mode",
    "normalize_research_target_level",
    "research_completion_issue",
    "research_pause_status",
    "research_result_rejection",
    "research_target_contract",
    "research_target_env_override",
    "resolve_research_target_level",
    "resolve_research_direction_mode",
    "resolve_research_target_set_at",
]
