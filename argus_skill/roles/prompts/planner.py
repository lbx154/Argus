"""Planner prompt operations and structured context requests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

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
You are the L4 Planner. Inspect current reality read-only, choose the next
highest-value legal work, and delegate implementation to Engineer through
concrete `TASK_*` blocks. Do not edit project files, run implementation work,
or claim that you implemented a change yourself. The sole write exception is
the shared declarative knowledge wiki under `.autors/*/wiki`: you may directly
create or refine those knowledge pages under its own evidence rules.

- Use read-only project tools to inspect relevant state, source, tests,
  artifacts, Reviewer briefings, and CHECKPOINT.md. Engineer owns edits,
  commands that change project state, builds, tests, and implementation evidence.
- Work the active vertical's current stage. The Manager alone changes
  `current_stage`; Planner and Engineer never edit it.
- Report `PROJECT_DONE=true` only when the operator objective and its hard success
  criteria are actually satisfied and no independent high-impact work remains.
  Integrity and reproducibility are admission constraints, not value by themselves.
  Empty backlog, an honestly reported negative result, or a failed approach is not
  automatically completion. A failed thesis is project evidence, not a routing command;
  inspect implementation adequacy, construct fidelity, and what the result changes,
  then delegate the next high-value move. When the Reviewer returns
  `replan_requested`, do not report completion; repair or replace the direction unless
  a later Reviewer explicitly certifies a valuable project thesis with `done`.
- Credentials, licensed access, irreversible external actions, and scope
  expansion require fresh operator authority; reversible project-local work does not.
- If `PROJECT_DONE=false`, do not leave an empty plan. Either report an
  intentional live wait with `WAITING=true` and a durable recheck contract, or
  emit concrete `TASK_*` blocks (`TASK_KEY`, `TASK_TITLE`, `TASK_OBJECTIVE`, and
  when known `TASK_ACCEPTANCE_CHECK`) for legal next work. Refs use
  `TASK_CONTEXT_REFS=kind::project/relative/path::why|...` (existing project
  files; omit if none). `TASK_SKIP_STAGE_TRANSITION=true` requires bounded +
  reviewed + `TASK_STAGE_CLOSING=false`. For a known blocker, keep one stable
  `TASK_BLOCKER_FINGERPRINT` across retries; blank otherwise. Use
  `item:<item_id>` for a failed non-resumable backlog item.
  Never fabricate work merely to satisfy this shape.
- Natural-language progress and a final summary are allowed. End the final response
  with plain key-value lines, not JSON or a Markdown fence. Always include:
  `PROJECT_DONE=true|false`
  `REASON=<concise implementation and verification summary or blocker>`
  When `PROJECT_DONE=false`, include the required `WAITING_*` or `TASK_*` lines
  described above.
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
    "Every task needs "
    "`TASK_IMPACT_SCORE=1..5`, `TASK_IMPACT_AREA`, and `TASK_EVIDENCE`; reserve "
    "4-5 for direct target movement or a proven prerequisite. Controller "
    "gate/feedback files are live truth."
)


def _join_prompt_blocks(*blocks: str) -> str:
    """Join only applicable prompt modules with one stable separator."""
    rendered = [block.strip() for block in blocks if block and block.strip()]
    return "\n\n".join(rendered) + "\n"


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
    return (
        "You are the bounded-task Planner. Decompose the Manager handoff into a "
        "small executable backlog DAG; do not solve the task and do not create files.\n\n"
        "Rules:\n"
        "- Every node gets one fresh Engineer session. The Engineer decides from "
        "the completed work and verification whether an independent Reviewer is "
        "useful; framework-required gates may still force review. Minimize total "
        "cost: default to ONE cohesive node for one code/deliverable change, and "
        "use multiple nodes only for genuinely independent artifacts or hard "
        "dependencies.\n"
        "- Each node must fit one fresh Engineer session and, when the Engineer "
        "requests it or the framework requires it, one Reviewer plus at most a "
        "small Reviewer-requested repair budget.\n"
        "- Fold prerequisite reading/audit, implementation, its tests, concise "
        "documentation, and final verification into the SAME node whenever one "
        "Engineer can do them coherently.\n"
        "- Never create standalone inspect/audit/planning or final-test/verification "
        "nodes when an implementation node can perform those checks itself.\n"
        "- Each downstream node must own a distinct durable deliverable that an "
        "upstream node is unlikely to satisfy incidentally; avoid overlapping or "
        "repeat-verification objectives.\n"
        "- Every objective must name exact files it reads/writes and one decisive "
        "acceptance command or check. A dependent node explicitly reads upstream "
        "artifacts.\n"
        "- Preserve bounded completion metadata separately from prose: one decisive "
        "acceptance check, explicit non-goals, safe project-relative context "
        "references, and whether independent review is required. Do not mark "
        "ordinary work as stage-closing.\n"
        "- Nodes execute directly. Do not assign planning/spec/brief creation unless "
        "that document is itself the requested deliverable. Do not initialize Git, "
        "create worktrees/branches, commit, spawn subagents, or invoke meta-workflow "
        "playbooks.\n"
        "- Use unique key values and same-batch prerequisite keys in deps. The graph "
        "must be acyclic.\n"
        "- Preserve the operator's acceptance requirements across the DAG; do not add "
        "unrelated research or ceremony.\n"
        "- For measurable optimization, rank nodes by credible movement toward the "
        "operator target, not by novelty or secondary speed. Public task-specific "
        "papers/discussions/source are allowed when operator policy allows them; "
        "only imported answers, labels, or predictions remain forbidden.\n"
        "- Return plain key-value text, not JSON. Start with `PLAN_REASON=...`, "
        "then emit one task block per node using `TASK_KEY=...`, "
        "`TASK_DEPS=comma,separated,keys` (empty when none), `TASK_TITLE=...`, "
        "`TASK_OBJECTIVE=...`, `TASK_IMPACT_SCORE=1..5`, "
        "`TASK_IMPACT_AREA=...`, `TASK_EVIDENCE=...`, "
        "`TASK_ACCEPTANCE_CHECK=...`, "
        "`TASK_NON_GOALS=item|item`, "
        "`TASK_CONTEXT_REFS=kind::project/relative/path::why|...`, "
        "`TASK_SCOPE=bounded`, `TASK_STAGE_CLOSING=true|false`, "
        "`TASK_REQUIRE_INDEPENDENT_REVIEW=true|false`, and "
        "`TASK_SKIP_STAGE_TRANSITION=true|false`. All four control fields are "
        "mandatory for every task. For review-only bounded work whose verdict "
        "must not invoke the formal lifecycle stage writer, set "
        "require-independent-review true, skip-stage-transition true, and "
        "stage-closing false. Never suppress a stage-closing task.\n\n"
        "Manager execution handoff:\n" + objective.strip()
    )


def build_continuous_prompt(
    *,
    continuous_objective: str,
    journal_tail: str,
    planning_cycle: int,
    runtime_change_summary: str = "",
    mission: Any | None = None,
    open_ended: bool = False,
) -> str:
    """Build the continuous Planner prompt from the unified role catalog."""
    from ...core.project import resolve_project_root
    from ...core.research_contract import resolve_research_target_level
    from ...skills.ground_truth import ground_truth_mandate
    from ...skills.vertical_select import resolve_evidence_mode
    from ...verticals.research.stages import CANONICAL_STAGE_ORDER
    from .registry import resolve_role_prompt

    cycle_line = f"This is planning cycle #{planning_cycle + 1}."
    _proot = resolve_project_root()
    prompt_context = resolve_role_prompt(continuous_request(_proot))
    stage = prompt_context.stage
    stage_checklist = prompt_context.stage_checklist
    stage_idx = CANONICAL_STAGE_ORDER.index(stage) if stage in CANONICAL_STAGE_ORDER else 0
    earlier_stages = ", ".join(CANONICAL_STAGE_ORDER[:stage_idx]) or "(none)"

    # Vertical-native prompt framing: resolve the active vertical and let it
    # supply the top-of-prompt role banner. The paper-pipeline framing below
    # (research gate, parallel paper-drafting, upstream rollback) applies
    # ONLY to a paper vertical (completion_gate == "full_paper"); for any
    # other vertical (e.g. speedrun) those blocks are suppressed and the
    # vertical's banner is prepended so the planner runs that vertical's loop
    # instead of demanding/rebuilding a research gate.
    _full_paper = prompt_context.full_paper
    optimize_banner = prompt_context.role_banner

    research_target_block = ""
    _research_target_level = resolve_research_target_level(_proot)
    if _research_target_level is not None:
        research_target_block = (
            "## Manager-owned research target\n"
            f"Preserve `research_target_level={_research_target_level}` from "
            "`research/PIPELINE_STATE.json`. At `publishable` or `doctoral`, "
            "`PROJECT_DONE=true` requires Reviewer-certified correctness, verified "
            "novelty, and an original result at that significance level. Known "
            "results, finite checks, or honest negative reports remain useful "
            "progress but are not completion. At `exploratory`, an independently "
            "verified negative report may satisfy the objective."
        )

    standing_research_block = ""
    if open_ended and _full_paper:
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

    # General stage gate (ALL verticals). The planner receives the current
    # stage and its checklist; this block makes the ordering rule concrete
    # and unconditional so the objective-driven optimization pull cannot
    # make it queue downstream work while the CURRENT stage's gate is still
    # open. Phrased only in terms of "the current stage and its checklist";
    # the stage names come from the active vertical, so it is not tied to
    # any one pipeline (paper or speedrun).
    _vstage_order = list(prompt_context.stage_order)
    try:
        _gate_idx = _vstage_order.index(stage)
    except ValueError:
        _gate_idx = 0
    _gate_earlier = ", ".join(_vstage_order[:_gate_idx]) or "(none)"
    _gate_downstream = ", ".join(_vstage_order[_gate_idx + 1 :]) or "(none)"
    stage_gate_block = (
        "## Stage gate — finish the CURRENT stage before anything downstream\n"
        f"`current_stage` (from research/PIPELINE_STATE.json) is `{stage}`.\n"
        f"Pipeline stage order for this vertical: {', '.join(_vstage_order)}.\n"
        f"Earlier stages already passed: {_gate_earlier}.\n"
        f"Downstream stages (LOCKED until the Manager advances the stage): "
        f"{_gate_downstream}.\n"
        "Advance stages STRICTLY IN ORDER. Until the checklist above is "
        "satisfied, downstream work is FORBIDDEN; perform only current-stage work. "
        "Manager owns stage transitions; Planner and Engineer never edit "
        "`research/PIPELINE_STATE.json`. If the stage itself blocks a necessary "
        "prerequisite, explain that in `reason` instead of silently working ahead. A paper "
        "overlap exception exists only when an explicit block below enables it."
    )

    # Parallel paper-drafting track: while a long experiment grinds in the
    # background during `run`/`analysis`, drafting manuscript prose is not
    # gated behind run/analysis (the draft/review/submission evidence gates
    # only fire once current_stage advances). Surface an explicit permission
    # block + the draft-stage checklist so the planner can keep the loop
    # productive instead of babysitting the run. Prose-only, never advances
    # the stage pointer; final-number integrity is preserved via placeholders.
    parallel_drafting_block = ""
    if stage in ("run", "analysis") and _full_paper:
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
    if not _full_paper:
        # non-paper verticals have no upstream paper stages to roll back into.
        upstream_rollback_block = ""

    # Planner role mission matcher (same primitive engineer/reviewer use).
    # No builtin_skills/planner/ OWN pool exists today, but the matcher pool
    # UNIONs the planner's cross-read references {engineer, reviewer}, so
    # mission.match() DOES make a real matcher backend call each round and
    # can surface engineer/reviewer skills to the planner as references.
    matched_planner_skill_block = ""
    if mission is not None:
        planner_match = mission.match(continuous_objective)
        if planner_match.block:
            matched_planner_skill_block = (
                "Matched planner skill(s) for this objective "
                "(read first; apply the relevant one(s)):\n"
                f"{planner_match.block}\n\n"
            )

    # ------------------------------------------------------------------
    # Shared declarative knowledge. Planner may maintain pages directly; task
    # history stays in events/handoffs and is intentionally not duplicated here.
    # ------------------------------------------------------------------
    wiki_block = ""
    autors_root = _proot / ".autors"
    wiki_candidates = sorted(autors_root.glob("*/wiki")) if autors_root.exists() else []
    wiki_candidates = [
        w
        for w in wiki_candidates
        if (w / "query_pack.md").exists()
    ]
    if wiki_candidates:
        parts: list[str] = ["## Shared project knowledge wiki (direct read/write)\n"]
        for wiki_root in wiki_candidates:
            project_name = wiki_root.parent.name
            parts.append(f"### project: {project_name}\n")
            pack = (wiki_root / "query_pack.md").read_text(encoding="utf-8")
            parts.append("#### query_pack.md\n")
            parts.append(pack.strip() + "\n\n")
            # Derived indexes surface current declarative knowledge without
            # copying full pages into every planning prompt.
            for name in ("by-status.md", "stale-watchlist.md", "open-contradictions.md"):
                qf = wiki_root / "queries" / name
                if qf.exists():
                    parts.append(f"#### queries/{name}\n")
                    parts.append(qf.read_text(encoding="utf-8").strip() + "\n\n")
        parts.append(
            "The wiki stores declarative concepts, structures, mechanisms, "
            "principles, facts, hypotheses, relationships, and contradictions. "
            "Skills store procedures; events and CHECKPOINT.md store execution "
            "history and current state. You may directly create or refine wiki "
            "pages when planning establishes durable knowledge. Cite real sources "
            "or artifacts and preserve uncertainty; never copy mission summaries "
            "or handoffs into the wiki.\n"
        )
        # M0.3: suggest a wiki_collect mission when cooldown has elapsed.
        # This is a suggestion in the planner prompt, not a harness-enforced
        # action; the planner still decides.
        from datetime import datetime, timezone

        from ...wiki.bot_state import (
            collect_backoff_hours,
            collect_cooldown_elapsed,
            load_bot_state,
        )

        for wiki_root in wiki_candidates:
            bot_state_path = wiki_root / "data" / "bot_state.json"
            state = load_bot_state(bot_state_path)
            if collect_cooldown_elapsed(state=state, now=datetime.now(timezone.utc)):
                collect_cooldown_hours = collect_backoff_hours(state)
                parts.append(
                    f"### wiki_collect suggestion ({wiki_root.parent.name})\n"
                    f"The wiki's collector cooldown of {collect_cooldown_hours:.0f}h "
                    f"has elapsed since the last collect "
                    f"(last_collected_at={state.last_collected_at}). "
                    "When relevant, delegate one small train-free wiki "
                    "collection pass using the `wiki-collector` guidance; it "
                    "derives 5-10 queries from project state and ingests new "
                    "arxiv / github hits into sources/*. Any role may synthesize "
                    "durable declarative knowledge from those sources; Reviewer "
                    "reconciles it against evidence during review.\n"
                )
        wiki_block = "".join(parts)

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
        load_contract_for_cwd(_proot),
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
    if _full_paper:
        planner_hygiene_block += (
            " For paper infrastructure, trust the fresh model-backed "
            "`paper/PAPER_INFRASTRUCTURE_REVIEW.json`; if missing or stale, "
            "run its generator rather than using an ad hoc keyword scan."
        )

    # Compile from structured state only: vertical/stage, target contract,
    # open-ended mode, available wiki, and matched skills. Do not keyword-route
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
        host_policy_block,
        objective_contract_block,
        external_target_block,
        stage_checklist,
        stage_gate_block,
        matched_planner_skill_block,
        upstream_rollback_block,
        parallel_drafting_block,
        wiki_block,
        search_altitude_block,
        "## Original operator request (immutable anchor)\n" + continuous_objective.strip(),
        "## Journal of completed work (most recent last)\n"
        + (journal_tail.strip() or "(no completed work yet — this is the first cycle)"),
        "## Current reality (authoritative over the journal above)\n"
        + (runtime_change_summary.strip() or "(no additional runtime context)"),
        planner_hygiene_block,
        cycle_line,
        "Inspect the project now, delegate the next concrete work or report a real "
        "blocker, then finish with the key-value completion footer.",
    )


__all__ = [
    "BOUNDED_DAG",
    "CONTINUOUS",
    "OPERATIONS",
    "PARALLEL_DRAFT",
    "PLAN_PREVIEW",
    "build_bounded_dag_prompt",
    "build_continuous_prompt",
    "continuous_request",
    "preview_request",
]
