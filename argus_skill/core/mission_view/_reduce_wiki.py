"""Wiki (project knowledge base) mission-view event-family reducers.

Covers wiki initialization/evolution storage counters and legacy page lifecycle
events. Current roles maintain declarative knowledge pages directly.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..event_catalog import EventType
from ._reduce_helpers import _integer, _text, _timeline, _upsert


def reduce_wiki_event(
    view: dict[str, Any],
    event: Mapping[str, Any],
    *,
    event_type: str,
    ts: float,
    mission: dict[str, Any],
) -> None:
    if event_type in {
        EventType.WIKI_INITIALIZED,
        EventType.WIKI_EVOLUTION_COMPLETED,
    }:
        storage = view.setdefault("storage", {})
        paths = [str(path) for path in storage.setdefault("wiki_paths", []) if path]
        candidates = list(event.get("paths") or [])
        path = _text(event, "path", 1000)
        if path:
            candidates.append(path)
        for candidate in candidates:
            value = str(candidate or "").strip()
            if value and value not in paths:
                paths.append(value)
        storage["wiki_paths"] = paths

    elif event_type == EventType.WIKI_RETIRED_COMPRESSED:
        storage = view.setdefault("storage", {})
        storage["wiki_retired_compressed"] = int(
            storage.get("wiki_retired_compressed") or 0
        ) + (_integer(event, "count") or 0)
        storage["wiki_retired_bytes_saved"] = int(
            storage.get("wiki_retired_bytes_saved") or 0
        ) + (_integer(event, "bytes_saved") or 0)

    elif event_type in {EventType.WIKI_CREATED, EventType.WIKI_UPDATED}:
        page_id = _text(event, "page_id")
        if page_id:
            _upsert(view.setdefault("learned_wiki_pages", []), "id", page_id, {
                "id": page_id,
                "title": _text(event, "title", 240) or page_id,
                "card_type": _text(event, "card_type"),
                "status": _text(event, "status") or "scratch",
                "path": _text(event, "path", 1000),
                "updated_at": ts,
            })
            _timeline(
                view,
                event,
                role="reviewer",
                title=(
                    "Knowledge captured"
                    if event_type == EventType.WIKI_CREATED
                    else "Knowledge refined"
                ),
                detail=_text(event, "title", 240) or page_id,
                tone="skill",
            )

    elif event_type == EventType.WIKI_RETIRED:
        page_id = _text(event, "page_id")
        if page_id:
            pages = view.setdefault("learned_wiki_pages", [])
            existing = next((page for page in pages if page.get("id") == page_id), None)
            if existing is not None:
                existing.update({"status": "retired", "updated_at": ts})
            else:
                pages.append({
                    "id": page_id,
                    "title": page_id,
                    "card_type": _text(event, "card_type"),
                    "status": "retired",
                    "path": "",
                    "updated_at": ts,
                })
            _timeline(
                view,
                event,
                role="reviewer",
                title="Knowledge retired",
                detail=page_id,
                tone="error",
            )

    elif event_type in {
        EventType.WIKI_PROMOTION_PROMOTED,
        EventType.WIKI_PROMOTION_DEMOTED,
    }:
        page_id = _text(event, "page_id")
        if page_id:
            pages = view.setdefault("learned_wiki_pages", [])
            existing = next((page for page in pages if page.get("id") == page_id), None)
            patch = {"status": _text(event, "to_status"), "updated_at": ts}
            if existing is not None:
                existing.update(patch)
            else:
                pages.append({
                    "id": page_id,
                    "title": page_id,
                    "card_type": _text(event, "card_type"),
                    "path": "",
                    **patch,
                })
            promoted = event_type == EventType.WIKI_PROMOTION_PROMOTED
            _timeline(
                view,
                event,
                role="reviewer",
                title="Knowledge promoted" if promoted else "Knowledge demoted",
                detail=f"{page_id} → {_text(event, 'to_status')}",
                tone="success" if promoted else "neutral",
            )
