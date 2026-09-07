"""Map records and summaries using the configured research runner."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import map_narrative
from ..map_feed import MapFeed
from ..map_history import history_info, history_page, indexed_evidence
from ..map_view import read_map, with_revisions


class MapCardIn(BaseModel):
    key: str = Field(min_length=1, max_length=240)
    task_id: str = Field(min_length=1, max_length=160)
    kind: Literal["task", "plan", "execution", "review", "revision", "result"]
    event_ids: list[str] = Field(default_factory=list, max_length=16)


class MapCopyIn(BaseModel):
    cards: list[MapCardIn] = Field(min_length=1, max_length=16)
    locale: Literal["zh-CN", "en-US"] = "zh-CN"


def register_map_live_routes(app, ctx, read_dataset):
    feed = MapFeed()

    @app.get("/api/projects/{sid}/map", dependencies=[Depends(ctx.require_auth)])
    def project_map(
        sid: str, after: str | None = Query(default=None, max_length=80),
        since: float | None = Query(default=None, ge=0, allow_inf_nan=False),
        event_since: float | None = Query(default=None, ge=0, allow_inf_nan=False),
        start_task: str | None = Query(default=None, max_length=160),
    ):
        value = feed.read(sid, ctx.project_root_or_404(sid), ctx.resolve_or_404(sid), after)
        if since is not None:
            full = feed.read(sid, ctx.project_root_or_404(sid), ctx.resolve_or_404(sid))
            ordered = [t["id"] for t in full["tasks"]]
            ids = set(ordered[ordered.index(start_task):]) if start_task in ordered else {
                t["id"] for t in full["tasks"] if (t.get("ts") or 0) >= since
            }
            value["tasks"] = [t for t in value["tasks"] if t["id"] in ids]
            value["events"] = [e for e in value["events"] if e["item_id"] in ids
                               and e["ts"] >= (event_since if event_since is not None else since)]
        value["generation_available"] = map_narrative.configured()
        return value

    @app.get("/api/projects/{sid}/map-info", dependencies=[Depends(ctx.require_auth)])
    def map_info(sid: str):
        root, life_dir = ctx.project_root_or_404(sid), ctx.resolve_or_404(sid)
        return history_info(read_map(sid, root, life_dir, include_events=False), life_dir)

    @app.get("/api/projects/{sid}/map-history", dependencies=[Depends(ctx.require_auth)])
    def map_history(
        sid: str, after: str | None = Query(default=None, max_length=80),
        task_after: str | None = Query(default=None, max_length=80),
    ):
        root, life_dir = ctx.project_root_or_404(sid), ctx.resolve_or_404(sid)
        value = feed.read(sid, root, life_dir, include_events=False)
        result = history_page(root, life_dir, value, after)
        delta = feed.read(sid, root, life_dir, task_after, include_events=False)
        result.update(tasks=delta["tasks"], tasks_complete=not delta["incremental"],
                      cursor=delta["cursor"], removed_task_ids=delta.get("removed_task_ids", []))
        return result

    def load(source, name, cards=None):
        if source == "project":
            root, life_dir = ctx.project_root_or_404(name), ctx.resolve_or_404(name)
            value = feed.read(name, root, life_dir)
            if cards:
                ids = list({key for c in cards for key in (
                    c.key, c.key.removesuffix(":next"), *c.event_ids,
                )})
                saved = indexed_evidence(root, life_dir, ids)
                value = {**value, "events": list({e["id"]: e for e in [
                    *saved, *value["events"],
                ]}.values())}
            return with_revisions(value)
        value = read_dataset(name)
        if value.get("id") != name or value.get("read_only") is not True:
            raise HTTPException(404, "unknown map")
        return with_revisions(value)

    def owner(source, name, session_id):
        if source == "project":
            if session_id and session_id != name:
                raise HTTPException(422, "map session does not match")
            session_id = name
        if session_id:
            return ctx.project_root_or_404(session_id), ctx.resolve_or_404(session_id)
        return ctx.roots[0], None

    @app.get("/api/map-copy/{source}/{name}", dependencies=[Depends(ctx.require_auth)])
    def cached_copy(
        source: Literal["project", "dataset"],
        name: str,
        locale: Literal["zh-CN", "en-US"] = "zh-CN",
        session_id: str | None = None,
    ):
        value = load(source, name)
        root, project_root = owner(source, name, session_id)
        cache = map_narrative.read_cache(root, value["id"] + ":" + locale)
        try:
            model_revision = map_narrative.resolve_map_model().revision
        except (OSError, ValueError, RuntimeError):
            model_revision = ""
        return {
            "cards": cache.get("cards", {}),
            "relations": cache.get("relations", []),
            "available": project_root is not None and map_narrative.configured(),
            "version": map_narrative.PROMPT_VERSION,
            "model_revision": model_revision,
            "cache_revision": cache.get("cache_revision", 0),
        }

    @app.post("/api/map-copy/{source}/{name}", dependencies=[Depends(ctx.require_auth)])
    async def make_copy(
        source: Literal["project", "dataset"], name: str, body: MapCopyIn,
        session_id: str | None = None,
    ):
        value = await run_in_threadpool(load, source, name, body.cards)
        root, project_root = owner(source, name, session_id)
        if project_root is None:
            raise HTTPException(422, "select a session for map summaries")
        try:
            return await run_in_threadpool(
                map_narrative.enrich, root, value, [c.model_dump() for c in body.cards], body.locale,
                project_root=project_root,
            )
        except ValueError as exc:
            logging.getLogger(__name__).warning("Map copy validation failed: %s", exc)
            raise HTTPException(422, "card content could not be prepared") from exc
        except (OSError, TimeoutError, RuntimeError) as exc:
            raise HTTPException(503, "card text is temporarily unavailable") from exc
