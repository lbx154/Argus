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
from argus_skill.skills.stage_checklists import (
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
        assert "MATHEMATICS" in context

    create = vertical_role_banner(math, "scientist_create")
    adapt = vertical_role_banner(math, "scientist")
    assert "initial CREATE" in create
    assert "failed-round" not in create
    assert "failed-round evidence" in adapt

    direct = load_vertical("direct")
    assert "MATHEMATICS" not in vertical_role_banner(direct, "engineer")
    assert "MATHEMATICS" not in vertical_role_banner(direct, "reviewer")


def test_math_scope_protocol_is_artifact_first_and_resumable() -> None:
    context = vertical_role_banner(load_vertical("math"), "engineer")

    assert "research/SCOPE.md" in context
    assert "incomplete" in context.lower()
    assert "small, completed writes" in context
    assert "argus_skill.tools.atomic_artifact write research/SCOPE.md" in context
    assert "same command with `append`" in context
    assert "atomic replacement" in context
    assert "directory `fsync`\n   where supported" in context
    assert "Do not repeat literature or source verification" in context
    assert context.index("research/SCOPE.md") < context.index(
        "literature or source verification"
    )


def test_math_solve_protocol_checkpoints_before_new_research() -> None:
    context = vertical_role_banner(load_vertical("math"), "engineer")

    assert "current stage is `solve`" in context
    assert "research/SOLVE.md" in context
    assert "research/CLAIM_LEDGER.md" in context
    assert "status: incomplete" in context
    assert "argus_skill.tools.atomic_artifact" in context
    assert "Do not wait for a complete proof" in context
    assert context.index(
        "Atomically create any missing `research/SOLVE.md`"
    ) < context.index(
        "before any new\n   literature retrieval"
    )


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


def test_math_solve_and_review_have_conditional_mechanism_overlap_gate() -> None:
    items = vertical_checklist_items(load_vertical("math"))
    solve = {item.id: item for item in items["solve"]}
    review = {item.id: item for item in items["review"]}

    assert "solve.mechanism-overlap-audit" in solve
    assert "review.mechanism-overlap-debt" in review
    assert "If no such mechanism emerged" in solve["solve.mechanism-overlap-audit"].statement
    assert "bounded item" in review["review.mechanism-overlap-debt"].statement
    assert "MECHANISM_OVERLAP_AUDIT.md" in solve["solve.mechanism-overlap-audit"].evidence_hint


def test_math_roles_route_triggered_overlap_audit_as_separate_short_node() -> None:
    math = load_vertical("math")
    planner = vertical_role_banner(math, "planner")
    engineer = vertical_role_banner(math, "engineer")
    reviewer = vertical_role_banner(math, "reviewer")

    assert "SEPARATE short DAG node" in planner
    assert "Do not impose this literature cost" in planner
    assert "novelty debt" in engineer
    assert "do not self-certify novelty" in engineer
    assert "bounded construction node may still" in reviewer
    assert "Final review" in reviewer and "must `continue`" in reviewer


def test_math_vertical_carries_conditional_ai4m_method_triggers() -> None:
    items = vertical_checklist_items(load_vertical("math"))
    solve = {item.id: item for item in items["solve"]}
    review = {item.id: item for item in items["review"]}

    assert {
        "solve.counterexample-guided-refinement",
        "solve.construction-admissibility",
        "solve.relational-premise-map",
    }.issubset(solve)
    assert "review.ai4m-verifier-separation" in review
    assert "not applicable" in solve["solve.construction-admissibility"].statement
    assert "not applicable" in solve["solve.relational-premise-map"].statement

    math = load_vertical("math")
    planner = vertical_role_banner(math, "planner")
    engineer = vertical_role_banner(math, "engineer")
    reviewer = vertical_role_banner(math, "reviewer")
    scientist_create = vertical_role_banner(math, "scientist_create")
    scientist_adapt = vertical_role_banner(math, "scientist")

    for phrase in (
        "Counterexample-guided refinement",
        "Enumerate→Conjecture→Prove",
        "Relational premise map",
        "Semantic round trip",
    ):
        assert phrase in planner
    assert "circular witness" in engineer
    assert "verifier separation" in reviewer.lower()
    assert "Never prescribe all AI4M techniques" in scientist_create
    assert "proposal and verification roles separate" in scientist_adapt


def test_math_review_checklist_is_loaded_and_required(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "math")

    contract = resolve_stage_checklist_contract("review", project_root=tmp_path)

    assert contract.state is ChecklistLoadState.LOADED
    assert contract.checklist_optional is False
    assert {
        "review.statement-fidelity",
        "review.no-goal-drift",
        "review.correctness-novelty-separated",
        "review.mechanism-overlap-debt",
    }.issubset({item.id for item in contract.items})


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
        "review.no-goal-drift",
        "review.correctness-novelty-separated",
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
    assert "`done` certifies only" in reviewer_context
    assert "does not certify the" in reviewer_context
    assert "doctoral project target" in reviewer_context


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
