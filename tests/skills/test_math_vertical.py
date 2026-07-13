from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.research_contract import (
    normalize_research_result,
    research_completion_issue,
    resolve_research_target_level,
)
from argus_skill.manager.stage_decider import final_stage_completion_decision
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
    vertical_research_target_levels,
    vertical_role_banner,
    vertical_workflow_mode,
)


def _research_result(
    result_class: str,
    *,
    correctness: str = "verified",
    novelty: str = "not_applicable",
    significance: str = "exploratory",
    fidelity: str = "verified",
) -> dict:
    return {
        "result_class": result_class,
        "correctness_status": correctness,
        "novelty_status": novelty,
        "significance_status": significance,
        "statement_fidelity_status": fidelity,
        "evidence": ["independently checked evidence"],
        "limitations": [],
    }


def _final_stage_decision(result: dict, target: str, *, scope: str = ""):
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
        research_result=result,
        scope=scope,
    )
    return final_stage_completion_decision(
        review,
        current_stage="review",
        stage_order=("scope", "solve", "review"),
        vertical="math",
        research_target_level=target,
    )


def test_math_is_registered_as_three_stage_targeted_vertical() -> None:
    assert "math" in VERTICALS
    assert "math" in VERTICAL_PURPOSES
    assert require_vertical("math") == "math"

    module = load_vertical("math")
    assert module.STAGE_ORDER == ("scope", "solve", "review")
    assert vertical_checklist_stage_order(module) == ("scope", "solve", "review")
    assert vertical_workflow_mode(module) == "proportional"
    assert vertical_completion_gate(module) == "none"
    assert vertical_research_target_levels(module) == (
        "exploratory",
        "publishable",
        "doctoral",
    )


def test_math_vertical_contains_only_contract_skills_and_metadata() -> None:
    root = Path(__file__).parents[2] / "argus_skill" / "verticals" / "math"
    files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }

    assert files == {
        "__init__.py",
        "stages.py",
        "skills/manager/math-research-manager.md",
        "skills/planner/math-research-planning.md",
        "skills/engineer/math-research-execution.md",
        "skills/reviewer/math-research-review.md",
        "skills/scientist/math-research-adaptation.md",
    }


def test_generic_roles_load_math_skill_context_only_for_math() -> None:
    math = load_vertical("math")
    for role in ("manager", "planner", "engineer", "reviewer", "scientist"):
        context = vertical_role_banner(math, role)
        assert "MATHEMATICS" in context

    direct = load_vertical("direct")
    assert "MATHEMATICS" not in vertical_role_banner(direct, "engineer")
    assert "MATHEMATICS" not in vertical_role_banner(direct, "reviewer")


def test_math_checklist_preserves_fidelity_and_lean_artifacts() -> None:
    items = vertical_checklist_items(load_vertical("math"))
    rendered = "\n".join(
        item.statement for stage in ("scope", "solve", "review") for item in items[stage]
    )

    assert "statement" in rendered.lower()
    for artifact in (
        "Main.lean",
        "compile.log",
        "lean_check.json",
        "statement_fidelity.md",
    ):
        assert artifact in rendered
    assert "argus_skill.tools.lean_check" in vertical_role_banner(
        load_vertical("math"),
        "engineer",
    )


@pytest.mark.parametrize(
    "result",
    [
        _research_result("finite_verification"),
        _research_result("partial_result"),
        _research_result("known_result"),
        _research_result(
            "novelty_unverified",
            novelty="unverified",
            significance="unverified",
        ),
        _research_result("structured_failure_report"),
        _research_result("exhausted_current_methods"),
        _research_result("lean_local_verification"),
        _research_result(
            "new_candidate",
            novelty="verified_new",
            significance="doctoral",
        ),
    ],
)
def test_doctoral_non_breakthrough_results_are_not_success(result: dict) -> None:
    assert research_completion_issue(
        result,
        research_target_level="doctoral",
    )
    assert _final_stage_decision(result, "doctoral") is None


def test_doctoral_verified_new_publishable_or_doctoral_result_succeeds() -> None:
    for significance in ("publishable", "doctoral"):
        result = _research_result(
            "new_theorem",
            novelty="verified_new",
            significance=significance,
        )
        assert research_completion_issue(
            result,
            research_target_level="doctoral",
        ) == ""
        assert _final_stage_decision(result, "doctoral") is not None


def test_exploratory_honest_failure_report_can_end_normally() -> None:
    result = _research_result("structured_failure_report")

    assert research_completion_issue(
        result,
        research_target_level="exploratory",
    ) == ""
    assert _final_stage_decision(result, "exploratory") is not None


@pytest.mark.parametrize(
    "result_class",
    ["finite_verification", "lean_local_verification"],
)
def test_exploratory_bounded_evidence_can_end_normally(result_class: str) -> None:
    result = _research_result(result_class)

    assert research_completion_issue(
        result,
        research_target_level="exploratory",
    ) == ""


def test_bounded_cycle_cannot_complete_doctoral_target() -> None:
    result = _research_result(
        "new_theorem",
        novelty="verified_new",
        significance="doctoral",
    )

    assert research_completion_issue(
        result,
        research_target_level="doctoral",
        scope="bounded",
    ) == "bounded_cycle_cannot_complete_doctoral"
    assert _final_stage_decision(result, "doctoral", scope="bounded") is None


def test_legacy_math_result_gets_conservative_significance() -> None:
    migrated = normalize_research_result({
        "result_class": "known_result",
        "correctness": "verified",
        "novelty": "known",
        "statement_fidelity": "verified",
        "evidence": ["legacy evidence"],
        "limitations": [],
    })

    assert migrated is not None
    assert migrated["significance_status"] == "exploratory"


def test_math_stage_completion_does_not_bypass_doctoral_target() -> None:
    finite = _research_result("finite_verification")
    assert _final_stage_decision(finite, "doctoral") is None


def test_research_target_persists_and_non_target_vertical_clears_it(tmp_path) -> None:
    persist_vertical(tmp_path, "math", research_target_level="doctoral")
    state_path = tmp_path / "research" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert resolve_research_target_level(tmp_path) == "doctoral"
    assert state["research_target_set_at"] > 0

    persist_vertical(tmp_path, "direct")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "research_target_level" not in state
    assert "research_target_set_at" not in state
