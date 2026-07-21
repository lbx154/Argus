"""Route verified research results into claims and paper work."""

from __future__ import annotations

from typing import Any

from .research_contract import normalize_research_result

_POSITIVE = frozenset({
    "verified_new_result",
    "complete_solution",
    "new_theorem",
    "improved_bound",
    "new_infinite_family",
    "new_reduction",
})
_NEGATIVE = frozenset({
    "counterexample",
    "exact_counterexample",
    "structured_failure_report",
    "exhausted_current_methods",
})
_BOUNDARY = frozenset({
    "known_result",
    "finite_verification",
    "partial_result",
    "lean_local_verification",
})
_INTERNAL_REPORT = frozenset({
    "structured_failure_report",
    "honest_final_report",
    "exhausted_current_methods",
})


def claim_route_for_result(result_class: str) -> str:
    if result_class in _POSITIVE:
        return "supported_positive"
    if result_class in _NEGATIVE:
        return "supported_negative"
    if result_class in _BOUNDARY:
        return "supported_boundary"
    return "supported_diagnostic"


def build_claim_synthesis(
    *,
    research_result: object,
    planner_report: object = None,
    step_back: object = None,
    scientific_decision: object = None,
) -> dict[str, Any] | None:
    result = normalize_research_result(research_result)
    if result is None:
        return None
    if result["correctness_status"] != "verified" or not result["evidence"]:
        return None
    route = claim_route_for_result(result["result_class"])
    _ = planner_report, step_back
    result_class = result["result_class"]
    explicitly_publishable = (
        str(scientific_decision or "").strip().lower() == "go"
        and result["significance_status"] in {"publishable", "doctoral"}
        and result_class not in _INTERNAL_REPORT
    )
    if route == "supported_positive":
        action = "expand_and_write"
        advance = True
    elif explicitly_publishable:
        action = "develop_publication_thesis"
        advance = True
    else:
        action = "diagnose_or_pivot"
        advance = False
    return {
        "schema_version": 1,
        "route": route,
        "action": action,
        "result_class": result_class,
        "evidence": list(result["evidence"]),
        "limitations": list(result["limitations"]),
        "scientific_result_valid": True,
        "advance_to_analysis_or_report": advance,
    }


def claim_synthesis_for_review(review: object) -> dict[str, Any] | None:
    return build_claim_synthesis(
        research_result=getattr(review, "research_result", None),
        planner_report=getattr(review, "planner_report", None),
        step_back=getattr(review, "step_back", None),
        scientific_decision=getattr(review, "scientific_decision", None),
    )


__all__ = [
    "build_claim_synthesis",
    "claim_route_for_result",
    "claim_synthesis_for_review",
]
