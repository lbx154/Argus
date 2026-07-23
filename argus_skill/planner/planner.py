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

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from ..core.models import RunnerOptions
from ..core.ports import RunnerBackend
from ..core.run_gateway import run_exec as gateway_run_exec

_DEFAULT_PLANNER_TIMEOUT_SECONDS = 300
TASK_SCOPE_BOUNDED = "bounded"
TASK_SCOPE_FINAL_SUBMISSION = "final_submission"
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
    open_ended: bool = False


@dataclass(frozen=True)
class TaskSpec:
    """One concrete task the planner wants the engineering team to tackle next."""

    title: str
    objective: str  # full actionable description for the engineer
    impact_score: int = 0  # model-authored 0-5 priority metadata
    impact_area: str = ""
    evidence: str = ""
    # One decisive completion check plus explicit read-only inputs. These form
    # the canonical Planner→Engineer context packet instead of forcing every
    # fresh session to rediscover the whole project.
    acceptance_check: str = ""
    non_goals: list[str] = field(default_factory=list)
    context_refs: list[dict[str, str]] = field(default_factory=list)
    scope: str = TASK_SCOPE_BOUNDED
    # A mission expected to satisfy the current-stage gate must receive an
    # independent Reviewer verdict so the Manager gets per-item evidence.
    stage_closing: bool = False
    # --- DAG fields (optional; flat tasks leave both at their defaults) ----
    # ``key`` is this task's *local* reference name, unique within one batch
    # of ``new_tasks``. Sibling tasks point at it via ``deps``. The supervisor
    # maps these local keys to the real backlog item ids when it enqueues the
    # batch (the keys themselves never reach the backlog). Empty ``key`` /
    # empty ``deps`` (the default) ⇒ a plain flat task, scheduled exactly as
    # before the DAG existed.
    key: str = ""
    deps: list[str] = field(default_factory=list)
    authorization_id: str = ""
    authorization_action: str = ""


@dataclass(frozen=True)
class WaitingContract:
    """Planner-authored durable identity and recheck policy for one blocker."""

    blocker_fingerprint: str
    recheck_condition: str
    recheck_token: str
    allow_verification_probe: bool = False
    recheck_after_seconds: int = 0
    stage_reconciliation_required: bool = False
    wait_mode: str = "poll"
    wake_on: tuple[str, ...] = ()
    watched_paths: tuple[str, ...] = ()
    expires_at: float = 0.0
    # True when only fresh operator input can change the blocker (for example,
    # new credentials, a scope choice, or authorization for an additional
    # mission/thesis).  Manager owns stage transitions, not operator scope.
    operator_action_required: bool = False


@dataclass(frozen=True)
class PlannerVerdict:
    """Result of a planner evaluation — new work or project done."""

    project_done: bool
    reason: str
    new_tasks: list[TaskSpec] = field(default_factory=list)
    raw_text: str = ""
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
    waiting_contract: WaitingContract | None = None
    schema_repair_attempted: bool = False
    schema_repair_succeeded: bool = False
    schema_repair_original_sha256: str = ""
    schema_repair_error: str = ""

    def schema_repair_event_payload(self) -> dict[str, Any]:
        if not self.schema_repair_attempted:
            return {}
        return {
            "schema_repair_attempted": True,
            "schema_repair_succeeded": self.schema_repair_succeeded,
            "schema_repair_original_sha256": self.schema_repair_original_sha256,
            "schema_repair_error": self.schema_repair_error,
        }


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
        prompt = self._build_planner_prompt(
            continuous_objective=continuous_objective,
            journal_tail=journal_tail,
            planning_cycle=planning_cycle,
            runtime_change_summary=runtime_change_summary,
            mission=self.mission,
            open_ended=cfg.open_ended,
        )
        planner_options = RunnerOptions(
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
        )
        try:
            result = gateway_run_exec(
                self.runner,
                prompt=prompt,
                resume_thread_id=None,
                options=planner_options,
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
            )
        parsed = parse_planner_text(text)
        if (
            parsed.error == "unparseable planner output"
            and text.strip()
            and str(getattr(result, "thread_id", "") or "").strip()
        ):
            original_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
            from ..roles.prompts.planner import build_schema_repair_prompt

            repair_prompt = build_schema_repair_prompt(original_sha256)
            repair_error = ""
            repair_succeeded = False
            try:
                repair_result = gateway_run_exec(
                    self.runner,
                    prompt=repair_prompt,
                    resume_thread_id=str(result.thread_id),
                    options=replace(
                        planner_options,
                        dangerous_yolo=False,
                        full_auto=False,
                        sandbox_mode="read-only",
                        external_interrupt_reason_provider=(
                            _planner_wall_clock_interrupt_provider()
                        ),
                    ),
                    run_label=f"planner.cycle{planning_cycle}.schema-repair",
                )
                repair_text = "\n".join(
                    getattr(repair_result, "agent_messages", None) or []
                )
                repaired = parse_planner_text(repair_text)
                if repaired.error:
                    repair_error = repaired.error
                else:
                    parsed = repaired
                    text = repair_text
                    repair_succeeded = True
            except Exception as exc:  # noqa: BLE001 - original error remains retryable
                repair_error = f"{type(exc).__name__}: {exc}"
            parsed = replace(
                parsed,
                schema_repair_attempted=True,
                schema_repair_succeeded=repair_succeeded,
                schema_repair_original_sha256=original_sha256,
                schema_repair_error=repair_error,
            )
        return parsed

    @staticmethod
    def _build_planner_prompt(
        *,
        continuous_objective: str,
        journal_tail: str,
        planning_cycle: int,
        runtime_change_summary: str = "",
        mission: Any | None = None,
        open_ended: bool = False,
    ) -> str:
        from ..roles.prompts.planner import build_continuous_prompt

        return build_continuous_prompt(
            continuous_objective=continuous_objective,
            journal_tail=journal_tail,
            planning_cycle=planning_cycle,
            runtime_change_summary=runtime_change_summary,
            mission=mission,
            open_ended=open_ended,
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
    tasks_raw = data.get("new_tasks") or []
    new_tasks: list[TaskSpec] = []
    raw_task_count = len(tasks_raw) if isinstance(tasks_raw, list) else 0
    if isinstance(tasks_raw, list):
        for entry in tasks_raw:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title", "")).strip()
            objective = str(entry.get("objective", "")).strip()
            key = str(entry.get("key") or "").strip()
            deps = [
                str(d).strip()
                for d in (entry.get("deps") or [])
                if str(d).strip()
            ]
            if not title or not objective:
                continue
            new_tasks.append(
                TaskSpec(
                    title=title,
                    objective=objective,
                    key=key,
                    deps=deps,
                )
            )
    if project_done and tasks_raw:
        return PlannerVerdict(
            project_done=False,
            reason="planner said project_done=true but returned tasks",
            new_tasks=[],
            raw_text=blob,
            error="planner claimed project_done=true with tasks",
        )
    if not project_done and not new_tasks:
        # Inconsistent: not done but no tasks → retry later, don't mark done.
        if raw_task_count:
            reason = "planner proposed only malformed tasks"
            error = "planner produced no usable tasks"
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
        raw_text=blob,
    )
