"""Planner agent — emits the next batch of backlog items each planning cycle.

Per planning cycle, the planner inspects the project (read files, run
`pytest -q`, etc.), then returns a :class:`PlannerVerdict` containing
either ``project_done=True`` (with ``new_tasks=[]``) or a list of
:class:`TaskSpec` describing the next missions for the engineer + reviewer
pair to work through.

This module used to also house a "critic" sub-agent that judged whether
a `done` mission was worth one more polishing round; that layer has been
removed entirely — the L2 reviewer subsumed its responsibility.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from ..core.models import RunnerOptions
from ..core.ports import RunnerBackend
from ..core.run_gateway import run_exec as gateway_run_exec
from ..skills.role_context import format_role_context

MIN_PLANNER_IMPACT_SCORE = 4
_DEFAULT_PLANNER_TIMEOUT_SECONDS = 300
TASK_SCOPE_BOUNDED = "bounded"
TASK_SCOPE_FINAL_SUBMISSION = "final_submission"
_TASK_SCOPES = {TASK_SCOPE_BOUNDED, TASK_SCOPE_FINAL_SUBMISSION}
PLANNER_SCHEMA_PATH = str(Path(__file__).with_name("planner_schema.json"))


@dataclass
class PlannerConfig:
    """Knobs the supervisor passes down to a Planner.plan_next() call."""

    model: str | None = None
    reasoning_effort: str | None = "xhigh"
    working_dir: str | None = None
    extra_args: list[str] = field(default_factory=list)
    skip_git_repo_check: bool = True
    full_auto: bool = False
    dangerous_yolo: bool = False


@dataclass(frozen=True)
class TaskSpec:
    """One concrete task the planner wants the engineering team to tackle next."""

    title: str
    objective: str  # full actionable description for the engineer
    impact_score: int = 0  # 0-5; parser accepts only high-value work
    impact_area: str = ""
    evidence: str = ""
    scope: str = TASK_SCOPE_BOUNDED
    # --- DAG fields (optional; flat tasks leave both at their defaults) ----
    # ``key`` is this task's *local* reference name, unique within one batch
    # of ``new_tasks``. Sibling tasks point at it via ``deps``. The supervisor
    # maps these local keys to the real backlog item ids when it enqueues the
    # batch (the keys themselves never reach the backlog). Empty ``key`` /
    # empty ``deps`` (the default) ⇒ a plain flat task, scheduled exactly as
    # before the DAG existed.
    key: str = ""
    deps: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PlannerVerdict:
    """Result of a planner evaluation — new work or project done."""

    project_done: bool
    reason: str
    new_tasks: list[TaskSpec] = field(default_factory=list)
    restart_daemon: bool = False
    restart_reason: str = ""
    raw_text: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    premium_requests: float = 0.0
    error: str = ""
    # ``waiting`` is a first-class, intentional idle outcome: the project is
    # correctly blocked on a live, nonterminal external long-running job (e.g.
    # a training run) and there is no genuinely new high-impact work to queue.
    # It is NOT an error and NOT make-work — the host backs off and re-checks
    # later. ``project_done`` stays False; ``new_tasks`` stays empty.
    waiting: bool = False
    waiting_reason: str = ""
    # The Planner OWNS the per-stage checklist. ``checklist_ops`` carries the
    # add/modify/remove/seed edits it authored this cycle; ``plan_next`` applies
    # them to the per-project checklist store after the verdict is parsed. Empty
    # for a cycle that did not touch the checklist (back-compat default).
    checklist_ops: list[dict] = field(default_factory=list)


def _planner_timeout_seconds(env_name: str) -> int:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return _DEFAULT_PLANNER_TIMEOUT_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_PLANNER_TIMEOUT_SECONDS


def _planner_wall_clock_interrupt_provider():
    limit_seconds = _planner_timeout_seconds("ARGUS_SKILL_PLANNER_MAX_SECONDS")
    if limit_seconds <= 0:
        return None
    deadline = time.monotonic() + float(limit_seconds)

    def _interrupt_reason() -> str | None:
        if time.monotonic() < deadline:
            return None
        return (
            "planner wall-clock timeout: exceeded "
            f"{limit_seconds}s; queue engineer work instead of continuing "
            "planner inspection"
        )

    return _interrupt_reason


class Planner:
    """Project-level planner.

    Per planning cycle: inspect project state and emit the next batch of
    backlog items (or declare project done).

    The historical Critic iteration layer was removed; the supervisor now
    relies on the L2 reviewer for verdicts and the planner for scheduling.
    """

    def __init__(self, runner: RunnerBackend, *, skill_store: Any | None = None) -> None:
        self.runner = runner
        # Optional role-mission skill matcher (same scaffold engineer and
        # reviewer use). There is no builtin_skills/planner/ OWN pool today, but
        # the matcher pool also UNIONs the planner's cross-read references
        # {engineer, reviewer} (non-empty), so this DOES fire a real matcher call
        # each planner round, surfacing engineer/reviewer skills to the planner
        # as read-only references — it is not a no-op.
        self.skill_store = skill_store
        from ..skills.missions import PlannerMission
        self.mission = PlannerMission(skill_store)

    # ------------------------------------------------------------------
    # Planner role — project-level planning
    # ------------------------------------------------------------------

    def plan_next(
        self,
        *,
        continuous_objective: str,
        journal_tail: str = "",
        planning_cycle: int = 0,
        runtime_change_summary: str = "",
        config: PlannerConfig | None = None,
    ) -> PlannerVerdict:
        """Inspect the project and generate the next batch of tasks.

        Called when the backlog is empty and continuous mode is active.
        The runner has shell access, so the planner can inspect code,
        run tests, read docs, etc. before deciding what to work on next.
        """
        cfg = config or PlannerConfig()
        # Meta-control: detect saturation and (if frozen past threshold) convene
        # a regime-jump turn. Computed here so the SAME decision drives both the
        # injected prompt block and the post-run ledger record. Fail-soft: any
        # error → no meta intervention, planner runs exactly as before.
        _meta_proot = None
        flow = None
        try:
            from ..regime_jump import flow_controller as _flow_controller
            from ..skills.harness_overlay import resolve_project_root as _rpr
            from ..skills.vertical_select import resolve_vertical as _rv
            from ..verticals._base import load_vertical as _lv

            _meta_proot = _rpr()
            flow = _flow_controller.decide(_meta_proot, _lv(_rv(_meta_proot)))
        except Exception:  # noqa: BLE001 — meta must never break planning
            flow = None
        prompt = self._build_planner_prompt(
            continuous_objective=continuous_objective,
            journal_tail=journal_tail,
            planning_cycle=planning_cycle,
            runtime_change_summary=runtime_change_summary,
            mission=self.mission,
            meta_block=(flow.prompt_block if flow is not None else ""),
        )
        try:
            result = gateway_run_exec(
                self.runner,
                prompt=prompt,
                resume_thread_id=None,
                options=RunnerOptions(
                    model=cfg.model,
                    reasoning_effort=cfg.reasoning_effort or "xhigh",
                    output_schema_path=PLANNER_SCHEMA_PATH,
                    working_dir=cfg.working_dir,
                    dangerous_yolo=cfg.dangerous_yolo,
                    full_auto=cfg.full_auto,
                    skip_git_repo_check=cfg.skip_git_repo_check,
                    extra_args=list(cfg.extra_args) if cfg.extra_args else None,
                    external_interrupt_reason_provider=(
                        _planner_wall_clock_interrupt_provider()
                    ),
                    watchdog_hard_idle_seconds=_planner_timeout_seconds(
                        "ARGUS_SKILL_PLANNER_HARD_IDLE_SECONDS"
                    ),
                ),
                run_label=f"planner.cycle{planning_cycle}",
            )
        except Exception as exc:  # noqa: BLE001
            exc_text = f"{type(exc).__name__}: {exc}"
            return PlannerVerdict(
                project_done=False,
                reason="planner backend raised; will retry later",
                new_tasks=[],
                raw_text=exc_text,
                error=exc_text,
            )
        input_tokens = int(getattr(result, "input_tokens", 0) or 0)
        cached_input_tokens = int(getattr(result, "cached_input_tokens", 0) or 0)
        output_tokens = int(getattr(result, "output_tokens", 0) or 0)
        reasoning_output_tokens = int(
            getattr(result, "reasoning_output_tokens", 0) or 0
        )
        premium_requests = float(
            getattr(result, "premium_requests", 0.0) or 0.0
        )
        text = "\n".join(getattr(result, "agent_messages", None) or [])
        if not text and int(getattr(result, "exit_code", 0) or 0) != 0:
            stderr_tail = "\n".join(
                str(line) for line in (getattr(result, "stderr_lines", None) or [])[-20:]
            )
            fatal = str(getattr(result, "fatal_error", "") or "").strip()
            details = "\n".join(part for part in (fatal, stderr_tail) if part).strip()
            return PlannerVerdict(
                project_done=False,
                reason="planner backend failed before producing output; will retry later",
                new_tasks=[],
                raw_text=details,
                error=f"planner backend exit {getattr(result, 'exit_code', 'unknown')}",
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                reasoning_output_tokens=reasoning_output_tokens,
                premium_requests=premium_requests,
            )
        parsed = parse_planner_text(text)
        # The Planner OWNS the per-stage checklist: apply any authored ops to the
        # per-project store AFTER the verdict is parsed (so the NEXT cycle / the
        # next reviewer round sees them; never mid-round). Fail-soft: any error
        # leaves the store untouched and planning continues.
        if parsed.checklist_ops:
            try:
                from ..skills.checklist_store import apply_checklist_ops
                from ..skills.harness_overlay import resolve_project_root

                summary = apply_checklist_ops(resolve_project_root(), parsed.checklist_ops)
                log.debug("planner applied checklist_ops: %s", summary)
            except Exception:  # noqa: BLE001 — checklist write must never break planning
                log.debug("planner checklist_ops application failed", exc_info=True)
        # Meta-control: persist the agent's own meta_decision — merge any
        # AGENT-declared forbidden directions into the never-cleared ledger and
        # append the decision-log row. The harness never invents a forbidden
        # direction; it only records and later re-injects the ones the planner
        # itself declared dead. Fail-soft.
        if flow is not None and _meta_proot is not None:
            try:
                from ..regime_jump import flow_controller as _flow_controller

                # Pull the agent's meta_decision straight from its structured
                # output (planner_schema now carries an optional meta_decision
                # field) so JUDGE no longer depends on scraping prose.
                _meta_obj = None
                _found = _load_json_object_with_schema(
                    text, required_keys=("project_done", "reason", "new_tasks")
                )
                if _found is not None and isinstance(_found[0].get("meta_decision"), dict):
                    _meta_obj = _found[0]["meta_decision"]
                _flow_controller.record_decision(
                    _meta_proot, text, flow, meta_obj=_meta_obj
                )
            except Exception:  # noqa: BLE001 — recording must never break planning
                pass
        return replace(
            parsed,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_output_tokens=reasoning_output_tokens,
            premium_requests=premium_requests,
        )

    @staticmethod
    def _missing_query_pack_diagnosis_refs(project_root: Path, query_pack_text: str) -> list[str]:
        refs = sorted(
            {
                match.group(0).rstrip("`),.;:")
                for match in re.finditer(
                    r"diagnosis/[A-Za-z0-9_./-]+\.(?:json|md)",
                    str(query_pack_text),
                )
            }
        )
        return [ref for ref in refs if not (project_root / ref).exists()]

    @staticmethod
    def _build_planner_prompt(
        *,
        continuous_objective: str,
        journal_tail: str,
        planning_cycle: int,
        runtime_change_summary: str = "",
        mission: Any | None = None,
        meta_block: str = "",
    ) -> str:
        cycle_line = f"This is planning cycle #{planning_cycle + 1}."
        from ..skills.harness_overlay import resolve_project_root
        from ..skills.stage_checklists import (
            CANONICAL_STAGE_ORDER,
            current_stage,
            format_stage_checklist,
        )
        from ..skills.vertical_select import resolve_vertical
        from ..verticals._base import (
            load_vertical,
            vertical_checklist_stage_order,
            vertical_completion_gate,
            vertical_role_banner,
            vertical_search_altitude,
            vertical_workflow_mode,
        )

        _proot = resolve_project_root()
        stage = current_stage(_proot)
        stage_checklist = format_stage_checklist(stage, role="planner", project_root=_proot)
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
        _vmod = load_vertical(resolve_vertical(_proot), project_root=_proot)
        _full_paper = vertical_completion_gate(_vmod) == "full_paper"
        optimize_banner = vertical_role_banner(_vmod, "planner")

        # Live search-altitude facts (NO verdict) so the planner can SEE the
        # floor / distance-to-target / how long it has been frozen / what it has
        # already recombined, instead of re-deriving it from attempts/ each
        # cycle. Empty for verticals that do not surface it.
        search_altitude_block = vertical_search_altitude(_vmod, _proot)

        # General stage gate (ALL verticals). The planner receives the current
        # stage and its checklist; this block makes the ordering rule concrete
        # and unconditional so the objective-driven optimization pull cannot
        # make it queue downstream work while the CURRENT stage's gate is still
        # open. Phrased only in terms of "the current stage and its checklist";
        # the stage names come from the active vertical, so it is not tied to
        # any one pipeline (paper or speedrun).
        _vstage_order = list(vertical_checklist_stage_order(_vmod))
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
            draft_checklist = format_stage_checklist(
                "draft", role="planner", project_root=_proot
            )
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
                "4. Ground before/with drafting: if exemplar/style grounding "
                "artifacts do not exist yet, the mission's first step is to study "
                "the exemplars and write the blueprint (the `exemplar_grounding` "
                "gate is structural at draft — pre-empting it avoids a later "
                "rewrite). This is not a results restriction.\n"
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
            "   If both `research.infra_shortlist` and `plan.infra_choice`\n"
            "   are missing, the rollback target is `research`, not `plan` —\n"
            "   the engineer cannot lock a choice without a shortlist.\n"
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
                missing_refs = Planner._missing_query_pack_diagnosis_refs(_proot, pack)
                if missing_refs:
                    parts.append("#### missing diagnosis refs from query_pack.md\n")
                    for ref in missing_refs:
                        parts.append(f"- `{ref}`\n")
                    parts.append(
                        "Repair by restoring the referenced diagnosis file or updating "
                        "query_pack.md to a current path before routing missions that "
                        "depend on those instructions.\n\n"
                    )
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

            from ..wiki.bot_state import (
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

        from ..skills.ground_truth import ground_truth_mandate

        host_policy_block = (
            "## Dynamic host policy\n"
            f"- Every task must have `impact_score >= {MIN_PLANNER_IMPACT_SCORE}`; "
            "the host rejects lower-impact tasks.\n"
            "- The final output must match the provided planner schema and be JSON "
            "only, with no prose or Markdown fence.\n\n"
        )

        return (
            ground_truth_mandate(
                "planner",
                workflow_mode=vertical_workflow_mode(_vmod),
            )
            + optimize_banner
            + format_role_context(
                "Argus planner role skill",
                "argus-planner-role.md",
            )
            + host_policy_block
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
            # Meta-control layer (saturation → enforced regime-jump). When the
            # floor has been frozen past the threshold, this block CONVENES a
            # jump turn: the never-cleared forbidden ledger + coverage + strategy
            # pool + a context reset, escalating the NO-VERDICT altitude facts
            # above into a binding "propose a regime jump" framing. Empty
            # (exploit) when not saturated, so the normal path is unchanged.
            + meta_block
            + "\n\nOriginal operator request (immutable anchor):\n"
            + continuous_objective.strip()
            + "\n\nOperator's continuous goal (do not mutate the anchor above):\n"
            + continuous_objective.strip()
            + "\n\nJournal of completed work (most recent last):\n"
            + (journal_tail.strip() or "(no completed work yet — this is the first cycle)")
            + "\n\nCurrent reality (authoritative over the journal above):\n"
            + (
                runtime_change_summary.strip()
                or "No runtime source changes have been detected since daemon start; set restart_daemon=false."
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


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _iter_json_objects(text: str):
    """Yield balanced top-level JSON object substrings from ``text``."""
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    for idx, ch in enumerate(text):
        if start is None:
            if ch == "{":
                start = idx
                depth = 1
                in_string = False
                escaped = False
            continue
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                yield text[start:idx + 1]
                start = None


def _load_json_object_with_schema(
    text: str,
    *,
    required_keys: tuple[str, ...],
) -> tuple[dict, str] | None:
    latest: tuple[dict, str] | None = None
    for blob in _iter_json_objects(text):
        try:
            data = json.loads(blob)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if all(key in data for key in required_keys):
            latest = (data, blob)
    return latest


def _parse_json_bool(value: object, default: bool) -> bool:
    """Coerce JSON-ish boolean payloads from model output.

    The parser is intentionally tolerant of quoted booleans because LLM
    output often serializes them as strings.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    if value is None:
        return default
    return bool(value)


def _parse_impact_score(value: object) -> int:
    """Coerce model-provided impact scores into the bounded 0-5 scale."""
    try:
        if isinstance(value, int | float):
            score = int(value)
        elif isinstance(value, str):
            value = value.strip()
            if not value:
                return 0
            score = int(float(value))  # tolerate "4" and "4.0"
        else:
            return 0
    except (TypeError, ValueError):
        return 0
    return max(0, min(5, score))


def _parse_task_scope(value: object) -> str:
    scope = str(value or TASK_SCOPE_BOUNDED).strip().lower().replace("-", "_")
    if scope not in _TASK_SCOPES:
        return TASK_SCOPE_BOUNDED
    return scope



_VALID_CHECKLIST_OPS = frozenset({"seed", "add", "modify", "remove"})


def _parse_checklist_ops(data: dict) -> list[dict]:
    """Parse the Planner's per-stage ``checklist_ops`` (fail-soft, capped).

    Drops malformed entries and any unknown op; a non-list value yields ``[]`` so
    the loop applies nothing. ``apply_checklist_ops`` enforces the protected-floor
    policy and bounds — this only normalizes shape."""
    raw = data.get("checklist_ops")
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for entry in raw[:30]:
        if not isinstance(entry, dict):
            continue
        op = str(entry.get("op", "")).strip().lower()
        stage = str(entry.get("stage", "")).strip().lower()
        if op not in _VALID_CHECKLIST_OPS or not stage:
            continue
        item: dict[str, str] = {"op": op, "stage": stage, "id": str(entry.get("id", "")).strip()}
        if "statement" in entry:
            item["statement"] = str(entry.get("statement") or "").strip()
        if "evidence_hint" in entry:
            item["evidence_hint"] = str(entry.get("evidence_hint") or "").strip()
        out.append(item)
    return out


def parse_planner_text(text: str) -> PlannerVerdict:
    """Parse a planner JSON verdict out of an agent message.

    Malformed or inconsistent output returns a retryable error verdict.
    """
    if not text:
        return PlannerVerdict(
            project_done=False,
            reason="planner returned empty output; will retry later",
            raw_text=text,
            error="empty planner output",
        )
    found = _load_json_object_with_schema(
        text,
        required_keys=("project_done", "reason", "new_tasks"),
    )
    if found is None:
        return PlannerVerdict(
            project_done=False,
            reason="planner returned unparseable output; will retry later",
            raw_text=text,
            error="unparseable planner output",
        )
    data, blob = found
    checklist_ops = _parse_checklist_ops(data)
    project_done = _parse_json_bool(data.get("project_done", True), True)
    reason = str(data.get("reason", ""))
    restart_daemon = _parse_json_bool(data.get("restart_daemon", False), False)
    restart_reason = str(data.get("restart_reason", "")).strip()
    if restart_daemon and not restart_reason:
        restart_reason = reason or "planner requested daemon restart"
    waiting = _parse_json_bool(data.get("waiting", False), False)
    waiting_reason = str(data.get("waiting_reason", "")).strip() or reason
    tasks_raw = data.get("new_tasks") or []
    new_tasks: list[TaskSpec] = []
    raw_task_count = len(tasks_raw) if isinstance(tasks_raw, list) else 0
    if isinstance(tasks_raw, list):
        for entry in tasks_raw:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title", "")).strip()
            objective = str(entry.get("objective", "")).strip()
            impact_score = _parse_impact_score(entry.get("impact_score"))
            impact_area = str(entry.get("impact_area", "")).strip()
            evidence = str(entry.get("evidence", "")).strip()
            scope = _parse_task_scope(entry.get("scope"))
            # Optional DAG fields; back-compat: a flat task simply omits them.
            key = str(entry.get("key") or "").strip()
            deps = [
                str(d).strip()
                for d in (entry.get("deps") or [])
                if str(d).strip()
            ]
            if (
                not title
                or not objective
                or impact_score < MIN_PLANNER_IMPACT_SCORE
                or not evidence
            ):
                continue
            new_tasks.append(
                TaskSpec(
                    title=title,
                    objective=objective,
                    impact_score=impact_score,
                    impact_area=impact_area,
                    evidence=evidence,
                    scope=scope,
                    key=key,
                    deps=deps,
                )
            )
            if len(new_tasks) >= 6:
                break
    if project_done and tasks_raw:
        return PlannerVerdict(
            project_done=False,
            reason="planner said project_done=true but returned tasks",
            new_tasks=[],
            raw_text=blob,
            error="planner claimed project_done=true with tasks",
        )
    # Explicit, intentional idle: the project is correctly waiting on a live
    # external job and the planner found no genuinely new high-impact work.
    # Honored ONLY when not also claiming done/restart and no concrete tasks
    # were accepted — real tasks always win over waiting. This is NOT an error
    # (it bypasses the "no concrete tasks" retry/churn path below).
    if waiting and not project_done and not restart_daemon and not new_tasks:
        if not waiting_reason:
            waiting_reason = "awaiting a live external job; no new high-impact work"
        return PlannerVerdict(
            project_done=False,
            reason=waiting_reason,
            new_tasks=[],
            raw_text=blob,
            waiting=True,
            waiting_reason=waiting_reason,
            checklist_ops=checklist_ops,
        )
    if not project_done and not new_tasks and not restart_daemon:
        # Inconsistent: not done but no tasks → retry later, don't mark done.
        if raw_task_count:
            reason = "planner proposed only low-impact or unevidenced tasks"
            error = "planner produced no high-impact tasks"
        else:
            error = "planner said not done but produced no concrete tasks"
        if not reason:
            reason = "planner said not done but produced no concrete tasks"
        return PlannerVerdict(
            project_done=False,
            reason=reason,
            new_tasks=[],
            raw_text=blob,
            error=error,
            checklist_ops=checklist_ops,
        )
    return PlannerVerdict(
        project_done=project_done,
        reason=reason,
        new_tasks=new_tasks,
        restart_daemon=restart_daemon,
        restart_reason=restart_reason,
        raw_text=blob,
        cached_input_tokens=0,
        checklist_ops=checklist_ops,
    )
