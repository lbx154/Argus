"""Map records and summaries using the configured research runner."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import map_narrative
from ..map_view import read_map


class MapCardIn(BaseModel):
    key: str = Field(min_length=1, max_length=240)
    task_id: str = Field(min_length=1, max_length=160)
    kind: Literal["task", "plan", "execution", "review", "revision", "result"]
    event_ids: list[str] = Field(default_factory=list, max_length=16)


class MapCopyIn(BaseModel):
    cards: list[MapCardIn] = Field(min_length=1, max_length=16)
    locale: Literal["zh-CN", "en-US"] = "zh-CN"


def register_map_live_routes(app, ctx, read_dataset):
    @app.get("/api/projects/{sid}/map", dependencies=[Depends(ctx.require_auth)])
    def project_map(sid: str):
        value = read_map(sid, ctx.project_root_or_404(sid), ctx.resolve_or_404(sid))
        value["generation_available"] = map_narrative.configured()
        return value

    def load(source, name):
        if source == "project":
            return read_map(name, ctx.project_root_or_404(name), ctx.resolve_or_404(name))
        value = read_dataset(name)
        if value.get("id") != name or value.get("read_only") is not True:
            raise HTTPException(404, "unknown map")
        return value

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
        }

    @app.post("/api/map-copy/{source}/{name}", dependencies=[Depends(ctx.require_auth)])
    async def make_copy(
        source: Literal["project", "dataset"], name: str, body: MapCopyIn,
        session_id: str | None = None,
    ):
        value = await run_in_threadpool(load, source, name)
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
