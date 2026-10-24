"""Map records, saved card text, and each task's recorded trajectory."""

from __future__ import annotations

from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import map_narrative
from ..map_feed import MapFeed
from ..map_history import history_info, history_page
from ..map_view import read_map, with_revisions
from ..reader_application import PROCESS_VERSION as APPLICATION_PROCESS_VERSION
from ..reader_application import foundation_reference


class MapQuestionSourceIn(BaseModel):
    card_key: str = Field(min_length=1, max_length=300)
    task_id: str = Field(min_length=1, max_length=200)
    locale: Literal["zh-CN", "en-US"] = "zh-CN"
    preview: map_narrative.Preview = False
    foundation_id: str | None = Field(default=None, min_length=1, max_length=80)


def _reader_copy(value: dict) -> dict:
    """Hide obsolete source wrappers without rewriting the saved explanation."""
    return {**value, "cards": {
        key: {field: content for field, content in card.items()
              if field not in {"source_snapshot", "progress_source"}}
        for key, card in value.get("cards", {}).items()
    }, "relations": [
        {key: content for key, content in relation.items() if key != "evidence"}
        for relation in value.get("relations", [])
    ]}


def register_map_live_routes(app, ctx, read_dataset):
    feed = MapFeed()

    @app.get("/api/projects/{sid}/map", dependencies=[Depends(ctx.require_auth)])
    def project_map(
        sid: str, after: str | None = Query(default=None, max_length=80),
        since: float | None = Query(default=None, ge=0, allow_inf_nan=False),
        event_since: float | None = Query(default=None, ge=0, allow_inf_nan=False),
        start_task: str | None = Query(default=None, max_length=160),
    ):
        value = feed.read(sid, ctx.project_root_or_404(sid), ctx.resolve_or_404(sid), after,
                          include_task_index=since is not None)
        if since is not None:
            index = value.pop("task_index")
            ordered = [task_id for task_id, _ in index]
            ids = set(ordered[ordered.index(start_task):]) if start_task in ordered else {
                task_id for task_id, ts in index if ts >= since
            }
            value["tasks"] = [t for t in value["tasks"] if t["id"] in ids]
            value["events"] = [e for e in value["events"] if e["item_id"] in ids
                               and e["ts"] >= (event_since if event_since is not None else since)]
        value["generation_available"] = map_narrative.configured()
        return value

    @app.get("/api/projects/{sid}/map-trace", dependencies=[Depends(ctx.require_auth)])
    async def map_trace(sid: str, task: str = Query(min_length=1, max_length=200)):
        """What the roles thought, said, ran and changed for one task, from the runner's log."""
        from ..map_trace import task_trace

        life_dir = ctx.resolve_or_404(sid)
        return await run_in_threadpool(task_trace, life_dir, task)

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
        # Tasks come from the live feed, so the cursor is the live map's own.
        delta = feed.read(
            sid, root, life_dir,
            task_after if result["incremental"] else None,
        )
        result.update(tasks=delta["tasks"], tasks_complete=not delta["incremental"],
                      cursor=delta["cursor"], removed_task_ids=delta.get("removed_task_ids", []))
        return result

    def load(source, name):
        if source == "project":
            root, life_dir = ctx.project_root_or_404(name), ctx.resolve_or_404(name)
            return with_revisions(feed.read(name, root, life_dir))
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

    def load_foundation(source, name, root, foundation_id, *, required):
        if source != "project":
            raise HTTPException(422, "question foundations require a project map")
        if not foundation_id:
            if required:
                raise HTTPException(422, "select a completed question foundation")
            return None
        from ..reader_foundation import read_foundation

        foundation = read_foundation(root, name, foundation_id)
        if foundation is None:
            if required:
                raise HTTPException(404, "question foundation not found in this project")
            return None
        try:
            foundation_reference(foundation)
        except ValueError as exc:
            if required:
                raise HTTPException(422, "question foundation is not complete or readable") from exc
            return None
        return foundation

    @app.get("/api/map-copy/{source}/{name}", dependencies=[Depends(ctx.require_auth)])
    def cached_copy(
        source: Literal["project", "dataset"],
        name: str,
        locale: Literal["zh-CN", "en-US"] = "zh-CN",
        session_id: str | None = None,
        preview: map_narrative.Preview = False,
        foundation_id: str | None = Query(default=None, min_length=1, max_length=80),
    ):
        value = load(source, name)
        root, project_root = owner(source, name, session_id)
        foundation = (load_foundation(source, name, root, foundation_id, required=False)
                      if preview == "question-foundation" else None)
        cache = ({"cards": {}, "relations": [], "cache_revision": 0}
                 if preview == "question-foundation" and foundation is None else map_narrative.read_cache(
                     root, map_narrative.copy_source(value["id"], locale, preview=preview,
                                                    **({"foundation_id": foundation["id"]} if foundation else {}))))
        try:
            model_revision = map_narrative.resolve_map_model().revision
        except (OSError, ValueError, RuntimeError):
            model_revision = ""
        return _reader_copy({
            "cards": cache.get("cards", {}),
            "relations": cache.get("relations", []),
            "available": project_root is not None and (preview != "question-foundation" or foundation is not None)
                         and map_narrative.configured(),
            "version": map_narrative.copy_version(preview=preview),
            "model_revision": model_revision,
            "cache_revision": cache.get("cache_revision", 0),
            **({"foundation_ref": foundation_reference(foundation),
                "process_version": APPLICATION_PROCESS_VERSION} if foundation else {}),
            **map_narrative._failure_metadata(cache),
        })

    @app.post("/api/map-question-source/{source}/{name}", dependencies=[Depends(ctx.require_auth)])
    async def map_question_source(
        source: Literal["project"], name: str, body: MapQuestionSourceIn,
        session_id: str | None = None,
    ):
        from ..reader_clarification import ReaderSourceUnavailable
        from ..reader_progress import retain_progress_source
        from .reader_foundation import _foundation_error

        def prepare():
            root, _ = owner(source, name, session_id)
            try:
                if body.preview == "question-foundation" and not body.foundation_id:
                    raise ReaderSourceUnavailable("select a saved question foundation explanation")
                value = load(source, name)
                copy_source = map_narrative.copy_source(
                    value["id"], body.locale, preview=body.preview, foundation_id=body.foundation_id,
                )
                card = map_narrative.read_cache(root, copy_source).get("cards", {}).get(body.card_key)
                if not isinstance(card, dict):
                    raise ReaderSourceUnavailable("no saved explanation to ask about")
                task_id = card.get("task_id") or card.get("source_snapshot", {}).get("task_id")
                if task_id and task_id != body.task_id:
                    raise ReaderSourceUnavailable("explanation does not belong to this task")
                events = {event["id"]: event for event in value["events"]}
                key = body.card_key
                task_keys = {body.task_id, *(body.task_id + suffix for suffix in (":brief", ":active", ":outcome"))}
                # A saved event card can outlive the feed's historical event.
                # Only a server-owned task binding permits that missing record.
                if task_id and key not in task_keys and key.removesuffix(":next") not in events:
                    key = body.task_id
                try:
                    documents = map_narrative.card_evidence(value, [{
                        "key": key, "task_id": body.task_id, "kind": "task",
                        "event_ids": [event_id for event_id in card.get("event_ids", []) if event_id in events],
                    }])
                except ValueError as exc:
                    raise ReaderSourceUnavailable(str(exc)) from exc
                if not documents:
                    raise ReaderSourceUnavailable("no saved explanation to ask about")
                document = map_narrative._bounded_document(documents[0])
                return retain_progress_source(
                    root, name, copy_source=copy_source, card_key=body.card_key, card=card, locale=body.locale,
                    records={key: document[key] for key in ("task_id", "task", "events")},
                )
            except (ValueError, OSError, TimeoutError, RuntimeError) as exc:
                raise _foundation_error(exc) from exc

        return await run_in_threadpool(prepare)
