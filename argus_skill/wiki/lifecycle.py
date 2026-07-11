"""Project wiki bootstrap and reviewer-owned post-mission evolution."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable

from ..core.event_catalog import EventType
from ..core.models import RoundRecord
from .auto_hooks import discover_wikis, run_post_mission_hooks

EventSink = Callable[[dict[str, Any]], None] | None
log = logging.getLogger(__name__)


def _emit(on_event: EventSink, event: dict[str, Any]) -> None:
    if not callable(on_event):
        return
    try:
        on_event(event)
    except Exception:  # noqa: BLE001 - telemetry must never break evolution
        return


def _project_slug(workdir: Path) -> str:
    from ..skills.store import _slugify

    explicit = str(os.environ.get("ARGUS_SKILL_WIKI_PROJECT", "") or "").strip()
    slug = _slugify(explicit or workdir.name)
    if slug:
        return slug[:80]
    try:
        from ..skills.vertical_select import _persisted_vertical

        slug = _slugify(_persisted_vertical(workdir) or "")
    except Exception:  # noqa: BLE001 - naming fallback must not block bootstrap
        slug = ""
    return slug or "argus-project"


def ensure_project_wiki(
    workdir: Path | str,
    *,
    enabled: bool,
    on_event: EventSink = None,
) -> Path | None:
    """Return an existing wiki or idempotently bootstrap the project default."""
    root = Path(workdir).expanduser()
    existing = discover_wikis(root)
    if existing:
        return existing[0]
    if not enabled or not root.is_dir():
        return None
    try:
        from .bootstrap import init_wiki

        project = _project_slug(root)
        wiki_root = init_wiki(project, base=root)
        _emit(
            on_event,
            {
                "type": EventType.WIKI_INITIALIZED,
                "project": project,
                "path": str(wiki_root),
                "auto": True,
                "text": f"initialized project wiki at {wiki_root}",
            },
        )
        return wiki_root
    except Exception as exc:  # noqa: BLE001 - wiki bootstrap is fail-open
        log.warning("automatic wiki initialization failed for %s", root, exc_info=True)
        _emit(
            on_event,
            {
                "type": EventType.WIKI_INITIALIZATION_FAILED,
                "workdir": str(root),
                "error": f"{type(exc).__name__}: {exc}",
                "text": "automatic project wiki initialization failed",
            },
        )
        return None


def collect_wiki_ops(rounds: list[RoundRecord]) -> list[dict[str, Any]]:
    """Aggregate reviewer wiki ops while removing repeated proposals."""
    seen: set[tuple[Any, ...]] = set()
    ops: list[dict[str, Any]] = []
    for record in rounds or []:
        review = getattr(record, "review", None)
        for op in getattr(review, "wiki_ops", None) or []:
            if not isinstance(op, dict):
                continue
            key = (
                op.get("op"),
                op.get("id", ""),
                str(op.get("body", "") or "")[:200],
            )
            if key in seen:
                continue
            seen.add(key)
            ops.append(op)
    return ops


def apply_wiki_ops(
    *,
    rounds: list[RoundRecord],
    workdir: Path,
    task: str,
    reviewer_runner: Any,
    reviewer_model: str,
    reviewer_reasoning_effort: str,
    on_event: EventSink = None,
) -> dict[str, int]:
    ops = collect_wiki_ops(rounds)
    totals = {
        "sources": 0,
        "created": 0,
        "updated": 0,
        "retired": 0,
        "skipped": 0,
        "rejected": 0,
    }
    if not ops:
        return totals
    from .router import WikiRouter

    for wiki_root in discover_wikis(workdir):
        counts = WikiRouter(
            wiki_root,
            judge_runner=reviewer_runner,
            judge_model=reviewer_model,
            judge_reasoning_effort=reviewer_reasoning_effort,
        ).apply_ops(ops, task=task, on_event=on_event)
        for key in totals:
            totals[key] += int(counts.get(key, 0) or 0)
    return totals


def evolve_wikis_after_mission(
    *,
    rounds: list[RoundRecord],
    workdir: Path,
    task: str,
    mission_id: str,
    success: bool,
    reviewer_runner: Any,
    reviewer_model: str,
    reviewer_reasoning_effort: str,
    apply_ops_enabled: bool,
    auto_compact_enabled: bool,
    on_event: EventSink = None,
) -> dict[str, Any]:
    """Run deterministic hooks, reviewer ops, promotion and optional compaction."""
    wiki_roots = discover_wikis(workdir)
    if not wiki_roots:
        return {"wiki_count": 0, "ops_proposed": 0, "paths": []}

    hook_summary = run_post_mission_hooks(
        workdir,
        mission_id=mission_id,
        success=success,
        emit=on_event,
    )
    totals: dict[str, Any] = {
        "wiki_count": len(wiki_roots),
        "ops_proposed": len(collect_wiki_ops(rounds)) if apply_ops_enabled else 0,
        "sources": sum(
            int(row.get("sources_written", 0) or 0) for row in hook_summary.values()
        ),
        "scratch_pages": sum(
            int(row.get("scratch_written", 0) or 0) for row in hook_summary.values()
        ),
        "created": 0,
        "updated": 0,
        "retired": 0,
        "skipped": 0,
        "rejected": 0,
        "promoted": 0,
        "demoted": 0,
        "compaction_clusters": 0,
        "compacted": 0,
        "errors": 0,
        "paths": [str(path) for path in wiki_roots],
    }

    from .promotion import mechanical_promote

    for wiki_root in wiki_roots:
        promotion = mechanical_promote(wiki_root, emit=on_event)
        totals["promoted"] += int(promotion.get("promoted", 0) or 0)
        totals["demoted"] += int(promotion.get("demoted", 0) or 0)
        totals["errors"] += int(promotion.get("errors", 0) or 0)

    if apply_ops_enabled:
        op_counts = apply_wiki_ops(
            rounds=rounds,
            workdir=workdir,
            task=task,
            reviewer_runner=reviewer_runner,
            reviewer_model=reviewer_model,
            reviewer_reasoning_effort=reviewer_reasoning_effort,
            on_event=on_event,
        )
        for key, value in op_counts.items():
            totals[key] += int(value or 0)

    if auto_compact_enabled:
        from .compaction import auto_compact_wiki

        for wiki_root in wiki_roots:
            compact = auto_compact_wiki(
                wiki_root,
                judge_runner=reviewer_runner,
                judge_model=reviewer_model,
                judge_reasoning_effort=reviewer_reasoning_effort,
                on_event=on_event,
            )
            totals["compaction_clusters"] += int(compact.get("clusters", 0) or 0)
            totals["compacted"] += int(compact.get("retired", 0) or 0)
            totals["errors"] += int(compact.get("errors", 0) or 0)

    _emit(
        on_event,
        {
            "type": EventType.WIKI_EVOLUTION_COMPLETED,
            **totals,
            "text": (
                "wiki evolution: "
                f"{totals['created']} created, {totals['updated']} updated, "
                f"{totals['retired']} retired, {totals['promoted']} promoted"
            ),
        },
    )
    return totals


__all__ = [
    "apply_wiki_ops",
    "collect_wiki_ops",
    "ensure_project_wiki",
    "evolve_wikis_after_mission",
]
