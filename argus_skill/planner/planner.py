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
import re
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
_TASK_SCOPES = {TASK_SCOPE_BOUNDED, TASK_SCOPE_FINAL_SUBMISSION}
_WAIT_MODES = {"poll", "event"}
_WAKE_SOURCES = {
    "authorization",
    "subagent_terminal",
    "artifact_revision",
    "manager_stage",
}
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
    impact_score: int = 0  # 0-5; parser accepts only high-value work
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


def _parse_context_refs(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    refs: list[dict[str, str]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        target = str(raw.get("ref") or "").strip()
        if not target:
            continue
        refs.append({
            "kind": str(raw.get("kind") or "artifact").strip() or "artifact",
            "ref": target,
            "why": str(raw.get("why") or "").strip(),
            "content_hash": str(raw.get("content_hash") or "").strip(),
        })
    return refs


def _requires_theorem_proof_contract(objective: str) -> bool:
    """Return whether the operator made theorem proof a hard deliverable.

    This is deliberately narrower than generic ``math`` detection.  Ordinary
    open-problem campaigns may legitimately schedule bounded discovery work;
    the guard activates only when the objective explicitly combines a hard
    success requirement with a theorem/lemma and a complete proof.
    """
    text = " ".join(str(objective or "").lower().split())
    hard = any(
        marker in text
        for marker in (
            "hard success criterion",
            "hard requirement",
            "must actually be proved",
            "must be proved",
            "硬性成功标准",
            "必须证明",
        )
    )
    theorem = bool(
        re.search(r"\b(?:theorem|lemma|proposition|corollary)\b", text)
        or any(marker in text for marker in ("定理", "引理", "命题", "推论"))
    )
    proof = bool(
        re.search(r"\b(?:complete|self-contained|rigorous)\b.{0,80}\bproof\b", text)
        or re.search(r"\bproof\b.{0,80}\b(?:complete|self-contained|rigorous)\b", text)
        or any(marker in text for marker in ("完整证明", "严格证明", "自包含证明"))
    )
    return hard and theorem and proof


def _theorem_proof_task_issue(task: TaskSpec) -> str:
    """Explain why one task cannot satisfy a hard theorem-proof objective."""
    text = " ".join(
        f"{task.title} {task.objective} {task.evidence}".lower().split()
    )
    has_statement = bool(
        re.search(r"\b(?:theorem|lemma|proposition|corollary)\b", text)
        or any(marker in text for marker in ("定理", "引理", "命题", "推论"))
    )
    has_proof = bool(
        re.search(r"\b(?:proof|prove|proving)\b", text)
        or "证明" in text
    )
    has_complete = any(
        marker in text
        for marker in (
            "complete",
            "self-contained",
            "rigorous",
            "完整",
            "自包含",
            "严格",
        )
    )
    missing: list[str] = []
    if not has_statement:
        missing.append("a precisely stated theorem/lemma")
    if not has_proof:
        missing.append("a proof deliverable")
    if not has_complete:
        missing.append("complete/self-contained rigor")
    if missing:
        return "missing " + ", ".join(missing)

    # A theorem-first mission may use these methods internally, but it cannot
    # declare success on a fallback that the operator explicitly excluded.
    acceptance_text = " ".join(task.objective.lower().split())
    excluded_success_patterns = (
        r"feasibility evidence only",
        r"finite (?:verification|computation|enumeration) only",
        r"bounded [^.]{0,100} evidence only",
        r"resource[- ]limited [^.]{0,100} only",
        r"otherwise classify [^.]{0,120} only",
        r"仅(?:作为|算作|提供).{0,30}(?:有限|可行性|枚举|计算)证据",
    )
    for pattern in excluded_success_patterns:
        if re.search(pattern, acceptance_text):
            return "acceptance permits an excluded non-proof-only outcome"
    return ""


def _project_has_theorem_baseline(project_root: Path) -> bool:
    """Whether the project already records a proved theorem to improve upon."""
    ledger = project_root / "research" / "CLAIM_LEDGER.md"
    try:
        text = " ".join(ledger.read_text(encoding="utf-8").lower().split())
    except OSError:
        return False
    return bool(
        re.search(
            r"(?:complete|proved|self-contained)[^|]{0,120}\btheorem\b",
            text,
        )
        or re.search(
            r"\btheorem\b[^|]{0,120}(?:complete|self-contained proof|proved)",
            text,
        )
    )


def _theorem_progression_task_issue(task: TaskSpec) -> str:
    """Require an explicit dominance comparison once a theorem baseline exists."""
    text = " ".join(
        f"{task.title} {task.objective} {task.evidence}".lower().split()
    )
    references_claim_ledger = "claim_ledger" in text or "claim ledger" in text
    references_lemma_graph = "lemma_graph" in text or "lemma graph" in text
    if not (references_claim_ledger and references_lemma_graph):
        return "missing CLAIM_LEDGER/LEMMA_GRAPH baseline comparison"

    strict_progress_patterns = (
        r"strictly strengthen",
        r"strictly improve",
        r"strict strengthening",
        r"sharper .{0,80}(?:theorem|bound|constant)",
        r"(?:improve|lower|reduce|replace).{0,80}(?:bound|constant)",
        r"\bk\s*<\s*\d+",
        r"weaken.{0,60}hypoth",
        r"remove.{0,60}hypoth",
        r"new .{0,40}bridge (?:lemma|theorem)",
        r"(?:close|resolve).{0,60}(?:gap|open node|missing bridge)",
        r"materially refine",
        r"严格(?:加强|改进|推进|优于)",
        r"更强(?:定理|结论|界)",
    )
    if not any(re.search(pattern, text) for pattern in strict_progress_patterns):
        return (
            "missing an explicit strict improvement over the strongest proved "
            "ledger theorem"
        )
    return ""


def _is_guarded_theorem_followup(
    task: TaskSpec,
    *,
    qualifying_keys: set[str],
) -> bool:
    """Allow a stage-closing audit after a qualifying theorem dependency.

    Dependency completion is the proof guard: the backlog cannot run this node
    unless the theorem node reached ``done``. Requiring the audit objective to
    repeat ``complete self-contained proof`` wording made semantically correct
    DAGs fail admission even though the audit consumes, rather than reproves,
    that theorem.
    """
    if (
        not task.stage_closing
        or not task.deps
        or not any(dep in qualifying_keys for dep in task.deps)
    ):
        return False
    text = " ".join(
        f"{task.title} {task.objective} {task.evidence}".lower().split()
    )
    audit_like = any(
        marker in text
        for marker in (
            "mechanism-overlap audit",
            "mechanism overlap audit",
            "overlap audit",
            "novelty audit",
            "audit and close",
            "审计",
        )
    )
    return audit_like


def _hard_objective_task_issues(
    continuous_objective: str,
    tasks: list[TaskSpec],
    *,
    current_stage: str = "solve",
    progression_required: bool = False,
) -> list[str]:
    if not _requires_theorem_proof_contract(continuous_objective):
        return []
    # The contract governs theorem-producing solve work. Scope and review may
    # still need bounded statement/venue/audit closure so the accepted theorem
    # can move through the ordinary staged lifecycle without being forced to
    # prove a second theorem inside bookkeeping.
    if str(current_stage or "").strip().lower() != "solve":
        return []
    basic_issues = [_theorem_proof_task_issue(task) for task in tasks]
    progression_issues = [
        (
            _theorem_progression_task_issue(task)
            if progression_required and not basic_issue
            else ""
        )
        for task, basic_issue in zip(tasks, basic_issues)
    ]
    qualifying_keys = {
        task.key
        for task, basic_issue, progression_issue in zip(
            tasks,
            basic_issues,
            progression_issues,
        )
        if task.key
        and not basic_issue
        and not progression_issue
    }
    issues: list[str] = []
    for task, basic_issue, progression_issue in zip(
        tasks,
        basic_issues,
        progression_issues,
    ):
        guarded_followup = _is_guarded_theorem_followup(
            task,
            qualifying_keys=qualifying_keys,
        )
        issue = basic_issue
        if issue and guarded_followup:
            issue = ""
        if not issue and progression_required:
            issue = progression_issue
            if issue and guarded_followup:
                issue = ""
        if issue:
            issues.append(f"{task.title}: {issue}")
    return issues


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
        active_stage = "solve"
        project_root: Path | None = None
        try:
            from ..skills.harness_overlay import resolve_project_root
            from ..skills.stage_checklists import current_stage

            project_root = resolve_project_root()
            active_stage = current_stage(project_root)
        except Exception:  # noqa: BLE001 - fail closed on the proof contract
            pass
        hard_objective_issues = _hard_objective_task_issues(
            continuous_objective,
            parsed.new_tasks,
            current_stage=active_stage,
            progression_required=(
                project_root is not None
                and _project_has_theorem_baseline(project_root)
            ),
        )
        if hard_objective_issues:
            issue_text = "; ".join(hard_objective_issues[:6])
            return replace(
                parsed,
                project_done=False,
                reason=(
                    "planner tasks violate the operator's hard objective contract; "
                    "re-plan with a theorem statement, complete self-contained proof, "
                    "and an explicit strict comparison to the strongest proved "
                    f"ledger result when one exists: {issue_text}"
                ),
                new_tasks=[],
                checklist_ops=[],
                error=f"hard objective contract violation: {issue_text}",
            )
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
        return parsed

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


def _operator_action_required_for_wait(
    *,
    blocker_fingerprint: str,
    recheck_condition: str,
    waiting_reason: str,
) -> bool:
    """Fail closed when a waiting contract asks to expand operator scope.

    Planner output is model-authored, so the explicit boolean is not enough by
    itself.  Infer operator ownership for common scope-expansion language to
    prevent a later Manager reconciliation call from inventing authorization.
    """
    text = " ".join(
        (blocker_fingerprint, recheck_condition, waiting_reason)
    ).casefold()
    normalized = re.sub(r"[^a-z0-9]+", " ", text)
    operator_terms = ("operator", "human", "user")
    operator_actions = (
        "authoriz",
        "approval",
        "credential",
        "licensed",
        "permission",
        "provide",
        "choose",
        "decision",
    )
    if any(term in normalized for term in operator_terms) and any(
        term in normalized for term in operator_actions
    ):
        return True
    # An open-ended campaign objective is standing authority to choose another
    # mechanism, benchmark, or paper framing inside that objective. Historical
    # code inferred an operator-only blocker from phrases such as "no viable
    # thesis" or "authorization exhausted", which let one NO-GO permanently
    # stop autonomous research. Scope expansion must now be explicit in the
    # structured field; prose about exhausted attempts is never sufficient.
    return False


def _parse_waiting_contract(
    data: dict,
    *,
    waiting_reason: str = "",
) -> WaitingContract | None:
    raw = data.get("waiting_contract")
    if not isinstance(raw, dict):
        return None
    blocker_fingerprint = str(raw.get("blocker_fingerprint") or "").strip()
    recheck_condition = str(raw.get("recheck_condition") or "").strip()
    recheck_token = str(raw.get("recheck_token") or "").strip()
    if not blocker_fingerprint or not recheck_condition or not recheck_token:
        return None
    try:
        recheck_after_seconds = int(raw.get("recheck_after_seconds", 0) or 0)
    except (TypeError, ValueError):
        return None
    recheck_after_seconds = max(0, min(604800, recheck_after_seconds))
    wait_mode = str(raw.get("wait_mode") or "poll").strip().lower()
    wake_on = tuple(dict.fromkeys(
        str(value or "").strip().lower()
        for value in (raw.get("wake_on") or [])
        if str(value or "").strip().lower() in _WAKE_SOURCES
    ))
    if wait_mode not in _WAIT_MODES or (wait_mode == "event" and not wake_on):
        wait_mode = "poll"
        wake_on = ()
    watched_paths: list[str] = []
    for value in (raw.get("watched_paths") or [])[:16]:
        candidate = str(value or "").strip().replace("\\", "/")
        parts = Path(candidate).parts
        if not candidate or candidate.startswith("/") or ".." in parts:
            continue
        if candidate.startswith("./"):
            candidate = candidate[2:]
        watched_paths.append(candidate[:500])
    try:
        expires_at = max(0.0, float(raw.get("expires_at", 0.0) or 0.0))
    except (TypeError, ValueError):
        expires_at = 0.0
    explicit_operator_action = _parse_json_bool(
        raw.get("operator_action_required", False),
        False,
    )
    return WaitingContract(
        blocker_fingerprint=blocker_fingerprint[:200],
        recheck_condition=recheck_condition[:1600],
        recheck_token=recheck_token[:200],
        stage_reconciliation_required=_parse_json_bool(
            raw.get("stage_reconciliation_required", False),
            False,
        ),
        allow_verification_probe=_parse_json_bool(
            raw.get("allow_verification_probe", False),
            False,
        ),
        recheck_after_seconds=recheck_after_seconds,
        wait_mode=wait_mode,
        wake_on=wake_on,
        watched_paths=tuple(dict.fromkeys(watched_paths)),
        expires_at=expires_at,
        operator_action_required=(
            explicit_operator_action
            or _operator_action_required_for_wait(
                blocker_fingerprint=blocker_fingerprint,
                recheck_condition=recheck_condition,
                waiting_reason=waiting_reason,
            )
        ),
    )


_VALID_CHECKLIST_OPS = frozenset({"seed", "add", "modify", "remove"})


def _parse_checklist_ops(data: dict) -> list[dict]:
    """Parse the Planner's per-stage ``checklist_ops`` (fail-soft).

    Drops malformed entries and any unknown op; a non-list value yields ``[]`` so
    the loop applies nothing. ``apply_checklist_ops`` enforces the protected-floor
    policy and bounds — this only normalizes shape."""
    raw = data.get("checklist_ops")
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for entry in raw:
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
    waiting = _parse_json_bool(data.get("waiting", False), False)
    waiting_reason = str(data.get("waiting_reason", "")).strip() or reason
    waiting_contract = _parse_waiting_contract(
        data,
        waiting_reason=waiting_reason,
    )
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
            acceptance_check = str(
                entry.get("acceptance_check") or evidence
            ).strip()
            non_goals = [
                str(item).strip()
                for item in (entry.get("non_goals") or [])
                if str(item).strip()
            ]
            context_refs = _parse_context_refs(entry.get("context_refs"))
            scope = _parse_task_scope(entry.get("scope"))
            stage_closing = _parse_json_bool(
                entry.get("stage_closing", False),
                False,
            )
            # Optional DAG fields; back-compat: a flat task simply omits them.
            key = str(entry.get("key") or "").strip()
            deps = [
                str(d).strip()
                for d in (entry.get("deps") or [])
                if str(d).strip()
            ]
            authorization_id = str(entry.get("authorization_id") or "").strip()
            authorization_action = str(
                entry.get("authorization_action") or ""
            ).strip().lower()
            if (
                not title
                or not objective
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
                    acceptance_check=acceptance_check,
                    non_goals=non_goals,
                    context_refs=context_refs,
                    scope=scope,
                    stage_closing=stage_closing,
                    key=key,
                    deps=deps,
                    authorization_id=authorization_id,
                    authorization_action=authorization_action,
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
    # Explicit, intentional idle: the project is correctly waiting on a live
    # external job and the planner found no genuinely new high-impact work.
    # Honored ONLY when not also claiming done and no concrete tasks
    # were accepted — real tasks always win over waiting. This is NOT an error
    # (it bypasses the "no concrete tasks" retry/churn path below).
    if waiting and not project_done and not new_tasks:
        if not waiting_reason:
            waiting_reason = "awaiting a live external job; no new high-impact work"
        if waiting_contract is None:
            return PlannerVerdict(
                project_done=False,
                reason="planner waiting verdict omitted a valid waiting contract",
                new_tasks=[],
                raw_text=blob,
                error="waiting verdict requires waiting_contract",
                checklist_ops=checklist_ops,
            )
        return PlannerVerdict(
            project_done=False,
            reason=waiting_reason,
            new_tasks=[],
            raw_text=blob,
            waiting=True,
            waiting_reason=waiting_reason,
            waiting_contract=waiting_contract,
            checklist_ops=checklist_ops,
        )
    if not project_done and not new_tasks:
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
        raw_text=blob,
        checklist_ops=checklist_ops,
    )
