"""Critic sub-agent implementation.

Stateless: one ``Critic.evaluate(...)`` call per iteration cycle.
The critic receives the original objective, the latest reviewer
completion summary (which by contract quotes concrete evidence — see
reviewer rule 7), and a tail of the journal for context, and is asked
to either:

* return one or more concrete, actionable improvement items, OR
* return an empty list AND ``stop=True`` if the artefact is
  genuinely done — no fluff, no nitpicking.

The prompt is engineered to reject vanity edits (rename, comment, tiny
refactor) unless the operator explicitly asked for them; the goal is
real value delivery on each cycle, not iteration theatre.

The :meth:`Critic.plan_next` method extends the critic into a
*planner* role: after all currently queued work is finished, the
planner inspects the project holistically and generates the next batch
of backlog items (or declares the project done). This enables 24/7
continuous improvement without human intervention.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..core.models import RunnerOptions
from ..core.ports import RunnerBackend


@dataclass
class CriticConfig:
    model: str | None = None
    reasoning_effort: str | None = None
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
    "iteration is iteration theatre.\n\n"
    "Output a JSON object with this exact shape:\n"
    "{\n"
    '  "stop": <true|false>,\n'
    '  "reason": "<one sentence>",\n'
    '  "improvements": [\n'
    "    {\n"
    '      "title":      "<short imperative, e.g. add property-based tests for amount validation>",\n'
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
    "2) `stop=false` is allowed ONLY when at least one improvement is\n"
    "   GENUINELY VALUABLE — examples:\n"
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
    "4) Each improvement's `acceptance` must be testable: a command\n"
    "   the engineer can run, an output that must be present, a\n"
    "   measurable property. If you cannot write an acceptance line,\n"
    "   it is not a real improvement; do not list it.\n"
    "5) Cap improvements at 3. Quality over quantity.\n"
    "6) Output JSON ONLY. No prose around it. No markdown fences.\n"
)


@dataclass(frozen=True)
class TaskSpec:
    """One concrete task the planner wants the engineering team to tackle next."""

    title: str
    objective: str  # full actionable description for the engineer


@dataclass(frozen=True)
class PlannerVerdict:
    """Result of a planner evaluation — new work or project done."""

    project_done: bool
    reason: str
    new_tasks: list[TaskSpec] = field(default_factory=list)
    raw_text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


_PLANNER_SYSTEM_PREAMBLE = (
    "You are the Planner agent (经理+总监) in a 7×24 supervised coding loop.\n"
    "The engineering team has completed all currently queued tasks.\n"
    "Your job: inspect the project, assess progress toward the\n"
    "operator's goal, and either declare the project done or queue\n"
    "the next batch of high-impact improvements.\n\n"
    "You HAVE shell access. USE IT to:\n"
    "- Read the project structure (`find`, `ls`, `tree`)\n"
    "- Run tests (`pytest -q`), linters (`ruff check`), type checkers\n"
    "- Read key source files and documentation\n"
    "- Check for TODO/FIXME/HACK comments\n"
    "- Assess code quality and architecture\n"
    "- Verify end-to-end workflows work\n\n"
    "Output a JSON object with this exact shape:\n"
    "{\n"
    '  "project_done": <true|false>,\n'
    '  "reason": "<one sentence justification>",\n'
    '  "new_tasks": [\n'
    "    {\n"
    '      "title": "<short imperative title>",\n'
    '      "objective": "<detailed, actionable objective with '
    "acceptance criteria>\"\n"
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Rules:\n"
    "1) `project_done=true` ONLY when:\n"
    "   - The operator's goal is FULLY satisfied, AND\n"
    "   - Tests pass, linters are clean, docs are accurate, AND\n"
    "   - You genuinely cannot find a concrete improvement worth an\n"
    "     engineer's time. Be STRICT: prefer finding more work.\n"
    "   When `project_done=true`, `new_tasks` MUST be `[]`.\n"
    "2) `project_done=false` when there is ANY concrete improvement\n"
    "   that would move the project closer to the operator's goal.\n"
    "   Prefer `false` when in doubt — iteration is cheap.\n"
    "3) Each task's `objective` must be ACTIONABLE: the engineer\n"
    "   should be able to start working immediately with no\n"
    "   clarification. Include:\n"
    "   - What to change and where in the code\n"
    "   - Concrete acceptance criteria (commands to run, expected output)\n"
    "   - Any constraints or gotchas\n"
    "4) Order tasks by impact: most important first.\n"
    "5) Cap at 3 tasks per planning cycle. Quality over quantity.\n"
    "6) NEVER repeat work already completed (check the journal below).\n"
    "7) NEVER propose vanity work (renames, comment polish, trivial\n"
    "   refactors) unless the operator explicitly asked for it.\n"
    "8) Each task should be independently completable in one mission\n"
    "   (not multi-step dependencies).\n"
    "9) Output JSON ONLY. No prose around it. No markdown fences.\n"
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
        budget_line = (
            f"You are at iteration cycle {cycles_done}/{cycles_max}. "
            f"Remaining budget: ${budget_remaining_usd:.2f}. "
            "If budget is low, prefer fewer / higher-impact improvements."
        )
        return (
            _CRITIC_SYSTEM_PREAMBLE
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
        )
        result = self.runner.run_exec(
            prompt=prompt,
            resume_thread_id=None,
            options=RunnerOptions(
                model=cfg.model,
                reasoning_effort=cfg.reasoning_effort or "high",
                dangerous_yolo=cfg.dangerous_yolo,
                full_auto=cfg.full_auto,
                skip_git_repo_check=cfg.skip_git_repo_check,
                extra_args=list(cfg.extra_args) if cfg.extra_args else None,
            ),
            run_label=f"planner.cycle{planning_cycle}",
        )
        input_tokens = int(getattr(result, "input_tokens", 0) or 0)
        cached_input_tokens = int(getattr(result, "cached_input_tokens", 0) or 0)
        output_tokens = int(getattr(result, "output_tokens", 0) or 0)
        text = "\n".join(result.agent_messages or [])
        parsed = parse_planner_text(text)
        if parsed is None:
            return PlannerVerdict(
                project_done=True,
                reason="planner returned unparseable output; defaulting to done",
                new_tasks=[],
                raw_text=text,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
            )
        return PlannerVerdict(
            project_done=parsed.project_done,
            reason=parsed.reason,
            new_tasks=parsed.new_tasks,
            raw_text=parsed.raw_text,
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
    ) -> str:
        budget_line = (
            f"This is planning cycle #{planning_cycle + 1}. "
            f"Remaining budget: ${budget_remaining_usd:.2f}. "
            "If budget is low, prioritize the single highest-impact task."
        )
        return (
            _PLANNER_SYSTEM_PREAMBLE
            + "\n\nOperator's continuous goal:\n"
            + continuous_objective.strip()
            + "\n\nJournal of completed work (most recent last):\n"
            + (journal_tail.strip() or "(no completed work yet — this is the first cycle)")
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
    if isinstance(improvements_raw, list):
        for entry in improvements_raw[:3]:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title", "")).strip()
            rationale = str(entry.get("rationale", "")).strip()
            acceptance = str(entry.get("acceptance", "")).strip()
            if not title or not acceptance:
                continue
            improvements.append(
                Improvement(title=title, rationale=rationale, acceptance=acceptance)
            )
    if stop and improvements:
        # Inconsistent — operator-facing rule: stop=True ⇒ no improvements.
        # Honor stop and discard.
        improvements = []
    if not stop and not improvements:
        # Inconsistent the other way — defensively treat as stop.
        stop = True
        if not reason:
            reason = "critic flagged continue but produced no concrete improvements"
    return CriticVerdict(
        stop=stop, reason=reason, improvements=improvements, raw_text=blob
    )


def parse_planner_text(text: str) -> PlannerVerdict | None:
    """Parse a planner JSON verdict out of an agent message.

    Returns ``None`` on unparseable output; the supervisor treats that
    as "project done" (safe fallback to avoid spinning forever).
    """
    if not text:
        return None
    found = _load_json_object_with_schema(
        text,
        required_keys=("project_done", "reason", "new_tasks"),
    )
    if found is None:
        return None
    data, blob = found
    project_done = _parse_json_bool(data.get("project_done", True), True)
    reason = str(data.get("reason", ""))
    tasks_raw = data.get("new_tasks") or []
    new_tasks: list[TaskSpec] = []
    if isinstance(tasks_raw, list):
        for entry in tasks_raw[:3]:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title", "")).strip()
            objective = str(entry.get("objective", "")).strip()
            if not title or not objective:
                continue
            new_tasks.append(TaskSpec(title=title, objective=objective))
    if project_done and new_tasks:
        # Inconsistent: project_done=True but tasks listed → honor done.
        new_tasks = []
    if not project_done and not new_tasks:
        # Inconsistent: not done but no tasks → treat as done.
        project_done = True
        if not reason:
            reason = "planner said not done but produced no concrete tasks"
    return PlannerVerdict(
        project_done=project_done,
        reason=reason,
        new_tasks=new_tasks,
        raw_text=blob,
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
        if imp.rationale:
            line += f"\n   why: {imp.rationale}"
        line += f"\n   acceptance: {imp.acceptance}"
        bullets.append(line)
    cycle_label = f"cycle #{cycles_done + 1}"
    return (
        f"Iteration {cycle_label} — polish the existing artefact. "
        "DO NOT rewrite from scratch; the previous cycle's work was "
        "already accepted by the reviewer. Apply the improvements "
        "below and verify each acceptance criterion before declaring "
        "done.\n\n"
        f"Original operator objective:\n{original_objective.strip()}\n\n"
        "Polish-pass improvements:\n" + "\n".join(bullets) + "\n"
    )
