"""SkillLoop — the integrated matcher → supervised-engineer flow.

This is the new code that argus-skill exists to deliver. It composes:

  * ``SkillStore`` (vendored from skill-agent): horizontal skill cache.
  * ``SupervisedEngineer`` (new, with ``Reviewer`` vendored from ArgusBot):
    vertical round-loop that accepts decisive Engineer self-verification for
    bounded work or otherwise supervises until the Reviewer is satisfied.

Skill and wiki memory normally use independent review. For a bounded mission,
the Engineer may explicitly self-verify and waive Reviewer; if it also identifies
durable skill learning, the same Engineer thread is resumed once to author the
create/update candidate, which still passes through SkillRouter safeguards.

End-to-end shape:

    task → matcher/Scientist → engineer round-loop (engineer → self-review|reviewer)
            outcome → record skill use and preserve validated memory edits
            continue → inject next_action, next round
            blocked → stop with reason; direct memory edits remain persisted
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .core.event_catalog import EventType
from .core.models import LoopOutcome, RoundRecord, RunnerOptions
from .core.ports import RunnerBackend
from .core.run_gateway import run_exec as gateway_run_exec
from .core.secret_guard import known_secret_values, redact_secrets_text
from .core.stop_kinds import stop_kind_is_recoverable
from .core.task_contract import EFFECTIVE_TASK_CONTRACT
from .engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from .engineer.self_review import (
    EngineerCompletionDecision,
    EngineerSkillMaintenanceOutcome,
)
from .reviewer import Reviewer, ReviewerConfig
from .skills.missions import EngineerMission
from .skills.role_match import render_skill_playbook
from .skills.skill_router import SkillRouter
from .skills.store import Skill, SkillStore

log = logging.getLogger(__name__)

# Reviewed ineffective uses are retained as evidence for later Reviewer-authored
# update/archive decisions. External/economic aborts remain neutral.
_INEFFECTIVE_SKILL_STATUSES: frozenset[str] = frozenset({"no_progress", "max_rounds"})
_ADAPTATION_FAILURE_CAUSES: frozenset[str] = frozenset({
    "method_failure",
    "skill_gap",
})


def _reviewer_engineer_skill_pointer(
    skill: Skill,
    rendered_skill: str,
) -> str:
    """Compact reference to the Engineer's matched skill for L2 review.

    The Reviewer already receives the objective and authoritative stage checklist.
    Reinjecting the full Engineer playbook duplicates thousands of tokens on every
    Reviewer tool turn. Keep provenance and an on-demand path without prescribing
    a second read of the whole skill.
    """
    description = " ".join(str(skill.description or "").split())[:80]
    path = str(skill.path or "").replace("`", "'")
    digest = hashlib.sha256(rendered_skill.encode("utf-8")).hexdigest()[:16]
    lines = [
        "## Engineer skill pointer (on demand)",
        f"- Used by Engineer: `{skill.name}`",
        f"- Expected version/hash: `{skill.version}` / `sha256:{digest}`",
    ]
    if description:
        lines.append(f"- Purpose: {description}")
    if path:
        lines.append(f"- Source: `{path}`")
    lines.append(
        "- Do not read it by default. If needed for a material claim, verify this "
        "hash first; current objective/artifacts remain authoritative."
    )
    return "\n".join(lines)


# Generic words that distinguish neither software tasks nor reusable skills.
# In particular, project/framework names and playbook boilerplate must not make
# the oldest, most-used skill look universally relevant.
_TRANSFER_STOPWORDS: frozenset[str] = frozenset({
    "add", "and", "application", "change", "code", "complete", "current",
    "existing", "feature", "fix", "flipt", "for", "from", "implementation",
    "instance", "into", "new", "not", "one", "problem", "production",
    "project", "repair", "repository", "request", "software", "statement",
    "support", "task", "tests", "that", "the", "this", "through", "use",
    "used", "using", "when", "where", "with", "wire", "wiring",
})
_TRANSFER_TOKEN_ALIASES: dict[str, str] = {
    "cached": "cache",
    "caches": "cache",
    "caching": "cache",
    "config": "configuration",
    "configured": "configuration",
    "configuring": "configuration",
    "evaluations": "evaluation",
    "initialized": "initialize",
    "initialization": "initialize",
    "initializing": "initialize",
    "middlewares": "middleware",
}


def _env_float_setting(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int_setting(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _transfer_terms(text: object) -> list[str]:
    """Return normalized, discriminative terms for cheap Skill retrieval."""
    terms: list[str] = []
    for raw in re.findall(r"[a-z][a-z0-9_]{2,}", str(text or "").lower()):
        term = _TRANSFER_TOKEN_ALIASES.get(raw, raw)
        if term in _TRANSFER_STOPWORDS:
            continue
        terms.append(term)
    return terms


def _nearest_transfer_scores(
    task: str,
    summaries: list[dict[str, Any]],
) -> list[float]:
    """Rank reusable Skills from stable semantic fields only.

    ``task_history`` is intentionally excluded. It records prior uses, including
    weak nearest-skill fallbacks, so feeding it back into retrieval creates a
    self-reinforcing failure mode where an early generic Skill gradually appears
    relevant to every later task.
    """
    task_counts = Counter(_transfer_terms(task))
    if not summaries:
        return []

    docs: list[dict[str, float]] = []
    for summary in summaries:
        weights: dict[str, float] = {}
        for key, field_weight in (
            ("name", 4.0),
            ("description", 2.0),
            ("category", 1.0),
        ):
            counts = Counter(_transfer_terms(summary.get(key)))
            for term, count in counts.items():
                weights[term] = weights.get(term, 0.0) + field_weight * (
                    1.0 + math.log(float(count))
                )
        docs.append(weights)

    document_frequency = Counter(
        term for weights in docs for term in weights
    )
    n_docs = float(len(docs))

    def idf(term: str) -> float:
        return math.log((n_docs + 1.0) / (document_frequency[term] + 1.0)) + 1.0

    task_vector = {
        term: (1.0 + math.log(float(count))) * idf(term)
        for term, count in task_counts.items()
    }
    task_norm = math.sqrt(sum(value * value for value in task_vector.values()))
    scores: list[float] = []
    for weights in docs:
        doc_vector = {term: value * idf(term) for term, value in weights.items()}
        doc_norm = math.sqrt(sum(value * value for value in doc_vector.values()))
        if not task_norm or not doc_norm:
            scores.append(0.0)
            continue
        dot = sum(
            task_vector.get(term, 0.0) * value
            for term, value in doc_vector.items()
        )
        scores.append(dot / (task_norm * doc_norm))
    return scores


@dataclass
class SkillLoopConfig:
    """All knobs for one SkillLoop.run invocation, in one place."""
    engineer_model: str | None = "gpt-5.5"
    reviewer_model: str | None = None  # default: same as engineer (cheap)
    matcher_model: str | None = None   # default: same as engineer
    # Direct/bounded work starts at high. A Reviewer-requested second round
    # escalates to ``engineer_reasoning_effort`` (xhigh by default). Staged and
    # paper missions retain xhigh from round one.
    engineer_initial_reasoning_effort: str | None = "high"
    engineer_reasoning_effort: str | None = "xhigh"
    reviewer_reasoning_effort: str = "high"
    matcher_reasoning_effort: str | None = "low"
    # Cheap task-conditioning pass over the closest matched skill. This is a
    # single no-tool input/output request, not a Scientist or execution agent.
    skill_adapter_model: str | None = None
    skill_adapter_reasoning_effort: str = "low"
    skill_adapter_enabled: bool = True
    skill_adapter_max_bullets: int = 8
    nearest_transfer_min_score: float = field(
        default_factory=lambda: _env_float_setting(
            "ARGUS_SKILL_NEAREST_TRANSFER_MIN_SCORE", 0.12
        )
    )
    nearest_transfer_max_bullets: int = 4
    # Evaluation/continuous-learning mode: completed tasks are asked to retain
    # only durable reusable learning. ``force_post_task_learning`` restores the
    # legacy every-task create/update contract for controlled ablations.
    require_post_task_learning: bool = field(
        default_factory=lambda: (
            os.environ.get("ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING", "0")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
        )
    )
    max_rounds: int = 500
    no_progress_threshold: int = 2
    # Anti-livelock escalation thresholds threaded into SupervisedConfig: at
    # ``soft_round_limit`` the reviewer is told to escalate an unresolvable
    # external blocker to ``blocked``; at ``hard_escalate_rounds`` the round loop
    # force-ends as ``blocked`` so the planner re-plans. 0 disables either.
    soft_round_limit: int = 12
    hard_escalate_rounds: int = 24
    backend_failure_threshold: int = 2
    backend_failure_backoff_seconds: float = 15.0
    # Repeated reviewer rejection is evidence that a matched playbook is not
    # enough. Ask the Scientist for a genuinely different strategy every N
    # non-terminal rounds; 0 disables.
    adaptive_skill_interval: int = 4
    # Restart-safe Scientist adaptation after Reviewer-classified method/skill
    # failures. Bounded calls prevent rejection loops from becoming unbounded
    # strategy generation.
    adaptive_rejection_threshold: int = 2
    adaptive_skill_max_triggers: int = 2
    adaptive_skill_max_cost_usd: float = 5.0
    # Legacy proposal compatibility only. Current Reviewers edit the injected
    # project skill path directly and their output schema has no skill_ops.
    skill_ops_enabled: bool = False
    # Legacy proposal compatibility only. Current Reviewers edit injected wiki
    # paths directly; deterministic post-mission hooks still maintain indexes.
    wiki_ops_enabled: bool = False
    # Bootstrap one project wiki before the first mission so every vertical can
    # use reviewer-owned wiki_ops without a separate learning-only setup step.
    # Library callers remain opt-in; the daemon runtime enables this by default.
    auto_init_wiki: bool = False
    # Automatic library housekeeping (explicit opt-in). Finds near-duplicate
    # skills/wiki-pages accumulated across tasks or concurrent writers and
    # merges each cluster down to one representative. LLM grouping sees compact
    # summaries only; a no-op-safe,
    # REVERSIBLE archive/retire move, never a hard delete; a protected/
    # governing skill is never a merge candidate (self-governance floor).
    # Off by default; the daemon enables it.
    auto_compact_enabled: bool = False
    full_auto: bool = True
    skip_git_repo_check: bool = True
    dangerous_yolo: bool = False
    extra_args: list[str] | None = None
    session_id: str | None = None
    # Engineer-authored review waivers are explicit and fail closed: no valid
    # marker or no verbatim verification means the ordinary Reviewer still runs.
    engineer_self_review_enabled: bool = field(
        default_factory=lambda: (
            os.environ.get("ARGUS_SKILL_ENGINEER_SELF_REVIEW", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        )
    )
    # If a self-approved Engineer requests skill create/update, resume that same
    # provider thread once and apply its candidate through SkillRouter.
    engineer_skill_maintenance_enabled: bool = field(
        default_factory=lambda: (
            os.environ.get(
                "ARGUS_SKILL_ENGINEER_SKILL_MAINTENANCE",
                "1",
            ).strip().lower()
            not in {"0", "false", "no", "off"}
        )
    )
    skill_maintenance_reasoning_effort: str = field(
        default_factory=lambda: os.environ.get(
            "ARGUS_SKILL_MAINTENANCE_REASONING_EFFORT", "low"
        )
    )
    # ``require_post_task_learning`` asks for selective durable learning. This
    # stronger compatibility flag restores the legacy every-task create/update
    # requirement for controlled evaluations only.
    force_post_task_learning: bool = field(
        default_factory=lambda: (
            os.environ.get("ARGUS_SKILL_FORCE_POST_TASK_LEARNING", "0")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
        )
    )
    engineer_file_read_budget: int = field(
        default_factory=lambda: _env_int_setting(
            "ARGUS_SKILL_ENGINEER_FILE_READ_BUDGET", 12
        )
    )
    engineer_test_run_budget: int = field(
        default_factory=lambda: _env_int_setting(
            "ARGUS_SKILL_ENGINEER_TEST_RUN_BUDGET", 3
        )
    )
    # Manager-selected execution topology. Every mode still uses skill/wiki.
    workflow_mode: str = "staged"
    # Explicit signal that this mission is a long-horizon academic-paper /
    # submission task. When True the engineer prompt carries the
    # long-horizon paper execution contract. Replaces the old keyword-based
    # objective sniffing; callers (e.g. the life runner) set it explicitly.
    paper_mission: bool = False
    # Ordinary Markdown file edited directly by Engineer and Reviewer as the
    # shared baton between fresh per-round sessions. None disables it.
    checkpoint_path: Path | None = None
    # Absolute path to this project's engineer execution log
    # (``<life_dir>/events.jsonl``), threaded down to SupervisedConfig so the
    # reviewer can grep HOW the engineer produced its result (process-correctness
    # audit). Empty = legacy behaviour (no audit section in the reviewer prompt);
    # the life runner fills it from the per-project state dir.
    engineer_log_path: str = ""
    # Campaign lifetime metadata threaded from the daemon's LifeWorkerConfig via
    # the argparse namespace so _SkillLoopRunner.execute can forward them to
    # _decide_stage_transition.  open_ended=True tells the Manager stage hook to
    # skip final_stage_completion_decision (which would otherwise overwrite the
    # Manager's own structured rollback verdict with a bounded completion).
    open_ended: bool = False
    continuous_objective: str = ""

    def resolved_reviewer_model(self) -> str:
        return self.reviewer_model or self.engineer_model

    def resolved_matcher_model(self) -> str:
        """Resolve the skill matcher model with env override.

        Precedence (highest first):
          1. ``ARGUS_SKILL_MATCHER_MODEL`` env var — operator override.
             Set to a cheap router (e.g. ``gpt-4o-mini``, ``haiku-3.5``)
             to slash selection cost: at our N=50 a single matcher call
             is ~180k input tokens, ~80% cheaper on gpt-4o-mini than on
             gpt-5.4 with negligible accuracy loss.
          2. ``matcher_model`` field (constructor / config).
          3. ``engineer_model`` fallback — backwards-compatible default.
        """
        import os
        env = os.environ.get("ARGUS_SKILL_MATCHER_MODEL", "").strip()
        if env:
            return env
        return self.matcher_model or self.engineer_model

    def resolved_skill_adapter_model(self) -> str:
        env = os.environ.get("ARGUS_SKILL_ADAPTER_MODEL", "").strip()
        return env or self.skill_adapter_model or self.engineer_model or ""

    def resolved_initial_engineer_effort(self) -> str | None:
        if self.workflow_mode != "direct" or self.paper_mission:
            return self.engineer_reasoning_effort
        env = os.environ.get(
            "ARGUS_SKILL_ENGINEER_INITIAL_REASONING_EFFORT", ""
        ).strip()
        return env or self.engineer_initial_reasoning_effort


class SkillLoop:
    """High-level entry point: ``loop.run("task description")``.

    Two injectable backends — typically the same in production (one codex
    CLI), but separable so tests can mock individually:

      * ``engineer_runner``  — for execution and skill distillation.
      * ``reviewer_runner``  — for the per-round verdict.

    There is no separate "author" backend: skill distillation reuses the
    engineer backend (and the unified ``gpt-5.5`` route). Pass the same
    backend twice if you only have one.
    """

    def __init__(
        self,
        *,
        skills_dir: Path,
        engineer_runner: RunnerBackend,
        reviewer_runner: RunnerBackend | None = None,
        config: SkillLoopConfig | None = None,
        skill_store: Any | None = None,
        on_event: Callable[[dict], None] | None = None,
        extra_guidance_provider: Callable[[], list[str]] | None = None,
    ) -> None:
        self.config = config or SkillLoopConfig()
        self.skills_dir = Path(skills_dir)
        self.engineer_runner = engineer_runner
        self.reviewer_runner = reviewer_runner or engineer_runner
        self.on_event = on_event
        # Optional callable consulted at the start of each engineer round.
        # Returns a list of additional guidance strings to append to the
        # prompt (used by the daemon to honour /inject between rounds).
        self.extra_guidance_provider = extra_guidance_provider

        self.skill_store = skill_store or SkillStore(
            self.skills_dir,
            runner=engineer_runner,
            matcher_model=self.config.resolved_matcher_model(),
            matcher_reasoning_effort=self.config.matcher_reasoning_effort,
        )
        self.engineer_mission = EngineerMission(
            self.skill_store, on_event=self.on_event
        )
        self.reviewer = Reviewer(self.reviewer_runner, skill_store=self.skill_store)
        # The single front door to the skill library: selection (delegated to
        # the role matcher) plus structurally-safe CRUD. New versions are active
        # immediately; the Reviewer uses real trajectories to update/archive,
        # while protected skills retain a mechanical self-governance floor.
        self.skill_router = SkillRouter(
            skill_store=self.skill_store,
            matcher=self.engineer_mission,
        )
        self.supervised = SupervisedEngineer(
            engineer_runner=engineer_runner,
            reviewer=self.reviewer,
            engineer_config=EngineerConfig(
                model=self.config.engineer_model,
                reasoning_effort=self.config.engineer_reasoning_effort,
                initial_reasoning_effort=(
                    self.config.resolved_initial_engineer_effort()
                ),
                extra_args=self.config.extra_args,
                full_auto=self.config.full_auto,
                skip_git_repo_check=self.config.skip_git_repo_check,
                dangerous_yolo=self.config.dangerous_yolo,
            ),
            reviewer_config=ReviewerConfig(
                model=self.config.resolved_reviewer_model(),
                reasoning_effort=self.config.reviewer_reasoning_effort,
                extra_args=self.config.extra_args or [],
                full_auto=self.config.full_auto,
                skip_git_repo_check=self.config.skip_git_repo_check,
                dangerous_yolo=self.config.dangerous_yolo,
            ),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, task: str, *, workdir: Path | None = None, seed_thread_id: str | None = None,
            objective_for_skill: str | None = None,
            original_objective: str | None = None,
            scope: str = "", per_mission_budget: Any | None = None) -> LoopOutcome:
        """Run one mission end-to-end.

        ``task`` is the *full* prompt the engineer sees (typically a long
        string with prelude, identity card, and live objective). It is
        the right thing to feed to the engineer because round prompts are
        meant to carry full context.

        ``objective_for_skill`` is the *clean* operator objective, with
        no prelude / boilerplate / identity-card prefix. It is what the
        skill matcher and ``task_history`` should see —
        otherwise we end up indexing skills under "### Memory context"
        boilerplate (literally happened, see commit history).
        Falls back to ``task`` when not supplied for back-compat.
        """
        workdir = Path(workdir) if workdir else Path.cwd()
        run_id = self.config.session_id or f"run-{uuid.uuid4().hex}"
        from .skills.adaptation import (
            adaptation_state_path,
            append_method_ledger,
            load_adaptation_state,
            save_adaptation_state,
        )
        from .skills.vertical_select import resolve_vertical
        from .verticals._base import load_vertical, vertical_role_banner

        active_vertical = resolve_vertical(workdir)
        vertical_module = load_vertical(active_vertical, project_root=workdir)
        engineer_role_banner = vertical_role_banner(vertical_module, "engineer")
        scientist_create_banner = vertical_role_banner(
            vertical_module,
            "scientist_create",
        )
        scientist_adaptation_banner = vertical_role_banner(
            vertical_module,
            "scientist",
        )
        if self.config.wiki_ops_enabled:
            from .wiki.lifecycle import ensure_project_wiki

            ensure_project_wiki(
                workdir,
                enabled=self.config.auto_init_wiki,
                on_event=self.on_event,
            )
        skill_task = (objective_for_skill or task).strip() or task
        request_anchor = (original_objective or objective_for_skill or task).strip() or task
        self._emit({
            "type": EventType.LOOP_START,
            "text": f"task: {skill_task[:120]}",
        })

        # Venue selection/format research must happen BEFORE skill matching. If a
        # missing/non-built-in venue is researched after matcher exclusion, the
        # same mission still hides the newly relevant venue-specific skills.
        if os.environ.get("ARGUS_SKILL_VENUE_RESEARCH", "1").strip().lower() not in (
            "0", "false", "no", "off",
        ):
            try:
                from .skills.stage_checklists import current_stage as _vr_stage
                from .skills.venue_research import (
                    needs_venue_research,
                    research_venue_profile,
                )
                from .skills.vertical_select import _persisted_vertical as _vr_vert

                if (
                    self.config.paper_mission
                    and (_vr_vert(workdir) or "research") == "research"
                    and (_vr_stage(workdir) or "").strip().lower()
                    in {"research", "plan", "benchmark", "run", "analysis"}
                    and needs_venue_research(workdir)
                ):
                    self._emit({
                        "type": "venue.research.started",
                        "text": "codex live web-search: selecting/researching target venue",
                    })
                    _ok = research_venue_profile(
                        self.engineer_runner,
                        workdir,
                        model=self.config.engineer_model,
                    )
                    self._emit({
                        "type": "venue.research.completed",
                        "text": (
                            "built research/VENUE_PROFILE.json"
                            if _ok else
                            "venue research produced no profile (venue remains unresolved)"
                        ),
                        "ok": _ok,
                    })
            except Exception:  # noqa: BLE001 — venue research never blocks the loop
                log.debug("venue-research hook skipped", exc_info=True)

        # Step 1: matcher (role mission — shared scaffold across all roles).
        # Suppress the other venue's paper skills so an AAAI project never
        # matches the EMNLP drafting/preflight/router/review skills (and the
        # AAAI siblings never dilute EMNLP matching). Resolution comes from an
        # explicit env/local/PIPELINE_STATE venue; unresolved projects exclude
        # all venue-specific skills rather than defaulting to EMNLP.
        from .skills.venue_profiles import venue_excluded_skill_files

        match = self.skill_router.select(
            skill_task,
            extra_exclude=venue_excluded_skill_files(workdir),
            # Every formal Argus workflow exercises Skill matching, including
            # the initial empty-library task used to bootstrap self-evolution.
            force_empty_match=True,
        )
        matcher_tokens = match.input_tokens + match.output_tokens
        matcher_input_tokens = match.input_tokens
        matcher_cached_input_tokens = match.cached_input_tokens
        matcher_output_tokens = match.output_tokens
        matcher_premium_requests = match.premium_requests
        # Own-role playbooks drive distill/writeback; cross-role references
        # are read-only context and never written back to.
        primary_skills: list[Skill] = list(match.primary_skills)
        reference_skills: list[Skill] = list(match.reference_skills)
        skill: Skill | None = match.primary
        strict_skill_hit = skill is not None
        # Reuse this one matcher result for Reviewer context too. Engineer-role
        # references are Reviewer-owned skills; the Engineer's own strict hit
        # becomes read-only context for Reviewer. This avoids a second matcher
        # call before every review round.
        reviewer_skill_block = render_skill_playbook(
            self.skill_store,
            reference_skills[:1],
            [],
        )
        if strict_skill_hit and skill is not None:
            pointer = _reviewer_engineer_skill_pointer(
                skill,
                self.skill_store.render_skill(skill),
            )
            reviewer_skill_block = (
                f"{reviewer_skill_block}\n\n{pointer}"
                if reviewer_skill_block
                else pointer
            )
        nearest_transfer_fallback = False
        low_confidence_transfer_hint = ""
        skill_distilled = False
        distill_result = None

        # Scientist tool on miss: author one reusable playbook, persist it in the
        # project layer immediately, and inject that exact version into this mission.
        if skill is None and not self.config.engineer_skill_maintenance_enabled:
            try:
                from .skills.scientist import SkillScientist

                self._emit({
                    "type": EventType.SKILL_SCIENTIST_STARTED,
                    "text": "no high-fit skill; asking Scientist to distill a reusable skill",
                })
                scientist = SkillScientist(
                    self.engineer_runner,
                    model=self.config.engineer_model,
                    reasoning_effort=self.config.engineer_reasoning_effort,
                    role_banner=scientist_create_banner,
                )
                raw_skill = scientist.distill(skill_task)
                distill_result = scientist.last_result
                if raw_skill:
                    distilled = self.skill_router.create_from_scientist(
                        raw_skill,
                        task=skill_task,
                        on_event=self._emit,
                    )
                    if distilled is not None:
                        primary_skills = [distilled]
                        skill = distilled
                        skill_name = distilled.name
                        skill_distilled = True
                        self._emit({
                            "type": EventType.SKILL_SCIENTIST_CREATED,
                            "skill_id": distilled.skill_id,
                            "name": distilled.name,
                            "version": distilled.version,
                            "path": str(distilled.path or ""),
                            "text": f"Scientist created active skill {distilled.name}",
                        })
            except Exception:  # noqa: BLE001
                log.debug("Scientist skill generation skipped", exc_info=True)

        if skill is None and self.config.require_post_task_learning:
            loaded: list[tuple[dict[str, Any], Skill]] = []
            for summary in self.skill_store.list_summaries():
                path = str(summary.get("path") or "").strip()
                if not path:
                    continue
                try:
                    candidate = self.skill_store.load(path)
                except Exception:  # noqa: BLE001 - one stale skill must not block transfer
                    continue
                if self.skill_store.role_for(candidate) not in {"engineer", "general"}:
                    continue
                loaded.append((summary, candidate))
            scores = _nearest_transfer_scores(
                skill_task,
                [summary for summary, _candidate in loaded],
            )
            candidates = [
                (score, candidate.name.casefold(), candidate)
                for score, (_summary, candidate) in zip(scores, loaded)
            ]
            if candidates:
                candidates.sort(key=lambda item: (-item[0], item[1]))
                score, _name, candidate = candidates[0]
                candidate_scores = [
                    {"name": item.name, "score": round(candidate_score, 6)}
                    for candidate_score, _candidate_name, item in candidates[:3]
                ]
                if score >= max(0.0, self.config.nearest_transfer_min_score):
                    skill = candidate
                    primary_skills = [skill]
                    nearest_transfer_fallback = True
                    text = (
                        f"no high-fit skill; transfer fallback selected nearest "
                        f"`{skill.name}` (static semantic score={score:.3f})"
                    )
                else:
                    low_confidence_transfer_hint = (
                        "## Low-confidence transfer hint\n"
                        f"Nearest prior skill: {candidate.name} "
                        f"(score {score:.3f}, below reuse threshold).\n"
                        "- Treat this only as an analogy, not a task playbook.\n"
                        "- Reuse project conventions and verification discipline only.\n"
                        "- Derive implementation details from the current repository.\n"
                        "- Ignore domain-specific steps that do not independently fit."
                    )
                    text = (
                        f"no high-fit skill; nearest `{candidate.name}` scored "
                        f"{score:.3f} below threshold; injecting compact hint only"
                    )
                self._emit({
                    "type": "match.info",
                    "selection_method": "static-semantic-tfidf",
                    "score": round(score, 6),
                    "candidate_scores": candidate_scores,
                    "text": text,
                })

        skill_text = render_skill_playbook(
            self.skill_store, primary_skills, reference_skills
        )
        if low_confidence_transfer_hint:
            skill_text = (
                low_confidence_transfer_hint
                + (f"\n\n{skill_text}" if skill_text else "")
            )
        skill_name = skill.name if skill else None
        learning_target_name = skill.name if strict_skill_hit and skill is not None else ""
        if skill is not None and self.config.skill_adapter_enabled:
            source_skill_text = self.skill_store.render_skill(skill, full=True)
            max_bullets = (
                self.config.nearest_transfer_max_bullets
                if nearest_transfer_fallback
                else self.config.skill_adapter_max_bullets
            )
            adapter_prompt = (
                "You are a low-cost Skill Adapter. Rewrite the reusable skill below "
                "into a concise guideline for the CURRENT task. Do not solve the "
                "task, use tools, inspect files, create artifacts, or discuss the "
                "adaptation process. Preserve the skill's valid mechanism, replace "
                "generic placeholders with task-relevant abstractions, remove "
                "irrelevant steps, and output only the adapted guideline in at most "
                f"{max(1, int(max_bullets))} short bullets.\n\n"
                f"## Current task\n{skill_task}\n\n"
                f"## Closest reusable skill: {skill.name}\n{source_skill_text}"
            )
            self._emit({
                "type": EventType.SKILL_TRANSFER_STARTED,
                "skill_name": skill.name,
                "model": self.config.resolved_skill_adapter_model(),
                "reasoning_effort": self.config.skill_adapter_reasoning_effort,
                "text": f"adapting matched skill {skill.name} to current task",
            })
            adapter_extra_args = list(self.config.extra_args or [])
            adapter_sandbox: str | None = "read-only"
            if str(getattr(self.engineer_runner, "_backend_name", "") or "").lower() == "copilot":
                adapter_sandbox = None
                adapter_extra_args.extend([
                    "--no-custom-instructions",
                    "--disable-builtin-mcps",
                    "--available-tools=",
                ])
            try:
                transfer_result = gateway_run_exec(
                    self.engineer_runner,
                    prompt=adapter_prompt,
                    options=RunnerOptions(
                        model=self.config.resolved_skill_adapter_model() or None,
                        reasoning_effort=self.config.skill_adapter_reasoning_effort,
                        extra_args=adapter_extra_args or None,
                        sandbox_mode=adapter_sandbox,
                        skip_git_repo_check=True,
                        full_auto=False,
                        dangerous_yolo=False,
                    ),
                    run_label="skill-adapter",
                    resume_thread_id=None,
                )
                adapted = str(
                    getattr(transfer_result, "last_agent_message", "") or ""
                ).strip()
                if (
                    int(getattr(transfer_result, "exit_code", 0) or 0) == 0
                    and not getattr(transfer_result, "fatal_error", None)
                    and adapted
                ):
                    skill_text = (
                        "## Task-adapted skill guideline\n"
                        f"Source skill: {skill.name}\n\n{adapted}"
                    )
                    distill_result = transfer_result
                    self._emit({
                        "type": EventType.SKILL_TRANSFER_COMPLETED,
                        "skill_name": skill.name,
                        "success": True,
                        "text": f"adapted skill {skill.name} for current task",
                    })
                else:
                    self._emit({
                        "type": EventType.SKILL_TRANSFER_COMPLETED,
                        "skill_name": skill.name,
                        "success": False,
                        "text": "skill adapter failed; using original skill",
                    })
            except Exception:  # noqa: BLE001 - original skill remains a safe fallback
                log.debug("skill adapter failed", exc_info=True)
                self._emit({
                    "type": EventType.SKILL_TRANSFER_COMPLETED,
                    "skill_name": skill.name,
                    "success": False,
                    "text": "skill adapter raised; using original skill",
                })
        adaptation_file: Path | None = None
        adaptation_disabled = False
        adaptation_state: dict[str, Any] = {
            "trigger_count": 0,
            "spent_usd": 0.0,
            "rejection_streak": [],
            "method_records": [],
        }
        if (
            self.config.session_id
            and self.config.checkpoint_path is not None
        ):
            adaptation_file = adaptation_state_path(
                self.config.checkpoint_path,
                run_id,
            )
            try:
                adaptation_state = load_adaptation_state(adaptation_file, run_id)
            except (OSError, ValueError):
                adaptation_disabled = True
                log.warning(
                    "Skill adaptation state is unreadable; Scientist adaptation "
                    "disabled for mission %s",
                    run_id,
                    exc_info=True,
                )
        adaptation_triggers = int(adaptation_state["trigger_count"])
        adaptation_spent = float(adaptation_state["spent_usd"])
        rejection_streak: list[dict[str, Any]] = list(
            adaptation_state["rejection_streak"]
        )
        method_records: list[dict[str, Any]] = list(
            adaptation_state["method_records"]
        )

        def persist_adaptation_state() -> None:
            if adaptation_file is None or adaptation_disabled:
                return
            save_adaptation_state(
                adaptation_file,
                run_id,
                trigger_count=adaptation_triggers,
                spent_usd=adaptation_spent,
                rejection_streak=rejection_streak,
                method_records=method_records,
            )

        def adapt_after_rejections(rounds: list) -> str:
            nonlocal skill, skill_text, skill_name, skill_distilled
            nonlocal distill_result, adaptation_triggers
            nonlocal adaptation_spent
            if self.config.engineer_skill_maintenance_enabled:
                return ""
            persistent_adaptation = adaptation_file is not None
            if persistent_adaptation:
                if not rounds or adaptation_disabled:
                    return ""
                interval = max(
                    1,
                    int(self.config.adaptive_rejection_threshold or 1),
                )
                latest = rounds[-1]
                qualifies = (
                    latest.review.status == "continue"
                    and not latest.review.backend_unavailable
                    and latest.review.failure_cause
                    in _ADAPTATION_FAILURE_CAUSES
                    and not bool(latest.fatal_error)
                )
                if qualifies:
                    rejection_streak.append({
                        "round_index": latest.round_index,
                        "reason": latest.review.reason,
                        "next_action": latest.review.next_action,
                    })
                    del rejection_streak[:-interval]
                else:
                    rejection_streak.clear()
                persist_adaptation_state()
                if len(rounds) >= int(self.config.max_rounds or 0):
                    return ""
                if len(rejection_streak) < interval:
                    return ""
                rejected = [dict(item) for item in rejection_streak[-interval:]]
                review_rounds = [int(item["round_index"]) for item in rejected]
                failure_reasons = [str(item["reason"]) for item in rejected]
                max_triggers = max(
                    0,
                    int(self.config.adaptive_skill_max_triggers or 0),
                )
                max_cost = max(
                    0.0,
                    float(self.config.adaptive_skill_max_cost_usd or 0.0),
                )
                if adaptation_triggers >= max_triggers:
                    rejection_streak.clear()
                    persist_adaptation_state()
                    return ""
                remaining_cost = max_cost - adaptation_spent
                if max_cost > 0 and remaining_cost <= 0:
                    append_method_ledger(
                        workdir,
                        {
                            "status": "cost_cap_reached",
                            "trigger_index": adaptation_triggers,
                            "review_rounds": review_rounds,
                            "failure_reasons": failure_reasons,
                        },
                    )
                    rejection_streak.clear()
                    persist_adaptation_state()
                    return ""
                evidence = "\n".join(
                    f"- Round {item['round_index']}: {item['reason']}; next: "
                    f"{item['next_action']}"
                    for item in rejected
                )
            else:
                interval = max(0, int(self.config.adaptive_skill_interval or 0))
                if skill is None or interval == 0 or len(rounds) % interval:
                    return ""
                recent = rounds[-interval:]
                remaining_cost = None
                review_rounds = [rec.round_index for rec in recent]
                failure_reasons = [rec.review.reason for rec in recent]
                evidence = "\n".join(
                    f"- Round {rec.round_index}: {rec.review.reason}; next: "
                    f"{rec.review.next_action}"
                    for rec in recent
                )
            if not skill_text:
                return ""
            from .skills.scientist import (
                SkillScientist,
                parse_mechanism_change,
            )

            spent_before_call = adaptation_spent
            if persistent_adaptation:
                adaptation_triggers += 1
                rejection_streak.clear()
                if max_cost > 0:
                    # Reserve the full remaining allowance before provider spawn.
                    # A crash may waste budget, but can never reset and overspend it.
                    adaptation_spent = max_cost
                persist_adaptation_state()
            self._emit({
                "type": EventType.SKILL_SCIENTIST_ADAPTATION_STARTED,
                "text": f"{interval} reviewer rejections; seeking a different playbook",
                "vertical": active_vertical,
                "trigger_index": adaptation_triggers if persistent_adaptation else 0,
                "failure_reasons": failure_reasons,
            })
            scientist = SkillScientist(
                self.engineer_runner,
                model=self.config.engineer_model,
                reasoning_effort=self.config.engineer_reasoning_effort,
                role_banner=scientist_adaptation_banner,
                max_budget_usd=remaining_cost,
            )
            raw_skill = scientist.distill_alternative(
                skill_task,
                evidence,
                current_skill=skill_text,
                method_history="\n".join(
                    str(record) for record in method_records
                ),
            )
            distill_result = scientist.last_result
            raw_result_cost = getattr(distill_result, "cost_usd", None)
            try:
                settled_cost = float(raw_result_cost)
            except (OverflowError, TypeError, ValueError):
                settled_cost = float("nan")
            result_cost = (
                settled_cost
                if math.isfinite(settled_cost) and settled_cost >= 0
                else None
            )
            if persistent_adaptation and max_cost > 0:
                if result_cost is not None:
                    adaptation_spent = spent_before_call + settled_cost
                persist_adaptation_state()
            if not raw_skill:
                if persistent_adaptation:
                    record = {
                        "status": "no_alternative",
                        "trigger_index": adaptation_triggers,
                        "review_rounds": review_rounds,
                        "failure_reasons": failure_reasons,
                        "prior_skill": skill_name or "",
                        "scientist_cost_usd": result_cost,
                    }
                    append_method_ledger(workdir, record)
                    method_records.append(record)
                    persist_adaptation_state()
                return ""
            mechanism_change = (
                parse_mechanism_change(raw_skill) if persistent_adaptation else None
            )
            if persistent_adaptation and mechanism_change is None:
                record = {
                    "status": "mechanism_change_rejected",
                    "trigger_index": adaptation_triggers,
                    "review_rounds": review_rounds,
                    "failure_reasons": failure_reasons,
                    "prior_skill": skill_name or "",
                    "scientist_cost_usd": result_cost,
                }
                append_method_ledger(workdir, record)
                method_records.append(record)
                persist_adaptation_state()
                return ""
            if persistent_adaptation and "".join(raw_skill.split()).casefold() == "".join(
                skill_text.split()
            ).casefold():
                record = {
                    "status": "duplicate_mechanism_rejected",
                    "trigger_index": adaptation_triggers,
                    "review_rounds": review_rounds,
                    "failure_reasons": failure_reasons,
                    "prior_skill": skill_name or "",
                    "scientist_cost_usd": result_cost,
                }
                append_method_ledger(workdir, record)
                method_records.append(record)
                persist_adaptation_state()
                return ""
            distilled = self.skill_router.create_from_scientist(
                raw_skill, task=skill_task, on_event=self._emit
            )
            if distilled is None:
                if persistent_adaptation:
                    record = {
                        "status": "invalid_alternative",
                        "trigger_index": adaptation_triggers,
                        "failure_reasons": failure_reasons,
                    }
                    append_method_ledger(workdir, record)
                    method_records.append(record)
                    persist_adaptation_state()
                return ""
            adaptive_text = render_skill_playbook(self.skill_store, [distilled], [])
            skill = distilled
            skill_text = (
                adaptive_text
                if persistent_adaptation
                else skill_text + "\n\n" + adaptive_text
            )
            skill_name = distilled.name
            skill_distilled = True
            if persistent_adaptation:
                record = {
                    "status": "created",
                    "trigger_index": adaptation_triggers,
                    "review_rounds": review_rounds,
                    "failure_reasons": failure_reasons,
                    "new_skill": distilled.name,
                    "mechanism_change_required": True,
                    "mechanism_change": mechanism_change,
                    "scientist_cost_usd": result_cost,
                }
                ledger_path = append_method_ledger(workdir, record)
                method_records.append(record)
                persist_adaptation_state()
            self._emit({
                "type": EventType.SKILL_SCIENTIST_ADAPTATION_CREATED,
                "text": f"Scientist created alternative skill {distilled.name}",
                "vertical": active_vertical,
                "trigger_index": adaptation_triggers if persistent_adaptation else 0,
                "method_ledger": (
                    str(ledger_path.relative_to(workdir))
                    if persistent_adaptation
                    else ""
                ),
            })
            return adaptive_text

        # Candidate SOURCE augmentation: on the "research" VERTICAL's research
        # stage only, run ONE codex live-web-search ideation and APPEND its
        # candidates to research/IDEA_CANDIDATES.md so idea-creator ranks over a
        # richer pool. NOT gated on the stage NAME alone — "research" is also
        # the first stage's name for the optimize-family verticals (kernelbench/
        # speedrun/nanochat/nanogpt_speedrun; see their own STAGE_ORDER), and this
        # feature's prompt is explicitly paper-ideation ("candidate discovery for
        # a paper") — firing it there wastes a live-web-search call (and rate-
        # limit budget) on a mission that will never read IDEA_CANDIDATES.md.
        # Selection is untouched; fail-open + run-once. Opt-out via
        # ARGUS_SKILL_IDEA_SEARCH=0. Recorded on the event stream so operators
        # (cockpit / --follow / events.jsonl) see the extra candidate source.
        if os.environ.get("ARGUS_SKILL_IDEA_SEARCH", "1").strip().lower() not in (
            "0", "false", "no", "off",
        ):
            try:
                from .skills.idea_search import (
                    _already_seeded as _ideas_seeded,
                )
                from .skills.idea_search import (
                    augment_idea_candidates as _augment_ideas,
                )
                from .skills.stage_checklists import current_stage as _cur_stage
                from .skills.vertical_select import _persisted_vertical

                is_research_vertical = (_persisted_vertical(workdir) or "research") == "research"
                if (
                    self.config.paper_mission
                    and is_research_vertical
                    and (_cur_stage(workdir) or "").strip().lower() == "research"
                    and not _ideas_seeded(workdir)
                ):
                    self._emit({
                        "type": "idea.search.started",
                        "text": "codex live web-search: seeding candidate ideas",
                    })
                    _n = _augment_ideas(
                        self.engineer_runner,
                        workdir,
                        # Candidate discovery needs the clean research direction,
                        # not the Engineer's full task prelude. Passing ``task``
                        # leaked machine special prompts (for example "/root is
                        # ephemeral; put durable artifacts under /data") into the
                        # "Research direction" field and caused the search agent to
                        # relocate an assigned project instead of researching it.
                        direction=(
                            self.config.continuous_objective.strip()
                            or request_anchor
                        ),
                        model=self.config.engineer_model,
                    )
                    self._emit({
                        "type": "idea.search.completed",
                        "text": (
                            f"appended {_n} web-search candidate(s) to "
                            "research/IDEA_CANDIDATES.md"
                        ),
                        "count": _n,
                    })
            except Exception:  # noqa: BLE001 — a candidate source never blocks
                log.debug("idea-search hook skipped", exc_info=True)
                self._emit({
                    "type": "idea.search.skipped",
                    "text": "idea-search hook error (fail-open)",
                })

        # Step 3: supervised round-loop
        def build_prompt(next_action: str | None, include_static: bool = True) -> str:
            prompt = self._build_engineer_prompt(
                task=task,
                skill_text=skill_text,
                next_action=next_action,
                original_request=request_anchor,
                include_static=include_static,
                role_banner=engineer_role_banner,
                allow_self_review=self.config.engineer_self_review_enabled,
                matched_skill_name=learning_target_name,
                require_post_task_learning=self.config.require_post_task_learning,
                force_post_task_learning=self.config.force_post_task_learning,
                file_read_budget=self.config.engineer_file_read_budget,
                test_run_budget=self.config.engineer_test_run_budget,
            )
            guidance: list[str] = []
            if self.extra_guidance_provider is not None:
                try:
                    guidance = [
                        str(item).strip()
                        for item in self.extra_guidance_provider()
                        if str(item).strip()
                    ]
                except Exception:  # noqa: BLE001 — steering must fail soft
                    log.exception("live Manager guidance provider failed")
            if not guidance:
                return prompt
            self._emit({
                "type": EventType.LIFE_INBOX_DRAINED,
                "count": len(guidance),
                "messages": guidance,
                "source": "engineer_round",
            })
            return (
                prompt
                + "\n\n## LIVE MANAGER / OPERATOR DIRECTIVES — HIGHEST PRIORITY\n"
                + "These directives supersede stale plans, checklists, and prior "
                "review guidance. Act on them in this round before lower-priority work.\n"
                + "\n".join(f"- {item}" for item in guidance)
            )

        def prepare_review_context() -> None:
            if not self.config.wiki_ops_enabled:
                return
            from .wiki.auto_hooks import run_post_mission_hooks

            run_post_mission_hooks(
                workdir,
                mission_id=run_id,
                success=False,
                emit=self.on_event,
            )

        def capture_reviewed_round(record: RoundRecord) -> None:
            if not self.config.wiki_ops_enabled:
                return
            from .wiki.lifecycle import capture_reviewed_round as _capture

            _capture(
                record=record,
                workdir=workdir,
                task=skill_task,
                mission_id=run_id,
                on_event=self.on_event,
            )

        def maintain_skill_with_engineer(
            decision: EngineerCompletionDecision,
            thread_id: str | None,
            engineer_summary: str,
        ) -> EngineerSkillMaintenanceOutcome:
            nonlocal skill_name, skill_distilled
            action = decision.skill_action
            if action not in {"create", "update"}:
                return EngineerSkillMaintenanceOutcome()
            if not thread_id:
                return EngineerSkillMaintenanceOutcome(
                    attempted=False,
                    success=False,
                    summary="same-session continuation unavailable: no thread id",
                )
            target_name = decision.skill_name.strip()
            action_instruction = (
                "Create one new reusable Engineer skill."
                if action == "create"
                else (
                    "Return a complete replacement for the existing Engineer "
                    f"skill named `{target_name}`; preserve that exact title."
                )
            )
            prompt = (
                "Continue the SAME Engineer session. The project task is already "
                "complete and self-verified. Do not change project deliverables, "
                "rerun the task, invoke a Reviewer, or launch subagents. Perform "
                "only the requested reusable skill maintenance.\n\n"
                f"Action: {action_instruction}\n"
                f"Why this is reusable: {decision.skill_reason}\n"
                f"Verified lesson: {decision.verification}\n\n"
                "Generalize away mission IDs, local absolute paths, exact issue "
                "text, and one-off constants. Return exactly one Markdown skill "
                "with these sections: `# <title>`, `## Description`, "
                "`## Category`, `## When to use`, `## When NOT to use`, "
                "`## How to solve`, and `## Pitfalls`. If the trajectory does "
                "not support a defensible reusable skill after all, output "
                "exactly `NONE`.\n\n"
                "For context, the completed Engineer summary was:\n"
                + engineer_summary[-8000:]
            )
            self._emit({
                "type": EventType.ENGINEER_SKILL_MAINTENANCE_STARTED,
                "action": action,
                "name": target_name,
                "session_id": thread_id,
                "text": (
                    "resuming Engineer session for skill "
                    f"{action}{f' `{target_name}`' if target_name else ''}"
                ),
            })
            backend_name = str(
                getattr(self.engineer_runner, "_backend_name", "") or ""
            ).strip().lower()
            extra_args = list(self.config.extra_args or [])
            sandbox_mode: str | None = "read-only"
            if backend_name == "copilot":
                sandbox_mode = None
                extra_args.extend([
                    "--no-custom-instructions",
                    "--disable-builtin-mcps",
                    "--available-tools=",
                ])
            try:
                result = gateway_run_exec(
                    self.engineer_runner,
                    prompt=prompt,
                    options=RunnerOptions(
                        model=self.config.engineer_model,
                        reasoning_effort=(
                            self.config.skill_maintenance_reasoning_effort
                        ),
                        extra_args=extra_args or None,
                        full_auto=False,
                        dangerous_yolo=False,
                        sandbox_mode=sandbox_mode,
                        skip_git_repo_check=True,
                        working_dir=str(workdir),
                    ),
                    run_label="engineer-skill-maintenance",
                    resume_thread_id=thread_id,
                )
            except Exception as exc:  # noqa: BLE001
                self._emit({
                    "type": EventType.ENGINEER_SKILL_MAINTENANCE_COMPLETED,
                    "action": action,
                    "name": target_name,
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "premium_requests": 0.0,
                    "usage_scope": "delta",
                    "text": "Engineer skill maintenance call failed",
                })
                return EngineerSkillMaintenanceOutcome(
                    attempted=True,
                    success=False,
                    summary=f"failed: {type(exc).__name__}: {exc}",
                    thread_id=thread_id,
                )

            raw_content = str(getattr(result, "last_agent_message", "") or "").strip()
            content = redact_secrets_text(
                raw_content,
                known_values=known_secret_values(),
            )
            counts = {"created": 0, "updated": 0, "archived": 0, "rejected": 0}
            error = str(getattr(result, "fatal_error", "") or "").strip()
            call_ok = int(getattr(result, "exit_code", 0) or 0) == 0 and not error
            if call_ok and content and not content.upper().startswith("NONE"):
                op = {
                    "op": action,
                    "content": content,
                    "why": decision.skill_reason,
                }
                if action == "update":
                    op["name"] = target_name
                counts = self.skill_router.apply_ops(
                    [op],
                    task=skill_task,
                    on_event=self._emit,
                )
                if action == "create" and counts["created"]:
                    from .skills.skill_prompts import Prompts

                    created_name, _, _, _ = Prompts.parse_skill_output(content)
                    skill_name = created_name
                    skill_distilled = True
            success = bool(counts["created"] or counts["updated"])
            if call_ok and content.upper().startswith("NONE"):
                summary = "Engineer found no defensible reusable skill"
            elif success:
                summary = (
                    f"{action} applied"
                    + (f" for `{target_name}`" if target_name else "")
                )
            elif error:
                summary = f"failed: {error}"
            else:
                summary = "skill candidate rejected or empty"
            self._emit({
                "type": EventType.ENGINEER_SKILL_MAINTENANCE_COMPLETED,
                "action": action,
                "name": target_name,
                "success": success,
                "counts": counts,
                "error": error,
                "session_id": getattr(result, "thread_id", None) or thread_id,
                "input_tokens": int(getattr(result, "input_tokens", 0) or 0),
                "cached_input_tokens": int(
                    getattr(result, "cached_input_tokens", 0) or 0
                ),
                "output_tokens": int(getattr(result, "output_tokens", 0) or 0),
                "reasoning_output_tokens": int(
                    getattr(result, "reasoning_output_tokens", 0) or 0
                ),
                "premium_requests": float(
                    getattr(result, "premium_requests", 0.0) or 0.0
                ),
                "usage_scope": "delta",
                "text": summary,
            })
            return EngineerSkillMaintenanceOutcome(
                attempted=True,
                success=success,
                summary=summary,
                thread_id=getattr(result, "thread_id", None) or thread_id,
            )

        status, rounds, final_message, reason, last_thread_id = self.supervised.run(
            objective=task,
            original_objective=request_anchor,
            engineer_prompt_builder=build_prompt,
            supervised_config=SupervisedConfig(
                max_rounds=self.config.max_rounds,
                no_progress_threshold=self.config.no_progress_threshold,
                soft_round_limit=self.config.soft_round_limit,
                hard_escalate_rounds=self.config.hard_escalate_rounds,
                backend_failure_threshold=self.config.backend_failure_threshold,
                backend_failure_backoff_seconds=self.config.backend_failure_backoff_seconds,
                session_id=self.config.session_id,
                checkpoint_path=self.config.checkpoint_path,
                engineer_log_path=self.config.engineer_log_path,
                allow_engineer_self_review=(
                    self.config.engineer_self_review_enabled
                ),
                allow_engineer_skill_maintenance=(
                    self.config.engineer_skill_maintenance_enabled
                ),
                required_skill_action=(
                    ("update" if learning_target_name else "create")
                    if (
                        self.config.require_post_task_learning
                        and self.config.force_post_task_learning
                    )
                    else ""
                ),
                required_skill_name=learning_target_name,
            ),
            workdir=workdir,
            on_event=self.on_event,
            seed_thread_id=seed_thread_id,
            scope=scope,
            per_mission_budget=per_mission_budget,
            prepare_review_context=prepare_review_context,
            review_completed_hook=capture_reviewed_round,
            continue_adaptor=adapt_after_rejections,
            reviewer_skill_block=reviewer_skill_block,
            engineer_skill_maintenance=maintain_skill_with_engineer,
        )

        # Step 4: learn from the OUTCOME. A called Reviewer may already have
        # edited durable memory; a self-approved Engineer may instead have used
        # its same-session maintenance continuation. Here we record effectiveness
        # evidence and retain legacy proposal replay compatibility.
        if skill is not None:
            try:
                if status == "done":
                    self.skill_store.record_reuse(
                        skill,
                        task_desc=skill_task,
                        success=True,
                        on_event=self._emit,
                    )
                elif status in _INEFFECTIVE_SKILL_STATUSES:
                    self.skill_store.record_reuse(
                        skill,
                        task_desc=skill_task,
                        success=False,
                        on_event=self._emit,
                    )
            except Exception as exc:  # noqa: BLE001 — never break the loop
                log.warning("skill use recording failed (%s: %s)",
                            type(exc).__name__, exc)

        try:
            from .skills.evolution import evolve_skills_after_mission

            evolve_skills_after_mission(
                skill_store=self.skill_store,
                skill_router=self.skill_router,
                reviewer_runner=self.reviewer_runner,
                reviewer_model=self.config.resolved_reviewer_model(),
                reviewer_reasoning_effort=(
                    self.config.matcher_reasoning_effort or "high"
                ),
                rounds=rounds,
                task=skill_task,
                apply_ops_enabled=self.config.skill_ops_enabled,
                auto_compact_enabled=self.config.auto_compact_enabled,
                fallback_skills_dir=self.skills_dir,
                on_event=self.on_event,
            )
        except Exception:  # noqa: BLE001 - evolution must never shadow the verdict
            log.debug("skill evolution raised", exc_info=True)

        stop_kind = rounds[-1].stop_kind if rounds else None
        if status == "paused_budget" and stop_kind is None:
            stop_kind = "budget_exhausted"
        outcome = LoopOutcome(
            status=status,
            rounds=rounds,
            skill_used=skill_name,
            skill_distilled=skill_distilled,
            final_message=final_message,
            reason=reason,
            workdir=str(workdir),
            last_thread_id=last_thread_id,
            stop_kind=stop_kind,
            recoverable=stop_kind_is_recoverable(stop_kind),
        )
        final_review = rounds[-1].review if rounds else None
        achievement = (
            final_review.achievement
            if final_review is not None and status == "done"
            else None
        )
        if isinstance(achievement, dict):
            self._emit({
                "type": EventType.RESEARCH_ACHIEVEMENT_CERTIFIED,
                "achievement_id": f"reviewer-{run_id}",
                "title": achievement["title"],
                "goal": achievement["goal"],
                "metric_id": achievement.get("metric_id", ""),
                "summary": achievement.get("summary", ""),
                "evidence": list(achievement.get("evidence") or []),
                "reviewer_certified": True,
            })
        # Step 4c: project-wiki evolution. The lifecycle module owns mechanical
        # source ingestion, scratch lift, reviewer wiki_ops, promotion and optional
        # reversible compaction so this main loop stays orchestration-only.
        try:
            from .wiki.lifecycle import evolve_wikis_after_mission

            evolve_wikis_after_mission(
                rounds=rounds,
                workdir=workdir,
                task=skill_task,
                mission_id=run_id,
                success=(status == "done"),
                reviewer_runner=self.reviewer_runner,
                reviewer_model=self.config.resolved_reviewer_model(),
                reviewer_reasoning_effort=(
                    self.config.matcher_reasoning_effort or "high"
                ),
                apply_ops_enabled=self.config.wiki_ops_enabled,
                auto_compact_enabled=self.config.auto_compact_enabled,
                on_event=self.on_event,
            )
        except Exception:  # noqa: BLE001 - wiki evolution must never block
            log.debug("wiki evolution raised", exc_info=True)
        # Effectiveness telemetry — one structured event per mission so
        # operators can compute hit-rate, mean-rounds-with-skill, and
        # mean-rounds-without-skill from events.jsonl alone.
        try:
            matcher_model = str(
                getattr(
                    self.skill_store,
                    "matcher_model",
                    self.config.resolved_matcher_model(),
                )
                or self.config.resolved_matcher_model()
            )
            transfer_used = bool(distill_result is not None and skill is not None)
            distiller_model = str(
                self.config.resolved_skill_adapter_model()
                if transfer_used and not skill_distilled
                else (self.config.engineer_model or "")
            )
            distiller_input_tokens = int(getattr(distill_result, "input_tokens", 0) or 0)
            distiller_cached_input_tokens = int(
                getattr(distill_result, "cached_input_tokens", 0) or 0
            )
            distiller_output_tokens = int(
                getattr(distill_result, "output_tokens", 0) or 0
            )
            distiller_reasoning_output_tokens = int(
                getattr(distill_result, "reasoning_output_tokens", 0) or 0
            )
            matcher_usage = {
                "model": matcher_model,
                "input_tokens": int(matcher_input_tokens or 0),
                "cached_input_tokens": int(matcher_cached_input_tokens or 0),
                "output_tokens": int(matcher_output_tokens or 0),
                "reasoning_output_tokens": int(
                    getattr(match, "reasoning_output_tokens", 0) or 0
                ),
            }
            distiller_usage = {
                "model": distiller_model,
                "input_tokens": distiller_input_tokens,
                "cached_input_tokens": distiller_cached_input_tokens,
                "output_tokens": distiller_output_tokens,
                "reasoning_output_tokens": distiller_reasoning_output_tokens,
            }
            self._emit({
                "type": EventType.SKILL_COST_COMPLETED,
                "agent_layer": "skill_transfer" if transfer_used else "scientist",
                "matcher_model": matcher_model,
                "distiller_model": distiller_model,
                "matcher": matcher_usage,
                "distiller": distiller_usage,
                "matcher_input_tokens": matcher_usage["input_tokens"],
                "matcher_cached_input_tokens": matcher_usage["cached_input_tokens"],
                "matcher_output_tokens": matcher_usage["output_tokens"],
                "matcher_reasoning_output_tokens": matcher_usage["reasoning_output_tokens"],
                "distiller_input_tokens": distiller_usage["input_tokens"],
                "distiller_cached_input_tokens": distiller_usage["cached_input_tokens"],
                "distiller_output_tokens": distiller_usage["output_tokens"],
                "distiller_reasoning_output_tokens": distiller_usage["reasoning_output_tokens"],
                "input_tokens": (
                    matcher_usage["input_tokens"] + distiller_usage["input_tokens"]
                ),
                "cached_input_tokens": (
                    matcher_usage["cached_input_tokens"]
                    + distiller_usage["cached_input_tokens"]
                ),
                "output_tokens": (
                    matcher_usage["output_tokens"] + distiller_usage["output_tokens"]
                ),
                "reasoning_output_tokens": (
                    matcher_usage["reasoning_output_tokens"]
                    + distiller_usage["reasoning_output_tokens"]
                ),
                # Native Copilot spend from BOTH routing calls. These used to
                # disappear from mission cost entirely: SkillMatch carried only
                # tokens, while SkillScientist returned only markdown.
                "premium_requests": float(matcher_premium_requests or 0.0)
                + float(getattr(distill_result, "premium_requests", 0.0) or 0.0),
                "usage_scope": "delta",
            })
            self._emit({
                "type": EventType.SKILL_OUTCOME,
                "skill_name": skill_name or "",
                "skill_hit": bool(strict_skill_hit),
                "nearest_transfer_fallback": bool(nearest_transfer_fallback),
                "low_confidence_transfer_hint": bool(
                    low_confidence_transfer_hint
                ),
                "skill_distilled": bool(skill_distilled),
                "matcher_model": matcher_model,
                "distiller_model": distiller_model,
                "matcher_tokens": int(matcher_tokens or 0),
                "matcher_input_tokens": matcher_input_tokens,
                "matcher_cached_input_tokens": matcher_cached_input_tokens,
                "matcher_output_tokens": matcher_output_tokens,
                "distiller_tokens": int(
                    distiller_input_tokens + distiller_output_tokens
                ),
                "distiller_input_tokens": distiller_input_tokens,
                "distiller_cached_input_tokens": distiller_cached_input_tokens,
                "distiller_output_tokens": distiller_output_tokens,
                "rounds": int(len(rounds)),
                "status": str(status),
                "success": bool(status == "done"),
            })
        except Exception:  # noqa: BLE001
            log.debug("skill.outcome emit failed", exc_info=True)
        self._emit({
            "type": EventType.LOOP_DONE,
            "text": f"status={status} rounds={len(rounds)} reason={reason[:80]}",
        })
        return outcome

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit(self, event: dict) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event)
        except Exception:  # never let UI errors kill the loop
            log.exception("on_event handler raised")

    @staticmethod
    def _build_engineer_prompt(
        *,
        task: str,
        skill_text: str,
        next_action: str | None,
        original_request: str = "",
        include_static: bool = True,
        role_banner: str = "",
        allow_self_review: bool = False,
        matched_skill_name: str = "",
        require_post_task_learning: bool = False,
        force_post_task_learning: bool = False,
        file_read_budget: int = 12,
        test_run_budget: int = 3,
    ) -> str:
        # STATIC remains byte-stable for provider prefix caching. Autonomous
        # Engineer calls are always fresh and receive the full prompt.
        sections: list[str] = []
        delta_sections: list[str] = []
        sections.append(EFFECTIVE_TASK_CONTRACT)
        if role_banner.strip():
            sections.append("## Active vertical role\n" + role_banner.strip())
        if skill_text:
            sections.append("## Skill playbook (read first)\n" + skill_text)
        if original_request.strip():
            sections.append(
                "## Original operator request\n"
                "Higher-priority live operator instructions may update this; "
                "lower-authority guidance may not silently change it.\n\n"
                + original_request.strip()
            )
        sections.append("## Current mission task\n" + task)
        if next_action:
            delta_sections.append(
                "## Reviewer guidance from prior round\n"
                "The previous round was judged incomplete. Address the\n"
                "following before declaring done:\n\n"
                + next_action
            )
        sections.append(
            "## This turn\n"
            "Land one coherent, verifiable increment; update "
            "CHECKPOINT.md, then yield. Pure reading without an artifact or "
            "measurement is not progress.\n"
            "Work in the current directory. Unless required, do not write "
            "planning/spec/brief documents, initialize Git, branch/worktree, commit, "
            "spawn subagents, or invoke meta-workflows.\n"
            f"Budget: inspect about {max(1, int(file_read_budget))} relevant files "
            "before editing and avoid rereads; run at most "
            f"{max(1, int(test_run_budget))} focused verification commands plus the "
            "decisive verifier. Exceed only after a concrete failure or code change. "
            "Ignore `.autors` unless retaining durable learning."
        )
        sections.append(
            "## Required output\n"
            "End with `## Verification (verbatim)` containing command "
            "output in a fenced block, then `## Summary` (at most 8 bullets).\n\n"
            + (
                "Set `review=skip` only for complete, bounded, low-risk work whose "
                "decisive verifier passed. Require Reviewer for unresolved failures, "
                "risky cross-module API/schema/migration/security/concurrency changes, "
                "or unsettled judgment—not reassurance. Request skill maintenance "
                "only for durable reusable learning; the harness resumes this session.\n"
                if allow_self_review
                else
                "Independent review required; set `review=required`.\n"
            )
            + "Final line exactly:\n"
            'ARGUS_ENGINEER_DECISION: {"review":"skip|required",'
            '"reason":"<brief judgment>","verification":"<what passed>",'
            '"skill_action":"none|create|update","skill_name":"<required for '
            'update, else empty>","skill_reason":"<required for create/update, '
            'else empty>"}'
        )
        if require_post_task_learning and force_post_task_learning:
            required_action = "update" if matched_skill_name else "create"
            target = (
                f" the matched skill `{matched_skill_name}`"
                if matched_skill_name
                else " one reusable Engineer skill"
            )
            sections.append(
                "## Required self-evolution\n"
                "After verification, request `skill_action=" + required_action + "` for"
                + target
                + "; the harness resumes this session to author it. Also retain one "
                "concise `.autors/<project>/wiki/` note with the reusable mechanism, "
                "failed approach, and decisive verification."
            )
        elif require_post_task_learning:
            sections.append(
                "## Selective self-evolution\n"
                "Use `skill_action=create|update` only for a verified durable mechanism "
                "that changes future work; otherwise use `skill_action=none`. Write a "
                "wiki note only for similarly durable project knowledge."
            )
        static_text = "\n\n".join(sections)
        delta_text = "\n\n".join(delta_sections)
        if include_static:
            return static_text + ("\n\n" + delta_text if delta_text else "")
        # Kept for source compatibility; autonomous calls always request full text.
        return delta_text

__all__ = ["SkillLoop", "SkillLoopConfig"]
