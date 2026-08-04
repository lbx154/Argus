"""Initialization only for the Agent-maintained minimal Wiki."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from ..core.event_catalog import EventType
from .auto_hooks import discover_wikis

EventSink = Callable[[dict[str, Any]], None] | None


def _emit(on_event: EventSink, event: dict[str, Any]) -> None:
    if callable(on_event):
        try:
            on_event(event)
        except Exception:  # noqa: BLE001
            pass


def ensure_project_wiki(
    workdir: Path | str,
    *,
    enabled: bool,
    on_event: EventSink = None,
) -> Path | None:
    root = Path(workdir).expanduser()
    existing = discover_wikis(root)
    if existing:
        return existing[0]
    if not enabled or not root.is_dir():
        return None
    from .bootstrap import init_wiki

    # Operators may provide the semantic project path.  Otherwise preserve the
    # existing project directory name verbatim; no generated slug or ID is used.
    project = str(os.environ.get("ARGUS_SKILL_WIKI_PROJECT", "") or "").strip()
    project = project or root.name
    wiki_root = init_wiki(project, base=root)
    _emit(
        on_event,
        {
            "type": EventType.WIKI_INITIALIZED,
            "project": project,
            "path": str(wiki_root),
            "auto": True,
            "text": f"initialized project Wiki at {wiki_root}",
        },
    )
    return wiki_root


def maintain_wikis_after_mission(
    *,
    workdir: Path,
    auto_compact_enabled: bool,
    reviewer_runner: Any,
    reviewer_model: str,
    reviewer_reasoning_effort: str,
    on_event: EventSink = None,
) -> dict[str, Any]:
    """Do nothing: Agents maintain pages and INDEX.md during the mission."""
    _ = (
        auto_compact_enabled,
        reviewer_runner,
        reviewer_model,
        reviewer_reasoning_effort,
        on_event,
    )
    roots = discover_wikis(workdir)
    return {"wiki_count": len(roots), "paths": [str(path) for path in roots]}


__all__ = ["ensure_project_wiki", "maintain_wikis_after_mission"]
