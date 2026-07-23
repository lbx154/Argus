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
from argus_skill.skills.stage_machine import (
    ChecklistLoadState,
    format_stage_checklist,
    resolve_stage_checklist_contract,
)
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


def _final_stage_decision(
    result: dict,
    target: str,
    *,
    scope: str = "",
    scientific_decision: str = "",
):
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
        scientific_decision=scientific_decision,
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
        "skills/scientist/math-research-distillation.md",
        "skills/scientist/math-research-adaptation.md",
    }


def test_generic_roles_load_math_skill_context_only_for_math() -> None:
    math = load_vertical("math")
    for role in (
        "manager",
        "planner",
        "engineer",
        "reviewer",
        "scientist_create",
        "scientist",
    ):
        context = vertical_role_banner(math, role)
        assert "mathemat" in context.lower()

    create = vertical_role_banner(math, "scientist_create")
    adapt = vertical_role_banner(math, "scientist")
    assert "without\nsolving the current instance" in create
    assert "concrete approach has failed" in adapt

    direct = load_vertical("direct")
    assert "MATHEMATICS" not in vertical_role_banner(direct, "engineer")
    assert "MATHEMATICS" not in vertical_role_banner(direct, "reviewer")


def test_math_engineer_uses_one_checkpoint_without_process_artifacts() -> None:
    context = vertical_role_banner(load_vertical("math"), "engineer")

    assert "`CHECKPOINT.md`" in context
    assert "process-only" in context
    assert "or formal source\nis the evidence" in context
    for artifact in (
        "SCOPE.md",
        "SOLVE.md",
        "CLAIM_LEDGER.md",
        "LEMMA_GRAPH.md",
        "MECHANISM_OVERLAP_AUDIT.md",
        "atomic_artifact",
    ):
        assert artifact not in context


def test_math_checklist_is_small_and_judges_results_not_files() -> None:
    items = vertical_checklist_items(load_vertical("math"))
    assert {stage: len(stage_items) for stage, stage_items in items.items()} == {
        "scope": 2,
        "solve": 3,
        "review": 3,
    }
    assert {
        stage: {item.id for item in stage_items}
        for stage, stage_items in items.items()
    } == {
        "scope": {"scope.problem-explicit", "scope.success-criterion"},
        "solve": {
            "solve.substantive-result",
            "solve.witness-valid",
            "solve.support-matches-claim",
        },
        "review": {
            "review.statement-fidelity",
            "review.argument-correct",
            "review.outcome-honest",
        },
    }
    rendered = "\n".join(
        item.statement + " " + item.evidence_hint
        for stage_items in items.values()
        for item in stage_items
    )
    for artifact in (
        "Main.lean",
        "compile.log",
        "lean_check.json",
        "statement_fidelity.md",
        "CLAIM_LEDGER",
        "LEMMA_GRAPH",
        "MECHANISM_OVERLAP_AUDIT",
    ):
        assert artifact not in rendered


def test_math_roles_keep_methods_optional_and_checks_real() -> None:
    math = load_vertical("math")
    planner = vertical_role_banner(math, "planner")
    engineer = vertical_role_banner(math, "engineer")
    reviewer = vertical_role_banner(math, "reviewer")
    scientist_create = vertical_role_banner(math, "scientist_create")
    scientist_adapt = vertical_role_banner(math, "scientist")

    assert "options, not mandatory phases" in planner
    assert "no fixed bundle of output filenames is required" in engineer
    assert "fresh real compiler run" in engineer
    assert "Do not require\nparticular filenames" in reviewer
    assert "separate audit artifact" in reviewer
    assert "required workflow or evidence package" in scientist_create
    assert "Do not create a process artifact" in scientist_adapt


def test_math_review_checklist_is_loaded_and_required(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "math")

    contract = resolve_stage_checklist_contract("review", project_root=tmp_path)

    assert contract.state is ChecklistLoadState.LOADED
    assert contract.checklist_optional is False
    assert {
        "review.statement-fidelity",
        "review.argument-correct",
        "review.outcome-honest",
    } == {item.id for item in contract.items}


def test_stale_research_env_cannot_replace_persisted_math_checklist(
    tmp_path: Path, monkeypatch
) -> None:
    persist_vertical(tmp_path, "math")
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "research")

    rendered = format_stage_checklist("review", role="reviewer", project_root=tmp_path)

    assert "review.statement-fidelity" in rendered
    assert "research.literature" not in rendered


def test_empty_math_review_store_entry_loads_seeds_not_empty(tmp_path: Path) -> None:
    """Seed-plus-override: an empty stages entry merges with vertical seeds → LOADED."""
    persist_vertical(tmp_path, "math")
    checklist_path = tmp_path / "research" / "CHECKLISTS.json"
    checklist_path.parent.mkdir(parents=True, exist_ok=True)
    checklist_path.write_text(
        json.dumps({"revision": 1, "vertical": "math", "stages": {"review": []}}),
        encoding="utf-8",
    )

    contract = resolve_stage_checklist_contract("review", project_root=tmp_path)

    # An empty stages entry no longer suppresses the vertical seeds.
    assert contract.state is ChecklistLoadState.LOADED
    assert contract.checklist_optional is False
    ids = {item.id for item in contract.items}
    assert {
        "review.statement-fidelity",
        "review.argument-correct",
        "review.outcome-honest",
    }.issubset(ids)


def test_math_has_no_target_schema_or_legacy_lifecycle_branches() -> None:
    root = Path(__file__).parents[2] / "argus_skill"
    manager = (root / "manager" / "_core.py").read_text(encoding="utf-8")
    domain_author = (root / "manager" / "domain_author.py").read_text(
        encoding="utf-8"
    )
    reviewer = (root / "reviewer" / "_core.py").read_text(encoding="utf-8")
    parsing = (root / "reviewer" / "_parsing.py").read_text(encoding="utf-8")

    assert 'explicit_builtin == "math"' not in manager
    assert 'vertical == "math"' not in manager
    assert 'name == "math" and target_level' not in domain_author
    assert 'resolve_vertical(root) == "math"' not in reviewer
    assert "math_result" not in parsing
    assert not (root / "reviewer" / "reviewer_math_schema.json").exists()
    assert (root / "reviewer" / "reviewer_legacy_research_schema.json").exists()


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


def test_exploratory_honesty_alone_cannot_end_research() -> None:
    result = _research_result("structured_failure_report")

    assert research_completion_issue(
        result,
        research_target_level="exploratory",
    ) == "result_class_not_exploratory_terminal:structured_failure_report"
    assert _final_stage_decision(result, "exploratory") is None
    assert _final_stage_decision(
        result,
        "exploratory",
        scientific_decision="continue",
    ) is None


def test_exploratory_decision_relevant_counterexample_can_end_research() -> None:
    result = _research_result("counterexample")

    assert research_completion_issue(
        result,
        research_target_level="exploratory",
    ) == ""
    assert _final_stage_decision(
        result,
        "exploratory",
        scientific_decision="continue",
    ) is not None


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


def test_bounded_item_can_complete_without_certifying_doctoral_target() -> None:
    result = _research_result(
        "novelty_unverified",
        novelty="unverified",
        significance="unverified",
    )

    assert research_completion_issue(
        result,
        research_target_level="doctoral",
        scope="bounded",
    ) == ""
    assert _final_stage_decision(result, "doctoral", scope="bounded") is not None

    reviewer_context = vertical_role_banner(load_vertical("math"), "reviewer")
    assert "bounded subproblem can be done" in reviewer_context
    assert "whole\nresearch goal is complete" in reviewer_context


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


def test_math_stage_completion_enforces_persisted_target() -> None:
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
