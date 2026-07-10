"""Skill-store compaction — merge near-duplicate playbooks.

Why: as the daemon runs, near-identical skills accumulate (same family,
slightly different framing). The matcher then has to score them all
on every match (~14k tokens per call × N candidates). Without
compaction the matcher cost grows linearly forever.

Approach — fully LLM-judged, batched (mirrors ``SkillStore.find_relevant``'s
one-call-per-batch shape: O(1) calls per batch, never O(n^2) pairwise calls):

  1. ``llm_plan_compaction`` asks an LLM, over compact summaries
     (name/description/category — progressive disclosure, never full skill
     bodies), which skills GROUP together as the same underlying capability.
     The model answers ONLY yes/no-shaped grouping — no similarity score is
     ever requested or computed; its verdict is trusted directly (the only
     mechanical check is that every name it returns actually exists in the
     batch, i.e. it isn't a hallucinated name).
  2. For each group of size >= 2, the harness (never the model) picks a
     representative — highest ``version`` x ``len(task_history)``
     (the most-reinforced) — since only the harness has that real
     proven-usage data, and ``archive``s the rest via
     ``lifecycle.archive_skill``. A protected/governing skill is NEVER an
     archive candidate, regardless of what the model said.

There is no mechanical/lexical fallback: when no judge runner is configured,
or every batch fails to produce a usable verdict, this pass is simply a
no-op for that mission (nothing is compacted) — never a silent degrade to a
scored heuristic.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.models import RunnerOptions

log = logging.getLogger(__name__)

MIN_CLUSTER_SIZE = 2


@dataclass
class CompactionPlan:
    clusters: list[list[Any]] = field(default_factory=list)
    keep: list[Any] = field(default_factory=list)
    archive: list[Any] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# A skill is PROTECTED (never a compaction-archive candidate) under the exact
# same rule SkillRouter enforces for skill_ops: an explicit ``protected: true``
# frontmatter flag, or a governing CATEGORY. Duplicated here (not imported)
# because ``skill_router`` imports FROM this module — importing back would be
# circular. Keep this set in sync with ``skill_router._PROTECTED_CATEGORIES``.
_PROTECTED_CATEGORIES = frozenset({"anti-cheat", "guardrail", "role-identity"})


def _is_protected(skill: Any) -> bool:
    if getattr(skill, "protected", False):
        return True
    category = (getattr(skill, "category", "") or "").strip().lower()
    return category in _PROTECTED_CATEGORIES


def _representative(cluster: list[Any]) -> Any:
    def score(s: Any) -> tuple[int, int, int, str]:
        version = int(getattr(s, "version", 1) or 1)
        history = len(getattr(s, "task_history", []) or [])
        # Protected (governing) skills ALWAYS win the representative pick —
        # never a candidate for archival, matching SkillRouter's self-
        # governance floor (compaction must not become a backdoor around it).
        protected_rank = 0 if _is_protected(s) else 1
        # Prefer (version * (1+history)) — proven skills win ties.
        return (protected_rank, -(version * (1 + history)), -history, getattr(s, "name", ""))
    return sorted(cluster, key=score)[0]


def _build_plan_from_clusters(clusters: list[list[Any]]) -> CompactionPlan:
    """"Which skills group together" (the LLM's job) -> "which one to keep"
    (always a harness/mechanical decision — ``_representative`` reads REAL
    usage data, ``version`` / ``task_history`` reuse count, and protected
    status, none of which the model ever saw)."""
    plan = CompactionPlan()
    plan.clusters = clusters
    for cluster in clusters:
        rep = _representative(cluster)
        plan.keep.append(rep)
        for s in cluster:
            if s is rep:
                continue
            # Defense in depth: even if a cluster somehow contains more than
            # one protected skill (so only one could win the "keep" pick
            # above), a protected skill is NEVER archived — full stop.
            if _is_protected(s):
                plan.notes.append(
                    f"kept protected skill '{getattr(s, 'name', '?')}' out of "
                    "the archive list (self-governance floor)"
                )
                continue
            plan.archive.append(s)
    return plan


# Batched-clustering cap for the LLM judge — mirrors
# ``SkillStore._matcher_max_candidates`` (same default, own env var so the
# two call sites can be tuned independently). The common case (library size
# <= cap) is a single judge call; larger libraries are chunked into
# deterministic, on-disk-order batches and every batch's clusters are
# unioned, exactly like the matcher's own batching.
_MAX_COMPACT_CANDIDATES_ENV = "ARGUS_SKILL_COMPACT_MAX_CANDIDATES"


def _compact_batches(skills: list[Any]) -> list[list[Any]]:
    cap = max(1, int(os.environ.get(_MAX_COMPACT_CANDIDATES_ENV, "80") or "80"))
    if len(skills) <= cap:
        return [skills]
    return [skills[i:i + cap] for i in range(0, len(skills), cap)]


def _summary_of(skill: Any) -> dict[str, str]:
    return {
        "name": getattr(skill, "name", "") or "",
        "description": getattr(skill, "description", "") or "",
        "category": getattr(skill, "category", "") or "",
    }


def _parse_compaction_response(text: str) -> list[dict] | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        left, right = text.find("{"), text.rfind("}")
        parsed = json.loads(text[left:right + 1]) if left >= 0 < right else json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    clusters = parsed.get("clusters")
    return clusters if isinstance(clusters, list) else []


def llm_plan_compaction(
    skills: list[Any],
    *,
    judge_runner: Any,
    judge_model: str = "",
    judge_reasoning_effort: str = "high",
    on_event: Any = None,
) -> CompactionPlan | None:
    """LLM-judged batched clustering (mirrors
    ``SkillStore.find_relevant``'s one-call-per-batch shape, O(1) calls per
    batch instead of O(n^2) pairwise judge calls), used for the periodic
    auto-compaction sweep. There is no lexical/scored fallback: this is the
    ONLY clustering mechanism.

    The LLM only decides WHICH skills group together (semantic judgment,
    its strength, expressed as a plain grouping — never a similarity score);
    the harness decides which one in each group to KEEP via
    ``_representative``/``_build_plan_from_clusters`` — that pick reads real
    proven-usage data (``version`` / ``task_history``) and the
    protected-skill floor, neither of which the model saw in the prompt.

    Returns ``None`` when ``judge_runner`` is not configured, or EVERY batch
    failed to produce a usable response (backend down, malformed output) —
    the caller treats that as "nothing to do this round", never falls back
    to a mechanical heuristic. A batch that legitimately reports no groups
    is NOT a failure — an empty (but non-None) plan is returned.

    Safety, independent of what the LLM says:
      * every name must resolve to a REAL skill in this exact batch and be
        copied verbatim — a hallucinated name silently drops from its group
        (existence check, not a similarity judgment); a group left with < 2
        resolved skills is dropped entirely;
      * a skill already claimed by an earlier group in this run cannot be
        claimed again;
      * a protected/governing skill is NEVER archived (enforced by
        ``_build_plan_from_clusters``).
    """
    if judge_runner is None:
        return None
    from .skill_prompts import Prompts

    clusters: list[list[Any]] = []
    claimed: set[str] = set()
    any_batch_succeeded = False
    for batch in _compact_batches(skills):
        if len(batch) < MIN_CLUSTER_SIZE:
            continue
        prompt = Prompts.skill_compaction_batch([_summary_of(s) for s in batch])
        try:
            result = judge_runner.run_exec(
                prompt=prompt,
                options=RunnerOptions(
                    model=judge_model or None,
                    reasoning_effort=judge_reasoning_effort,
                    skip_git_repo_check=True,
                    full_auto=True,
                ),
                run_label="skill.compaction_batch",
            )
            from ..core.cost_events import emit_codex_util_cost

            emit_codex_util_cost(
                on_event,
                layer="reviewer",
                model=judge_model,
                result=result,
                run_label="skill.compaction_batch",
            )
        except Exception as exc:  # noqa: BLE001 — judge is best-effort
            log.warning("skill compaction judge failed (%s: %s)", type(exc).__name__, exc)
            continue
        raw_clusters = _parse_compaction_response(getattr(result, "last_agent_message", "") or "")
        if raw_clusters is None:
            continue
        any_batch_succeeded = True
        batch_names = {getattr(s, "name", ""): s for s in batch}
        for raw_group in raw_clusters:
            if not isinstance(raw_group, list):
                continue
            resolved: list[Any] = []
            for raw_name in raw_group:
                name = str(raw_name or "").strip()
                if not name or name in claimed:
                    continue
                skill = batch_names.get(name)
                if skill is None:
                    continue
                resolved.append(skill)
            if len(resolved) < MIN_CLUSTER_SIZE:
                continue
            for s in resolved:
                claimed.add(getattr(s, "name", ""))
            clusters.append(resolved)
    if not any_batch_succeeded:
        return None
    plan = _build_plan_from_clusters(clusters)
    if not clusters:
        plan.notes.append("llm judge found no clusters this pass")
    return plan


def execute_plan(
    plan: CompactionPlan, *, archive_skill_fn: Any
) -> dict[str, Any]:
    """Execute the plan; return a result dict with archived paths."""
    archived: list[str] = []
    errors: list[str] = []
    for s in plan.archive:
        path = getattr(s, "path", None)
        if not path:
            errors.append(f"{getattr(s, 'name', '?')}: no path on skill")
            continue
        try:
            target = archive_skill_fn(path)
            if target is None:
                errors.append(f"{getattr(s, 'name', '?')}: source missing")
            else:
                archived.append(str(target))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{getattr(s, 'name', '?')}: {type(exc).__name__}: {exc}")
    return {"archived": archived, "errors": errors}


EventSink = Any  # Callable[[dict], None] | None — kept loose to avoid an import cycle.


def auto_compact_skills(
    skills_dir: Path,
    *,
    judge_runner: Any = None,
    judge_model: str = "",
    judge_reasoning_effort: str = "high",
    on_event: EventSink = None,
) -> dict[str, int]:
    """Automatic (unattended) compaction pass, run after every mission close.

    A no-op whenever ``judge_runner`` is not configured, or the library is
    empty/singleton, or the LLM path finds nothing to do — there is no
    lexical/scored fallback. Archival is the SAME reversible
    ``archive_skill`` move (moves the file to ``skills/_archive/``, never a
    hard delete), and, exactly like ``SkillRouter``'s self-governance floor,
    a protected/governing skill is NEVER an archive candidate. Fail-soft: any
    error is caught and reported via ``on_event``/return value, never raised
    into the mission.
    """
    counts = {"clusters": 0, "archived": 0, "errors": 0}
    try:
        if judge_runner is None:
            return counts
        skills_dir = Path(skills_dir)
        if not skills_dir.is_dir():
            return counts
        from .lifecycle import archive_skill
        from .store import SkillStore

        store = SkillStore(skills_dir=skills_dir)
        summaries = store.list_summaries()
        if not summaries:
            return counts
        skills = [store.load(s["path"]) for s in summaries]
        plan = llm_plan_compaction(
            skills, judge_runner=judge_runner, judge_model=judge_model,
            judge_reasoning_effort=judge_reasoning_effort,
            on_event=on_event,
        )
        if plan is None:
            return counts
        counts["clusters"] = len(plan.clusters)
        if not plan.archive:
            return counts
        result = execute_plan(plan, archive_skill_fn=archive_skill)
        counts["archived"] = len(result["archived"])
        counts["errors"] = len(result["errors"])
        if callable(on_event):
            for cluster in plan.clusters:
                rep = next((s for s in plan.keep if s in cluster), None)
                if rep is None:
                    continue
                for s in cluster:
                    if s is rep or _is_protected(s):
                        continue
                    try:
                        on_event({
                            "type": "skill.compacted",
                            "text": (
                                f"auto-compacted '{getattr(s, 'name', '?')}' "
                                f"into '{getattr(rep, 'name', '?')}' (near-duplicate)"
                            ),
                        })
                    except Exception:  # noqa: BLE001 — telemetry must never break the loop
                        log.debug("skill.compacted emit failed", exc_info=True)
            for err in result["errors"]:
                try:
                    on_event({"type": "skill.compact.error", "text": err})
                except Exception:  # noqa: BLE001
                    log.debug("skill.compact.error emit failed", exc_info=True)
    except Exception as exc:  # noqa: BLE001 — auto-compaction must never block a mission
        log.warning("auto_compact_skills failed (%s: %s)", type(exc).__name__, exc)
        counts["errors"] += 1
    return counts


__all__ = [
    "CompactionPlan",
    "llm_plan_compaction",
    "execute_plan",
    "auto_compact_skills",
]
