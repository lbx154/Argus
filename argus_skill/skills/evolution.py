"""Post-mission skill evolution after independent review."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..core.cold_storage import cold_storage_stats, compact_skill_histories
from ..core.event_catalog import EventType
from ..core.knobs import resolve_knob
from ..core.models import RoundRecord

EventSink = Callable[[dict[str, Any]], None] | None


def _emit(on_event: EventSink, event: dict[str, Any]) -> None:
    if not callable(on_event):
        return
    try:
        on_event(event)
    except Exception:  # noqa: BLE001 - telemetry must never break evolution
        return


def collect_skill_ops(rounds: list[RoundRecord]) -> list[dict[str, Any]]:
    """Aggregate reviewer skill ops while removing repeated proposals."""
    seen: set[tuple[Any, ...]] = set()
    ops: list[dict[str, Any]] = []
    for record in rounds or []:
        review = getattr(record, "review", None)
        for op in getattr(review, "skill_ops", None) or []:
            if not isinstance(op, dict):
                continue
            key = (
                op.get("op"),
                op.get("name", ""),
                str(op.get("content", "") or "")[:200],
            )
            if key in seen:
                continue
            seen.add(key)
            ops.append(op)
    return ops


def apply_skill_ops(
    skill_router: Any,
    rounds: list[RoundRecord],
    *,
    task: str,
    on_event: EventSink = None,
) -> dict[str, int]:
    ops = collect_skill_ops(rounds)
    if not ops:
        return {"created": 0, "updated": 0, "archived": 0, "rejected": 0}
    return skill_router.apply_ops(ops, task=task, on_event=on_event)


def _store_snapshot(skill_store: Any) -> dict[str, Any]:
    project = getattr(skill_store, "project", None)
    global_store = getattr(skill_store, "global_", None)
    if project is None and global_store is None:
        project = skill_store

    def _path(store: Any) -> str:
        value = getattr(store, "skills_dir", None) if store is not None else None
        return str(Path(value)) if value else ""

    def _count(store: Any) -> int:
        if store is None or not callable(getattr(store, "list_summaries", None)):
            return 0
        try:
            return len(store.list_summaries())
        except Exception:  # noqa: BLE001 - observability must not break evolution
            return 0

    return {
        "project_skill_dir": _path(project),
        "global_skill_dir": _path(global_store),
        "project_skill_count": _count(project),
        "global_skill_count": _count(global_store),
    }


def evolve_skills_after_mission(
    *,
    skill_store: Any,
    skill_router: Any,
    reviewer_runner: Any,
    reviewer_model: str,
    reviewer_reasoning_effort: str,
    rounds: list[RoundRecord],
    task: str,
    apply_ops_enabled: bool,
    auto_compact_enabled: bool,
    fallback_skills_dir: Path,
    on_event: EventSink = None,
) -> dict[str, int]:
    """Apply reviewer ops and optional reversible compaction, then summarize."""
    ops = collect_skill_ops(rounds) if apply_ops_enabled else []
    counts = {"created": 0, "updated": 0, "archived": 0, "rejected": 0}
    if ops:
        counts.update(skill_router.apply_ops(ops, task=task, on_event=on_event))

    project_store = getattr(skill_store, "project", None)
    project_skill_dir = (
        Path(project_store.skills_dir)
        if project_store is not None
        else Path(fallback_skills_dir)
    )
    compact = {"clusters": 0, "archived": 0, "errors": 0}
    if auto_compact_enabled:
        from .compaction import auto_compact_skills

        # A project mission may evolve only its own layer. Shared/global skill
        # maintenance requires an explicit operator maintenance pass, matching
        # SkillRouter's refusal to archive global skills from a project review.
        result = auto_compact_skills(
            project_skill_dir,
            judge_runner=reviewer_runner,
            judge_model=reviewer_model,
            judge_reasoning_effort=reviewer_reasoning_effort,
            on_event=on_event,
        )
        for key in compact:
            compact[key] += int(result.get(key, 0) or 0)

    try:
        keep_hot = max(
            0,
            int(resolve_knob("ARGUS_SKILL_HISTORY_HOT_VERSIONS", "20").value),
        )
    except ValueError:
        keep_hot = 20
    compressed_history = compact_skill_histories(
        project_skill_dir,
        keep_hot=keep_hot,
    )
    history_stats = cold_storage_stats(compressed_history)
    if compressed_history:
        shown = [str(path) for path in compressed_history[:20]]
        _emit(on_event, {
            "type": EventType.SKILL_HISTORY_COMPRESSED,
            "count": len(compressed_history),
            "keep_hot": keep_hot,
            "paths": shown,
            "truncated": len(shown) < len(compressed_history),
            **history_stats,
            "text": f"compressed {len(compressed_history)} cold skill versions",
        })

    summary = {
        "ops_proposed": len(ops),
        **counts,
        "compaction_clusters": compact["clusters"],
        "compacted": compact["archived"],
        "history_compressed": len(compressed_history),
        "history_bytes_saved": history_stats["bytes_saved"],
        "errors": compact["errors"],
        **_store_snapshot(skill_store),
    }
    _emit(
        on_event,
        {
            "type": EventType.SKILL_EVOLUTION_COMPLETED,
            **summary,
            "text": (
                "skill evolution: "
                f"{counts['created']} created, {counts['updated']} updated, "
                f"{counts['archived']} archived, {counts['rejected']} rejected"
            ),
        },
    )
    return summary


__all__ = [
    "apply_skill_ops",
    "collect_skill_ops",
    "evolve_skills_after_mission",
]
