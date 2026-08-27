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
    vertical_completion_contract_version,
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
    assert vertical_completion_contract_version(module) == 1
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

    # Math stays light on machinery compared with kernel_engineering. The
    # modules below are the exception, and the reason is narrow: without a way
    # to measure the distance to the goal, "how hard was this step" silently
    # replaces "how much closer did this get us". `lean_evidence` is the same
    # kind of exception for formal proof — it reads what a compiler recorded so
    # a `sorry`, a stale pass, or a formalization of the wrong statement cannot
    # be presented as evidence. They measure; they do not add stages, roles, or
    # required paperwork, and a project with no `.lean` file never sees them.
    #
    # Two of them are not measures. `math_state` is the write path into the
    # research-math kernel, and it lives here rather than inside that package
    # for two reasons: it holds the repository's file lock, which the kernel may
    # not import without losing the property that lets it travel, and the rule
    # it enforces — that no agent-typed argument selects an evidence tier which
    # confers kernel status — is policy about this host's agents, which the
    # kernel is deliberately free of. `context_projection` is an adapter: it
    # reads that same state kernel and the claimed backlog item and renders what
    # *this* mission needs to know about the one claim it is about, and it lives
    # here because it is the only part that touches an Argus type
    # (`BacklogItem`), which that package's whole point is to do without.
    # Neither adds a stage or a required file: a project with no recorded claim
    # never loads either.
    #
    # `citation_check` is the third of that kind and the one that earns its
    # place least obviously, since a project can cite nothing and never load it.
    # It is here because the risk it addresses has no other checker: Lean can
    # certify that a theorem follows from its hypotheses and can say nothing
    # about whether the hypothesis imported as "Theorem 3.2 of [K]" is in [K],
    # and a fabricated reference is not a citation defect, it is a proof that
    # does not exist. It adds no stage and no required file — it derives its own
    # work list from the ledger — and blocks only at `review`.
    # `lean_async` is the one module here that is machinery rather than a
    # measure, and it is worth being uncomfortable about: starting a process and
    # asking later is a generic capability, and generic capabilities belong in
    # core. It is here because everything that makes it more than `Popen` is
    # policy that only this vertical holds — that a compiler answer is bound to
    # the digest of the source *and* of the statement fidelity document, that a
    # run whose text moved publishes nothing rather than something, and that a
    # worker which died is an environment failure and not a broken proof. Those
    # rules live in `lean_evidence`, and this is `verify` with the waiting taken
    # out, so a caller reaches it through the same CLI and gets the same records.
    # It is a separate module rather than more of `lean_evidence` for one
    # concrete reason: `lean_evidence` is imported by `stages.py` on every
    # completion check, and the completion gate has no business importing a
    # process launcher. It adds no stage and no required file, and nothing loads
    # it unless someone types `submit`.
    assert files == {
        "__init__.py",
        "stages.py",
        "citation_check.py",
        "context_projection.py",
        "lean_async.py",
        "lean_evidence.py",
        "math_state.py",
        "objective_mode.py",
        "proof_graph.py",
        "proof_graph_check.py",
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

    software = load_vertical("software")
    assert "MATHEMATICS" not in vertical_role_banner(software, "engineer")
    assert "MATHEMATICS" not in vertical_role_banner(software, "reviewer")


def test_math_completion_hook_requires_objective_and_policy_graph(tmp_path: Path) -> None:
    from argus_skill.verticals.math import objective_mode
    from argus_skill.verticals.math.stages import stage_completion_issues

    persist_vertical(tmp_path, "math")
    assert "objective mode" in " ".join(stage_completion_issues("scope", tmp_path))

    objective_mode.set_objective(tmp_path, mode="targeted", goal="G")
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["verification_profile"] = "develop"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert "PROOF_GRAPH.json" in " ".join(stage_completion_issues("solve", tmp_path))

    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PROOF_GRAPH.json").write_text(
        json.dumps({
            "goal": "G",
            "routes": [{"name": "route", "status": "current", "evidence": ""}],
            "nodes": {
                "G": {
                    "statement": "G",
                    "status": "proved",
                    "is_goal": True,
                    "depends_on": [],
                    "reviewer_confirmed": True,
                }
            },
        }),
        encoding="utf-8",
    )
    assert stage_completion_issues("solve", tmp_path) == ()


def test_math_engineer_uses_one_checkpoint_without_process_artifacts() -> None:
    context = vertical_role_banner(load_vertical("math"), "engineer")

    assert "`CHECKPOINT.md`" in context
    assert "process-only" in context
    # Collapsed so the assertion survives rewrapping.
    assert "or formal source is the\n evidence".replace("\n ", " ") in " ".join(
        context.split()
    )
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
        "scope": 3,
        "solve": 4,
        "review": 4,
    }
    assert {stage: {item.id for item in stage_items} for stage, stage_items in items.items()} == {
        "scope": {
            "scope.problem-explicit",
            "scope.success-criterion",
            "scope.known-status-recorded",
        },
        "solve": {
            "solve.substantive-result",
            "solve.witness-valid",
            "solve.support-matches-claim",
            "solve.gap-reduced",
        },
        "review": {
            "review.goal-achieved",
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
    assert "error-free attempt" in rendered
    assert "leave this item unsatisfied" in rendered
    assert "original Goal Gate is achieved" in rendered
    # The gap item must be satisfied by a proposition changing status, not by a
    # file existing — otherwise the graph becomes the paperwork it replaced.
    gap_item = next(
        item for item in items["solve"] if item.id == "solve.gap-reduced"
    )
    assert "which proposition changed status" in gap_item.statement
    assert "exploratory project this item is satisfied by a substantive" in gap_item.statement


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


def test_parallel_routes_are_dispatched_without_a_prescribed_width() -> None:
    """Several attacks on one goal are the OR the ledger already models.

    The two things this guidance must not lose. It must not name a number:
    how many routes are worth opening is a mathematical judgement about
    whether they fail for different reasons, and a fixed width would turn a
    judgement into a quota. And it must say which file the workers legitimately
    share, because the generic team gate asks for disjoint writable paths and a
    reader who applies that literally will either serialize the ledger or, worse,
    give each route its own copy and lose the OR.
    """
    math = load_vertical("math")
    planner = vertical_role_banner(math, "planner")
    engineer = vertical_role_banner(math, "engineer")

    assert "two routes — an OR — and" in planner
    assert "leave the\ncount to the Engineer" in planner

    assert "one task per route" in engineer
    assert "fail for different reasons" in engineer
    assert "agent-team-lead.md" in engineer
    # The dispatcher describes the goal; the worker does the thinking.
    assert "do not hand over a\ndecomposition into steps" in engineer
    # Shared ledger, private working files.
    assert "research/MATH_STATE.json`. That one is safe to share" in engineer
    assert "statement_fidelity.md" in engineer


def test_math_review_checklist_is_loaded_and_required(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "math")

    contract = resolve_stage_checklist_contract("review", project_root=tmp_path)

    assert contract.state is ChecklistLoadState.LOADED
    assert contract.checklist_optional is False
    assert {
        "review.goal-achieved",
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
        "review.goal-achieved",
        "review.statement-fidelity",
        "review.argument-correct",
        "review.outcome-honest",
    }.issubset(ids)


def test_math_has_no_target_schema_or_legacy_lifecycle_branches() -> None:
    root = Path(__file__).parents[2] / "argus_skill"
    manager = (root / "manager" / "_core.py").read_text(encoding="utf-8")
    domain_author = (root / "manager" / "domain_author.py").read_text(encoding="utf-8")
    reviewer = (root / "reviewer" / "_core.py").read_text(encoding="utf-8")
    parsing = (root / "reviewer" / "_parsing.py").read_text(encoding="utf-8")

    assert 'explicit_builtin == "math"' not in manager
    assert 'vertical == "math"' not in manager
    assert 'name == "math" and target_level' not in domain_author
    assert 'resolve_vertical(root) == "math"' not in reviewer
    assert "math_result" not in parsing
    assert not list((root / "reviewer").glob("reviewer*_schema.json"))


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


def test_a_doctoral_target_is_not_cleared_by_publishable_significance() -> None:
    """This test used to assert the opposite, and that was the defect.

    ``publishable`` and ``doctoral`` accepted the same significance set for
    every original result, so asking for doctoral work bought nothing: a
    correct, verified-new theorem its own author graded ``publishable``
    completed a doctoral project. The survey branch of the same function had
    the ladder right the whole time, which is what made the omission easy to
    miss — the vocabulary, the prompts, and the operator's expectation all had
    two levels, and only the check had one.
    """
    publishable = _research_result(
        "new_theorem", novelty="verified_new", significance="publishable"
    )

    assert (
        research_completion_issue(publishable, research_target_level="doctoral")
        == "significance_below_doctoral:publishable"
    )
    assert _final_stage_decision(publishable, "doctoral") is None

    # ...and the same result still completes the target it was graded for.
    assert (
        research_completion_issue(publishable, research_target_level="publishable")
        == ""
    )
    assert _final_stage_decision(publishable, "publishable") is not None


def test_doctoral_significance_completes_a_doctoral_target() -> None:
    result = _research_result(
        "new_theorem", novelty="verified_new", significance="doctoral"
    )

    assert research_completion_issue(result, research_target_level="doctoral") == ""
    assert _final_stage_decision(result, "doctoral") is not None


def test_a_higher_rating_never_fails_a_lower_target() -> None:
    """The ladder is monotone: nothing is refused for being too good."""
    from argus_skill.core.research_contract import ACCEPTED_SIGNIFICANCE

    order = ("exploratory", "publishable", "doctoral")
    for index, target in enumerate(order):
        assert ACCEPTED_SIGNIFICANCE[target] == frozenset(order[index:]), target


def test_literature_review_uses_survey_quality_not_original_novelty() -> None:
    exploratory = _research_result(
        "literature_review",
        novelty="known",
        significance="exploratory",
    )
    publishable = _research_result(
        "literature_review",
        novelty="not_applicable",
        significance="publishable",
    )
    doctoral = _research_result(
        "literature_review",
        novelty="not_applicable",
        significance="doctoral",
    )

    assert research_completion_issue(
        exploratory,
        research_target_level="exploratory",
    ) == ""
    assert research_completion_issue(
        publishable,
        research_target_level="publishable",
    ) == ""
    assert research_completion_issue(
        publishable,
        research_target_level="doctoral",
    ) == "survey_significance_below_doctoral:publishable"
    assert research_completion_issue(
        doctoral,
        research_target_level="doctoral",
    ) == ""


def test_literature_review_cannot_leave_novelty_unverified() -> None:
    result = _research_result(
        "literature_review",
        novelty="unverified",
        significance="publishable",
    )

    assert research_completion_issue(
        result,
        research_target_level="publishable",
    ) == "survey_novelty_must_be_not_applicable"


def test_exploratory_honesty_alone_cannot_end_research() -> None:
    result = _research_result("structured_failure_report")

    assert (
        research_completion_issue(
            result,
            research_target_level="exploratory",
        )
        == "result_class_not_exploratory_terminal:structured_failure_report"
    )
    assert _final_stage_decision(result, "exploratory") is None
    assert (
        _final_stage_decision(
            result,
            "exploratory",
            scientific_decision="continue",
        )
        is None
    )


def test_exploratory_decision_relevant_counterexample_can_end_research() -> None:
    result = _research_result("counterexample")

    assert (
        research_completion_issue(
            result,
            research_target_level="exploratory",
        )
        == ""
    )
    assert (
        _final_stage_decision(
            result,
            "exploratory",
            scientific_decision="continue",
        )
        is not None
    )


@pytest.mark.parametrize(
    "result_class",
    ["finite_verification", "lean_local_verification"],
)
def test_exploratory_bounded_evidence_can_end_normally(result_class: str) -> None:
    result = _research_result(result_class)

    assert (
        research_completion_issue(
            result,
            research_target_level="exploratory",
        )
        == ""
    )


def test_bounded_item_can_complete_without_certifying_doctoral_target() -> None:
    result = _research_result(
        "novelty_unverified",
        novelty="unverified",
        significance="unverified",
    )

    assert (
        research_completion_issue(
            result,
            research_target_level="doctoral",
            scope="bounded",
        )
        == ""
    )
    # The bounded item may close honestly without meeting the doctoral target,
    # but it cannot certify the whole final Goal Gate.
    assert _final_stage_decision(result, "doctoral", scope="bounded") is None

    reviewer_context = vertical_role_banner(load_vertical("math"), "reviewer")
    assert "bounded subproblem can be done" in reviewer_context
    assert "whole\nresearch goal is complete" in reviewer_context


def test_legacy_math_result_gets_conservative_significance() -> None:
    migrated = normalize_research_result(
        {
            "result_class": "known_result",
            "correctness": "verified",
            "novelty": "known",
            "statement_fidelity": "verified",
            "evidence": ["legacy evidence"],
            "limitations": [],
        }
    )

    assert migrated is not None
    assert migrated["significance_status"] == "exploratory"


def test_math_stage_completion_enforces_persisted_target() -> None:
    finite = _research_result("finite_verification")
    assert _final_stage_decision(finite, "doctoral") is None


def test_research_target_persists_and_non_target_vertical_clears_it(tmp_path) -> None:
    persist_vertical(tmp_path, "math", research_target_level="doctoral")
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert resolve_research_target_level(tmp_path) == "doctoral"
    assert state["research_target_set_at"] > 0

    persist_vertical(tmp_path, "direct")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "research_target_level" not in state
    assert "research_target_set_at" not in state


def test_reviewer_keeps_its_stage_checklist_when_the_daemon_names_the_vertical(
    tmp_path: Path,
) -> None:
    """The daemon passes ``vertical_override`` for a real campaign.

    That used to route the Reviewer down the same branch as a vertical named
    for a directory with no pipeline state, which suppresses the checklist —
    so a math Reviewer in ``solve`` was judging without the ~2k characters of
    acceptance criteria the Engineer's own prompt still carried.
    """
    import json

    from argus_skill import SkillLoop, SkillLoopConfig
    from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
    from argus_skill.verticals.math.objective_mode import set_objective

    skills = tmp_path / "skills"
    skills.mkdir()
    (tmp_path / ".argus").mkdir(exist_ok=True)
    (tmp_path / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "math", "current_stage": "solve"}), encoding="utf-8"
    )
    set_objective(tmp_path, mode="targeted", goal="G")

    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="Worked the route."))
    backend.queue("reviewer", CannedResponse(message=json.dumps({
        "status": "done",
        "reason": "Verified.",
        "next_action": "None.",
        "round_summary_markdown": "# Review\n\n- verified\n",
        "completion_summary_markdown": "Verified.",
    })))
    loop = SkillLoop(
        skills_dir=skills,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            max_rounds=1, workflow_mode="direct", active_vertical="math",
        ),
    )
    loop.run("Prove G.", workdir=tmp_path, scope="bounded")

    reviewer_prompt = next(
        prompt for label, prompt, _options in backend.history if label == "reviewer"
    )
    assert "Stage checklist (solve)" in reviewer_prompt
    assert "solve.substantive-result" in reviewer_prompt
    # The failure this replaced: a stage the checklist loader could not resolve
    # renders as a manufactured blocker rather than as nothing.
    assert "Configuration error" not in reviewer_prompt


def test_math_never_certifies_its_own_proof() -> None:
    """A proof is the one deliverable whose author cannot certify it.

    Without this declaration the contract defaults to ``False``, and a testbed
    run closed an open conjecture in a single round on the Engineer's own
    verdict — "independent review was not required for this mission" — with no
    Reviewer, no artifact and no proof graph. Every sibling research vertical
    already declares it; math was the omission.
    """
    from argus_skill.verticals._base import (
        load_vertical,
        vertical_requires_independent_review,
    )

    assert vertical_requires_independent_review(load_vertical("math")) is True


def test_math_review_survives_a_direct_workflow_decision(tmp_path: Path) -> None:
    """The guard has to hold on the path that actually broke.

    ``_independent_review_required_for_project_root`` reads the vertical
    contract with no ``workflow_mode == "direct"`` exemption — unlike the paper
    and completion-gate checks beside it. A Manager that collapses a proof into
    one direct work package must still not collapse away its verification.
    """
    from argus_skill.apps._runtime_supervisor import (
        _independent_review_required_for_project_root,
    )

    persist_vertical(tmp_path, "math", workflow_mode="direct")

    assert _independent_review_required_for_project_root(tmp_path) is True


def test_the_scope_instruction_survived_the_deletion() -> None:
    """The reviewer checklist held one instruction that lived nowhere else.

    Everything else in it restated "review the mathematics, not the paperwork",
    which the review skill already says. This did not: establish the problem's
    known status while scoping, instead of letting each later worker rediscover
    it. Deleting the dict without moving it would have deleted the instruction.
    """
    skill = (
        Path(__file__).resolve().parents[2]
        / "argus_skill/verticals/math/skills/reviewer/math-research-review.md"
    ).read_text(encoding="utf-8")

    assert "known status of the problem" in " ".join(skill.split())
    assert "was established here, and written down" in " ".join(skill.split())
    assert "rediscover" in skill
