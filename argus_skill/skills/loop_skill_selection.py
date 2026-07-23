"""Skill-selection and Scientist-adaptation phase for ``SkillLoop.run``.

Covers: role-matcher selection, Scientist distillation on a miss, static
semantic nearest-transfer fallback, skill-text rendering/task adaptation,
adaptive-rejection persistence, and the venue-research/idea-search
candidate-source hooks that must run before/around matching. Extracted
verbatim from the historical ``SkillLoop.run`` body; only free-variable
references were rewritten to ``mission.``/``state.`` attribute access
(closures over ``nonlocal`` locals became explicit ``state`` mutation).
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ..core.event_catalog import EventType
from ..core.models import RunnerOptions
from ..core.run_gateway import run_exec as gateway_run_exec
from .adaptation import (
    adaptation_state_path,
    append_method_ledger,
    load_adaptation_state,
    save_adaptation_state,
)
from .loop_state import MissionContext, SkillSelectionState
from .role_match import render_skill_playbook
from .store import Skill

log = logging.getLogger(__name__)

# Reviewed ineffective uses are retained as evidence for later Reviewer-authored
# update/archive decisions. External/economic aborts remain neutral.
_ADAPTATION_FAILURE_CAUSES: frozenset[str] = frozenset(
    {
        "method_failure",
        "skill_gap",
    }
)


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
_TRANSFER_STOPWORDS: frozenset[str] = frozenset(
    {
        "add",
        "and",
        "application",
        "change",
        "code",
        "complete",
        "current",
        "existing",
        "feature",
        "fix",
        "flipt",
        "for",
        "from",
        "implementation",
        "instance",
        "into",
        "new",
        "not",
        "one",
        "problem",
        "production",
        "project",
        "repair",
        "repository",
        "request",
        "software",
        "statement",
        "support",
        "task",
        "tests",
        "that",
        "the",
        "this",
        "through",
        "use",
        "used",
        "using",
        "when",
        "where",
        "with",
        "wire",
        "wiring",
    }
)
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

    document_frequency = Counter(term for weights in docs for term in weights)
    n_docs = float(len(docs))

    def idf(term: str) -> float:
        return math.log((n_docs + 1.0) / (document_frequency[term] + 1.0)) + 1.0

    task_vector = {
        term: (1.0 + math.log(float(count))) * idf(term) for term, count in task_counts.items()
    }
    task_norm = math.sqrt(sum(value * value for value in task_vector.values()))
    scores: list[float] = []
    for weights in docs:
        doc_vector = {term: value * idf(term) for term, value in weights.items()}
        doc_norm = math.sqrt(sum(value * value for value in doc_vector.values()))
        if not task_norm or not doc_norm:
            scores.append(0.0)
            continue
        dot = sum(task_vector.get(term, 0.0) * value for term, value in doc_vector.items())
        scores.append(dot / (task_norm * doc_norm))
    return scores


class SkillSelectionMixin:
    """Skill selection + Scientist adaptation phase methods for ``SkillLoop``."""

    def _select_and_prepare_skill(self, mission: MissionContext) -> SkillSelectionState:
        """Run every skill-selection/adaptation sub-phase for one mission."""
        self._run_venue_research(mission)
        state = SkillSelectionState()
        self._match_skill(mission, state)
        self._maybe_distill_on_miss(mission, state)
        self._maybe_nearest_transfer(mission, state)
        self._render_skill_text_and_adapt(mission, state)
        self._load_adaptation_state(mission, state)
        self._maybe_seed_idea_candidates(mission)
        return state

    def _run_venue_research(self, mission: MissionContext) -> None:
        # Venue selection/format research must happen BEFORE skill matching. If a
        # missing/non-built-in venue is researched after matcher exclusion, the
        # same mission still hides the newly relevant venue-specific skills.
        if os.environ.get("ARGUS_SKILL_VENUE_RESEARCH", "1").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        ):
            try:
                from .stage_machine import current_stage as _vr_stage
                from .venue_research import (
                    needs_venue_research,
                    research_venue_profile,
                )
                from .vertical_select import _persisted_vertical as _vr_vert

                if (
                    self.config.paper_mission
                    and (_vr_vert(mission.workdir) or "research") == "research"
                    and (_vr_stage(mission.workdir) or "").strip().lower()
                    in {"research", "plan", "benchmark", "run", "analysis"}
                    and needs_venue_research(mission.workdir)
                ):
                    self._emit(
                        {
                            "type": "venue.research.started",
                            "text": "codex live web-search: selecting/researching target venue",
                        }
                    )
                    _ok = research_venue_profile(
                        self.engineer_runner,
                        mission.workdir,
                        model=self.config.engineer_model,
                    )
                    self._emit(
                        {
                            "type": "venue.research.completed",
                            "text": (
                                "built research/VENUE_PROFILE.json"
                                if _ok
                                else "venue research produced no profile (venue remains unresolved)"
                            ),
                            "ok": _ok,
                        }
                    )
            except Exception:  # noqa: BLE001 — venue research never blocks the loop
                log.debug("venue-research hook skipped", exc_info=True)

    def _match_skill(self, mission: MissionContext, state: SkillSelectionState) -> None:
        from .venue_profiles import venue_excluded_skill_files

        state.match = self.skill_router.select(
            mission.skill_task,
            extra_exclude=venue_excluded_skill_files(mission.workdir),
            # Every formal Argus workflow exercises Skill matching, including
            # the initial empty-library task used to bootstrap self-evolution.
            force_empty_match=True,
        )
        state.matcher_tokens = state.match.input_tokens + state.match.output_tokens
        state.matcher_input_tokens = state.match.input_tokens
        state.matcher_cached_input_tokens = state.match.cached_input_tokens
        state.matcher_output_tokens = state.match.output_tokens
        state.matcher_premium_requests = state.match.premium_requests
        # Own-role playbooks drive distill/writeback; cross-role references
        # are read-only context and never written back to.
        state.primary_skills: list[Skill] = list(state.match.primary_skills)
        state.reference_skills: list[Skill] = list(state.match.reference_skills)
        state.skill: Skill | None = state.match.primary
        state.strict_skill_hit = state.skill is not None
        # Reuse this one matcher result for Reviewer context too. Engineer-role
        # references are Reviewer-owned skills; the Engineer's own strict hit
        # becomes read-only context for Reviewer. This avoids a second matcher
        # call before every review round.
        state.reviewer_skill_block = render_skill_playbook(
            self.skill_store,
            state.reference_skills[:1],
            [],
        )
        if state.strict_skill_hit and state.skill is not None:
            pointer = _reviewer_engineer_skill_pointer(
                state.skill,
                self.skill_store.render_skill(state.skill),
            )
            state.reviewer_skill_block = (
                f"{state.reviewer_skill_block}\n\n{pointer}"
                if state.reviewer_skill_block
                else pointer
            )
        state.nearest_transfer_fallback = False
        state.low_confidence_transfer_hint = ""
        state.skill_distilled = False
        state.distill_result = None

    def _maybe_distill_on_miss(self, mission: MissionContext, state: SkillSelectionState) -> None:
        _ = mission, state

    def _maybe_nearest_transfer(self, mission: MissionContext, state: SkillSelectionState) -> None:
        if state.skill is None and self.config.nearest_transfer_enabled:
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
                mission.skill_task,
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
                    state.skill = candidate
                    state.primary_skills = [state.skill]
                    state.nearest_transfer_fallback = True
                    text = (
                        f"no high-fit skill; transfer fallback selected nearest "
                        f"`{state.skill.name}` (static semantic score={score:.3f})"
                    )
                else:
                    state.low_confidence_transfer_hint = (
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
                self._emit(
                    {
                        "type": "match.info",
                        "selection_method": "static-semantic-tfidf",
                        "score": round(score, 6),
                        "candidate_scores": candidate_scores,
                        "text": text,
                    }
                )

    def _render_skill_text_and_adapt(
        self, mission: MissionContext, state: SkillSelectionState
    ) -> None:
        state.skill_text = render_skill_playbook(
            self.skill_store, state.primary_skills, state.reference_skills
        )
        if state.low_confidence_transfer_hint:
            state.skill_text = state.low_confidence_transfer_hint + (
                f"\n\n{state.skill_text}" if state.skill_text else ""
            )
        state.skill_name = state.skill.name if state.skill else None
        state.learning_target_name = (
            state.skill.name if state.strict_skill_hit and state.skill is not None else ""
        )
        if state.skill is not None and self.config.skill_adapter_enabled:
            source_skill_text = self.skill_store.render_skill(state.skill, full=True)
            adapter_reasoning_effort = self.config.resolved_skill_adapter_reasoning_effort()
            max_bullets = (
                self.config.nearest_transfer_max_bullets
                if state.nearest_transfer_fallback
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
                f"## Current task\n{mission.skill_task}\n\n"
                f"## Closest reusable skill: {state.skill.name}\n{source_skill_text}"
            )
            self._emit(
                {
                    "type": EventType.SKILL_TRANSFER_STARTED,
                    "skill_name": state.skill.name,
                    "model": self.config.resolved_skill_adapter_model(),
                    "reasoning_effort": adapter_reasoning_effort,
                    "text": f"adapting matched skill {state.skill.name} to current task",
                }
            )
            adapter_extra_args = list(self.config.extra_args or [])
            adapter_sandbox: str | None = "read-only"
            if str(getattr(self.engineer_runner, "_backend_name", "") or "").lower() == "copilot":
                adapter_sandbox = None
                adapter_extra_args.extend(
                    [
                        "--no-custom-instructions",
                        "--disable-builtin-mcps",
                        "--available-tools=",
                    ]
                )
            try:
                transfer_result = gateway_run_exec(
                    self.engineer_runner,
                    prompt=adapter_prompt,
                    options=RunnerOptions(
                        model=self.config.resolved_skill_adapter_model() or None,
                        reasoning_effort=adapter_reasoning_effort,
                        extra_args=adapter_extra_args or None,
                        sandbox_mode=adapter_sandbox,
                        skip_git_repo_check=True,
                        full_auto=False,
                        dangerous_yolo=False,
                    ),
                    run_label="skill-adapter",
                    resume_thread_id=None,
                )
                adapted = str(getattr(transfer_result, "last_agent_message", "") or "").strip()
                if (
                    int(getattr(transfer_result, "exit_code", 0) or 0) == 0
                    and not getattr(transfer_result, "fatal_error", None)
                    and adapted
                ):
                    state.skill_text = (
                        "## Task-adapted skill guideline\n"
                        f"Source skill: {state.skill.name}\n\n{adapted}"
                    )
                    state.distill_result = transfer_result
                    self._emit(
                        {
                            "type": EventType.SKILL_TRANSFER_COMPLETED,
                            "skill_name": state.skill.name,
                            "success": True,
                            "text": f"adapted skill {state.skill.name} for current task",
                        }
                    )
                else:
                    self._emit(
                        {
                            "type": EventType.SKILL_TRANSFER_COMPLETED,
                            "skill_name": state.skill.name,
                            "success": False,
                            "text": "skill adapter failed; using original skill",
                        }
                    )
            except Exception:  # noqa: BLE001 - original skill remains a safe fallback
                log.debug("skill adapter failed", exc_info=True)
                self._emit(
                    {
                        "type": EventType.SKILL_TRANSFER_COMPLETED,
                        "skill_name": state.skill.name,
                        "success": False,
                        "text": "skill adapter raised; using original skill",
                    }
                )

    def _load_adaptation_state(self, mission: MissionContext, state: SkillSelectionState) -> None:
        state.adaptation_file: Path | None = None
        state.adaptation_disabled = False
        adaptation_state: dict[str, Any] = {
            "trigger_count": 0,
            "spent_usd": 0.0,
            "rejection_streak": [],
            "method_records": [],
        }
        if self.config.session_id and self.config.checkpoint_path is not None:
            state.adaptation_file = adaptation_state_path(
                self.config.checkpoint_path,
                mission.run_id,
            )
            try:
                adaptation_state = load_adaptation_state(state.adaptation_file, mission.run_id)
            except (OSError, ValueError):
                state.adaptation_disabled = True
                log.warning(
                    "Skill adaptation state is unreadable; Scientist adaptation "
                    "disabled for mission %s",
                    mission.run_id,
                    exc_info=True,
                )
        state.adaptation_triggers = int(adaptation_state["trigger_count"])
        state.adaptation_spent = float(adaptation_state["spent_usd"])
        state.rejection_streak: list[dict[str, Any]] = list(adaptation_state["rejection_streak"])
        state.method_records: list[dict[str, Any]] = list(adaptation_state["method_records"])

    def _persist_adaptation_state(self, mission, state) -> None:
        if state.adaptation_file is None or state.adaptation_disabled:
            return
        save_adaptation_state(
            state.adaptation_file,
            mission.run_id,
            trigger_count=state.adaptation_triggers,
            spent_usd=state.adaptation_spent,
            rejection_streak=state.rejection_streak,
            method_records=state.method_records,
        )

    def _adapt_after_rejections(self, mission, state, rounds: list) -> str:
        persistent_adaptation = state.adaptation_file is not None
        if persistent_adaptation:
            if not rounds or state.adaptation_disabled:
                return ""
            interval = max(
                1,
                int(self.config.adaptive_rejection_threshold or 1),
            )
            latest = rounds[-1]
            qualifies = (
                latest.review.status == "continue"
                and not latest.review.backend_unavailable
                and not bool(latest.fatal_error)
            )
            if qualifies:
                state.rejection_streak.append(
                    {
                        "round_index": latest.round_index,
                        "reason": latest.review.reason,
                        "next_action": latest.review.next_action,
                    }
                )
                del state.rejection_streak[:-interval]
            else:
                state.rejection_streak.clear()
            self._persist_adaptation_state(mission, state)
            if len(rounds) >= int(self.config.max_rounds or 0):
                return ""
            if len(state.rejection_streak) < interval:
                return ""
            rejected = [dict(item) for item in state.rejection_streak[-interval:]]
            review_rounds = [int(item["round_index"]) for item in rejected]
            failure_reasons = [str(item["reason"]) for item in rejected]
            max_triggers = max(
                0,
                int(self.config.adaptive_skill_max_triggers or 0),
            )
            if state.adaptation_triggers >= max_triggers:
                state.rejection_streak.clear()
                self._persist_adaptation_state(mission, state)
                return ""
            evidence = "\n".join(
                f"- Round {item['round_index']}: {item['reason']}; next: {item['next_action']}"
                for item in rejected
            )
        else:
            interval = max(0, int(self.config.adaptive_skill_interval or 0))
            if state.skill is None or interval == 0 or len(rounds) % interval:
                return ""
            recent = rounds[-interval:]
            review_rounds = [rec.round_index for rec in recent]
            failure_reasons = [rec.review.reason for rec in recent]
            evidence = "\n".join(
                f"- Round {rec.round_index}: {rec.review.reason}; next: {rec.review.next_action}"
                for rec in recent
            )
        if not state.skill_text:
            return ""
        from .scientist import (
            SkillScientist,
            parse_mechanism_change,
        )

        if persistent_adaptation:
            state.adaptation_triggers += 1
            state.rejection_streak.clear()
            self._persist_adaptation_state(mission, state)
        self._emit(
            {
                "type": EventType.SKILL_SCIENTIST_ADAPTATION_STARTED,
                "text": f"{interval} reviewer rejections; seeking a different playbook",
                "vertical": mission.active_vertical,
                "trigger_index": state.adaptation_triggers if persistent_adaptation else 0,
                "failure_reasons": failure_reasons,
            }
        )
        scientist = SkillScientist(
            self.engineer_runner,
            model=self.config.engineer_model,
            reasoning_effort=self.config.engineer_reasoning_effort,
            role_banner=mission.scientist_adaptation_banner,
        )
        raw_skill = scientist.distill_alternative(
            mission.skill_task,
            evidence,
            current_skill=state.skill_text,
            method_history="\n".join(str(record) for record in state.method_records),
        )
        state.distill_result = scientist.last_result
        raw_result_cost = getattr(state.distill_result, "cost_usd", None)
        try:
            settled_cost = float(raw_result_cost)
        except (OverflowError, TypeError, ValueError):
            settled_cost = float("nan")
        result_cost = settled_cost if math.isfinite(settled_cost) and settled_cost >= 0 else None
        if persistent_adaptation and result_cost is not None:
            state.adaptation_spent += settled_cost
            self._persist_adaptation_state(mission, state)
        if not raw_skill:
            if persistent_adaptation:
                record = {
                    "status": "no_alternative",
                    "trigger_index": state.adaptation_triggers,
                    "review_rounds": review_rounds,
                    "failure_reasons": failure_reasons,
                    "prior_skill": state.skill_name or "",
                    "scientist_cost_usd": result_cost,
                }
                append_method_ledger(mission.workdir, record)
                state.method_records.append(record)
                self._persist_adaptation_state(mission, state)
            return ""
        mechanism_change = parse_mechanism_change(raw_skill) if persistent_adaptation else None
        if persistent_adaptation and mechanism_change is None:
            record = {
                "status": "mechanism_change_rejected",
                "trigger_index": state.adaptation_triggers,
                "review_rounds": review_rounds,
                "failure_reasons": failure_reasons,
                "prior_skill": state.skill_name or "",
                "scientist_cost_usd": result_cost,
            }
            append_method_ledger(mission.workdir, record)
            state.method_records.append(record)
            self._persist_adaptation_state(mission, state)
            return ""
        if (
            persistent_adaptation
            and "".join(raw_skill.split()).casefold()
            == "".join(state.skill_text.split()).casefold()
        ):
            record = {
                "status": "duplicate_mechanism_rejected",
                "trigger_index": state.adaptation_triggers,
                "review_rounds": review_rounds,
                "failure_reasons": failure_reasons,
                "prior_skill": state.skill_name or "",
                "scientist_cost_usd": result_cost,
            }
            append_method_ledger(mission.workdir, record)
            state.method_records.append(record)
            self._persist_adaptation_state(mission, state)
            return ""
        distilled = self.skill_router.create_from_scientist(
            raw_skill, task=mission.skill_task, on_event=self._emit
        )
        if distilled is None:
            if persistent_adaptation:
                record = {
                    "status": "invalid_alternative",
                    "trigger_index": state.adaptation_triggers,
                    "failure_reasons": failure_reasons,
                }
                append_method_ledger(mission.workdir, record)
                state.method_records.append(record)
                self._persist_adaptation_state(mission, state)
            return ""
        adaptive_text = render_skill_playbook(self.skill_store, [distilled], [])
        state.skill = distilled
        state.skill_text = (
            adaptive_text if persistent_adaptation else state.skill_text + "\n\n" + adaptive_text
        )
        state.skill_name = distilled.name
        state.skill_distilled = True
        if persistent_adaptation:
            record = {
                "status": "created",
                "trigger_index": state.adaptation_triggers,
                "review_rounds": review_rounds,
                "failure_reasons": failure_reasons,
                "new_skill": distilled.name,
                "mechanism_change_required": True,
                "mechanism_change": mechanism_change,
                "scientist_cost_usd": result_cost,
            }
            ledger_path = append_method_ledger(mission.workdir, record)
            state.method_records.append(record)
            self._persist_adaptation_state(mission, state)
        self._emit(
            {
                "type": EventType.SKILL_SCIENTIST_ADAPTATION_CREATED,
                "text": f"Scientist created alternative skill {distilled.name}",
                "vertical": mission.active_vertical,
                "trigger_index": state.adaptation_triggers if persistent_adaptation else 0,
                "method_ledger": (
                    str(ledger_path.relative_to(mission.workdir)) if persistent_adaptation else ""
                ),
            }
        )
        return adaptive_text

    def _maybe_seed_idea_candidates(self, mission: MissionContext) -> None:
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
            "0",
            "false",
            "no",
            "off",
        ):
            try:
                from .idea_search import (
                    _already_seeded as _ideas_seeded,
                )
                from .idea_search import (
                    augment_idea_candidates as _augment_ideas,
                )
                from .stage_machine import current_stage as _cur_stage
                from .vertical_select import _persisted_vertical

                is_research_vertical = (
                    _persisted_vertical(mission.workdir) or "research"
                ) == "research"
                if (
                    self.config.paper_mission
                    and is_research_vertical
                    and (_cur_stage(mission.workdir) or "").strip().lower() == "research"
                    and not _ideas_seeded(mission.workdir)
                ):
                    self._emit(
                        {
                            "type": "idea.search.started",
                            "text": "codex live web-search: seeding candidate ideas",
                        }
                    )
                    _n = _augment_ideas(
                        self.engineer_runner,
                        mission.workdir,
                        # Candidate discovery needs the clean research direction,
                        # not the Engineer's full task prelude. Passing ``task``
                        # leaked machine special prompts (for example "/root is
                        # ephemeral; put durable artifacts under /data") into the
                        # "Research direction" field and caused the search agent to
                        # relocate an assigned project instead of researching it.
                        direction=(
                            self.config.continuous_objective.strip() or mission.request_anchor
                        ),
                        model=self.config.engineer_model,
                    )
                    self._emit(
                        {
                            "type": "idea.search.completed",
                            "text": (
                                f"appended {_n} web-search candidate(s) to "
                                "research/IDEA_CANDIDATES.md"
                            ),
                            "count": _n,
                        }
                    )
            except Exception:  # noqa: BLE001 — a candidate source never blocks
                log.debug("idea-search hook skipped", exc_info=True)
                self._emit(
                    {
                        "type": "idea.search.skipped",
                        "text": "idea-search hook error (fail-open)",
                    }
                )
