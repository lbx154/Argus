"""SkillLoop — the integrated matcher → supervised-engineer flow.

This is the new code that argus-skill exists to deliver. It composes:

  * ``SkillStore`` (vendored from skill-agent): horizontal skill cache.
  * ``SupervisedEngineer`` (new, with ``Reviewer`` vendored from ArgusBot):
    vertical round-loop that supervises the engineer until the reviewer
    is satisfied.

Skill AND wiki memory are REVIEWER-owned: there is no separate authoring
agent and no Manager approval gate — the reviewer is the sole authority. The
reviewer emits ``skill_ops`` (create/update/delete/archive on the reusable
skill library) and ``wiki_ops`` (create_page/update_page/retire_page on the
project idea-wiki) per round; the loop applies both at mission end via
``SkillRouter`` / ``WikiRouter`` respectively.

End-to-end shape:

    task → matcher/Scientist → engineer round-loop (engineer turn → reviewer)
            outcome → record skill use, apply skill_ops/wiki_ops
            continue → inject next_action, next round
            blocked → stop with reason; still apply skill_ops/wiki_ops
"""
from __future__ import annotations

import logging
import math
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .core.event_catalog import EventType
from .core.models import LoopOutcome, RoundRecord
from .core.ports import RunnerBackend
from .core.stop_kinds import stop_kind_is_recoverable
from .engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
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


@dataclass
class SkillLoopConfig:
    """All knobs for one SkillLoop.run invocation, in one place."""
    engineer_model: str | None = "gpt-5.5"
    reviewer_model: str | None = None  # default: same as engineer (cheap)
    matcher_model: str | None = None   # default: same as engineer
    engineer_reasoning_effort: str | None = "xhigh"
    reviewer_reasoning_effort: str = "xhigh"
    matcher_reasoning_effort: str | None = "high"
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
    # Reviewer-owned skill memory: the reviewer emits ``skill_ops`` per round
    # (create/update/delete/archive) and the loop applies them via
    # SkillRouter — no Manager approval gate. Off by default; the daemon
    # enables it.
    skill_ops_enabled: bool = False
    # Reviewer-owned project wiki memory: the wiki's structured counterpart
    # to ``skill_ops_enabled`` above. The reviewer emits ``wiki_ops``
    # (create_page/update_page/retire_page) per round and the loop applies
    # them via WikiRouter — also no Manager gate. A no-op whenever the
    # project has no initialized wiki (see ``wiki.auto_hooks.discover_wikis``).
    # Off by default; the daemon enables it.
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
    # ``direct`` skips skill/wiki preflight ceremony for a bounded one-off
    # deliverable; the Engineer and Reviewer still run normally.
    workflow_mode: str = "staged"
    # Explicit signal that this mission is a long-horizon academic-paper /
    # submission task. When True the engineer prompt carries the
    # long-horizon paper execution contract. Replaces the old keyword-based
    # objective sniffing; callers (e.g. the life runner) set it explicitly.
    paper_mission: bool = False
    # Where to persist the curated working-memory checkpoint so the reviewer's
    # per-round handoff (goal / done / tried-and-failed / open-blocker /
    # next-step) survives across missions AND daemon restarts. None = in-memory
    # only for the current mission (legacy behaviour; e.g. tests / chat). The
    # life runner sets this to the per-project state-dir checkpoint file.
    checkpoint_path: Path | None = None
    # Absolute path to this project's engineer execution log
    # (``<life_dir>/events.jsonl``), threaded down to SupervisedConfig so the
    # reviewer can grep HOW the engineer produced its result (process-correctness
    # audit). Empty = legacy behaviour (no audit section in the reviewer prompt);
    # the life runner fills it from the per-project state dir.
    engineer_log_path: str = ""

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
        direct_workflow = self.config.workflow_mode == "direct"
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
        scientist_role_banner = vertical_role_banner(vertical_module, "scientist")
        if self.config.wiki_ops_enabled and not direct_workflow:
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

        # Step 1: matcher (role mission — shared scaffold across all roles).
        # Suppress the other venue's paper skills so an AAAI project never
        # matches the EMNLP drafting/preflight/router/review skills (and the
        # newly-added AAAI siblings never dilute EMNLP matching). Resolves
        # from research/PIPELINE_STATE.json target_venue; EMNLP by default.
        from .skills.venue_profiles import venue_excluded_skill_files

        if direct_workflow:
            from .skills.role_match import RoleSkillMatch

            match = RoleSkillMatch(role="engineer")
        else:
            match = self.skill_router.select(
                skill_task, extra_exclude=venue_excluded_skill_files(workdir)
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
        skill_distilled = False
        distill_result = None

        # Scientist tool on miss: author one reusable playbook, persist it in the
        # project layer immediately, and inject that exact version into this mission.
        if skill is None and not direct_workflow:
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
                    role_banner=scientist_role_banner,
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

        skill_text = render_skill_playbook(
            self.skill_store, primary_skills, reference_skills
        )
        skill_name = skill.name if skill else None
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
                role_banner=scientist_role_banner,
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
        # Venue-format research: if target_venue is a NON-standard venue (not
        # EMNLP/AAAI) with no cached profile yet, run ONE codex live-web-search
        # round to build research/VENUE_PROFILE.json so the paper is graded
        # against the RIGHT venue instead of the EMNLP default. Fail-open +
        # run-once (cached by the file). Opt-out via ARGUS_SKILL_VENUE_RESEARCH=0.
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
                    and (_vr_stage(workdir) or "").strip().lower() == "research"
                    and needs_venue_research(workdir)
                ):
                    self._emit({
                        "type": "venue.research.started",
                        "text": "codex live web-search: researching non-standard venue format",
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
                            "venue research produced no profile (falling back to default)"
                        ),
                        "ok": _ok,
                    })
            except Exception:  # noqa: BLE001 — venue research never blocks the loop
                log.debug("venue-research hook skipped", exc_info=True)

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
                        direction=task,
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
            return self._build_engineer_prompt(
                task=task,
                skill_text=skill_text,
                next_action=next_action,
                original_request=request_anchor,
                include_static=include_static,
                role_banner=engineer_role_banner,
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
            ),
            workdir=workdir,
            on_event=self.on_event,
            seed_thread_id=seed_thread_id,
            scope=scope,
            per_mission_budget=per_mission_budget,
            prepare_review_context=prepare_review_context,
            review_completed_hook=capture_reviewed_round,
            continue_adaptor=adapt_after_rejections,
        )

        # Step 4: learn from the OUTCOME. The REVIEWER owns skill AND wiki
        # memory: it emits ``skill_ops`` (create/update/delete/archive on the
        # skill library) and ``wiki_ops`` (create_page/update_page/retire_page
        # on the project wiki) per round — no Manager approval gate for
        # either. The loop only applies what the reviewer requested; there is
        # no separate author. Skills are active immediately; reviewed outcomes
        # are recorded as use evidence, while later update/archive ops express
        # the Reviewer's judgment about what to retain.
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
            distiller_model = str(self.config.engineer_model or "")
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
                "agent_layer": "scientist",
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
                "skill_hit": bool(skill_name) and not skill_distilled,
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
    ) -> str:
        # STATIC = byte-stable prefix (constant within a mission: task / skill)
        # → restores gpt-5.5 prefix-cache. DELTA = the per-round changing tail
        # (reviewer next_action). On a RESUMED thread we send DELTA only;
        # STATIC is re-sent on round 1 / after a session roll / after a compaction
        # (the anti-amnesia hedge). See SupervisedEngineer.run (F5).
        sections: list[str] = []
        delta_sections: list[str] = []
        if role_banner.strip():
            sections.append("## Active vertical role\n" + role_banner.strip())
        if skill_text:
            sections.append("## Skill playbook (read first)\n" + skill_text)
        if original_request.strip():
            sections.append(
                "## Original operator request (immutable anchor)\n"
                "This is the user's original ask before planner/reviewer/prelude "
                "rewrites. Treat it as the non-negotiable north star; if later "
                "guidance conflicts, satisfy the original intent or call out the "
                "conflict explicitly.\n\n"
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
            "## Turn discipline — bounded progress, then yield\n"
            "Do NOT try to finish this whole stage in a single turn. A turn\n"
            "that runs for hundreds of internal steps overflows the context\n"
            "window, forces repeated lossy auto-compaction, and burns the\n"
            "budget re-summarizing itself instead of working.\n\n"
            "Instead, each turn: advance ONE concrete increment — land a\n"
            "checklist item (or a few tightly-coupled blockers), create or\n"
            "modify a real artifact — then STOP and emit your status block.\n"
            "You WILL be resumed; the curated checkpoint block injected at the\n"
            "top of this prompt carries your goal, what is done, what failed,\n"
            "the open blocker, and the next step across turns, so you lose\n"
            "nothing by stopping. Treat ~30-40 tool calls as a soft ceiling\n"
            "for one turn.\n\n"
            "Use the lightest useful handoff. Normally, stop and let the\n"
            "Reviewer judge the increment. If you have landed real work, the\n"
            "next local execution step is already obvious, and an independent\n"
            "review right now would only repeat your own plan, you may request\n"
            "ONE additional Engineer turn by making the final non-empty line:\n"
            "`CONTINUE_WORK: <specific next step>`\n"
            "The following turn is reviewed normally. Do not use this when you\n"
            "think the mission is done, need acceptance criteria interpreted,\n"
            "need a strategy pivot, are uncertain about evidence, or are\n"
            "blocked.\n\n"
            "Every turn MUST land a concrete, checkpoint-worthy increment with\n"
            "verification output below. A turn that ships and MEASURES a change\n"
            "— even one that scores temporarily WORSE — or that makes a real\n"
            "build step toward a declared NEW mechanism named in your checkpoint\n"
            "(a profile run, a researched technique applied, a kernel/precision\n"
            "edit that compiles) IS a landed increment, NOT no-progress. Only a\n"
            "turn that SOLELY reads/explores and stops with nothing built or\n"
            "measured is judged as no forward progress and, repeated, aborts the\n"
            "mission. If the stage gate is genuinely within a couple of quick\n"
            "items, finish them rather than stopping artificially.\n"
        )
        sections.append(
            "## Required output\n"
            "Make concrete progress: read files, run commands, edit code as\n"
            "needed.\n\n"
            "End your response with a fenced markdown section titled\n"
            "**`## Verification (verbatim)`** containing the *literal stdout*\n"
            "of every acceptance command you ran this round — pytest summary\n"
            "line, ruff result, mypy result, coverage table, `ls` output,\n"
            "etc. Quote the actual lines, not paraphrases. Use a fenced\n"
            "code block. The reviewer is text-only and must see the real\n"
            "command output to judge completion; without it the round will\n"
            "be marked `continue` and burn another cycle.\n\n"
            "Below the verification block, add a short `## Summary`\n"
            "section (≤8 bullets) describing what you changed."
        )
        static_text = "\n\n".join(sections)
        delta_text = "\n\n".join(delta_sections)
        if include_static:
            # Full send (round 1 / post-roll / post-compaction): STATIC then DELTA.
            return static_text + ("\n\n" + delta_text if delta_text else "")
        # Resume send: DELTA only (may be "" when nothing changed this round).
        return delta_text

__all__ = ["SkillLoop", "SkillLoopConfig"]
