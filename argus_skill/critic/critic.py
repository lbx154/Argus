"""Critic sub-agent implementation.

Stateless: one ``Critic.evaluate(...)`` call per iteration cycle.
The critic receives the original objective, the latest reviewer
completion summary (which by contract quotes concrete evidence — see
reviewer rule 7), and a tail of the journal for context, and is asked
to either:

* return one or more concrete, actionable improvement items, OR
* return an empty list AND ``stop=True`` if the artefact is
  genuinely done — no fluff, no nitpicking.

The prompt and parser are engineered to reject vanity edits (rename,
comment, tiny refactor) unless the operator explicitly asked for them.
The goal is sustained high-value work: L3 stops low-value local polish
so L4 can find the next valuable mission instead of burning tokens on
iteration theatre.

The :meth:`Critic.plan_next` method extends the critic into a
*planner* role: after all currently queued work is finished, the
planner inspects the project holistically and generates the next batch
of backlog items (or declares the project done). This enables 24/7
continuous improvement without human intervention.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

from ..core.models import RunnerOptions
from ..core.ports import RunnerBackend
from ..skills.role_context import format_role_context

MIN_CRITIC_IMPACT_SCORE = 4
MIN_PLANNER_IMPACT_SCORE = 4
TASK_SCOPE_BOUNDED = "bounded"
TASK_SCOPE_FINAL_SUBMISSION = "final_submission"
_TASK_SCOPES = {TASK_SCOPE_BOUNDED, TASK_SCOPE_FINAL_SUBMISSION}
_CRITIC_ROLE_SKILL = "argus-critic-role.md"
_PLANNER_ROLE_SKILL = "argus-planner-role.md"
_CRITIC_ROLE_FALLBACK = """# Argus Critic Role

The Critic is argus-skill's post-review quality filter. Continue only for
operator-visible high-impact improvements; stop local iteration for vanity or
cosmetic work.
"""
_PLANNER_ROLE_FALLBACK = """# Argus Planner Role

The Planner is argus-skill's manager/director. Inspect project state and queue
the next high-impact bounded missions, reserving final_submission for the
whole-project readiness gate.
"""


@dataclass
class CriticConfig:
    model: str | None = None
    reasoning_effort: str | None = None
    working_dir: str | None = None
    extra_args: list[str] = field(default_factory=list)
    skip_git_repo_check: bool = True
    full_auto: bool = False
    dangerous_yolo: bool = False


@dataclass(frozen=True)
class Improvement:
    """One concrete polish proposal the critic wants the engineer to apply."""

    title: str
    rationale: str
    acceptance: str  # how the operator would verify the improvement landed
    impact_score: int = 0  # 0-5; parser accepts only high-value proposals
    impact_area: str = ""
    evidence: str = ""


@dataclass(frozen=True)
class CriticVerdict:
    stop: bool  # True ⇒ finalize the item, do not iterate further
    reason: str
    improvements: list[Improvement] = field(default_factory=list)
    raw_text: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0


_CRITIC_SYSTEM_PREAMBLE = (
    "You are the Critic agent in a 7×24 supervised coding loop. "
    "An engineer just produced an artefact and the reviewer accepted "
    "it as `done`. Your job: decide whether one more polishing cycle "
    "would deliver real, operator-visible value, or whether further "
    "iteration is iteration theatre. `stop=true` does NOT mean the "
    "daemon stops working; it means this local artifact is done and the "
    "L4 planner should find the next valuable mission.\n\n"
    "Output a JSON object with this exact shape:\n"
    "{\n"
    '  "stop": <true|false>,\n'
    '  "reason": "<one sentence>",\n'
    '  "improvements": [\n'
    "    {\n"
    '      "title":      "<short imperative, e.g. add property-based tests for amount validation>",\n'
    '      "impact_score": <0-5 integer>,\n'
    '      "impact_area": "<correctness|security|operator_ux|performance|reliability|integration|requirement_gap>",\n'
    '      "evidence":   "<specific signal showing this is worth another agent round>",\n'
    '      "rationale":  "<why this matters to the operator, 1-2 sentences>",\n'
    '      "acceptance": "<what the engineer must show next round to prove it landed>"\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Rules:\n"
    "1) `stop=true` MUST be returned when:\n"
    "   - the operator's original objective is fully satisfied with no\n"
    "     concrete weakness left, OR\n"
    "   - any further work would be cosmetic (rename, doc polish, "
    "trivial refactor) and the operator did NOT ask for that.\n"
    "   When `stop=true`, `improvements` MUST be `[]`.\n"
    "2) `stop=false` is allowed ONLY when at least one improvement has\n"
    f"   `impact_score >= {MIN_CRITIC_IMPACT_SCORE}` and is GENUINELY VALUABLE — examples:\n"
    "   * missing edge cases the test suite does not exercise;\n"
    "   * obvious correctness gap (race, off-by-one, leak, security);\n"
    "   * a feature the operator asked for that is incomplete;\n"
    "   * performance / robustness in a path the operator cares about;\n"
    "   * an integration / end-to-end demo proving the artefact works\n"
    "     in the real environment, not just unit tests.\n"
    "3) NEVER propose: variable renames, type-hint tightening,\n"
    "   docstring polish, removing trailing whitespace, splitting a\n"
    "   30-line file into more files, adding logging just to add\n"
    "   logging, or any change whose acceptance criterion is itself\n"
    "   subjective. Those are vanity. Vanity ⇒ `stop=true`.\n"
    "4) Each improvement must include concrete `evidence`: failing or\n"
    "   missing coverage, observed runtime risk, user-visible gap,\n"
    "   documented requirement gap, production-like smoke failure, etc.\n"
    "   If the evidence is only \"would be cleaner\", stop instead.\n"
    "5) Each improvement's `acceptance` must be testable: a command\n"
    "   the engineer can run, an output that must be present, a\n"
    "   measurable property. If you cannot write an acceptance line,\n"
    "   it is not a real improvement; do not list it.\n"
    "6) Cap improvements at 3. Quality over quantity.\n"
    "7) If the original objective or metadata says `planner_scope: final_submission`\n"
    "   or `Task scope: final_submission`, `stop=true` is allowed ONLY when the\n"
    "   latest evidence quotes `python -m argus_skill.skills.pipeline_contracts\n"
    "   validate-full-emnlp --project-root .` exiting 0 and the submission\n"
    "   assurance has no hard blockers. A passing `validate-pipeline`,\n"
    "   manifest check, pilot run, underlength draft, missing strong baseline,\n"
    "   missing ablation, negative-result pivot for a positive paper objective,\n"
    "   baseline-only win that does not support the proposed contribution, or\n"
    "   failed/missing full gate is a high-impact\n"
    "   `requirement_gap` and must yield `stop=false`. Do NOT apply this\n"
    "   rule to `planner_scope: bounded` or unscoped bounded subtasks.\n"
    "8) If the original objective or metadata says `paper_optimization_task`,\n"
    "   treat it as a long-horizon paper mission even when\n"
    "   `planner_scope: bounded`. `stop=true` is allowed only when the latest\n"
    "   evidence shows fresh paper validators were run or inspected and either\n"
    "   the addressable blockers were fixed or the remaining blockers are\n"
    "   explicitly listed as outside this mission's budget. Underfilled body,\n"
    "   stale artifacts, missing manuscript, failed `validate-research-md-format`,\n"
    "   or untriaged `validate-full-emnlp` blockers are high-impact\n"
    "   `requirement_gap`s. This does not demand full-gate exit 0 unless the\n"
    "   scope is `final_submission`; it prevents tiny local paper fixes from\n"
    "   being accepted as enough.\n"
    "9) Output JSON ONLY. No prose around it. No markdown fences.\n"
)


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
    "     skills, run or inspect `validate-full-emnlp`, then repair all\n"
    "     addressable manuscript/evidence/layout/review/artifact blockers in the\n"
    "     same mission before stopping.\n"
    "   - `final_submission` ONLY for the single project-final readiness task\n"
    "     whose acceptance is proving the whole EMNLP/ACL submission package.\n"
    "     That objective must require verbatim success for\n"
    "     `python -m argus_skill.skills.pipeline_contracts validate-full-emnlp\n"
    "     --project-root .` before anyone may declare it done.\n"
    f"5) Every task must have `impact_score >= {MIN_PLANNER_IMPACT_SCORE}` and\n"
    "   concrete `evidence`. Lower-score work is rejected by the host.\n"
    "6) For an operator goal that asks for a full EMNLP/ACL paper or\n"
    "   submission-ready package, `project_done=true` requires journal evidence\n"
    "   that `validate-full-emnlp --project-root .` exited 0. If the full gate\n"
    "   is missing or failing, set `project_done=false` and queue one broad\n"
    "   bounded long-horizon paper optimization blocker mission by default, or a\n"
    "   `final_submission` task only when the package appears ready and just\n"
    "   needs final proof. `validate-pipeline` alone is never enough.\n"
    "   For positive paper objectives, a negative-result pivot or a baseline-only\n"
    "   win is not project_done; require a structured X-Y-Z-W paper_contribution\n"
    "   claim where the proposed artifact/protocol beats the strongest nontrivial\n"
    "   baseline with statistical support.\n"
    "7) Order tasks by impact: most important first.\n"
    "8) Cap at 3 tasks per planning cycle. For EMNLP/ACL/paper goals, prefer\n"
    "   1 broad task over 3 microtasks unless the blockers are truly independent.\n"
    "9) NEVER repeat work already completed (check the journal below).\n"
    "10) NEVER propose vanity work (renames, comment polish, trivial\n"
    "   refactors) unless the operator explicitly asked for it.\n"
    "11) Each non-paper task should be independently completable in one mission\n"
    "   (not multi-step dependencies). Paper optimization tasks may be broad,\n"
    "   multi-file, and multi-validator because the Engineer is expected to run\n"
    "   long-horizon missions, not wait for Planner to decompose every paragraph.\n"
    "12) Set `restart_daemon=true` ONLY when the prompt says runtime\n"
    "   source changed AND a fresh daemon is needed for the next step —\n"
    "   for example daemon/CLI/lifecycle code changed, a large runtime\n"
    "   refactor landed, or verification requires the installed daemon\n"
    "   process to reload new code. Otherwise set it false.\n"
    "13) `restart_daemon=true` is not a substitute for useful work: if\n"
    "   new tasks are still needed after restart, include them too. If\n"
    "   restart itself is the next verification step, `new_tasks` may be []\n"
    "   with `project_done=false`.\n"
    "14) Self-architecture is allowed when the current harness/reviewer/\n"
    "   critic/planner/tooling structure is measurably preventing progress.\n"
    "   Such tasks must include observed evidence, tests or smoke checks, and\n"
    "   acceptance criteria proving the agent now handles the blocked class of\n"
    "   tasks. Do NOT self-modify for cosmetic architecture preferences.\n"
    "15) Output JSON ONLY. No prose around it. No markdown fences.\n"
)


class Critic:
    """Critic + Planner agent. Stateless per call.

    * :meth:`evaluate` — per-iteration: polish or finalize one mission.
    * :meth:`plan_next` — per-planning-cycle: inspect the project and
      generate the next batch of backlog items (or declare done).
    """

    def __init__(self, runner: RunnerBackend) -> None:
        self.runner = runner

    def evaluate(
        self,
        *,
        original_objective: str,
        latest_completion_summary: str,
        cycles_done: int,
        cycles_max: int,
        budget_remaining_usd: float,
        journal_tail: str = "",
        config: CriticConfig | None = None,
    ) -> CriticVerdict:
        cfg = config or CriticConfig()
        prompt = self._build_prompt(
            original_objective=original_objective,
            latest_completion_summary=latest_completion_summary,
            cycles_done=cycles_done,
            cycles_max=cycles_max,
            budget_remaining_usd=budget_remaining_usd,
            journal_tail=journal_tail,
        )
        result = self.runner.run_exec(
            prompt=prompt,
            resume_thread_id=None,
            options=RunnerOptions(
                model=cfg.model,
                reasoning_effort=cfg.reasoning_effort,
                working_dir=cfg.working_dir,
                dangerous_yolo=cfg.dangerous_yolo,
                full_auto=cfg.full_auto,
                skip_git_repo_check=cfg.skip_git_repo_check,
                extra_args=list(cfg.extra_args) if cfg.extra_args else None,
            ),
            run_label=f"critic.cycle{cycles_done + 1}",
        )
        input_tokens = int(getattr(result, "input_tokens", 0) or 0)
        cached_input_tokens = int(getattr(result, "cached_input_tokens", 0) or 0)
        output_tokens = int(getattr(result, "output_tokens", 0) or 0)
        text = "\n".join(result.agent_messages or [])
        parsed = parse_critic_text(text)
        if parsed is None:
            # On parse failure we play it safe and stop — better to
            # accept the reviewer's `done` than to spin a malformed
            # prompt forever.
            return CriticVerdict(
                stop=True,
                reason="critic returned unparseable output; defaulting to stop",
                improvements=[],
                raw_text=text,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
            )
        return CriticVerdict(
            stop=parsed.stop,
            reason=parsed.reason,
            improvements=parsed.improvements,
            raw_text=text,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _build_prompt(
        *,
        original_objective: str,
        latest_completion_summary: str,
        cycles_done: int,
        cycles_max: int,
        budget_remaining_usd: float,
        journal_tail: str,
    ) -> str:
        min_impact_score = 5 if cycles_done >= 1 else MIN_CRITIC_IMPACT_SCORE
        budget_line = (
            f"You are at iteration cycle {cycles_done}/{cycles_max}. "
            f"Remaining budget: ${budget_remaining_usd:.2f}. "
            f"This cycle requires impact_score >= {min_impact_score}; "
            "later polish rounds must clear a higher bar than the first pass. "
            "If budget is low, prefer stopping this artifact so the planner "
            "can find a higher-impact mission."
        )
        from ..tools.validator_toolbelt import format_validator_toolbelt_for_role

        return (
            format_role_context(
                "Argus critic role skill",
                _CRITIC_ROLE_SKILL,
                _CRITIC_ROLE_FALLBACK,
            )
            + format_validator_toolbelt_for_role("critic")
            + "\n\n"
            + _CRITIC_SYSTEM_PREAMBLE
            + "\n\nOriginal operator objective:\n"
            + original_objective.strip()
            + "\n\nLatest reviewer-accepted completion summary:\n"
            + latest_completion_summary.strip()
            + "\n\nJournal tail (recent events for context):\n"
            + (journal_tail.strip() or "(none)")
            + "\n\n"
            + budget_line
            + "\n\nReturn the JSON verdict now. No prose.\n"
        )

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
        config: CriticConfig | None = None,
    ) -> PlannerVerdict:
        """Inspect the project and generate the next batch of tasks.

        Called when the backlog is empty and continuous mode is active.
        The runner has shell access, so the planner can inspect code,
        run tests, read docs, etc. before deciding what to work on next.
        """
        cfg = config or CriticConfig()
        prompt = self._build_planner_prompt(
            continuous_objective=continuous_objective,
            journal_tail=journal_tail,
            budget_remaining_usd=budget_remaining_usd,
            planning_cycle=planning_cycle,
            runtime_change_summary=runtime_change_summary,
        )
        try:
            result = self.runner.run_exec(
                prompt=prompt,
                resume_thread_id=None,
                options=RunnerOptions(
                    model=cfg.model,
                    reasoning_effort=cfg.reasoning_effort or "high",
                    working_dir=cfg.working_dir,
                    dangerous_yolo=cfg.dangerous_yolo,
                    full_auto=cfg.full_auto,
                    skip_git_repo_check=cfg.skip_git_repo_check,
                    extra_args=list(cfg.extra_args) if cfg.extra_args else None,
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
    def _build_planner_prompt(
        *,
        continuous_objective: str,
        journal_tail: str,
        budget_remaining_usd: float,
        planning_cycle: int,
        runtime_change_summary: str = "",
    ) -> str:
        budget_line = (
            f"This is planning cycle #{planning_cycle + 1}. "
            f"Remaining budget: ${budget_remaining_usd:.2f}. "
            "If budget is low, prioritize the single highest-impact task. "
            "Keep searching for valuable work; do not spend tokens on "
            "low-value polish just to keep the loop busy."
        )
        from ..tools.validator_toolbelt import format_validator_toolbelt_for_role

        return (
            format_role_context(
                "Argus planner role skill",
                _PLANNER_ROLE_SKILL,
                _PLANNER_ROLE_FALLBACK,
            )
            + format_validator_toolbelt_for_role("planner")
            + "\n\n"
            + _PLANNER_SYSTEM_PREAMBLE
            + "\n\nOperator's continuous goal:\n"
            + continuous_objective.strip()
            + "\n\nJournal of completed work (most recent last):\n"
            + (journal_tail.strip() or "(no completed work yet — this is the first cycle)")
            + "\n\nRuntime source-change signal:\n"
            + (
                runtime_change_summary.strip()
                or "No runtime source changes have been detected since daemon start; set restart_daemon=false."
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


def parse_critic_text(text: str) -> CriticVerdict | None:
    """Parse the JSON verdict out of an agent message.

    Returns ``None`` if the payload cannot be coerced to the expected
    shape; the supervisor treats that as "stop". Tolerant of leading /
    trailing prose and markdown fences even though the prompt forbids
    them.
    """
    if not text:
        return None
    found = _load_json_object_with_schema(
        text,
        required_keys=("stop", "reason", "improvements"),
    )
    if found is None:
        return None
    data, blob = found
    stop = _parse_json_bool(data.get("stop", True), True)
    reason = str(data.get("reason", ""))
    improvements_raw = data.get("improvements") or []
    improvements: list[Improvement] = []
    raw_improvement_count = len(improvements_raw) if isinstance(improvements_raw, list) else 0
    if isinstance(improvements_raw, list):
        for entry in improvements_raw:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title", "")).strip()
            rationale = str(entry.get("rationale", "")).strip()
            acceptance = str(entry.get("acceptance", "")).strip()
            impact_score = _parse_impact_score(entry.get("impact_score"))
            impact_area = str(entry.get("impact_area", "")).strip()
            evidence = str(entry.get("evidence", "")).strip()
            if (
                not title
                or not acceptance
                or impact_score < MIN_CRITIC_IMPACT_SCORE
                or not evidence
            ):
                continue
            improvements.append(
                Improvement(
                    title=title,
                    rationale=rationale,
                    acceptance=acceptance,
                    impact_score=impact_score,
                    impact_area=impact_area,
                    evidence=evidence,
                )
            )
            if len(improvements) >= 3:
                break
    if stop and improvements:
        # Inconsistent — operator-facing rule: stop=True ⇒ no improvements.
        # Honor stop and discard.
        improvements = []
    if not stop and not improvements:
        # Inconsistent the other way — defensively treat as stop.
        stop = True
        if raw_improvement_count:
            reason = "critic improvements did not meet the high-impact gate"
        elif not reason:
            reason = "critic flagged continue but produced no concrete improvements"
    return CriticVerdict(
        stop=stop, reason=reason, improvements=improvements, raw_text=blob
    )


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


def render_iteration_objective(
    *,
    original_objective: str,
    cycles_done: int,
    improvements: list[Improvement],
) -> str:
    """Build the next-cycle objective handed back to the engineer.

    The engineer sees the original operator request, then the
    polish-pass improvements; we explicitly tell it not to redo work
    already accepted.
    """
    if not improvements:
        return original_objective
    bullets = []
    for i, imp in enumerate(improvements, 1):
        line = f"{i}. {imp.title}"
        line += f"\n   impact: {imp.impact_score}/5"
        if imp.impact_area:
            line += f" ({imp.impact_area})"
        if imp.evidence:
            line += f"\n   evidence: {imp.evidence}"
        if imp.rationale:
            line += f"\n   why: {imp.rationale}"
        line += f"\n   acceptance: {imp.acceptance}"
        bullets.append(line)
    cycle_label = f"cycle #{cycles_done + 1}"
    return (
        f"Iteration {cycle_label} — continue high-value work on the existing artefact. "
        "DO NOT rewrite from scratch; the previous cycle's work was "
        "already accepted by the reviewer. Apply the improvements "
        "below, and for paper/submission objectives keep working through "
        "adjacent validator blockers when budget allows instead of treating "
        "this as a tiny polish-only pass. Verify each acceptance criterion "
        "before declaring done.\n\n"
        f"Original operator objective:\n{original_objective.strip()}\n\n"
        "Polish-pass improvements:\n" + "\n".join(bullets) + "\n"
    )
