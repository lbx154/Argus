"""Bootstrap and mechanical maintenance for the direct-edit knowledge wiki."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable

from ..core.cold_storage import cold_storage_stats, compact_wiki_retired
from ..core.event_catalog import EventType
from ..core.knobs import resolve_knob
from .auto_hooks import discover_wikis, rebuild_wiki_indexes

EventSink = Callable[[dict[str, Any]], None] | None
log = logging.getLogger(__name__)


def _emit(on_event: EventSink, event: dict[str, Any]) -> None:
    if not callable(on_event):
        return
    try:
        on_event(event)
    except Exception:  # noqa: BLE001 - telemetry must never break maintenance
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
        _emit(on_event, {
            "type": EventType.WIKI_INITIALIZED,
            "project": project,
            "path": str(wiki_root),
            "auto": True,
            "text": f"initialized project knowledge wiki at {wiki_root}",
        })
        return wiki_root
    except Exception as exc:  # noqa: BLE001 - wiki bootstrap is fail-open
        log.warning("automatic wiki initialization failed for %s", root, exc_info=True)
        _emit(on_event, {
            "type": EventType.WIKI_INITIALIZATION_FAILED,
            "workdir": str(root),
            "error": f"{type(exc).__name__}: {exc}",
            "text": "automatic project knowledge wiki initialization failed",
        })
        return None


def maintain_wikis_after_mission(
    *,
    workdir: Path,
    auto_compact_enabled: bool,
    reviewer_runner: Any,
    reviewer_model: str,
    reviewer_reasoning_effort: str,
    on_event: EventSink = None,
) -> dict[str, Any]:
    """Refresh indexes and optional reversible storage maintenance."""
    wiki_roots = discover_wikis(workdir)
    if not wiki_roots:
        return {"wiki_count": 0, "paths": []}

    totals: dict[str, Any] = {
        "wiki_count": len(wiki_roots),
        "compaction_clusters": 0,
        "compacted": 0,
        "retired_compressed": 0,
        "errors": 0,
        "paths": [str(path) for path in wiki_roots],
    }
    for wiki_root in wiki_roots:
        rebuild_wiki_indexes(wiki_root, emit=on_event)

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

    try:
        keep_hot = max(
            0,
            int(resolve_knob("ARGUS_SKILL_WIKI_RETIRED_HOT_VERSIONS", "20").value),
        )
    except ValueError:
        keep_hot = 20
    compressed_retired: list[Path] = []
    for wiki_root in wiki_roots:
        compressed_retired.extend(compact_wiki_retired(wiki_root, keep_hot=keep_hot))
    totals["retired_compressed"] = len(compressed_retired)
    retired_stats = cold_storage_stats(compressed_retired)
    totals["retired_bytes_saved"] = retired_stats["bytes_saved"]
    if compressed_retired:
        _emit(on_event, {
            "type": EventType.WIKI_RETIRED_COMPRESSED,
            "count": len(compressed_retired),
            "keep_hot": keep_hot,
            "paths": [str(path) for path in compressed_retired[:20]],
            **retired_stats,
            "text": f"compressed {len(compressed_retired)} cold wiki tombstones",
        })

    _emit(on_event, {
        "type": EventType.WIKI_EVOLUTION_COMPLETED,
        **totals,
        "text": "knowledge wiki indexes refreshed",
    })
    return totals


__all__ = ["ensure_project_wiki", "maintain_wikis_after_mission"]
