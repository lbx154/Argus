"""Structured result taxonomy and completion policy for the Math vertical."""
from __future__ import annotations

from typing import Any

RESULT_CLASSES = (
    "known_result",
    "finite_verification",
    "counterexample",
    "partial_result",
    "new_candidate",
    "novelty_unverified",
    "verified_new_result",
)
CORRECTNESS_VERDICTS = ("verified", "incorrect", "uncertain")
NOVELTY_VERDICTS = ("known", "unverified", "verified_new", "not_applicable")
FIDELITY_VERDICTS = ("verified", "failed", "uncertain")

_COMPLETION_CLASSES = frozenset(
    {"known_result", "counterexample", "verified_new_result"}
)


def math_completion_issue(value: Any, *, bounded: bool = False) -> str:
    """Return a stable reason a Math result cannot complete, else ``""``."""
    if not isinstance(value, dict):
        return "missing_math_result"
    result_class = str(value.get("result_class") or "").strip()
    correctness = str(value.get("correctness") or "").strip()
    novelty = str(value.get("novelty") or "").strip()
    fidelity = str(value.get("statement_fidelity") or "").strip()
    evidence = value.get("evidence")
    if result_class not in RESULT_CLASSES:
        return "invalid_result_class"
    if correctness != "verified":
        return "math_correctness_not_verified"
    if fidelity != "verified":
        return "statement_fidelity_not_verified"
    if not isinstance(evidence, list) or not any(
        str(item or "").strip() for item in evidence
    ):
        return "missing_math_evidence"
    if novelty not in NOVELTY_VERDICTS:
        return "invalid_novelty"
    if novelty == "unverified":
        return "math_novelty_not_verified"
    if result_class == "known_result" and novelty not in {
        "known",
        "not_applicable",
    }:
        return "known_result_novelty_mismatch"
    if result_class == "verified_new_result" and novelty != "verified_new":
        return "new_result_novelty_not_verified"
    if not bounded and result_class not in _COMPLETION_CLASSES:
        return f"result_class_not_terminal:{result_class}"
    return ""


__all__ = [
    "CORRECTNESS_VERDICTS",
    "FIDELITY_VERDICTS",
    "NOVELTY_VERDICTS",
    "RESULT_CLASSES",
    "math_completion_issue",
]
