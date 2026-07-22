"""Planner prompt operations and structured context requests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .types import ChecklistMode, RoleName, RolePromptRequest

CONTINUOUS = "continuous"
BOUNDED_DAG = "bounded_dag"
PLAN_PREVIEW = "plan_preview"
SCHEMA_REPAIR = "schema_repair"
PARALLEL_DRAFT = "parallel_draft"

OPERATIONS = frozenset(
    {
        CONTINUOUS,
        BOUNDED_DAG,
        PLAN_PREVIEW,
        SCHEMA_REPAIR,
        PARALLEL_DRAFT,
    }
)


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


def build_schema_repair_prompt(original_sha256: str) -> str:
    return (
        "Your previous Planner response could not be parsed as the required "
        "JSON object. Re-emit the exact same decision once, conforming to "
        "the provided output schema. Do not inspect files, call tools, add "
        "or remove tasks, change waiting state, or revise any scientific or "
        "planning judgment. Return only the repaired structured response. "
        f"Original response SHA-256: {original_sha256}"
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
        "- Nodes execute directly. Do not assign planning/spec/brief creation unless "
        "that document is itself the requested deliverable. Do not initialize Git, "
        "create worktrees/branches, commit, spawn subagents, or invoke meta-workflow "
        "playbooks.\n"
        "- Use unique key values and same-batch prerequisite keys in deps. The graph "
        "must be acyclic.\n"
        "- Preserve the operator's acceptance requirements across the DAG; do not add "
        "unrelated research or ceremony.\n"
        "- Return JSON only matching the supplied schema.\n\n"
        "Manager execution handoff:\n"
        + objective.strip()
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
    from ...core.research_contract import resolve_research_target_level
    from ...skills.ground_truth import ground_truth_mandate
    from ...skills.harness_overlay import resolve_project_root
    from ...skills.role_context import format_role_context
    from ...skills.vertical_select import resolve_evidence_mode
    from ...verticals.research.stages import CANONICAL_STAGE_ORDER
    from .registry import resolve_role_prompt

    cycle_line = f"This is planning cycle #{planning_cycle + 1}."
    _proot = resolve_project_root()
    prompt_context = resolve_role_prompt(continuous_request(_proot))
    stage = prompt_context.stage
    stage_checklist = prompt_context.stage_checklist
    stage_idx = (
        CANONICAL_STAGE_ORDER.index(stage)
        if stage in CANONICAL_STAGE_ORDER
        else 0
    )
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
            f"`research_target_level` is `{_research_target_level}` in "
            "`research/PIPELINE_STATE.json`. Every mission and completion "
            "recommendation must preserve this exact success bar. For "
            "`publishable` or `doctoral`, do not set project_done or route a "
            "final-report-only mission as completion unless the Reviewer has "
            "certified correctness_status=verified, "
            "novelty_status=verified_new, and an original result "
            "with publishable/doctoral significance. Literature review, known "
            "results, finite computation, local Lean verification, and honest "
            "failure reports remain useful artifacts but are not success. A "
            "bounded review ends only the current cycle; route a new method or "
            "leave the work resumable instead of declaring the research goal "
            "complete. For `exploratory`, an independently verified honest "
            "negative report may satisfy the goal.\n\n"
        )

    standing_research_block = ""
    if open_ended and _full_paper:
        standing_research_block = (
            "## Standing research objective\n"
            "A failed hypothesis, negative experiment, or rejected direction is "
            "project memory, not a scheduling command and not completion of the "
            "standing research goal. Read the stored result and decide for yourself "
            "what it changes: it may call for a revised explanation, a different "
            "mechanism, a stronger benchmark, a new framing, or no immediate action. "
            "The host never maps a failure label to a next task. Set "
            "`project_done=true` only after the persisted research target itself is "
            "met and independently reviewed. Do not turn internal stop decisions, "
            "checklist language, or workflow ceremony into the paper's story unless "
            "they are scientifically essential.\n\n"
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
        f"{_gate_downstream}.\n\n"
        "HARD RULE (overrides the operator objective's optimization pull): "
        "advance pipeline stages STRICTLY IN ORDER. While the CURRENT stage "
        f"(`{stage}`) checklist shown above is not fully satisfied, the ONLY "
        "mission you may queue is one whose body COMPLETES THE CURRENT STAGE "
        "— i.e. produces the artifacts that the current-stage checklist names "
        "— so the reviewer can certify this stage. Do NOT queue any "
        "downstream-stage work — including metric/recipe/throughput "
        "optimization, measurement, analysis, drafting, review, or "
        f"submission — until the Manager has advanced `current_stage` past "
        f"`{stage}` (the Manager owns stage transitions; neither you nor the "
        "engineer edits `research/PIPELINE_STATE.json`). Skipping "
        f"`{stage}`, or working ahead of it because the "
        "objective says to drive the metric down, is FORBIDDEN: the current "
        "stage's gate exists to be satisfied FIRST. (Sole carve-out: the "
        "parallel paper-drafting track below, when present — prose-only "
        "drafting that does NOT advance the stage.)\n\n"
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
            "mission touches must stay internally consistent or remain "
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
            "SHOULD queue ONE bounded paper-DRAFTING mission in parallel that "
            "writes/extends `paper/main.tex` (and section files): "
            "Introduction, Related Work, Background, Problem Definition, "
            "Method/Approach narrative, Experimental-Setup description, and "
            "Results-section SCAFFOLDING. There is no results-dependency "
            "restriction on WHICH sections may be drafted.\n\n"
            "Hard rules for a parallel drafting mission:\n"
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
            "3. Maintain a placeholder ledger: have the mission keep "
            "`paper/RESULT_PLACEHOLDERS.md` listing each placeholder, its "
            "owning source artifact, and the backfill condition, so a later "
            "analysis/draft mission can find and fill every TBD.\n"
            "4. Ground style proportionally: inspect one or two relevant venue "
            "papers when that would improve the draft, but do not create exemplar-"
            "conformance schemas or copy another paper's section sequence. The "
            "project's thesis and evidence determine the structure.\n"
            "5. Do NOT let drafting starve experiment monitoring: the mission "
            "(or the next cycle) must still do one lightweight run-health "
            "check on the live run each cycle.\n"
            f"{analysis_caveat}"
            "6. REVIEWER FRAMING — phrase the mission `objective` so the L2 "
            "reviewer judges it ONLY by the requested draft artifacts and "
            "placeholder integrity, NOT by run/analysis-stage advancement. "
            "State plainly in the objective: 'Bounded overlap paper-drafting "
            "mission while current_stage stays `" + stage + "`; the "
            "run/analysis-stage checklist and gates are BACKGROUND context "
            "only and must not be treated as acceptance for this mission "
            "unless the background run has catastrophically failed; judge "
            "completion by the paper sections written and by placeholder "
            "integrity (no fabricated numbers).'\n\n"
            "Draft-stage checklist (for shaping the drafting mission scope; "
            "do NOT mark its items done while current_stage is `" + stage
            + "`):\n"
            f"{draft_checklist}\n"
        )

    upstream_rollback_block = (
        "## Upstream defect detection and rollback\n"
        f"Current stage according to `research/PIPELINE_STATE.json`: `{stage}`.\n"
        f"Earlier stages: {earlier_stages}.\n\n"
        "While inspecting the project to decide the next mission you may "
        "discover that an *upstream* (earlier-stage) artifact is missing, "
        "stale, or unreliable. Examples:\n"
        "- you're at `run` but `research/INFRA_CHOICE.md` does not exist,\n"
        "  even though the project does training/large-scale inference;\n"
        "- you're at `analysis` but every `scored_rows.jsonl` has uniform\n"
        "  scores (the benchmark evaluator is a stub);\n"
        "- you're at `draft` but `research/RESEARCH_BRIEF.md` was never\n"
        "  filled in with a real thesis.\n\n"
        "When that happens, do NOT queue a forward-progress task that\n"
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
        "   back. Do NOT queue a mission that calls `rollback_stage` and do\n"
        "   NOT write `research/PIPELINE_STATE.json`; the Manager performs the\n"
        "   transition.\n"
        "4. **Do not queue forward-progress work that depends on the broken\n"
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
    # Idea-wiki block. Surface only when the project actually has a wiki
    # (parasitic auto-collection: no wiki means nothing has been written
    # yet, and we do not want to nag). Pure read; planner never writes.
    # ------------------------------------------------------------------
    wiki_block = ""
    autors_root = _proot / ".autors"
    wiki_candidates = (
        sorted(autors_root.glob("*/wiki")) if autors_root.exists() else []
    )
    wiki_candidates = [
        w for w in wiki_candidates if (w / "query_pack.md").exists()
    ]
    if wiki_candidates:
        parts: list[str] = ["## Idea wiki (read-only)\n"]
        for wiki_root in wiki_candidates:
            project_name = wiki_root.parent.name
            parts.append(f"### project: {project_name}\n")
            pack = (wiki_root / "query_pack.md").read_text(encoding="utf-8")
            parts.append("#### query_pack.md\n")
            parts.append(pack.strip() + "\n\n")
            # by-status surfaces the CURRENT page inventory (incl. freshly
            # learned technique pages), so knowledge distilled into the wiki
            # actually reaches the planner instead of being write-only. It is
            # regenerated by index.rebuild_indexes; a plain new page only shows
            # up here (and in by-tag), never in the static query_pack.md.
            for name in ("by-status.md", "stale-watchlist.md", "open-contradictions.md"):
                qf = wiki_root / "queries" / name
                if qf.exists():
                    parts.append(f"#### queries/{name}\n")
                    parts.append(qf.read_text(encoding="utf-8").strip() + "\n\n")
            runs_dir = wiki_root / "sources" / "runs"
            run_cards: list[tuple[str, float, Any]] = []
            if runs_dir.exists():
                from ...wiki.schema import SourceRun, parse_frontmatter

                for run_path in runs_dir.glob("*.md"):
                    try:
                        run = parse_frontmatter(
                            run_path.read_text(encoding="utf-8"),
                            SourceRun,
                        )
                        run_cards.append(
                            (
                                run.closed_at,
                                run_path.stat().st_mtime,
                                run,
                            )
                        )
                    except Exception:  # noqa: BLE001 - one bad card is isolated
                        continue
                run_cards.sort(key=lambda row: (row[0], row[1]))
                latest_by_mission = {
                    row[2].mission_id: row for row in run_cards
                }
                run_cards = sorted(
                    latest_by_mission.values(),
                    key=lambda row: (row[0], row[1]),
                )
            if run_cards:
                parts.append("#### recent reviewed runs\n")
                for _closed_at, _mtime, run in reversed(run_cards[-3:]):
                    excerpt = " ".join(run.body.split())[:500]
                    parts.append(
                        f"- `{run.mission_id}` outcome={run.outcome}; "
                        f"next={run.next_action or '(none)'}\n"
                    )
                    if excerpt:
                        parts.append(f"  {excerpt}\n")
                parts.append("\n")
        parts.append(
            "If backlog is empty, you MAY use the stale watchlist or open "
            "contradictions to seed an `idea-creator` mission. Read-only: "
            "do not write to the wiki yourself; the reviewer's "
            "`wiki-curator` skill handles all writes.\n"
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
                    "If the active backlog has space, consider enqueueing one "
                    "`wiki_collect` mission with the `wiki-collector` engineer "
                    "skill. It is a small, train-free background mission that "
                    "derives 5-10 queries from project state and ingests new "
                    "arxiv / github hits into sources/*. The reviewer's "
                    "wiki-curator handles promotion on the same mission's "
                    "reviewer pass.\n"
                )
        wiki_block = "".join(parts)

    host_policy_block = (
        "## Dynamic host policy\n"
        "- Planner owns task sizing and impact priority. The host does not reject "
        "tasks based on score, batch size, artifact count, context count, prose "
        "length, or keyword-inferred phase count.\n"
        "- A reversible project-local archive/quarantine with provenance is "
        "ordinary Engineer work, not an external operator dependency. If both "
        "archive and delete/overwrite would unblock progress, queue the safe "
        "archive; require operator approval only for the destructive option.\n"
        "- The final output must match the provided planner schema and be JSON "
        "only, with no prose or Markdown fence.\n\n"
    )

    objective_contract_block = (
        "## Immutable objective acceptance contract\n"
        "The operator's hard success criteria and explicit non-qualifying "
        "outcomes are acceptance constraints, not an optimization hint. The "
        "current-stage gate controls ordering but never lowers those criteria. "
        "Do not enqueue a mission whose acceptance can be satisfied entirely "
        "by an outcome the operator says does not count. Supporting searches, "
        "probes, computation, and literature work may be internal steps inside "
        "a qualifying mission; they are not a successful mission outcome by "
        "themselves.\n\n"
    )
    return (
        ground_truth_mandate(
            "planner",
            workflow_mode=resolve_evidence_mode(_proot),
        )
        + optimize_banner
        + research_target_block
        + standing_research_block
        + format_role_context(
            "Argus planner role skill",
            "argus-planner-role.md",
        )
        + host_policy_block
        + objective_contract_block
        + stage_checklist
        + "\n\n"
        + stage_gate_block
        + matched_planner_skill_block
        + upstream_rollback_block
        + "\n"
        + parallel_drafting_block
        + ("\n" if parallel_drafting_block else "")
        + wiki_block
        + ("\n" if wiki_block else "")
        + search_altitude_block
        + "\n\nOriginal operator request (immutable anchor):\n"
        + continuous_objective.strip()
        + "\n\nJournal of completed work (most recent last):\n"
        + (journal_tail.strip() or "(no completed work yet — this is the first cycle)")
        + "\n\nCurrent reality (authoritative over the journal above):\n"
        + (
            runtime_change_summary.strip()
            or "(no additional runtime context)"
        )
        + "\n\nPlanner hygiene:\n"
        + (
            "Do not copy stale host-specific paths from the journal into new tasks. "
            "Use the active project files, project-local argus_builtin_skills, and "
            "`python -m argus_skill ...` or the launcher-provided ARGUS_SKILL_PYTHON "
            "environment instead of retired absolute paths. For paper infrastructure "
            "leaks, do not run ad hoc grep/rg pattern scans in the Planner. Inspect "
            "only whether the model-backed paper infrastructure review artifact under "
            "`paper/PAPER_INFRASTRUCTURE_REVIEW.json` is fresh; if it is missing or "
            "stale, queue the Engineer to run `paper_infrastructure_review "
            "--review-mode model --write`. Do not use a hand-written string-match "
            "pass as context, acceptance, or a substitute for the reviewer artifact."
        )
        + "\n\n"
        + cycle_line
        + "\n\nInspect the project now and return the JSON verdict.\n"
    )


__all__ = [
    "BOUNDED_DAG",
    "CONTINUOUS",
    "OPERATIONS",
    "PARALLEL_DRAFT",
    "PLAN_PREVIEW",
    "SCHEMA_REPAIR",
    "build_bounded_dag_prompt",
    "build_continuous_prompt",
    "build_schema_repair_prompt",
    "continuous_request",
    "preview_request",
]
