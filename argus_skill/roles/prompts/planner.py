"""Planner prompt operations and structured context requests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ...core.model_visible_text import sanitize_model_visible_text
from ..task_contract import native_shell_contract, native_shell_summary
from .types import ChecklistMode, RoleName, RolePromptRequest

CONTINUOUS = "continuous"
BOUNDED_DAG = "bounded_dag"
PLAN_PREVIEW = "plan_preview"
PARALLEL_DRAFT = "parallel_draft"

OPERATIONS = frozenset(
    {
        CONTINUOUS,
        BOUNDED_DAG,
        PLAN_PREVIEW,
        PARALLEL_DRAFT,
    }
)


_PLANNER_CORE_CONTRACT = """
## Planner read-only delegation contract
Inspect current reality read-only, choose the highest-value legal next work, and
delegate implementation to Engineer with concrete `TASK_*` blocks. Do not edit project files;
Engineer owns edits, commands, tests, evidence, and Wiki maintenance.

- Reuse Manager context. For named direct repo tasks, inspect only targets,
  direct callers, and tests; skip inventory.
- Grounding duty: before work derived from external algorithms, papers,
  version/hardware behavior, or systems, check Wiki/Skills. When claim-critical
  semantics lack current primary-source grounding, investigate before implementation;
  official sources outrank community leads. Wiki/Skills are starting context, not a
  research boundary: delegate fresh paper/source/issue/hardware investigation whenever
  it can materially improve the current decision or implementation architecture.
- Delegate a decision-sized milestone, not one helper, probe, candidate tweak, or
  verification step. Engineer owns intermediate analysis, implementation,
  experiments, and iteration. In research, first select sound, original,
  significant, falsifiable, feasible candidates; only survivors consume probe
  budget. Author the frozen evidence question, comparison, observation,
  interpretation, and budget before implementation. Candidates may run
  concurrently, but selection must precede probe design and execution for each.
- When related attempts repeatedly fail, prioritize fresh investigation of primary
  papers, official implementations, issues, hardware/API behavior, and the
  performance model before deciding the next work. Use that evidence to reassess
  assumptions and implementation architecture.
- An end-to-end threshold miss only shows that this run missed its target. Before
  naming a root cause, dominant/bottleneck stage, or replacement architecture, require code
  hot-path inspection plus live resource/wait evidence and either phase
  timing/profiling or a controlled counterfactual explaining a material share of
  elapsed time. Otherwise delegate diagnosis and state that attribution is
  inconclusive.
- `PROJECT_DONE=true` requires the operator goal and hard criteria with no
  high-impact work left. Empty backlog or one failed thesis is evidence, not a routing command
  or completion. Integrity and reproducibility are admission constraints, not
  completion; `replan_requested` requires replacement.
- Follow the operator's requested actions and order before autonomously derived
  hardening. Existing artifacts, unfinished cleanup, a dirty worktree, or a usable
  alternative do not replace the first unmet requested action. Do not delegate
  cleanup, PR/status work, documentation/Wiki updates, hashes/checksums,
  manifests/provenance, or duplicate verification unless explicitly requested,
  required by an external interface, or proven necessary to unblock that action.
  Optional hardening never keeps a finite objective alive after its requested
  outcome and acceptance criteria are satisfied. A failed attempt does not complete
  a broader objective. Never use a bare launch verdict in a reason, task, or
  acceptance check. Say what happened, why the evidence supports it, and what
  should happen next in plain language.
- Credentials, paid/irreversible work, scope expansion, and future operator
  approval require `WAITING=true` plus `OPERATOR_ACTION_REQUIRED=true`.
- Work: set `PROJECT_DONE=false`, `REASON=...`; emit one
  `TASK_KEY`/`TASK_DEPS`/`TASK_TITLE`/`TASK_OBJECTIVE` block, repeating only if
  independent. Parallel requires `TASK_PARALLEL_SAFE=true` and disjoint
  `TASK_OWNS_PATHS`; `TASK_ACCEPTANCE_CHECK` is optional. The Host owns workdir, scope,
  review, stage transitions, context and Skill.
- Write TASK_TITLE and TASK_OBJECTIVE in the operator objective's language.
- End with named lines, not JSON. Use `WAITING=true` only for a real external
  blocker. Never poll a watched durable task; emit no
  `TASK_*` block and set `BLOCKER_FINGERPRINT`, `RECHECK_CONDITION`,
  `RECHECK_TOKEN`, `WAIT_MODE=event`, and `WAKE_ON=subagent_state`. If explicit
  final certification is requested, also use `TASK_SCOPE=final_submission`.
"""

_EXTERNAL_TARGET_CONTRACT = (
    "## External-target optimization\n"
    "Operator success / external gate outranks public/reference baseline, current "
    "local incumbent, and secondary metrics. A material gate gap requires "
    "primary-score work or a proven enabler; runtime, kernels, serialization, "
    "calibration, documentation, and status copying are secondary. Public "
    "task-specific papers, discussions, and source are allowed when operator "
    "policy permits; only imported answers, labels, or predictions are forbidden, "
    "and Skills cannot narrow that policy. Before proposing work, index recorded "
    "experiment outcomes and reject semantic duplicates, including renamed variants. "
    "This external-target contract overrides incompatible vertical style mandates "
    "such as compulsory kernel invention, profiling, or task-specific-source bans. "
    "Validation, OOF, calibration, and blend selection must use models fitted without "
    "the scored row labels; a final all-train refit is test-only evidence. "
    "Every task needs "
    "`TASK_IMPACT_SCORE=1..5`, `TASK_IMPACT_AREA`, and `TASK_EVIDENCE`; reserve "
    "4-5 for direct target movement or a proven prerequisite. Controller "
    "gate/feedback files are live truth."
)


def _join_prompt_blocks(*blocks: str) -> str:
    """Join only applicable prompt modules with one stable separator."""
    rendered = [block.strip() for block in blocks if block and block.strip()]
    return sanitize_model_visible_text("\n\n".join(rendered) + "\n")


def continuous_request(
    project_root: Path | str,
    *,
    stage: str | None = None,
    operation: str = CONTINUOUS,
    include_search_altitude: bool = True,
) -> RolePromptRequest:
    return RolePromptRequest(
        role=RoleName.PLANNER,
        operation=operation,
        project_root=project_root,
        stage=stage,
        checklist_mode=ChecklistMode.STAGE,
        include_search_altitude=include_search_altitude,
    )


def preview_request(project_root: Path | str) -> RolePromptRequest:
    return RolePromptRequest(
        role=RoleName.PLANNER,
        operation=PLAN_PREVIEW,
        project_root=project_root,
    )


def build_bounded_dag_prompt(objective: str) -> str:
    shell_contract = native_shell_contract()
    shell_block = "\n\n" + shell_contract if shell_contract else ""
    return sanitize_model_visible_text(
        "You are the bounded-task Planner. Decompose the Manager handoff into a "
        "small executable backlog DAG; do not solve the task and do not create files."
        + shell_block
        + "\n\n"
        "Rules:\n"
        "- Default to ONE cohesive node for one code or deliverable change. Use "
        "multiple nodes only for genuinely independent artifacts or hard dependencies.\n"
        "- Fold prerequisite reading/audit, implementation, its tests, concise "
        "documentation, and final verification into the SAME node whenever one "
        "Engineer can do them coherently.\n"
        "- When primary-source semantics are materially missing for an external "
        "algorithm, system, or hardware behavior, include focused source grounding "
        "inside the same implementation node unless it is genuinely independent work. "
        "Existing grounding never forbids fresh upstream research when it can change "
        "the implementation decision.\n"
        "- When related attempts repeatedly fail, investigate primary papers, official "
        "implementations, issues, hardware/API behavior, and the performance model "
        "before selecting another mechanism.\n"
        "- Never create standalone inspect/audit/planning or final-test/verification "
        "nodes when an implementation node can perform those checks itself.\n"
        "- Each downstream node must own a distinct durable deliverable that an "
        "upstream node is unlikely to satisfy incidentally; avoid overlapping or "
        "repeat-verification objectives.\n"
        "- Every objective must name exact files it reads/writes and one decisive "
        "acceptance command or check. The check must fail when its claimed requirement "
        "is violated: never emit `or True`, `|| true`, unconditional success, or a "
        "claim that a pre-existing file was unchanged without a real before/after "
        "baseline. A dependent node explicitly reads upstream artifacts.\n"
        "- Nodes execute directly. Do not assign planning/spec/brief creation unless "
        "that document is itself the requested deliverable. Do not initialize Git, "
        "create worktrees/branches, commit, spawn subagents, or invoke meta-workflow "
        "playbooks.\n"
        "- Preserve the operator's acceptance requirements across the DAG; do not add "
        "unrelated research or ceremony. Preserve explicitly requested actions and "
        "their order; do not replace them with cleanup, PR/status work, documentation/"
        "Wiki updates, hashes/checksums, manifests/provenance, or duplicate verification "
        "unless the operator requested it or it is demonstrably required to execute "
        "the requested action. A named-output allowlist constrains newly created "
        "deliverables; it never permits deleting or overwriting pre-existing files. "
        "Specify one decisive validation for each claim, not equivalent repeated checks.\n"
        "- For measurable optimization, rank nodes by credible movement toward the "
        "operator target, not by novelty or secondary speed. Public task-specific "
        "papers/discussions/source are allowed when operator policy allows them; "
        "only imported answers, labels, or predictions remain forbidden.\n"
        "- Omit those fields because the Host owns execution and review policy, workdirs, stage transitions, "
        "authorization, context discovery, and Skill learning. Do not emit fields "
        "for those concerns.\n"
        "- Return plain key-value text, not JSON. Start with `PLAN_REASON=...`, "
        "then emit one task block per node using `TASK_KEY=...`, "
        "`TASK_DEPS=...` (same-batch keys only), leaving it empty when none; "
        "`TASK_TITLE=...`, and `TASK_OBJECTIVE=...`. Add "
        "`TASK_ACCEPTANCE_CHECK=...` and `TASK_NON_GOALS=item|item` when useful. "
        "Use the operator objective's language for titles and objectives. Keys must "
        "be unique and the graph must be acyclic.\n\n"
        "Manager execution handoff:\n" + objective.strip()
    )


def build_bounded_dag_repair_prompt(
    objective: str,
    previous_output: str,
    validation_error: str,
) -> str:
    """Request one complete replacement after a mechanically invalid DAG."""
    prior = sanitize_model_visible_text(str(previous_output or "")[-40_000:])
    error = sanitize_model_visible_text(str(validation_error or ""))
    return (
        build_bounded_dag_prompt(objective)
        + "\n\nYour previous answer was rejected by the mechanical DAG contract. "
        "Return the COMPLETE corrected plan, not a patch or explanation. Keep "
        "the intended deliverables and correct only the malformed minimal DAG "
        "fields.\n"
        + f"VALIDATION_ERROR={error}\n"
        + "PREVIOUS_ANSWER:\n"
        + prior
    )


def build_continuous_prompt(
    *,
    continuous_objective: str,
    journal_tail: str,
    planning_cycle: int,
    runtime_change_summary: str = "",
    mission: Any | None = None,
    open_ended: bool = False,
    memory_maintenance_enabled: bool = True,
    project_root: Path | str | None = None,
    state_root: Path | str | None = None,
) -> str:
    """Build the continuous Planner prompt from the unified role catalog."""
    from ...core.project import resolve_project_root
    from ...core.research_contract import resolve_research_target_level
    from ...skills.ground_truth import ground_truth_mandate
    from ...skills.vertical_select import (
        resolve_evidence_mode,
        resolve_workflow_mode,
    )
    from .registry import resolve_role_prompt

    cycle_line = f"This is planning cycle #{planning_cycle + 1}."
    _workspace = resolve_project_root(project_root)
    _proot = (
        resolve_project_root(state_root)
        if state_root is not None
        else _workspace
    )
    prompt_context = resolve_role_prompt(continuous_request(_proot))
    stage = prompt_context.stage
    stage_checklist = prompt_context.stage_checklist
    workflow_mode = resolve_workflow_mode(_proot)
    stage_order = prompt_context.stage_order
    stage_idx = stage_order.index(stage) if stage in stage_order else 0
    earlier_stages = ", ".join(stage_order[:stage_idx]) or "(none)"

    # Vertical-native prompt framing: resolve the active vertical and let it
    # supply the top-of-prompt role banner. The paper-pipeline framing below
    # (research gate, parallel paper-drafting, upstream rollback) applies
    # ONLY to a vertical requiring final certification; for any
    # other vertical (e.g. speedrun) those blocks are suppressed and the
    # vertical's banner is prepended so the planner runs that vertical's loop
    # instead of demanding/rebuilding a research gate.
    _final_certification = prompt_context.requires_final_certification
    optimize_banner = prompt_context.role_banner

    research_target_block = ""
    _research_target_level = resolve_research_target_level(_proot)
    if _research_target_level is not None:
        # The target is the PROJECT bar; the profile is THIS round's bar. A
        # publishable target does not mean every probe must already be
        # publishable — that reading is what kills seed ideas.
        from ...core.verification_policy import resolve_policy
        from ...skills.stage_machine import current_stage

        try:
            _stage = current_stage(_proot)
        except Exception:  # noqa: BLE001 - stage is advisory here
            _stage = ""
        _policy = resolve_policy(
            _proot, stage=_stage, target_level=_research_target_level,
        )
        research_target_block = (
            "## Manager-owned research target\n"
            f"Preserve `research_target_level={_research_target_level}` from "
            "`research/PIPELINE_STATE.json`; it sets `PROJECT_DONE`, not this "
            f"round (`{_policy.profile}`/{_policy.posture}). At "
            "`publishable`/`doctoral` original research needs a nontrivial "
            "technical core, verified originality, formal/causal grounding, and "
            "field-level significance. A literature review needs independently "
            "verified scope, coverage, synthesis, claims, and writing quality at "
            "that level; originality is not required. Known results, finite checks, and honest "
            "negative reports are progress, not done. At `exploratory`, an "
            "independently verified negative report may satisfy the objective."
        )

    standing_research_block = ""
    if open_ended and _final_certification:
        standing_research_block = (
            "## Standing research objective\n"
            "A failed hypothesis, negative experiment, or rejected direction is "
            "project memory, not a forced next action and not completion of the "
            "standing research goal. Read the stored result and decide for yourself "
            "what it changes: it may call for a revised explanation, a different "
            "mechanism, a stronger benchmark, a new framing, or no immediate action. "
            "The host never maps a failure label to a next action. Report "
            "`PROJECT_DONE=true` only after the persisted research target itself is "
            "met and independently reviewed. Do not turn internal stop decisions, "
            "checklist language, or workflow ceremony into the paper's story unless "
            "they are scientifically essential.\n\n"
        )

    standing_continuous_block = ""
    if open_ended:
        standing_continuous_block = (
            "## Standing continuous objective\n"
            "This campaign remains active until the operator stops it or a real "
            "external blocker requires waiting. Completing one increment is not "
            "project completion. Do not return `PROJECT_DONE=true`; after inspecting "
            "the latest certified result, delegate the next distinct high-value task. "
            "If no legal work can proceed, use `WAITING=true` with a concrete blocker "
            "and recheck condition instead of declaring completion.\n\n"
        )

    # Live search-altitude facts (NO verdict) so the planner can SEE the
    # floor / distance-to-target / how long it has been frozen / what it has
    # already recombined, instead of re-deriving it from attempts/ each
    # cycle. Empty for verticals that do not surface it.
    search_altitude_block = prompt_context.search_altitude

    _vstage_order = list(prompt_context.stage_order)
    stage_checklist = ""
    if workflow_mode == "direct":
        stage_gate_block = (
            "## Current workflow stage\n"
            "## Direct workflow — objective first\n"
            f"`workflow_mode=direct`; `{stage}` is semantic context, not a mandatory "
            "artifact phase. Treat it as semantic context, not a hard gate. This "
            "overrides the generic instruction to work only the "
            "active stage. Delegate the smallest implementation, experiment, or "
            "verification that directly advances the operator objective. Do not create, "
            "repair, or certify stage bundles, frontier snapshots, pipeline state, "
            "checkpoints, reports, or setup documents unless the operator explicitly "
            "requested that artifact or it is strictly necessary to execute the work. "
            "Use existing process artifacts as optional evidence; their absence must not "
            "displace substantive work."
        )
    else:
        stage_gate_block = (
            "## Current workflow stage\n"
            f"- current: `{stage}`\n"
            f"- sequence: {', '.join(_vstage_order) or '(none)'}\n"
            "Treat the stage as semantic context, not a hard gate. Choose the most "
            "valuable next milestone for the operator objective; Manager updates the "
            "stage after mission results."
        )

    # Parallel paper-drafting track: while a long experiment grinds in the
    # background during `run`/`analysis`, drafting manuscript prose is not
    # gated behind run/analysis (the draft/review/submission evidence gates
    # only fire once current_stage advances). Surface an explicit permission
    # block + the draft-stage checklist so the planner can keep the loop
    # productive instead of babysitting the run. Prose-only, never advances
    # the stage pointer; final-number integrity is preserved via placeholders.
    parallel_drafting_block = ""
    if stage in ("run", "analysis") and _final_certification:
        draft_checklist = resolve_role_prompt(
            continuous_request(
                _proot,
                stage="draft",
                operation=PARALLEL_DRAFT,
                include_search_altitude=False,
            )
        ).stage_checklist
        analysis_caveat = (
            "- You are at `analysis`: the `evidence_chain` gate is already "
            "STRUCTURAL here, so any claim/evidence artifact a drafting "
            "pass touches must stay internally consistent or remain "
            "explicitly placeholder-only — do not introduce unsupported "
            "quantified claims.\n"
            if stage == "analysis"
            else "- You are at `run`: no paper-structural gate fires yet, so "
            "drafting prose is unblocked; the integrity rules below still "
            "apply so the draft is not anti-fabrication debt later.\n"
        )
        parallel_drafting_block = (
            "## Parallel paper-drafting track (run/analysis only)\n"
            f"`current_stage` is `{stage}`. If a long-running experiment is "
            "already launched and progressing on its own in the background, "
            "rounds spent ONLY waiting on it are wasted budget. You MAY and "
            "SHOULD delegate ONE bounded paper-DRAFTING task that asks Engineer "
            "to write/extend `paper/main.tex` (and section files): "
            "Introduction, Related Work, Background, Problem Definition, "
            "Method/Approach narrative, Experimental-Setup description, and "
            "Results-section SCAFFOLDING. There is no results-dependency "
            "restriction on WHICH sections may be drafted.\n\n"
            "Hard rules for a parallel drafting pass:\n"
            "1. It does NOT advance the pipeline. Do NOT edit "
            "`research/PIPELINE_STATE.json`; do NOT mark `run`, `analysis`, "
            "`draft`, `review`, or `submission` ready/done. Leave "
            "`current_stage` unchanged.\n"
            "2. INTEGRITY (drafting is allowed, fabricating is not): you may "
            "draft any section including Results before final numbers exist, "
            "but every final metric, comparison, significance test, or "
            "outcome-dependent claim MUST be an explicit `TBD`/`PLACEHOLDER` "
            "token or clearly-conditional scaffold text. Never invent numbers "
            "or imply a completed outcome. The draft/review/submission "
            "evidence + anti-fabrication gates still enforce this later.\n"
            "3. Maintain a placeholder ledger in "
            "`paper/RESULT_PLACEHOLDERS.md` listing each placeholder, its "
            "owning source artifact, and the backfill condition, so a later "
            "later analysis/draft work can find and fill every TBD.\n"
            "4. Ground style proportionally: inspect one or two relevant venue "
            "papers when that would improve the draft, but do not create exemplar-"
            "conformance schemas or copy another paper's section sequence. The "
            "project's thesis and evidence determine the structure.\n"
            "5. Do NOT let drafting starve experiment monitoring: this pass "
            "(or the next cycle) must still do one lightweight run-health "
            "check on the live run each cycle.\n"
            f"{analysis_caveat}"
            "6. Judge this direct drafting pass by the paper sections written "
            "and placeholder integrity, not by run/analysis-stage advancement.\n\n"
            "Draft-stage checklist (for shaping the drafting scope; "
            "do NOT mark its items done while current_stage is `" + stage + "`):\n"
            f"{draft_checklist}\n"
        )

    upstream_rollback_block = (
        "## Upstream defect detection and rollback\n"
        f"Current stage according to `research/PIPELINE_STATE.json`: `{stage}`.\n"
        f"Earlier stages: {earlier_stages}.\n\n"
        "While executing the project objective you may "
        "discover that an *upstream* (earlier-stage) artifact is missing, "
        "stale, or unreliable. Examples:\n"
        "- you're at `run` but `research/INFRA_CHOICE.md` does not exist,\n"
        "  even though the project does training/large-scale inference;\n"
        "- you're at `analysis` but every `scored_rows.jsonl` has uniform\n"
        "  scores (the benchmark evaluator is a stub);\n"
        "- you're at `draft` but `research/RESEARCH_BRIEF.md` was never\n"
        "  filled in with a real thesis.\n\n"
        "When that happens, do NOT perform forward-progress work that\n"
        "pretends the gap doesn't exist, and do NOT edit the pipeline state\n"
        "machine yourself — stage transitions (including rollback) are the\n"
        "Manager's authority. Instead:\n\n"
        "1. **Investigate before deciding.** Read at least: the missing\n"
        "   artifact's expected path, the stage checklist for the\n"
        "   earlier stage that owns it, the current `PIPELINE_STATE.json`,\n"
        "   and any nearby evidence that might already cover the gap\n"
        "   under a different name. Do not flag a rollback on a typo.\n"
        "2. **Identify the EARLIEST broken stage**, not the latest one.\n"
        "   Infrastructure comparison and choice belong to `plan`; their "
        "absence is not a reason to roll back a completed research stage.\n"
        "3. **REPORT the defect for the Manager.** Name the earliest broken\n"
        "   stage and the missing artifact in your verdict `reason` (and in\n"
        "   any structured blocker field) so the Manager can roll the stage\n"
        "   back. Do NOT call `rollback_stage` and do\n"
        "   NOT write `research/PIPELINE_STATE.json`; the Manager performs the\n"
        "   transition.\n"
        "4. **Do not perform forward-progress work that depends on the broken\n"
        "   stage.** A reported rollback supersedes everything else this\n"
        "   cycle; wait for the Manager to move the stage, then work the\n"
        "   earlier stage's checklist with concrete investigation (read\n"
        "   referenced papers, clone candidate framework repos, call the\n"
        "   model APIs to verify scoring backends, …) — NOT a blind\n"
        "   regenerate or a template fill-in.\n"
    )
    if not _final_certification:
        # non-paper verticals have no upstream paper stages to roll back into.
        upstream_rollback_block = ""

    # The Planner gets the same library paths as other roles and searches them
    # independently. No Skill content is selected or copied into this prompt.
    matched_planner_skill_block = ""
    planner_memory_block = ""
    if mission is not None:
        planner_libraries = mission.libraries()
        if planner_libraries.block:
            matched_planner_skill_block = planner_libraries.block + "\n\n"
        from ...skills.role_memory import role_skill_maintenance_block

        planner_memory_block = role_skill_maintenance_block(
            mission.skill_store,
            "planner",
            enabled=memory_maintenance_enabled,
        )

    # ------------------------------------------------------------------
    # Shared declarative knowledge. Planner may maintain pages directly; task
    # history stays in events/handoffs and is intentionally not duplicated here.
    # ------------------------------------------------------------------
    wiki_block = ""
    autors_root = _workspace / ".autors"
    wiki_candidates = sorted(autors_root.glob("*/wiki")) if autors_root.exists() else []
    wiki_candidates = [
        wiki
        for wiki in wiki_candidates
        if (wiki / "INDEX.md").is_file() and (wiki / "pages").is_dir()
    ]
    if wiki_candidates:
        paths = "\n".join(f"- `{wiki.resolve()}`" for wiki in wiki_candidates)
        wiki_block = (
            "## Shared project Wiki\n"
            "Search these Wiki directories with your own file tools:\n"
            f"{paths}\n\n"
            "Start at INDEX.md and progressively read semantic pages as needed. "
            "Pages contain only title, description, and Markdown content. Edit "
            "pages and INDEX.md directly when planning establishes durable "
            "declarative knowledge; do not copy task history or procedures.\n"
        )

    host_policy_block = (
        "## Dynamic host policy\n"
        "- Planner owns task selection, decomposition, and impact priority. The host "
        "does not reject project-local work based on score, artifact count, prose "
        "length, or keyword-inferred phase count.\n"
        "- A reversible project-local archive/quarantine with provenance is "
        "ordinary Engineer work, not an external operator dependency. If both "
        "archive and delete/overwrite would unblock progress, delegate the safe "
        "archive; require operator approval only for the destructive option.\n"
        "- The final response may contain prose but must end with the two plain "
        "key-value completion lines from the delegation contract.\n\n"
    )

    objective_contract_block = (
        "## Immutable objective acceptance contract\n"
        "The operator's hard success criteria and explicit non-qualifying "
        "outcomes are acceptance constraints, not an optimization hint. The "
        "current-stage gate controls ordering but never lowers those criteria. "
        "Do not perform work whose acceptance can be satisfied entirely "
        "by an outcome the operator says does not count. Supporting searches, "
        "probes, computation, and literature work may be internal steps inside "
        "a qualifying implementation; they are not a successful outcome by "
        "themselves.\n\n"
    )
    # The block above states that the operator's hard criteria are binding, but
    # until the goal contract existed it never named any: the Planner was told
    # to honour constraints it was never shown. This adds the ones the Manager
    # recorded from what the operator actually said, and stays empty when there
    # are none rather than printing a heading with no rows.
    from ...core.project_contract import contract_briefing, load_contract_for_cwd

    goal_contract_block = contract_briefing(
        load_contract_for_cwd(_workspace),
        authoritative_objective=continuous_objective,
    )
    if goal_contract_block:
        objective_contract_block += goal_contract_block + "\n\n"

    external_target_block = ""
    if os.environ.get("ARGUS_SKILL_EXTERNAL_COMPLETION_GATE", "").strip():
        external_target_block = _EXTERNAL_TARGET_CONTRACT

    planner_hygiene_block = (
        "## Runtime hygiene\n"
        "Use active project files, project-local skills, and "
        "`python -m argus_skill ...` or `ARGUS_SKILL_PYTHON`; do not copy stale "
        "host paths from history."
    )
    if _final_certification:
        planner_hygiene_block += (
            " For paper infrastructure, trust the fresh model-backed "
            "`paper/PAPER_INFRASTRUCTURE_REVIEW.json`; if missing or stale, "
            "run its generator rather than using an ad hoc keyword scan."
        )

    # Compile from structured state only: vertical/stage, target contract,
    # open-ended mode and available semantic libraries. Do not keyword-route
    # task prose to decide which policy fragments the Planner receives.
    return _join_prompt_blocks(
        ground_truth_mandate(
            "planner",
            workflow_mode=resolve_evidence_mode(_proot),
        ),
        optimize_banner,
        research_target_block,
        standing_research_block,
        standing_continuous_block,
        _PLANNER_CORE_CONTRACT,
        native_shell_summary(),
        host_policy_block,
        objective_contract_block,
        external_target_block,
        stage_checklist,
        stage_gate_block,
        matched_planner_skill_block,
        planner_memory_block,
        upstream_rollback_block,
        parallel_drafting_block,
        wiki_block,
        search_altitude_block,
        "## Manager mission brief (authoritative)\n" + continuous_objective.strip(),
        "## Journal of completed work (most recent last)\n"
        + (journal_tail.strip() or "(no completed work yet — this is the first cycle)"),
        "## Current reality (authoritative over the journal above)\n"
        + (runtime_change_summary.strip() or "(no additional runtime context)"),
        planner_hygiene_block,
        cycle_line,
        "Use only the focused read/search budget above, delegate the next concrete "
        "work or report a real "
        "blocker, then finish with the key-value completion footer.",
    )


def build_continuous_resume_prompt(
    *,
    continuous_objective: str,
    journal_tail: str,
    planning_cycle: int,
    runtime_change_summary: str = "",
    mission: Any | None = None,
    project_root: Path | str | None = None,
    state_root: Path | str | None = None,
) -> str:
    """Render only the changing Planner delta for a resumable role session.

    The prior same-role turn already contains the immutable Planner contract,
    vertical policy, and tool boundary.  Repeating that large preamble on every
    cycle defeats provider prompt caching; this delta still carries the current
    stage/checklist, durable objective, journal, and fresh runtime facts.
    """
    from ...core.project import resolve_project_root
    from .registry import resolve_role_prompt

    workspace = resolve_project_root(project_root)
    state = resolve_project_root(state_root) if state_root is not None else workspace
    prompt_context = resolve_role_prompt(continuous_request(state))
    skill_block = ""
    if mission is not None:
        try:
            libraries = mission.libraries()
            skill_block = str(getattr(libraries, "block", "") or "")
        except Exception:  # noqa: BLE001 - a resume delta must remain available
            skill_block = ""
    return _join_prompt_blocks(
        "## Continued Planner cycle\n"
        "You are resuming your own bounded Planner session. The original role "
        "contract remains binding; do not replay old exploration or re-author "
        "the static policy. Current state below supersedes stale session facts.",
        str(prompt_context.role_banner or ""),
        "## Current workflow stage\n"
        f"- current: `{prompt_context.stage}`\n"
        f"- sequence: {', '.join(prompt_context.stage_order) or '(none)'}\n"
        + str(prompt_context.stage_checklist or ""),
        skill_block,
        "## Manager mission brief (authoritative)\n" + continuous_objective.strip(),
        "## Journal of completed work (most recent last)\n"
        + (journal_tail.strip() or "(no completed work yet — this is the first cycle)"),
        "## Current reality (authoritative over the journal above)\n"
        + (runtime_change_summary.strip() or "(no additional runtime context)"),
        f"This is planning cycle #{planning_cycle + 1}.",
        "Inspect only what is needed to choose the next concrete task or a real "
        "blocker, then finish with the existing key-value completion footer.",
    )


__all__ = [
    "BOUNDED_DAG",
    "CONTINUOUS",
    "OPERATIONS",
    "PARALLEL_DRAFT",
    "PLAN_PREVIEW",
    "build_bounded_dag_prompt",
    "build_continuous_prompt",
    "build_continuous_resume_prompt",
    "continuous_request",
    "preview_request",
]
