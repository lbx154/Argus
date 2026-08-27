"""Minimal dynamic-path vertical for mathematical research.

The stages are deliberately coarse. Background retrieval, examples and
counterexamples, computation, natural-language proof, and Lean formalization are
methods selected for the problem at hand, not mandatory pipeline stages.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...skills.stage_machine import ChecklistItem

STAGE_ORDER = ("scope", "solve", "review")
CHECKLIST_STAGE_ORDER = STAGE_ORDER
WORKFLOW_MODE = "proportional"
VERIFICATION_STAGE_PROFILES = {
    "scope": "explore",
    "solve": "develop",
    "review": "certify",
}
RESEARCH_TARGET_LEVELS = ("exploratory", "publishable", "doctoral")

# A proof is the one deliverable whose author cannot certify it. Every sibling
# research vertical already declares this (``research``, ``materials``,
# ``chip_design``); math was the omission, and defaulting to ``False`` is what
# let a testbed run close an open conjecture on the Engineer's own verdict:
# "Engineer reported the requested milestone complete; independent review was
# not required for this mission." One round, no Reviewer, no artifact, no proof
# graph — and the claim was simply believed.
#
# Deliberately declared here rather than argued per mission: unlike the paper /
# completion-gate checks, ``_independent_review_required_for_project_root``
# reads this contract with no ``workflow_mode == "direct"`` exemption, so a
# Manager that collapses a proof to a single direct work package still cannot
# collapse away its verification.
REQUIRE_INDEPENDENT_REVIEW = True

# Math has no ``research`` stage, so the framework's default live-search stage
# never fires here: without this declaration the Engineer would do literature
# work from recall alone. ``scope`` needs the literature to state the problem
# and its known status; ``solve`` needs it to find existing techniques,
# counterexamples, and prior results. ``review`` is deliberately excluded: it is
# independent verification of an argument already in hand, and the Reviewer
# (which always runs with live search) owns the source checks there.

# Math missions end through the ordinary reviewer-certified final-stage path.
# They are neither paper-submission missions nor metric-optimization campaigns.
completion_gate = "none"
COMPLETION_CONTRACT_VERSION = 1
PROTECTED_ITEM_IDS = frozenset({"review.goal-achieved"})

def adopt_operator_objective(project_root: Path, request: str) -> object:
    """Vertical-contract hook: give the objective mode an in-product channel.

    Math is the only vertical that refuses to complete *any* stage until an
    out-of-band choice has been made (see ``objective_mode``), and until now
    the sole way to make that choice was a module CLI on the host. The hook is
    declared here rather than in core because the concept is math-local: no
    other vertical has two completion bars to pick between, and core must not
    learn about ``math_objective_mode`` to deliver one.

    Re-exported rather than reimplemented — ``objective_mode`` owns the rule
    that a transcription never overwrites an operator's choice.
    """
    from .objective_mode import adopt_operator_objective as _adopt

    return _adopt(project_root, request)


def stage_completion_issues(stage: str, project_root: Path) -> tuple[str, ...]:
    """Validate objective identity, Lean evidence, and the policy-required graph."""
    from ...core.verification_policy import resolve_policy
    from .lean_evidence import lean_evidence_issues
    from .objective_mode import resolve_objective
    from .proof_graph import graph_required_for, load_graph

    stage_name = (stage or "").strip().lower()
    objective = resolve_objective(project_root)
    if stage_name not in STAGE_ORDER:
        return (f"unknown math stage {stage_name!r}",)
    if not objective.resolved:
        return (objective.note,)
    if stage_name == "scope":
        return ()

    policy = resolve_policy(
        project_root,
        stage=stage_name,
        vertical="math",
        stage_profiles=VERIFICATION_STAGE_PROFILES,
    )
    # Formalization stays optional: a project with no `.lean` file gets an
    # empty tuple here and never loads the checker. Once one is present it is a
    # claim, and every claim must be redeemable — so the source must show a
    # current, hash-bound compiler result that says the proof went through.
    # A failure the environment caused (no toolchain, no Mathlib) is worded
    # differently from a broken proof, because the reviewer needs to tell them
    # apart, but it does not pass: an unverified formalization is not evidence
    # however good the excuse. The escape hatch is not committing the source.
    # This never runs a compiler; it reads what one already recorded.
    issues = list(lean_evidence_issues(project_root))
    issues.extend(_math_state_issues(project_root))
    # Only at the last stage. Citation checking runs out of band and lands when
    # it lands; blocking `solve` on it would stop the reasoning to wait for a
    # registry, which is the one thing the asynchronous design exists to avoid.
    # `review` is where the work is handed over, and nothing is handed over
    # standing on a source nobody went and read.
    if stage_name == "review":
        issues.extend(_citation_delivery_issues(project_root))
    if not graph_required_for(policy.profile, objective.mode):
        return tuple(issues)
    graph = load_graph(project_root)
    if graph is None:
        issues.append(
            "targeted math under develop/certify requires "
            "research/PROOF_GRAPH.json"
        )
        return tuple(issues)
    issues.extend(graph.validate())
    if graph.goal != objective.goal:
        issues.append(
            "proof graph goal does not match the Manager-owned math_goal"
        )
    issues.extend(_targeted_goal_closure_issues(stage_name, objective, graph))
    return tuple(issues)


def _targeted_goal_closure_issues(
    stage_name: str, objective: Any, graph: Any
) -> tuple[str, ...]:
    """A targeted project completes by closing its goal, not by passing review.

    Everything else the completion path checks is about the *strength* of a
    result — ``research_completion_issue`` reads result_class, novelty and
    significance against the research target level, and the reviewer checklist
    is LLM-judged prose. None of it asks the one question this mode exists to
    ask: is G proved or refuted?

    ``ProofGraph.gap()`` has answered that since it was written and had exactly
    one caller, the standalone operator CLI ``proof_graph_check``. So a
    targeted project could reach ``decision=complete`` with its root node still
    ``open``: the deterministic state on disk said "G is unproved" while the
    certificate said "done", and nothing put the two in the same room. The
    reviewer is the only thing that ever stood in the way, and it is never told
    the persisted ``math_goal`` or the gap — and the failure case is by
    construction one where its verdict is ``done``.

    Gated to ``review`` deliberately. Applying it at ``solve`` would make the
    middle stage uncompletable — a stage whose whole job is to shrink the gap
    cannot be blocked on the gap being zero. That is the shape of bug #41, and
    it is not worth re-creating to catch a case ``review`` catches anyway.

    Exploratory mode is untouched: it has no single G, which is the entire
    reason the two modes exist separately.
    """
    if stage_name != "review" or not objective.is_targeted:
        return ()
    report = graph.gap()
    if not report.reachable:
        return (
            "targeted math cannot complete: research/PROOF_GRAPH.json has no "
            "goal node, so the gap to the named goal is unmeasurable. Mark the "
            "goal node with `is_goal: true` (or name it after the goal) and "
            "record what it still rests on",
        )
    if report.gap_size:
        blocking = ", ".join(report.blocking_nodes[:6])
        if len(report.blocking_nodes) > 6:
            blocking += f", ... ({len(report.blocking_nodes)} total)"
        return (
            "targeted math cannot complete: the named goal still rests on "
            f"{report.gap_size} unproved proposition(s): {blocking}. A targeted "
            "project completes by proving or refuting its goal — if the goal "
            "turned out to be the wrong question, the honest close is to say "
            "so and switch the objective mode, not to certify the stage",
        )
    return ()


def _math_state_issues(project_root: Path) -> tuple[str, ...]:
    """Structural defects in the claim ledger, when the project keeps one.

    The same rule as the Lean sources above, for the same reason. A project with
    no ``research/MATH_STATE.json`` pays nothing: ``load_state`` reads a missing
    file as an empty state, and an empty state has no defects, so absence needs
    no special case here. A project that does keep a ledger has to keep one that
    holds together — a claim stated against a superseded context, or evidence
    bound to a statement the claim no longer carries, is exactly the kind of
    drift that makes a status mean less than it reads.

    Until this call the ledger was advisory: ``proof_ledger`` derived
    ``closed_kernel`` and ``conditional_kernel`` and nothing consulted the
    answer, so a project could finish a stage with a ledger contradicting
    itself. ``StateIssue.rendered()`` was written for this — it produces the
    same ``path: message`` shape ``lean_evidence`` and ``literature_ledger``
    already emit, so all three render alike here.

    What this deliberately does *not* do is let a claim's derived status decide
    whether the mission is complete. That question belongs with the objective
    mode and the requested bar below, it is a policy judgement rather than a
    structural one, and conflating the two would make "the ledger is consistent"
    and "the mathematics is finished" the same check when they are not.

    An unreadable ledger is reported as a defect rather than raised. Letting
    ``MathStateError`` escape would take down the completion gate for every
    other check with it, and a gate that reports nothing is indistinguishable
    from a gate that found nothing wrong.

    ``certificate_issues`` is asked alongside the kernel's own ``validate``
    because the kernel does not know what a Lean certificate is, and the defect
    it catches — a fidelity verdict still standing after the reading it approved
    was replaced — is precisely one a stage must not complete over. Both render
    the same way, so the caller cannot tell which found what, and does not need
    to.
    """
    from ...proof_ledger import MathStateError, load_state, state_path
    from .math_state import certificate_issues

    try:
        state = load_state(project_root)
    except MathStateError as exc:
        return (f"{state_path(project_root).name}: {exc}",)
    return tuple(
        item.rendered()
        for item in (*state.validate(), *certificate_issues(state))
    )


def _citation_delivery_issues(project_root: Path) -> tuple[str, ...]:
    """Every imported result has to have been looked up before anything ships.

    The half of research mathematics no proof checker touches. Lean can verify
    that a theorem follows from its hypotheses and say nothing about whether the
    hypothesis imported as "Theorem 3.2 of [K]" is in [K] at all — and a
    fabricated or misquoted citation is not a formatting defect, it is a proof
    that does not exist, wearing a reference. So this is asked once, here, and it
    is the only citation gate: ``scope`` and ``solve`` complete with citations in
    any state, because a checker that interrupted the mathematics to wait on a
    registry would simply be turned off.

    ``confirmed`` and ``uncited`` are the two states that pass, and the second is
    not a loophole. An assumption that records prose and no locator has said
    plainly that it leans on something unciteable — a private communication, an
    unpublished note — which a reader can weigh. What must not pass is the
    citation that *looks* checkable and was never checked, and its neighbour: a
    ``disputed`` one, where somebody did go and found the proposition missing.
    That is settled work and it still must not ship, which is why this asks a
    narrower question than ``CitationAssessment.is_settled``.

    Both remedies work with no network, because a gate whose only remedy needs
    one is a gate that traps an offline run: a reader who has the paper records
    what it says with ``citation_check attribute``, and a source that genuinely
    cannot be obtained is restated in prose, which drops the claim to ``uncited``
    and says so in the open rather than leaving a lookup nobody can perform.
    """
    from ...proof_ledger import MathStateError, load_state, state_path
    from .citation_check import DELIVERABLE_STATUSES

    try:
        state = load_state(project_root)
    except MathStateError as exc:
        return (f"{state_path(project_root).name}: {exc}",)
    where = "/".join(state_path(project_root).parts[-2:])
    issues: list[str] = []
    for claim in state.current_claims():
        for citation in state.citations(claim.claim_id):
            if citation.status in DELIVERABLE_STATUSES:
                continue
            issues.append(
                f"{where}: claim {claim.claim_id} stands on "
                f"{citation.assumption_id} ({citation.cited_proposition}), "
                f"whose citation is {citation.status.value}. Record what a "
                "reader found at that proposition with `python -m "
                "argus_skill.verticals.math.citation_check attribute --claim "
                f"{claim.claim_id} --assumption {citation.assumption_id} "
                "--excerpt-file <what you read> --verdict ... --by you`, or, if "
                "the source cannot be obtained, restate the assumption without "
                "--source-id/--locator so it reads as the prose citation it is"
            )
    return tuple(issues)


def prepare_mission(  # noqa: ARG001 - see the docstring on stage/state_root
    *,
    stage: str,
    project_root: Path,
    state_root: Path,
    mission: object,
) -> str:
    """Give this mission the state of the claim it is about, and nothing else.

    Keyword-only because the framework forwards this hook by keyword; the
    parameter names are the contract.

    ``stage`` is unread: what is recorded about a claim is the same fact in
    `scope`, `solve`, and `review`, and a projection that changed with the
    stage would be telling three different stories about one statement.
    ``state_root`` is unread because the mathematical state is project state —
    it sits in the project's `research/` directory beside `PROOF_GRAPH.json`,
    not in the per-session runtime root.

    Imported lazily: a project with no `research/MATH_STATE.json` never touches
    the state kernel, exactly as it never touches the Lean checker.
    """
    from .context_projection import project_mission_context

    return project_mission_context(project_root=project_root, mission=mission)


CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "scope": (
        ChecklistItem(
            id="scope.problem-explicit",
            statement=(
                "The problem is understood precisely enough to work on: the relevant "
                "objects, assumptions, quantifiers, and requested conclusion are clear."
            ),
            evidence_hint="the problem statement as actually understood",
        ),
        ChecklistItem(
            id="scope.success-criterion",
            statement=(
                "It is clear whether success means a proof, counterexample, construction, "
                "classification, estimate, or honest progress on an open problem. The "
                "objective mode is recorded, not assumed: `targeted` names one goal to "
                "prove or refute, `exploratory` names a direction whose deliverable is "
                "substantive partial results. The two have different completion bars, so "
                "an unset mode is a scope gap rather than a default."
            ),
            evidence_hint=(
                "the requested outcome and completion bar; math_objective_mode (and "
                "math_goal when targeted) in .argus/PIPELINE_STATE.json"
            ),
        ),
        ChecklistItem(
            id="scope.known-status-recorded",
            statement=(
                "What is already known about this problem has been established here "
                "and written into the ledger: the results the work will lean on, "
                "recorded as assumptions with their citations, and the approaches "
                "already known to fail. Completeness is not the bar and a literature "
                "survey is not the deliverable. The bar is that the answer exists in "
                "one place before several workers start, because a search performed "
                "in scope is paid once and the same search performed in solve is paid "
                "once per worker. \"Searched and found nothing relevant\" is a "
                "recorded answer, and so is \"no source could be obtained here\", "
                "which states a limitation a reviewer can weigh instead of leaving a "
                "gap that reads like an omission."
            ),
            evidence_hint=(
                "the assumptions recorded in research/MATH_STATE.json with their "
                "sources, or an explicit statement of what was searched and what it "
                "returned"
            ),
        ),
    ),
    "solve": (
        ChecklistItem(
            id="solve.substantive-result",
            statement=(
                "There is a substantive result relevant to the problem, supported by an "
                "argument, a valid witness, or a reproducible computation as appropriate."
            ),
            evidence_hint="the result and the mathematics or real run supporting it",
        ),
        ChecklistItem(
            id="solve.witness-valid",
            statement=(
                "Any counterexample or constructed object satisfies the original conditions; "
                "it is not a circular restatement or an answer to an easier problem."
            ),
            evidence_hint="a direct check of the relevant conditions",
        ),
        ChecklistItem(
            id="solve.support-matches-claim",
            statement=(
                "The strength of the conclusion matches the support: finite computation is "
                "not called a universal proof, and formal compilation is not treated as "
                "evidence for a mistranslated statement."
            ),
            evidence_hint="the actual tested range or compiler run and the stated limitation",
        ),
        ChecklistItem(
            id="solve.gap-reduced",
            statement=(
                "For a targeted project, the round moved the distance to the goal, not "
                "merely produced something new. Extending a finite verification to a wider "
                "range, more moduli, or more primes yields a fresh artifact and no gap "
                "reduction; repeating it at a larger bound buys the same information. Say "
                "which proposition changed status, or that none did. For an exploratory "
                "project this item is satisfied by a substantive, correctly-scoped result."
            ),
            evidence_hint=(
                "the proposition whose status changed; once a targeted route is settled, "
                "research/PROOF_GRAPH.json checked with `python -m "
                "argus_skill.verticals.math.proof_graph_check gap`"
            ),
        ),
    ),
    "review": (
        ChecklistItem(
            id="review.goal-achieved",
            statement=(
                "The completion claim matches the effective scope: project or final-stage "
                "completion requires the requested terminal mathematical outcome to be "
                "achieved. An error-free attempt, correct intermediate lemma, honest partial "
                "result, or unresolved conclusion is not final-stage completion. A bounded "
                "subtask may itself be done, but leave this item unsatisfied unless the "
                "original Goal Gate is achieved."
            ),
            evidence_hint=(
                "a direct mapping from the requested success criterion to the theorem, "
                "counterexample, construction, classification, or estimate actually obtained"
            ),
        ),
        ChecklistItem(
            id="review.statement-fidelity",
            statement=(
                "The natural-language problem and every formal statement are faithfully "
                "equivalent in objects, quantifiers, hypotheses, and conclusion."
            ),
            evidence_hint="a direct comparison with the original question",
        ),
        ChecklistItem(
            id="review.argument-correct",
            statement=(
                "The main argument is independently convincing: important steps are justified, "
                "dependencies are available, and no hidden assumption closes the gap."
            ),
            evidence_hint="the argument itself and any cited dependency",
        ),
        ChecklistItem(
            id="review.outcome-honest",
            statement=(
                "The conclusion says plainly what was proved, disproved, computed, conjectured, "
                "or left open. Novelty is claimed only when an appropriate source check supports "
                "it; otherwise uncertainty is stated without blocking a valid bounded result."
            ),
            evidence_hint="the stated conclusion, limitations, and sources if novelty is claimed",
        ),
    ),
}


def _lean_workspace_note() -> str:
    """Name the prebuilt Mathlib workspace this host already carries.

    ``math-research-execution.md`` promises that "if the host has Mathlib
    installed it is used automatically", and it does — but only through
    ``_resolve_lake_workspace``, whose first and highest-priority step is "a
    lakefile above the source". An Engineer who cannot see that a workspace
    exists does the obvious thing and writes its own lakefile into the project
    root, which *is* that first step, and so shadows the built library with an
    empty one. The promise then costs a full Mathlib fetch and build to keep:
    run 7 duplicated 7.5 GB into the project while a built workspace sat
    unused. Naming the path is the whole remedy — it turns an invisible default
    into something the agent can choose.

    Resolved at call time rather than at import: the search re-reads
    ``Path.home()`` and ``$ARGUS_SKILL_MATHLIB_WORKSPACE`` on every call, and a
    banner quoting a path the search does not use would be worse than silence.
    Fail-soft for the same reason — a host without Mathlib should get no
    paragraph, not a paragraph about a directory that is not there.
    """
    try:
        from .lean_evidence import resolved_mathlib_workspace
    except Exception:  # noqa: BLE001 - optional heavy import
        return ""
    try:
        workspace = resolved_mathlib_workspace(Path.home() / "Main.lean")
    except Exception:  # noqa: BLE001 - a banner never fails a mission
        return ""
    if workspace is None:
        return ""
    return (
        "\n\n## This host's Lean environment\n\n"
        f"A built Lake workspace with Mathlib already exists at `{workspace}`.\n"
        "Compiling from a directory with no lakefile of its own picks it up "
        "automatically, so `import Mathlib` needs no project scaffolding from "
        "you. Authoring a `lakefile.toml` in the project root does the "
        "opposite of what it looks like: the workspace search takes the "
        "nearest lakefile above the source first, so your new one shadows the "
        "built library and you pay for a fresh Mathlib fetch and build. Write "
        "one only if you actually need a different Mathlib revision, and say "
        "in the round summary why."
    )


def role_banner(role: str) -> str:
    """Load Math context as a Skill for the generic role implementation."""
    role_name = (role or "").strip().lower()
    skill_name = {
        "manager": "manager/math-research-manager.md",
        "planner": "planner/math-research-planning.md",
        "engineer": "engineer/math-research-execution.md",
        "reviewer": "reviewer/math-research-review.md",
        "scientist_create": "scientist/math-research-distillation.md",
        "scientist": "scientist/math-research-adaptation.md",
    }.get(role_name)
    if skill_name is None:
        return ""
    text = (Path(__file__).parent / "skills" / skill_name).read_text(
        encoding="utf-8"
    )
    if text.startswith("---"):
        _frontmatter, _separator, body = text[3:].partition("---")
        text = body
    banner = text.strip()
    # Only the two roles that compile. The Planner picks a route and the
    # Manager picks a stage; neither runs Lean, and a host fact they cannot act
    # on is prompt weight spent for nothing.
    if role_name in {"engineer", "reviewer"}:
        banner += _lean_workspace_note()
    return banner


__all__ = [
    "CHECKLIST_ITEMS",
    "CHECKLIST_STAGE_ORDER",
    "COMPLETION_CONTRACT_VERSION",
    "PROTECTED_ITEM_IDS",
    "REQUIRE_INDEPENDENT_REVIEW",
    "RESEARCH_TARGET_LEVELS",
    "STAGE_ORDER",
    "WORKFLOW_MODE",
    "adopt_operator_objective",
    "completion_gate",
    "prepare_mission",
    "role_banner",
    "stage_completion_issues",
]
