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
import os
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..core.models import RunnerOptions
from ..core.ports import RunnerBackend
from ..skills.role_context import format_role_context

MIN_PLANNER_IMPACT_SCORE = 4
DEFAULT_PLANNER_HARD_IDLE_SECONDS = 300
DEFAULT_PLANNER_MAX_SECONDS = 300
TASK_SCOPE_BOUNDED = "bounded"
TASK_SCOPE_FINAL_SUBMISSION = "final_submission"
_TASK_SCOPES = {TASK_SCOPE_BOUNDED, TASK_SCOPE_FINAL_SUBMISSION}
_PLANNER_ROLE_SKILL = "argus-planner-role.md"
PLANNER_SCHEMA_PATH = str(Path(__file__).with_name("planner_schema.json"))
_PLANNER_ROLE_FALLBACK = """# Argus Planner Role

The Planner is argus-skill's manager/director. Inspect project state and queue
the next high-impact bounded missions, reserving final_submission for the
whole-project readiness gate.
"""


@dataclass
class PlannerConfig:
    """Knobs the supervisor passes down to a Planner.plan_next() call."""

    model: str | None = None
    reasoning_effort: str | None = None
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
    error: str = ""
    # ``waiting`` is a first-class, intentional idle outcome: the project is
    # correctly blocked on a live, nonterminal external long-running job (e.g.
    # a training run) and there is no genuinely new high-impact work to queue.
    # It is NOT an error and NOT make-work — the host backs off and re-checks
    # later. ``project_done`` stays False; ``new_tasks`` stays empty.
    waiting: bool = False
    waiting_reason: str = ""


_PLANNER_SYSTEM_PREAMBLE = (
    "You are the Planner agent (经理+总监) in a 7×24 supervised coding loop.\n"
    "The engineering team has completed all currently queued tasks.\n"
    "Your job: inspect the project, assess progress toward the\n"
    "operator's goal, and keep the daemon busy with the next batch of\n"
    "high-impact work. If local polish is exhausted, broaden the search\n"
    "to correctness, reliability, integration, operator UX, performance,\n"
    "security, and production-like verification before declaring done.\n\n"
    "You HAVE shell access. USE IT to:\n"
    "- Read the project structure (`find`, `ls`, `tree`)\n"
    "- Run tests (`pytest -q`), linters (`ruff check`), type checkers\n"
    "- Read key source files and documentation\n"
    "- Check for TODO/FIXME/HACK comments\n"
    "- Assess code quality and architecture\n"
    "- Decide whether the current agent architecture itself is blocking the\n"
    "  operator's goal; if so, propose a self-architecture mission that changes\n"
    "  daemon/reviewer/critic/planner/tooling code and verifies the new behavior\n"
    "- Verify end-to-end workflows work\n\n"
    "Output a JSON object with this exact shape:\n"
    "{\n"
    '  "project_done": <true|false>,\n'
    '  "reason": "<one sentence justification>",\n'
    '  "restart_daemon": <true|false>,\n'
    '  "restart_reason": "<why a fresh daemon is needed, or empty string>",\n'
    '  "waiting": <true|false>,\n'
    '  "waiting_reason": "<if waiting=true: the live external job you are '
    "waiting on and why no new work is queued; else empty string>\",\n"
    '  "new_tasks": [\n'
    "    {\n"
    '      "title": "<short imperative title>",\n'
    '      "impact_score": <0-5 integer>,\n'
    '      "impact_area": "<correctness|security|operator_ux|performance|reliability|integration|requirement_gap|discovery>",\n'
    '      "evidence": "<specific signal or hypothesis proving this is worth a mission>",\n'
    '      "scope": "<bounded|final_submission>",\n'
    '      "objective": "<detailed, actionable objective with '
    "acceptance criteria>\"\n"
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Rules:\n"
    "1) Your default job is continuous high-value discovery: keep looking\n"
    "   for useful work, not busywork. `project_done=true` is allowed ONLY\n"
    "   when:\n"
    "   - The operator's goal is FULLY satisfied, AND\n"
    "   - Tests pass, linters are clean, docs are accurate, AND\n"
    "   - You inspected the major value horizons above and cannot find a\n"
    f"     task with `impact_score >= {MIN_PLANNER_IMPACT_SCORE}`.\n"
    "   When `project_done=true`, `new_tasks` MUST be `[]`.\n"
    "2) `project_done=false` when there is ANY concrete high-impact task\n"
    "   that would move the project closer to the operator's goal. Do not\n"
    "   queue cosmetic work just to stay busy; instead search a wider\n"
    "   value horizon or queue a bounded discovery/verification task with\n"
    "   a plausible high-impact hypothesis.\n"
    "3) Each task's `objective` must be ACTIONABLE: the engineer\n"
    "   should be able to start working immediately with no\n"
    "   clarification. Include:\n"
    "   - What to change and where in the code\n"
    "   - Concrete acceptance criteria (commands to run, expected output)\n"
    "   - Any constraints or gotchas\n"
    "4) Every task MUST set `scope`:\n"
    "   - `bounded` for non-final missions. For EMNLP/ACL/paper goals,\n"
    "     bounded does NOT mean tiny: prefer one long-horizon paper optimization\n"
    "     mission that tells the Engineer to read `AGENTS.md` and built-in paper\n"
    "     skills, work the per-stage checklist, then repair all addressable\n"
    "     manuscript/evidence/layout/review/artifact blockers in the same\n"
    "     mission before stopping.\n"
    "   - `final_submission` ONLY for the single project-final readiness task\n"
    "     whose acceptance is proving the whole EMNLP/ACL submission package.\n"
    "     That objective must require the L2 reviewer to mark `done` against\n"
    "     the full pipeline checklist (research → submission) before anyone\n"
    "     may declare it done.\n"
    f"5) Every task must have `impact_score >= {MIN_PLANNER_IMPACT_SCORE}` and\n"
    "   concrete `evidence`. Lower-score work is rejected by the host.\n"
    "6) For an operator goal that asks for a full EMNLP/ACL paper or\n"
    "   submission-ready package, `project_done=true` requires journal evidence\n"
    "   that a recent `final_submission` mission was marked `done` by the L2\n"
    "   reviewer against the full pipeline checklist. If that journal entry is\n"
    "   missing or the submission-stage items still report blockers, set\n"
    "   `project_done=false` and queue one broad bounded long-horizon paper\n"
    "   optimization blocker mission by default, or a `final_submission` task\n"
    "   only when the package appears ready and just needs final proof.\n"
    "   A single-stage checklist alone is never enough.\n"
    "   For positive paper objectives, a negative-result pivot or a baseline-only\n"
    "   win is not project_done; require a structured X-Y-Z-W paper_contribution\n"
    "   claim where the proposed artifact/protocol beats the strongest nontrivial\n"
    "   baseline with statistical support.\n"
    "7) For EMNLP/ACL/paper goals, do not queue downstream analysis, paper,\n"
    "   review, or submission-package tasks while their upstream stage's\n"
    "   checklist items are still unchecked. Queue the current-stage mission\n"
    "   instead; the host will refuse premature gated downstream tasks.\n"
    "   EXCEPTION — parallel paper-drafting during `run`/`analysis`: when a\n"
    "   long-running experiment is already launched and progressing\n"
    "   independently in the background, you MAY (and should) queue a bounded\n"
    "   paper-DRAFTING mission in parallel even though `current_stage` is still\n"
    "   `run` or `analysis`. See the '## Parallel paper-drafting track' block\n"
    "   below for the exact rules. Such a drafting mission does NOT advance\n"
    "   `current_stage`, does NOT satisfy any downstream checklist/gate, and\n"
    "   must leave `research/PIPELINE_STATE.json` untouched. This exception is\n"
    "   ONLY for writing manuscript prose with placeholders — never for\n"
    "   marking a stage done or fabricating results.\n"
    "8) Keep planning lightweight. Inspect enough to route the next mission, but\n"
    "   do not run long pytest suites, full experiments, full paper compilation,\n"
    "   or broad artifact repair inside the planner. Queue that work for the\n"
    "   Engineer instead, with concrete commands and acceptance criteria. The\n"
    "   host may interrupt planner wall-clock overruns and fall back to an\n"
    "   automatic gate-derived Engineer task.\n"
    "9) Order tasks by impact: most important first.\n"
    "10) Cap at 3 tasks per planning cycle. For EMNLP/ACL/paper goals, prefer\n"
    "   1 broad task over 3 microtasks unless the blockers are truly independent.\n"
    "   Trust the Engineer model with multi-file, multi-validator objectives when\n"
    "   the acceptance criteria are concrete; do not decompose a coherent paper\n"
    "   repair into tiny tasks that can oscillate.\n"
    "11) NEVER repeat work already completed (check the journal below).\n"
    "12) NEVER propose vanity work (renames, comment polish, trivial\n"
    "   refactors) unless the operator explicitly asked for it.\n"
    "13) Each non-paper task should be independently completable in one mission\n"
    "   (not multi-step dependencies). Paper optimization tasks may be broad,\n"
    "   multi-file, and multi-validator because the Engineer is expected to run\n"
    "   long-horizon missions, not wait for Planner to decompose every paragraph.\n"
    "14) Set `restart_daemon=true` ONLY when the prompt says runtime\n"
    "   source changed AND a fresh daemon is needed for the next step —\n"
    "   for example daemon/CLI/lifecycle code changed, a large runtime\n"
    "   refactor landed, or verification requires the installed daemon\n"
    "   process to reload new code. Otherwise set it false.\n"
    "15) `restart_daemon=true` is not a substitute for useful work: if\n"
    "   new tasks are still needed after restart, include them too. If\n"
    "   restart itself is the next verification step, `new_tasks` may be []\n"
    "   with `project_done=false`.\n"
    "16) Self-architecture is allowed when the current harness/reviewer/\n"
    "   critic/planner/tooling structure is measurably preventing progress.\n"
    "   Such tasks must include observed evidence, tests or smoke checks, and\n"
    "   acceptance criteria proving the agent now handles the blocked class of\n"
    "   tasks. Do NOT self-modify for cosmetic architecture preferences.\n"
    "17) Read the L2 reviewer's structured briefing (the `reviewer→planner:`\n"
    "   block under each recent journal entry, with `forward_progress`,\n"
    "   `headline`, `blocker`, `recommended_next`, and `evidence_files`), not\n"
    "   just the `status` field. When `forward_progress=False`, the mission did\n"
    "   NOT advance the project even if it is marked `mission_complete` — it\n"
    "   finished via a blocked / rollback / allowed-failure / gate-blocked /\n"
    "   not-launched path and the underlying blocker is still open. When\n"
    "   `blocker` names a root cause and owning stage, the next mission MUST\n"
    "   attack that root cause — follow `recommended_next`: fix the method, or\n"
    "   roll back to the owning stage and redo it — and must NOT re-queue an\n"
    "   equivalent gate-refresh/rename/'mark v_N as history' task that leaves\n"
    "   the blocker in place. If the same `blocker` recurs across two or more\n"
    "   recent entries, escalate to a single root-cause or pivot mission that\n"
    "   names the repeated blocker and requires the Engineer to inspect root\n"
    "   cause before another local patch, instead of broadening guessed\n"
    "   fallbacks.\n"
    "17b) When a recent briefing lists `evidence_files` for a failed / no-\n"
    "   progress / surprising mission (esp. a training run), OPEN AND READ those\n"
    "   files yourself BEFORE emitting JSON — do not route the next mission off\n"
    "   the one-line headline alone. Inspect the run's `status.json` /\n"
    "   `progress.jsonl` metric series, the training/eval source script, the\n"
    "   data-provenance file, and any `*_NO_GO.md`, so the next mission attacks\n"
    "   the ACTUAL root cause. Note: a mechanical `*_NO_GO.md` / `state=failed`\n"
    "   produced by a single metric-threshold breach is ADVISORY — judge run\n"
    "   health from the metric TREND and the supervisor handoff, not the flag.\n"
    "17c) GRADUATION (stop the smoke-thrash): a smoke / micro-run (tiny\n"
    "   `max_steps`, `num_generations=2`, a few rows) only proves the harness\n"
    "   WIRING runs — it is never paper evidence. Once a smoke has executed,\n"
    "   the next training mission must EITHER scale up to a real pilot/full run\n"
    "   OR diagnose a NAMED root cause from the evidence_files. Do NOT queue\n"
    "   another equivalent micro-smoke that only tweaks a threshold/flag; that\n"
    "   is no forward progress and burns budget.\n"
    "18) Do ALL inspection with your tools BEFORE you emit the final JSON. The\n"
    "   final JSON is a committed decision, not a status update. Returning\n"
    "   `project_done=false` with `new_tasks=[]`, `restart_daemon=false`, and\n"
    "   `waiting=false` is INVALID — never emit a placeholder verdict whose\n"
    "   `reason` says you are 'inspecting', 'deciding', or 'about to' route a\n"
    "   mission. By the time you output JSON you MUST have finished inspecting\n"
    "   and either (a) committed at least one concrete task, (b) set\n"
    "   `project_done=true`, (c) set `restart_daemon=true`, or (d) set\n"
    "   `waiting=true` (see rule 18b). If you are unsure what the next mission\n"
    "   is, default to one bounded current-stage gate mission derived from the\n"
    "   stage checklist — do not stall.\n"
    "18b) WAITING (the correct way to idle, instead of make-work): set\n"
    "   `waiting=true` with `new_tasks=[]` and `project_done=false` ONLY when\n"
    "   ALL hold: (i) a tracked long-running EXTERNAL job (e.g. a verl/training\n"
    "   or eval run) is live and NONTERMINAL — confirmed from its status.json /\n"
    "   progress.jsonl, not guessed; (ii) the single allowed parallel paper-\n"
    "   drafting mission (rule 7 exception) is ALREADY queued/running or recently\n"
    "   completed; and (iii) you inspected the value horizons and there is NO\n"
    "   genuinely high-impact task left that does not depend on that job\n"
    "   finishing. `waiting=true` is a first-class, intentional idle: the host\n"
    "   backs off and re-checks later WITHOUT burning a mission. Do NOT use\n"
    "   `waiting=true` to dodge real, available work — inventing a low-value\n"
    "   'harden/prepare/scaffold while X runs' mission is WORSE than waiting, but\n"
    "   skipping genuinely-ready work by claiming waiting is also wrong. Put the\n"
    "   job id/path and current progress in `waiting_reason`.\n"
    "19) Output JSON ONLY. No prose around it. No markdown fences.\n"
)


def _planner_hard_idle_seconds() -> int:
    raw = os.environ.get("ARGUS_SKILL_PLANNER_HARD_IDLE_SECONDS", "").strip()
    if not raw:
        return DEFAULT_PLANNER_HARD_IDLE_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_PLANNER_HARD_IDLE_SECONDS


def _planner_max_seconds() -> int:
    raw = os.environ.get("ARGUS_SKILL_PLANNER_MAX_SECONDS", "").strip()
    if not raw:
        return DEFAULT_PLANNER_MAX_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_PLANNER_MAX_SECONDS


def _planner_wall_clock_interrupt_provider():
    limit_seconds = _planner_max_seconds()
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

    The historical "Critic.evaluate()" per-iteration polish layer was
    removed; the supervisor now relies on the L2 reviewer for verdicts
    and the planner for forward scheduling. The exported ``Critic``
    alias below preserves any third-party import sites.
    """

    def __init__(self, runner: RunnerBackend, *, skill_store: Any | None = None) -> None:
        self.runner = runner
        # Optional role-mission skill matcher (same scaffold engineer and
        # reviewer use). There is no builtin_skills/planner/ pool today, so
        # the matcher short-circuits to empty with no backend call; wiring it
        # establishes the uniform mission path for when planner skills land.
        self.skill_store = skill_store
        from ..missions import PlannerMission
        self.mission = PlannerMission(skill_store)

    # ------------------------------------------------------------------
    # Planner role — project-level planning
    # ------------------------------------------------------------------

    def plan_next(
        self,
        *,
        continuous_objective: str,
        journal_tail: str = "",
        budget_remaining_usd: float = 0.0,
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
        prompt = self._build_planner_prompt(
            continuous_objective=continuous_objective,
            journal_tail=journal_tail,
            budget_remaining_usd=budget_remaining_usd,
            planning_cycle=planning_cycle,
            runtime_change_summary=runtime_change_summary,
            mission=self.mission,
        )
        try:
            result = self.runner.run_exec(
                prompt=prompt,
                resume_thread_id=None,
                options=RunnerOptions(
                    model=cfg.model,
                    reasoning_effort=cfg.reasoning_effort or "high",
                    output_schema_path=PLANNER_SCHEMA_PATH,
                    working_dir=cfg.working_dir,
                    dangerous_yolo=cfg.dangerous_yolo,
                    full_auto=cfg.full_auto,
                    skip_git_repo_check=cfg.skip_git_repo_check,
                    extra_args=list(cfg.extra_args) if cfg.extra_args else None,
                    external_interrupt_reason_provider=(
                        _planner_wall_clock_interrupt_provider()
                    ),
                    watchdog_hard_idle_seconds=_planner_hard_idle_seconds(),
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
            )
        parsed = parse_planner_text(text)
        return replace(
            parsed,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
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
        budget_remaining_usd: float,
        planning_cycle: int,
        runtime_change_summary: str = "",
        mission: Any | None = None,
    ) -> str:
        budget_line = (
            f"This is planning cycle #{planning_cycle + 1}. "
            f"Remaining budget: ${budget_remaining_usd:.2f}. "
            "If budget is low, prioritize the single highest-impact task. "
            "Keep searching for valuable work; do not spend tokens on "
            "low-value polish just to keep the loop busy."
        )
        from ..skills.harness_overlay import resolve_project_root
        from ..skills.stage_checklists import (
            CANONICAL_STAGE_ORDER,
            current_stage,
            format_stage_checklist,
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

        # Parallel paper-drafting track: while a long experiment grinds in the
        # background during `run`/`analysis`, drafting manuscript prose is not
        # gated behind run/analysis (the draft/review/submission evidence gates
        # only fire once current_stage advances). Surface an explicit permission
        # block + the draft-stage checklist so the planner can keep the loop
        # productive instead of babysitting the run. Prose-only, never advances
        # the stage pointer; final-number integrity is preserved via placeholders.
        parallel_drafting_block = ""
        if stage in ("run", "analysis"):
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
            "pretends the gap doesn't exist. Instead:\n\n"
            "1. **Investigate before deciding.** Read at least: the missing\n"
            "   artifact's expected path, the stage checklist for the\n"
            "   earlier stage that owns it, the current `PIPELINE_STATE.json`,\n"
            "   and any nearby evidence that might already cover the gap\n"
            "   under a different name. Do not roll back on a typo.\n"
            "2. **Identify the EARLIEST broken stage**, not the latest one.\n"
            "   If both `research.infra_shortlist` and `plan.infra_choice`\n"
            "   are missing, the rollback target is `research`, not `plan` —\n"
            "   the engineer cannot lock a choice without a shortlist.\n"
            "3. **Queue exactly one bounded mission** whose body is to:\n"
            "   (a) call `python -c \"from argus_skill.skills.stage_checklists "
            "import rollback_stage; rollback_stage('.', target_stage='<X>', "
            "reason='<one-sentence quoting the missing artifact>')\"` so the "
            "state machine and audit trail are correct;\n"
            "   (b) re-do the broken stage with concrete investigation\n"
            "   (read referenced papers, clone candidate framework repos,\n"
            "   call the model APIs to verify scoring backends, …) — NOT a\n"
            "   blind regenerate or a template fill-in;\n"
            "   (c) only after the earlier-stage checklist is satisfied,\n"
            "   re-advance.\n"
            "4. **Do not queue parallel forward + rollback tasks.** A\n"
            "   rollback supersedes everything else this cycle.\n"
        )

        # Planner role mission matcher (same primitive engineer/reviewer use).
        # No builtin_skills/planner/ pool exists today, so this short-circuits
        # to empty with no backend call; it establishes the uniform mission
        # path for when planner skills are added.
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
                for name in ("stale-watchlist.md", "open-contradictions.md"):
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
            from ..wiki.bot_state import collect_backoff_hours, collect_cooldown_elapsed, load_bot_state

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

        return (
            format_role_context(
                "Argus planner role skill",
                _PLANNER_ROLE_SKILL,
                _PLANNER_ROLE_FALLBACK,
            )
            + stage_checklist
            + "\n\n"
            + matched_planner_skill_block
            + upstream_rollback_block
            + "\n"
            + parallel_drafting_block
            + ("\n" if parallel_drafting_block else "")
            + wiki_block
            + ("\n" if wiki_block else "")
            + _PLANNER_SYSTEM_PREAMBLE
            + "\n\nOperator's continuous goal:\n"
            + continuous_objective.strip()
            + "\n\nJournal of completed work (most recent last):\n"
            + (journal_tail.strip() or "(no completed work yet — this is the first cycle)")
            + "\n\nRuntime/project context signal:\n"
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
            + budget_line
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
    for blob in _iter_json_objects(text):
        try:
            data = json.loads(blob)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if all(key in data for key in required_keys):
            return data, blob
    return None


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
                )
            )
            if len(new_tasks) >= 3:
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
        )
    return PlannerVerdict(
        project_done=project_done,
        reason=reason,
        new_tasks=new_tasks,
        restart_daemon=restart_daemon,
        restart_reason=restart_reason,
        raw_text=blob,
        cached_input_tokens=0,
    )


# ---------------------------------------------------------------------------
# Objective rendering for the next cycle
# ---------------------------------------------------------------------------
