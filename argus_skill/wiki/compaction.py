"""Wiki-page compaction — merge near-duplicate pages, the wiki's structured
counterpart to ``skills.compaction``.

Why: two missions that never see each other's work can each distill the SAME
technique into the wiki under a different id/title (the create-time
independence check in ``WikiRouter`` catches this for pages proposed AFTER a
duplicate already exists, but not two pages that predate the check, nor a
race between two concurrent missions each comparing against a library
snapshot that doesn't yet contain the other's proposal). This module is the
periodic, unattended cleanup pass for what slips through — auto-invoked after
every mission close (see ``SkillLoopConfig.auto_compact_enabled`` in
``argus_skill.loop``), mirroring ``skills.compaction``'s design:

  1. ``llm_cluster_wiki`` asks an LLM, over compact summaries
     (title/card_type/a body excerpt — progressive disclosure), which pages
     GROUP together as the same underlying knowledge. The model answers
     ONLY yes/no-shaped grouping — no similarity score is ever requested or
     computed; its verdict is trusted directly (the only mechanical check is
     that every title it returns actually exists in the batch, i.e. it isn't
     a hallucinated title). There is no lexical/scored fallback: when no
     judge runner is configured, or every batch fails, this pass is simply a
     no-op for that mission.
  2. For each group of size >= 2, keep the most MATURE representative
     (higher ``status`` — stable > candidate > scratch — wins; ties broken by
     more cited ``sources``, then by id for determinism — real maturity
     signals the model never sees) and ``retire`` the rest via
     ``WikiStore.retire_page`` — a tombstone, never a hard delete, exactly
     like a skill's reversible archive.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

from ..core.models import RunnerOptions

log = logging.getLogger(__name__)

EventSink = Callable[[dict], None] | None

MIN_CLUSTER_SIZE = 2
# Batched-clustering cap for the LLM judge — mirrors
# ``skills.compaction._MAX_COMPACT_CANDIDATES_ENV`` (own env var so the two
# libraries can be tuned independently).
_MAX_COMPACT_CANDIDATES_ENV = "ARGUS_SKILL_WIKI_COMPACT_MAX_CANDIDATES"

_STATUS_RANK = {"stable": 0, "candidate": 1, "scratch": 2}


def _representative(cluster: list[Any]) -> Any:
    def score(p: Any) -> tuple[int, int, str]:
        status_rank = _STATUS_RANK.get(str(getattr(p, "status", "") or ""), 2)
        sources = len(getattr(p, "sources", []) or [])
        # Prefer stable > candidate > scratch, then more cited sources.
        return (status_rank, -sources, getattr(p, "id", ""))
    return sorted(cluster, key=score)[0]


def build_duplicate_check_prompt(
    *, title: str, body: str, card_type: str, existing_pages: list[Any],
) -> str:
    """Ask a small model whether a NEW page proposal duplicates an EXISTING
    one — semantic judgment over compact summaries (title + card_type + a
    short body excerpt), the wiki-page counterpart to
    ``skills.skill_prompts.Prompts.skill_duplicate_check`` (same
    progressive-disclosure shape: summaries only, never every existing page's
    full body in one prompt beyond a short excerpt).

    Catches paraphrased duplicates a lexical/cosine comparison misses (e.g.
    two missions that each distill the SAME technique under a different
    id/title)."""
    def _excerpt(text: str, limit: int = 200) -> str:
        text = " ".join((text or "").split())
        return text[:limit] + ("…" if len(text) > limit else "")

    listing = "\n".join(
        f"- **{p.title}** [{p.type}]: {_excerpt(p.body)}"
        for p in existing_pages
    ) or "(wiki is empty)"
    return (
        "You are the project wiki's independence judge. A NEW page has been "
        "proposed for a shared, reusable project knowledge base. Decide "
        "whether it is a near-duplicate of an EXISTING page — i.e. it "
        "records the SAME underlying fact/technique/pattern, even if the "
        "title or wording differs.\n\n"
        f"## New page proposal\n- **{title}** [{card_type}]: {_excerpt(body)}\n\n"
        f"## Existing pages in the wiki\n{listing}\n\n"
        "## Instructions\n"
        "Reply with ONLY a JSON object: "
        "{\"duplicate\": true|false, \"of\": \"<existing page title or empty "
        "string>\", \"why\": \"<one short clause>\"}.\n"
        "- `duplicate: true` ONLY when the new page records the SAME "
        "underlying knowledge as one existing page — different title/wording "
        "alone does NOT make two pages distinct.\n"
        "- Pages of a DIFFERENT card_type (e.g. a `conflict` vs a "
        "`technique`) serve a different purpose and are NEVER duplicates of "
        "each other, even on the same topic.\n"
        "- When genuinely unsure, prefer `duplicate: false` (a missed "
        "near-duplicate is cheaply caught by the periodic housekeeping pass; "
        "a wrongly-rejected distinct page is knowledge lost)."
    )


def build_compaction_batch_prompt(existing_pages: list[Any]) -> str:
    """Ask a small model to find every GROUP of near-duplicate pages in ONE
    batch of the wiki — the batched-clustering counterpart to
    ``build_duplicate_check_prompt`` above, mirroring
    ``skills.skill_prompts.Prompts.skill_compaction_batch``'s shape.

    Deliberately asks ONLY for the grouping, never which page to keep: the
    model sees title/card_type/a body excerpt here, not each page's PROVEN
    maturity signal (``status`` — scratch/candidate/stable, promoted by
    real cross-mission reference count — and cited ``sources`` count) that
    the harness has and the model does not. The harness picks the
    representative from that data (see ``_representative``); the model's
    job is purely "which pages record the same thing?"."""
    def _excerpt(text: str, limit: int = 200) -> str:
        text = " ".join((text or "").split())
        return text[:limit] + ("…" if len(text) > limit else "")

    listing = "\n".join(
        f"- **{p.title}** [{p.type}]: {_excerpt(p.body)}"
        for p in existing_pages
    )
    return (
        "You are the project wiki's compaction judge, doing periodic "
        "housekeeping on a shared knowledge base. Below is a batch of pages "
        "currently in it (title + card_type + a short body excerpt). Find "
        "every GROUP of 2+ pages that record the SAME underlying "
        "fact/technique/pattern (paraphrases of each other, even with "
        "disjoint title/wording) — these are near-duplicates that should be "
        "merged down to ONE. You decide WHICH pages group together; the "
        "harness decides which one in each group to keep (from proven "
        "maturity/reference data you cannot see) — do not try to rank "
        "them.\n\n"
        f"## Pages in this batch\n{listing}\n\n"
        "## Instructions\n"
        "Reply with ONLY a JSON object: "
        "{\"clusters\": [[\"<title>\", \"<title>\", ...], ...]} — a list of "
        "groups, each group a list of 2+ exact page titles.\n"
        "- Only emit a group when its pages record the SAME underlying "
        "knowledge — different title/wording alone does NOT make two pages "
        "distinct.\n"
        "- Pages of a DIFFERENT card_type (e.g. a `conflict` vs a "
        "`technique`) serve a different purpose and can NEVER group "
        "together, even on the same topic.\n"
        "- Every title MUST be copied EXACTLY from the list above — never "
        "invent or paraphrase one.\n"
        "- A page may appear in AT MOST one group.\n"
        "- Most batches have NO groups at all — `{\"clusters\": []}` is the "
        "common, correct answer. When unsure whether two pages truly "
        "duplicate, leave them out (a missed near-duplicate is cheap to "
        "catch on a later pass; a wrongly-merged distinct page is knowledge "
        "lost)."
    )


def _compact_batches(pages: list[Any]) -> list[list[Any]]:
    cap = max(1, int(os.environ.get(_MAX_COMPACT_CANDIDATES_ENV, "80") or "80"))
    if len(pages) <= cap:
        return [pages]
    return [pages[i:i + cap] for i in range(0, len(pages), cap)]


def _parse_cluster_response(text: str) -> list | None:
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


def llm_cluster_wiki(
    pages: list[Any],
    *,
    judge_runner: Any,
    judge_model: str = "",
    judge_reasoning_effort: str = "high",
) -> list[list[Any]] | None:
    """LLM-judged batched grouping (mirrors
    ``skills.compaction.llm_plan_compaction``'s shape) used by the periodic
    ``auto_compact_wiki`` sweep. There is no lexical/scored fallback: this
    is the ONLY grouping mechanism.

    Returns ``None`` when ``judge_runner`` is not configured, or EVERY batch
    failed to produce a usable response — the caller treats that as
    "nothing to do this round", never falls back to a mechanical heuristic.
    A batch that legitimately reports no groups is NOT a failure — an empty
    (but non-None) list is returned.

    Safety, independent of what the LLM says: every title must resolve to a
    REAL page in this exact batch and be copied verbatim (a hallucinated
    title silently drops from its group — existence check, not a similarity
    judgment; a group left with < 2 resolved pages is dropped entirely); a
    page already claimed by an earlier group cannot be claimed again."""
    if judge_runner is None:
        return None
    clusters: list[list[Any]] = []
    claimed: set[str] = set()
    any_batch_succeeded = False
    for batch in _compact_batches(pages):
        if len(batch) < MIN_CLUSTER_SIZE:
            continue
        prompt = build_compaction_batch_prompt(batch)
        try:
            result = judge_runner.run_exec(
                prompt=prompt,
                options=RunnerOptions(
                    model=judge_model or None,
                    reasoning_effort=judge_reasoning_effort,
                    skip_git_repo_check=True,
                    full_auto=True,
                ),
                run_label="wiki.compaction_batch",
            )
        except Exception as exc:  # noqa: BLE001 — judge is best-effort
            log.warning("wiki compaction judge failed (%s: %s)", type(exc).__name__, exc)
            continue
        raw_clusters = _parse_cluster_response(getattr(result, "last_agent_message", "") or "")
        if raw_clusters is None:
            continue
        any_batch_succeeded = True
        batch_titles = {getattr(p, "title", ""): p for p in batch}
        for raw_group in raw_clusters:
            if not isinstance(raw_group, list):
                continue
            resolved: list[Any] = []
            for raw_title in raw_group:
                title = str(raw_title or "").strip()
                if not title or title in claimed:
                    continue
                page = batch_titles.get(title)
                if page is None:
                    continue
                resolved.append(page)
            if len(resolved) < MIN_CLUSTER_SIZE:
                continue
            for p in resolved:
                claimed.add(getattr(p, "title", ""))
            clusters.append(resolved)
    return clusters if any_batch_succeeded else None


def auto_compact_wiki(
    wiki_root: "str | Path",
    *,
    retired_by: str = "auto-compaction",
    judge_runner: Any = None,
    judge_model: str = "",
    judge_reasoning_effort: str = "high",
    on_event: EventSink = None,
) -> dict[str, int]:
    """Automatic (unattended) wiki-page compaction pass for ONE wiki root,
    run after every mission close.

    A no-op whenever ``judge_runner`` is not configured, or the wiki has
    fewer than 2 pages, or the LLM path finds nothing to do — there is no
    lexical/scored fallback. Retirement is the SAME reversible tombstone
    move ``WikiRouter.apply_ops``'s ``retire_page`` uses (moves the page
    under ``pages/_retired/``, never a hard delete). Fail-soft: any error is
    caught and reported via the return value / ``on_event``, never raised
    into the mission.
    """
    counts = {"clusters": 0, "retired": 0, "errors": 0}
    try:
        if judge_runner is None:
            return counts
        from .store import WikiStore

        store = WikiStore(wiki_root)
        pages = store.iter_pages()
        if len(pages) < MIN_CLUSTER_SIZE:
            return counts
        interesting = llm_cluster_wiki(
            pages, judge_runner=judge_runner, judge_model=judge_model,
            judge_reasoning_effort=judge_reasoning_effort,
        )
        if not interesting:
            return counts
        counts["clusters"] = len(interesting)
        for cluster in interesting:
            rep = _representative(cluster)
            for page in cluster:
                if page is rep:
                    continue
                try:
                    store.retire_page(
                        page.type, page.id,
                        reason=f"auto-compacted into '{rep.id}' (near-duplicate)",
                        retired_by=retired_by,
                    )
                    counts["retired"] += 1
                    if callable(on_event):
                        try:
                            on_event({
                                "type": "wiki.compacted",
                                "text": (
                                    f"auto-compacted {page.type}/{page.id} into "
                                    f"{rep.type}/{rep.id} (near-duplicate)"
                                ),
                            })
                        except Exception:  # noqa: BLE001
                            log.debug("wiki.compacted emit failed", exc_info=True)
                except (FileNotFoundError, KeyError, ValueError) as exc:
                    counts["errors"] += 1
                    if callable(on_event):
                        try:
                            on_event({
                                "type": "wiki.compact.error",
                                "text": f"{page.type}/{page.id}: {type(exc).__name__}: {exc}",
                            })
                        except Exception:  # noqa: BLE001
                            log.debug("wiki.compact.error emit failed", exc_info=True)
        if counts["retired"]:
            from .index import rebuild_indexes
            try:
                rebuild_indexes(store)
            except Exception:  # noqa: BLE001 — index maintenance must never break the caller
                log.debug("wiki index rebuild after auto-compaction failed", exc_info=True)
    except Exception as exc:  # noqa: BLE001 — auto-compaction must never block a mission
        log.warning("auto_compact_wiki failed (%s: %s)", type(exc).__name__, exc)
        counts["errors"] += 1
    return counts


__all__ = [
    "build_duplicate_check_prompt",
    "build_compaction_batch_prompt",
    "llm_cluster_wiki",
    "auto_compact_wiki",
]
