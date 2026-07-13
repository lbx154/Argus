from __future__ import annotations

from types import SimpleNamespace

from argus_skill.apps._runtime import _workflow_mode_for_project_root
from argus_skill.manager.stage_decider import final_stage_completion_decision
from argus_skill.skills.role_context import load_builtin_skill_text
from argus_skill.skills.vertical_select import (
    VERTICAL_PURPOSES,
    VERTICALS,
    persist_vertical,
    require_vertical,
)
from argus_skill.verticals._base import (
    load_vertical,
    vertical_checklist_items,
    vertical_checklist_stage_order,
    vertical_completion_gate,
    vertical_role_banner,
    vertical_workflow_mode,
)
from argus_skill.verticals.math.results import math_completion_issue


def test_math_is_registered_and_loadable() -> None:
    assert "math" in VERTICALS
    assert "math" in VERTICAL_PURPOSES
    assert require_vertical("math") == "math"

    mod = load_vertical("math")
    assert mod.__name__ == "argus_skill.verticals.math.stages"


def test_math_stage_contract_is_three_coarse_stages() -> None:
    mod = load_vertical("math")

    assert mod.STAGE_ORDER == ("scope", "solve", "review")
    assert vertical_checklist_stage_order(mod) == ("scope", "solve", "review")
    assert tuple(mod.STAGE_CHECKS) == mod.STAGE_ORDER
    assert tuple(mod.REVIEWER_CHECKLISTS) == mod.STAGE_ORDER
    assert vertical_workflow_mode(mod) == "proportional"


def test_math_runtime_uses_proportional_staged_evidence(tmp_path) -> None:
    persist_vertical(tmp_path, "math")

    assert _workflow_mode_for_project_root(tmp_path) == "proportional"


def test_planner_reuses_nonempty_expert_checklists() -> None:
    role = load_builtin_skill_text("argus-planner-role.md")

    assert "do not restate it or expand it with generic snapshots" in role
    assert "manifests" in role and "checksums" in role


def test_math_uses_reviewer_certified_non_paper_gate() -> None:
    mod = load_vertical("math")

    gate = vertical_completion_gate(mod)
    assert gate == "none"
    assert gate not in {"metric", "full_paper"}


def test_none_gate_can_complete_after_certified_review_stage() -> None:
    review = SimpleNamespace(
        status="done",
        planner_report={"forward_progress": True},
        checklist=[
            {"item": "review.statement-fidelity", "satisfied": True, "evidence": "audit"},
        ],
    )

    decision = final_stage_completion_decision(
        review,
        current_stage="review",
        stage_order=("scope", "solve", "review"),
    )

    assert decision is not None
    assert decision.action == "complete"
    assert decision.target_stage == "review"


def test_every_math_stage_has_checklist_items() -> None:
    mod = load_vertical("math")
    items = vertical_checklist_items(mod)

    assert set(items) == {"scope", "solve", "review"}
    assert all(items[stage] for stage in mod.CHECKLIST_STAGE_ORDER)


def test_math_role_banners_encode_dynamic_execution_and_independent_review() -> None:
    mod = load_vertical("math")

    engineer = vertical_role_banner(mod, "engineer")
    planner = vertical_role_banner(mod, "planner")
    reviewer = vertical_role_banner(mod, "reviewer")
    scientist = vertical_role_banner(mod, "scientist")

    assert "Reuse reviewer-certified" in planner
    assert "snapshot, manifest, or checksum" in planner
    assert "structured lean_check tool" in planner
    assert "Dynamically choose" in engineer
    assert "fixed workflow" in engineer
    assert "conjecture" in engineer
    assert "natural-language proof" in engineer
    assert "formal verification" in engineer
    assert "new mathematical delta" in engineer
    assert "finite verification" in engineer
    assert "new candidate" in engineer

    assert "failed mechanism" in scientist
    assert "structural rather than parametric" in scientist

    assert "Independently check mathematical correctness" in reviewer
    assert "computational evidence" in reviewer
    assert "fresh real compilation" in reviewer
    assert "Lean compilation does not prove" in reviewer
    assert "faithfully represents the original problem" in reviewer
    assert "current claim and its dependency edges" in reviewer
    assert "math_result" in reviewer
    assert "novelty-unverified work cannot complete" in reviewer


def _math_result(
    result_class: str,
    *,
    correctness: str = "verified",
    novelty: str = "not_applicable",
    fidelity: str = "verified",
) -> dict:
    return {
        "result_class": result_class,
        "correctness": correctness,
        "novelty": novelty,
        "statement_fidelity": fidelity,
        "evidence": ["checked proof"],
        "limitations": [],
    }


def _final_math_decision(math_result: dict):
    review = SimpleNamespace(
        status="done",
        planner_report={"forward_progress": True},
        checklist=[
            {
                "item": "review.statement-fidelity",
                "satisfied": True,
                "evidence": "semantic audit",
            }
        ],
        math_result=math_result,
    )
    return final_stage_completion_decision(
        review,
        current_stage="review",
        stage_order=("scope", "solve", "review"),
        vertical="math",
    )


def test_math_novelty_unverified_cannot_complete() -> None:
    result = _math_result(
        "novelty_unverified",
        novelty="unverified",
    )

    assert math_completion_issue(result)
    assert _final_math_decision(result) is None


def test_math_finite_verification_cannot_complete_as_proof() -> None:
    result = _math_result("finite_verification")

    assert math_completion_issue(result)
    assert _final_math_decision(result) is None


def test_math_counterexample_with_unverified_novelty_cannot_complete() -> None:
    result = _math_result("counterexample", novelty="unverified")

    assert math_completion_issue(result) == "math_novelty_not_verified"
    assert _final_math_decision(result) is None


def test_math_counterexample_requires_valid_novelty_verdict() -> None:
    result = _math_result("counterexample", novelty="bogus")

    assert math_completion_issue(result) == "invalid_novelty"
    assert _final_math_decision(result) is None


def test_math_explicit_counterexample_can_complete_false_claim() -> None:
    result = _math_result("counterexample")
    result["evidence"] = ["n = 1 gives 1 + 1 != 1"]
    result["limitations"] = ["refutes only the stated universal claim"]

    assert math_completion_issue(result) == ""
    assert _final_math_decision(result) is not None


def test_math_statement_fidelity_failure_cannot_complete() -> None:
    result = _math_result(
        "verified_new_result",
        novelty="verified_new",
        fidelity="failed",
    )

    assert math_completion_issue(result) == "statement_fidelity_not_verified"
    assert _final_math_decision(result) is None


def test_math_known_result_cannot_be_claimed_as_new() -> None:
    result = _math_result("known_result", novelty="verified_new")

    assert math_completion_issue(result) == "known_result_novelty_mismatch"
    assert _final_math_decision(result) is None


def test_math_verified_new_result_requires_verified_novelty() -> None:
    result = _math_result("verified_new_result", novelty="unverified")

    assert math_completion_issue(result) == "math_novelty_not_verified"
    assert _final_math_decision(result) is None


def test_math_correctness_and_novelty_are_independent() -> None:
    result = _math_result(
        "verified_new_result",
        correctness="uncertain",
        novelty="verified_new",
    )

    assert result["novelty"] == "verified_new"
    assert math_completion_issue(result) == "math_correctness_not_verified"
    assert _final_math_decision(result) is None


def test_math_verified_new_result_can_complete() -> None:
    result = _math_result(
        "verified_new_result",
        novelty="verified_new",
    )

    assert math_completion_issue(result) == ""
    assert _final_math_decision(result) is not None
