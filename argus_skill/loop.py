"""SkillLoop — the integrated matcher → supervised-engineer flow.

This is the new code that argus-skill exists to deliver. It composes:

  * ``SkillStore`` (vendored from skill-agent): horizontal skill cache.
  * ``SupervisedEngineer`` (new, with ``Reviewer`` vendored from ArgusBot):
    vertical round-loop that supervises the engineer until the reviewer
    is satisfied.

Skill memory is REVIEWER-owned: there is no separate authoring agent. The
reviewer emits ``skill_ops`` per round (create/update PROPOSALS gated by the
Manager generality check; delete/archive applied directly), and the loop
applies them at mission end.

End-to-end shape:

    task → matcher → engineer round-loop (engineer turn → checks → reviewer)
            done    → confirm a proven candidate, apply skill_ops, return success
            continue → inject next_action, next round
            blocked → stop with reason; still apply skill_ops
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .core.models import LoopOutcome, RoundRecord
from .core.ports import RunnerBackend
from .engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from .reviewer import Reviewer, ReviewerConfig
from .skills.missions import EngineerMission
from .skills.role_match import render_skill_playbook
from .skills.skill_router import SkillRouter
from .skills.store import Skill, SkillStore

log = logging.getLogger(__name__)


@dataclass
class SkillLoopConfig:
    """All knobs for one SkillLoop.run invocation, in one place."""
    engineer_model: str = "gpt-5.5"
    reviewer_model: str | None = None  # default: same as engineer (cheap)
    matcher_model: str | None = None   # default: same as engineer
    engineer_reasoning_effort: str | None = "high"
    reviewer_reasoning_effort: str = "high"
    matcher_reasoning_effort: str | None = "high"
    max_rounds: int = 500
    check_commands: list[str] = field(default_factory=list)
    check_timeout_seconds: int = 600
    no_progress_threshold: int = 2
    # Anti-livelock escalation thresholds threaded into SupervisedConfig: at
    # ``soft_round_limit`` the reviewer is told to escalate an unresolvable
    # external blocker to ``blocked``; at ``hard_escalate_rounds`` the round loop
    # force-ends as ``blocked`` so the planner re-plans. 0 disables either.
    soft_round_limit: int = 12
    hard_escalate_rounds: int = 24
    backend_failure_threshold: int = 2
    backend_failure_backoff_seconds: float = 15.0
    # Reviewer-owned skill memory: the reviewer emits ``skill_ops`` per round
    # (create/update PROPOSALS gated by the Manager generality check;
    # delete/archive applied directly). Off by default; the daemon enables it.
    skill_ops_enabled: bool = False
    full_auto: bool = True
    skip_git_repo_check: bool = True
    dangerous_yolo: bool = False
    extra_args: list[str] | None = None
    session_id: str | None = None
    # Explicit signal that this mission is a long-horizon academic-paper /
    # EMNLP-submission task. When True the engineer prompt carries the
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
        skill_store: SkillStore | None = None,
        on_event: Callable[[dict], None] | None = None,
        extra_guidance_provider: Callable[[], list[str]] | None = None,
        manager: Any = None,
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
        # The single Manager instance, threaded from the runner. When present the
        # SkillRouter's approval gate runs on the Manager's backend (the real
        # consolidation); when None it falls back to the reviewer-backed module
        # function (tests / no-manager callers).
        self.manager = manager

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
        # The single front door to the skill library: selection (delegated to the
        # role matcher) + validated CRUD (independence → mechanical → Manager
        # generality/correctness gate). No role mutates skills directly.
        self.skill_router = SkillRouter(
            skill_store=self.skill_store,
            matcher=self.engineer_mission,
            judge_runner=self.reviewer_runner,
            judge_model=self.config.resolved_reviewer_model(),
            manager=self.manager,
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
            failed_tool_ledger: Any | None = None,
            objective_for_skill: str | None = None,
            original_objective: str | None = None,
            scope: str = "") -> LoopOutcome:
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
        skill_task = (objective_for_skill or task).strip() or task
        request_anchor = (original_objective or objective_for_skill or task).strip() or task
        self._emit({"type": "loop.start", "text": f"task: {skill_task[:120]}"})

        # Step 1: matcher (role mission — shared scaffold across all roles).
        # Suppress the other venue's paper skills so an AAAI project never
        # matches the EMNLP drafting/preflight/router/review skills (and the
        # newly-added AAAI siblings never dilute EMNLP matching). Resolves
        # from research/PIPELINE_STATE.json target_venue; EMNLP by default.
        from .skills.venue_profiles import venue_excluded_skill_files

        match = self.skill_router.select(
            skill_task, extra_exclude=venue_excluded_skill_files(workdir)
        )
        matcher_tokens = match.input_tokens + match.output_tokens
        matcher_input_tokens = match.input_tokens
        matcher_cached_input_tokens = match.cached_input_tokens
        matcher_output_tokens = match.output_tokens
        # Own-role playbooks drive distill/writeback; cross-role references
        # are read-only context and never written back to.
        primary_skills: list[Skill] = list(match.primary_skills)
        reference_skills: list[Skill] = list(match.reference_skills)
        skill: Skill | None = match.primary
        skill_distilled = False
        distill_result = None

        # No proactive distill-on-miss: a missed match never authors a skill
        # pre-emptively (that minted a throwaway playbook for every trivial
        # task). Skill creation is now gated solely by the reviewer's
        # ``skill_gap`` verdict on the OUTCOME — see Step 4 / _apply_skill_ops.

        skill_text = render_skill_playbook(
            self.skill_store, primary_skills, reference_skills
        )
        skill_name = skill.name if skill else None

        # Step 3: supervised round-loop
        def build_prompt(next_action: str | None) -> str:
            extra = self._collect_extra_guidance()
            return self._build_engineer_prompt(
                task=task,
                skill_text=skill_text,
                next_action=next_action,
                extra_guidance=extra,
                paper_mission=self.config.paper_mission,
                original_request=request_anchor,
            )

        status, rounds, final_message, reason, last_thread_id = self.supervised.run(
            objective=task,
            original_objective=request_anchor,
            engineer_prompt_builder=build_prompt,
            supervised_config=SupervisedConfig(
                max_rounds=self.config.max_rounds,
                check_commands=list(self.config.check_commands),
                check_timeout_seconds=self.config.check_timeout_seconds,
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
            failed_tool_ledger=failed_tool_ledger,
            scope=scope,
        )

        # Step 4: learn from the OUTCOME. The REVIEWER owns skill memory: it
        # emits ``skill_ops`` per round (create/update PROPOSALS gated by the
        # Manager generality-check; delete/archive applied directly). The loop
        # only applies what the reviewer requested — there is no separate author.
        # A matched CANDIDATE that just proved effective is confirmed ("入库");
        # the provisional→confirm lifecycle still gates effectiveness.
        if (
            status == "done"
            and skill is not None
            and getattr(skill, "provisional", False)
        ):
            try:
                if self.skill_store.confirm_provisional(skill):
                    self._emit({"type": "skill.confirmed",
                                "text": f"confirmed {skill.name} — it proved effective"})
            except Exception as exc:  # noqa: BLE001 — never break the loop
                log.warning("confirm_provisional failed (%s: %s)",
                            type(exc).__name__, exc)

        if self.config.skill_ops_enabled:
            self._apply_skill_ops(rounds=rounds, skill_task=skill_task)

        outcome = LoopOutcome(
            status=status,
            rounds=rounds,
            skill_used=skill_name,
            skill_distilled=skill_distilled,
            final_message=final_message,
            reason=reason,
            workdir=str(workdir),
            last_thread_id=last_thread_id,
        )
        # Step 4c: wiki harness hooks — back-fill sources, mechanically
        # lift unjudged sources/papers/* into scratch pages/techniques/*,
        # rebuild queries indexes, then run mechanical promotion based on
        # cross-RunCard references. See argus_skill/wiki/auto_hooks.py
        # for the diagnosis and design references (SkillEvolBench,
        # EverOS, mem0 v3). Fail-open: NEVER blocks a verdict.
        try:
            from .wiki.auto_hooks import run_post_mission_hooks
            from .wiki.promotion import mechanical_promote
            mission_id = (
                self.config.session_id
                or (last_thread_id or "")[:12]
                or "unknown"
            )
            hook_summary = run_post_mission_hooks(
                workdir,
                mission_id=mission_id,
                success=(status == "done"),
                emit=self.on_event,
            )
            for wiki_path in hook_summary.keys():
                mechanical_promote(Path(wiki_path), emit=self.on_event)
        except Exception:  # noqa: BLE001 — wiki maintenance must never block
            log.debug("wiki post-mission hooks raised", exc_info=True)
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
            matcher_usage = {
                "model": matcher_model,
                "input_tokens": int(matcher_input_tokens or 0),
                "cached_input_tokens": int(matcher_cached_input_tokens or 0),
                "output_tokens": int(matcher_output_tokens or 0),
            }
            distiller_usage = {
                "model": distiller_model,
                "input_tokens": distiller_input_tokens,
                "cached_input_tokens": distiller_cached_input_tokens,
                "output_tokens": distiller_output_tokens,
            }
            self._emit({
                "type": "skill.cost.completed",
                "agent_layer": "scientist",
                "matcher_model": matcher_model,
                "distiller_model": distiller_model,
                "matcher": matcher_usage,
                "distiller": distiller_usage,
                "matcher_input_tokens": matcher_usage["input_tokens"],
                "matcher_cached_input_tokens": matcher_usage["cached_input_tokens"],
                "matcher_output_tokens": matcher_usage["output_tokens"],
                "distiller_input_tokens": distiller_usage["input_tokens"],
                "distiller_cached_input_tokens": distiller_usage["cached_input_tokens"],
                "distiller_output_tokens": distiller_usage["output_tokens"],
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
                "usage_scope": "delta",
            })
            self._emit({
                "type": "skill.outcome",
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
            "type": "loop.done",
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

    def _render_skill_playbook(self, skills: list[Skill]) -> str:
        """Deprecated shim — delegates to the shared role-mission renderer.

        Kept so external callers/tests referencing this method keep working;
        new code should call ``render_skill_playbook`` directly. Treats every
        passed skill as a primary (own-role) playbook.
        """
        return render_skill_playbook(self.skill_store, skills)

    @staticmethod
    def _build_engineer_prompt(
        *,
        task: str,
        skill_text: str,
        next_action: str | None,
        extra_guidance: list[str] | None = None,
        paper_mission: bool = False,
        original_request: str = "",
    ) -> str:
        sections: list[str] = []
        # Vertical-native prompt framing (resolved up-front so it can gate the
        # paper-execution contract below): the active vertical supplies the
        # engineer role banner, and the long-horizon paper contract applies ONLY
        # to a paper vertical (completion_gate == "full_emnlp"). A non-paper
        # vertical (e.g. speedrun) runs a lean edit→score loop: prepend its
        # banner and skip the long-horizon paper contract entirely.
        from .skills.harness_overlay import resolve_project_root
        from .skills.vertical_select import resolve_vertical
        from .verticals._base import (
            load_vertical,
            vertical_completion_gate,
            vertical_role_banner,
        )

        _proot = resolve_project_root()
        _vmod = load_vertical(resolve_vertical(_proot), project_root=_proot)
        _full_emnlp = vertical_completion_gate(_vmod) == "full_emnlp"
        # An optimize vertical (kernelbench/speedrun/…) is never a paper mission —
        # keep the engineer prompt consistent with the supervisor scaffold even if
        # a stale True default leaks in.
        if not _full_emnlp:
            paper_mission = False
        # Measured-benchmark mode (operator opt-in via ARGUS_SKILL_MEASURED_MODE):
        # the task has a TRUSTED scorer whose measured number is the ONLY judge, so
        # the ground-truth / stage-checklist GATE framing — which forces the
        # engineer to maintain provenance files BEFORE it may optimize — is
        # replaced with a lean explore→write→score directive. Off by default, so
        # paper / non-benchmark tasks are completely unchanged.
        import os as _os
        _measured = _os.environ.get("ARGUS_SKILL_MEASURED_MODE", "").strip().lower() in ("1", "true", "yes", "on")
        _banner = vertical_role_banner(_vmod, "engineer")
        if _banner:
            sections.append(_banner)
        # Valley-immunity: while a post-jump exploration window is open, tell the
        # engineer the frozen floor is safe and a regressing candidate is EXPECTED
        # — score + iterate it (do NOT skip on the train-only proxy gate, do NOT
        # restore on round 1) so the new regime can cross its initial valley.
        try:
            from .regime_jump.ledger import load_ledger as _load_meta_ledger
            from .regime_jump.meta_prompter import explore_window_block as _explore_block

            _ewin = int(getattr(_load_meta_ledger(_proot), "explore_window", 0) or 0)
            if _ewin > 0:
                sections.append(_explore_block(_ewin))
        except Exception:  # noqa: BLE001 — meta grace must never break prompt building
            pass
        # Stage-aware SETUP action control (deterministic safety net). General:
        # keyed purely on the pipeline stage, NOT on any task/benchmark. At the
        # setup stage (pre-optimize) the optimize banner's pull toward an
        # edit→score hill-climb is PREMATURE — the only deliverable is
        # profiling + the ground-truth gate. Inject a hard override right under
        # the banner so it suppresses that pull before the engineer acts.
        from .skills.ground_truth import GROUND_TRUTH_RELPATH
        from .skills.stage_checklists import current_stage as _current_stage

        try:
            _stage_now = _current_stage(_proot)
        except Exception:  # noqa: BLE001 — stage read is best-effort
            _stage_now = None
        sections.append(
            "## Pipeline stage is Manager-owned — do NOT edit it\n"
            "You may create/update NON-stage fields in "
            "`research/PIPELINE_STATE.json` (objective, target_venue, artifact "
            "paths), but you MUST NOT edit the stage fields — `current_stage` or "
            "any per-stage `status` — and you MUST NOT call `rollback_stage` or "
            "any other stage-transition helper. Stage transitions (advance AND "
            "rollback) are decided and written by the Manager, from the "
            "reviewer's verdict. Your job: produce the current stage's required "
            "artifacts and report readiness in your summary; the reviewer "
            "certifies and the Manager moves the stage."
        )
        if _stage_now == "setup" and not _measured:
            sections.append(
                "## SETUP STAGE — action control (HARD OVERRIDE)\n"
                "The active stage is `setup` (pre-optimize). Any optimize/"
                "speedrun framing above that pulls you toward an edit→score "
                "hill-climb does NOT apply yet — ignore that pull until the "
                "stage advances past setup. At this stage you are FORBIDDEN "
                "from:\n"
                "- running `./eval_solution.sh` (or any scorer) to TUNE or "
                "chase the metric, and\n"
                "- editing the recipe / training script to improve the score.\n\n"
                "Your ONLY deliverables at setup are: (1) PROFILE the run to "
                "find the real binding constraint with measured numbers, and "
                "(2) write the verified picture into `"
                + GROUND_TRUTH_RELPATH
                + "`. You MAY run the scorer/recipe exactly ONCE, read-only, "
                "to capture a baseline measurement for the profile — never to "
                "tune. Scoring-to-tune or recipe edits to chase the number are "
                "out of scope until setup is complete and the stage advances "
                "to optimize."
            )
        if _measured:
            sections.append(
                "## MEASURED-BENCHMARK MODE — the scorer is the ONLY judge\n"
                "This task has a TRUSTED scorer that returns a real measured number on the "
                "target hardware. That number is the ONLY thing that matters and the ONLY "
                "proof anyone needs.\n\n"
                "**DO NOT write, read, repair, or 'maintain' any GROUND_TRUTH / gate / marker "
                "/ status / evidence / manifest files. The harness does NOT read them — doing "
                "so is pure wasted effort. There is no gate to pass except a higher score.**\n\n"
                "Spend the ENTIRE round on **explore → write → score**:\n"
                "1. EXPLORE: pick ONE concrete mechanism to try this round, grounded in the "
                "PROFILE (the real measured bottleneck) + the best library / SOTA / open-source "
                "implementation for this exact op. Name it in one line.\n"
                "2. WRITE: implement that candidate in your solution file.\n"
                "3. SCORE: run the scorer to get the real measured number.\n"
                "4. JUDGE BY THE NUMBER ALONE: if it beats your best, keep it; if not, NEXT "
                "round try a DIFFERENT mechanism — never keep tweaking a direction that loses. "
                "Record ONE terse line (mechanism + measured score) and move on.\n\n"
                "No bookkeeping, no provenance files, no self-verification ritual — the "
                "scorer's number IS the verification."
            )
        else:
            from .skills.ground_truth import ground_truth_mandate

            sections.append(ground_truth_mandate("engineer").rstrip())
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
        if paper_mission and _full_emnlp:
            sections.append(
                "## Long-horizon paper execution contract\n"
                "This is not a one-file bounded patch. Treat the engineer as the\n"
                "owner of the paper trajectory for this mission. The mission spans\n"
                "MANY bounded turns (see the turn-discipline section below), not\n"
                "one marathon turn — own the whole stage across those turns, but\n"
                "land one concrete increment per turn and yield.\n\n"
                "- The injected skill playbook, current-stage checklist, and\n"
                "  checkpoint are your brief. `AGENTS.md` and the built-in paper\n"
                "  skills are reference at their paths — open a specific section\n"
                "  only when the injected context cannot answer a concrete\n"
                "  question; do not dump them in full.\n"
                "- The L2 reviewer rules against the per-stage checklist injected\n"
                "  below. Make concrete progress on its currently-unchecked items;\n"
                "  there is no `validate-*` shell command to chase. Read artifacts\n"
                "  directly when you need to decide what is and is not satisfied.\n"
                "- Fix multiple adjacent blockers across the mission's turns when\n"
                "  budget allows: evidence, `paper/main.tex`, body/page flow,\n"
                "  citations, figures, tables, reviews, assurance, manifest\n"
                "  freshness, and submission state.\n"
                "- Do not abandon the mission after one checklist item passes if\n"
                "  obvious paper-quality blockers remain and are addressable —\n"
                "  keep going on the NEXT turn (do not cram them all into this one).\n"
                "- Runtime context is for execution only. Do not copy daemon config,\n"
                "  local device/cache/path details, capability-vault paths, or\n"
                "  Argus/Codex reviewer/engineer route names into manuscript prose.\n"
                "- If the same checklist item repeats, switch from local micro-edits to\n"
                "  root-cause repair: inspect evidence sufficiency, section depth,\n"
                "  page map, stale generated artifacts, and figure/table provenance.\n"
                "- For underfilled papers, improve reader-facing prose, evidence\n"
                "  integration, and figure/table placement toward 7.5-8 main-content\n"
                "  pages; keep main/body content within 8 pages, start references\n"
                "  and appendices on page 9 or later, and do not impose a total-page\n"
                "  maximum after references begin.\n"
                "- If the full gate still fails, end with the exact remaining blockers\n"
                "  and the next concrete command."
            )
        if next_action:
            sections.append(
                "## Reviewer guidance from prior round\n"
                "The previous round was judged incomplete. Address the\n"
                "following before declaring done:\n\n"
                + next_action
            )
        if extra_guidance:
            sections.append(
                "## Operator guidance (injected since last round)\n"
                + "\n\n".join(extra_guidance)
            )
        from .skills.harness_overlay import resolve_project_root
        from .skills.stage_checklists import format_stage_checklist

        # Always-on project-venv reminder. Injected for every stage / every
        # round so the agent never has an excuse for `import X` failures or
        # for stubbing around a missing dependency. Loaded directly from the
        # bundled skill so the canonical text in the markdown is the single
        # source of truth.
        try:
            from .skills.builtins import iter_builtin_skill_texts
            for fname, body in iter_builtin_skill_texts():
                if fname == "project-venv-package-management.md":
                    sections.append("## Project venv (install anything you need here)\n" + body)
                    break
        except Exception:  # noqa: BLE001 - defensive; missing skill is non-fatal
            pass

        _proot = resolve_project_root()
        # Reuse the stage resolved (guarded) near the top of this builder so a
        # broken state read degrades to "no stage checklist" instead of raising.
        stage = _stage_now
        stage_checklist = (
            format_stage_checklist(stage, role="engineer", project_root=_proot)
            if stage
            else ""
        )
        if stage_checklist and not _measured:
            sections.append(stage_checklist)
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
        return "\n\n".join(sections)

    def _collect_extra_guidance(self) -> list[str]:
        if self.extra_guidance_provider is None:
            return []
        try:
            collected = self.extra_guidance_provider() or []
        except Exception:  # never let a hook raise into the loop
            log.exception("extra_guidance_provider raised")
            return []
        return [str(item).strip() for item in collected if str(item).strip()]

    # ------------------------------------------------------------------
    # Reviewer-owned skill memory (applied at mission end from per-round ops)
    # ------------------------------------------------------------------
    def _apply_skill_ops(
        self,
        *,
        rounds: list[RoundRecord],
        skill_task: str,
    ) -> None:
        """Hand the reviewer's per-round ``skill_ops`` (aggregated across the
        mission) to the SkillRouter, which runs the validation pipeline
        (independence → mechanical → Manager generality/correctness gate) and
        applies create/update/archive. Best-effort — the router never raises."""
        ops = self._collect_skill_ops(rounds)
        if not ops:
            return
        self.skill_router.apply_ops(ops, task=skill_task, on_event=self.on_event)

    @staticmethod
    def _collect_skill_ops(rounds: list[RoundRecord]) -> list[dict]:
        """Aggregate ``skill_ops`` across all rounds, de-duplicating identical
        (op, name, content-prefix) requests the reviewer may repeat round to
        round."""
        seen: set[tuple] = set()
        ops: list[dict] = []
        for rec in rounds or []:
            review = getattr(rec, "review", None)
            for op in (getattr(review, "skill_ops", None) or []):
                if not isinstance(op, dict):
                    continue
                key = (op.get("op"), op.get("name", ""),
                       (op.get("content", "") or "")[:200])
                if key in seen:
                    continue
                seen.add(key)
                ops.append(op)
        return ops


__all__ = ["SkillLoop", "SkillLoopConfig"]
