"""Canvas endpoints: cards, links and places a person put on the project map.

GET    /api/projects/{sid}/canvas                    → the canvas record
POST   /api/projects/{sid}/canvas/cards              → {canvas, card}
PATCH  /api/projects/{sid}/canvas/cards/{card_id}    → {canvas, card}
DELETE /api/projects/{sid}/canvas/cards/{card_id}?revision=N
POST   /api/projects/{sid}/canvas/links              → {canvas, link}
PATCH  /api/projects/{sid}/canvas/links/{link_id}    → {canvas, link}
DELETE /api/projects/{sid}/canvas/links/{link_id}?revision=N
PUT    /api/projects/{sid}/canvas/positions          → {canvas}

Every write carries the revision it read; a stale one answers 409 with the
current canvas in ``detail.canvas``. Every canvas sent back carries ``route``:
the person's required steps (必经步骤) by number with where each stands, read
from the backlog; it is never stored. A write that changes the route (a step's
words, kind, checkpoint, or a link) replaces the route in the operator context,
so the Planner plans again. Every accepted write also puts a
``project.canvas.updated`` event (its new revision) on the project's live
stream, so other open windows refetch the canvas at once. Plain reads and
writes, no model calls.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ...core.event_catalog import EventType
from .. import canvas as store
from ..staged_attachments import StagedAttachmentError, unstage

logger = logging.getLogger(__name__)


class Point(BaseModel):
    x: float
    y: float


class CardIn(BaseModel):
    revision: int = Field(ge=0)
    kind: Literal["idea", "file", "step"]
    text: str = Field(default="", max_length=store.MAX_TEXT_CHARS)
    attachment: dict[str, Any] | None = None
    position: Point | None = None
    checkpoint: bool = False


class CardPatch(BaseModel):
    revision: int = Field(ge=0)
    text: str | None = Field(default=None, max_length=store.MAX_TEXT_CHARS)
    kind: Literal["idea", "step"] | None = None
    checkpoint: bool | None = None


class LinkIn(BaseModel):
    revision: int = Field(ge=0)
    source: str = Field(min_length=1, max_length=store.MAX_ID_CHARS)
    target: str = Field(min_length=1, max_length=store.MAX_ID_CHARS)
    kind: Literal["basis", "order", "check", "related"]


class LinkPatch(BaseModel):
    revision: int = Field(ge=0)
    kind: Literal["basis", "order", "check", "related"]


class PositionsIn(BaseModel):
    revision: int = Field(ge=0)
    positions: dict[str, Point | None]


def tell_open_maps(life_dir, canvas: dict[str, Any], changed: str) -> None:
    """Put the canvas's new revision on the live stream every open map reads.

    A window that already holds this revision (the one that wrote it) ignores
    it; any other refetches the canvas. Best effort: the write stands either way.
    """
    try:
        from ...life.event_log import JsonlEventSink

        JsonlEventSink(None, life_dir=life_dir).append({
            "type": EventType.PROJECT_CANVAS_UPDATED.value,
            "revision": int(canvas["revision"]),
            "change": changed,
        })
    except Exception:  # noqa: BLE001 - other windows still catch up on their next read
        logger.warning("Could not announce canvas revision in %s", life_dir, exc_info=True)


def route_view(life_dir) -> dict[str, Any]:
    """The person's steps by number, each with its status and the tasks tied to it."""
    from ...core.waypoints import read_route, read_state, step_states

    steps = read_route(life_dir)
    if not steps:
        return {"steps": [], "done": 0, "total": 0}
    try:
        from ...life.memory import LifeMemory

        rows = LifeMemory.open(life_dir).backlog.history()
    except Exception:  # noqa: BLE001 - the canvas still shows; statuses read as not started
        logger.warning("Could not read the backlog for step statuses in %s", life_dir, exc_info=True)
        rows = []
    states = step_states(steps, rows, read_state(life_dir))
    return {"steps": [entry.to_json() for entry in states],
            "done": sum(entry.complete for entry in states), "total": len(states)}


def with_route(life_dir, canvas: dict[str, Any]) -> dict[str, Any]:
    try:
        return {**canvas, "route": route_view(life_dir)}
    except Exception:  # noqa: BLE001 - the canvas itself must still load
        logger.exception("Could not read the route in %s", life_dir)
        return {**canvas, "route": {"steps": [], "done": 0, "total": 0}}


def sync_route(life_dir) -> None:
    """Bind the route the canvas now shows; the write stands even if this fails (the daemon re-syncs)."""
    try:
        from ...core.waypoints import sync_route_directive

        sync_route_directive(life_dir)
    except Exception:  # noqa: BLE001
        logger.warning("Could not bind the required steps in %s", life_dir, exc_info=True)


def say_step_rewording(life_dir, before: dict[str, Any] | None, card: dict[str, Any]) -> None:
    """A step reworded while the team works on it: one line in the chat saying when the words apply."""
    if not before or card.get("kind") != "step" or before.get("kind") != "step" or before.get("text") == card.get("text"):
        return
    from ...core.mission_view import uses_cjk
    from ...core.transcript import append_turn
    from ..manager_pending_question import _emit_ui_turn

    entry = next((step for step in route_view(life_dir)["steps"] if step["id"] == card["id"]), None)
    if entry is None or entry["status"] not in {"running", "review"}:
        return
    label = f"第 {entry['number']} 步" if entry["number"] else "这一步"
    words = " ".join(str(card.get("text") or "").split())[:80]
    text = (
        f"{label}改成了“{words}”。正在做的这一轮照旧做完，下一轮按新说法做。"
        if uses_cjk(words) or not words
        else f"{'Step ' + str(entry['number']) if entry['number'] else 'This step'} now reads “{words}”. "
             "The round in progress finishes as it was; the next round follows the new words."
    )
    message_id = f"step-reworded-{card['id']}-{int(float(card.get('updated_ts') or 0) * 1000)}"
    try:
        append_turn(life_dir, "argus", text, message_id=message_id)
        _emit_ui_turn(life_dir, "argus", text, message_id=message_id)
    except Exception:  # noqa: BLE001 - a courtesy line
        logger.warning("Could not tell the chat about a reworded step", exc_info=True)


def register_canvas_routes(app, ctx) -> None:
    auth = [Depends(ctx.require_auth)]

    def run(sid: str, changed: str, operation, *args, **kwargs) -> tuple[dict[str, Any], Any]:
        life_dir = ctx.resolve_or_404(sid)
        try:
            result = operation(life_dir, *args, **kwargs)
        except store.CanvasConflict as exc:
            raise HTTPException(409, {"message": str(exc), "canvas": exc.current}) from exc
        except store.CanvasError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except OSError as exc:
            logger.exception("Canvas write failed")
            raise HTTPException(500, "the canvas could not be saved; check disk space and retry") from exc
        tell_open_maps(life_dir, result[0], changed)
        if changed != "positions":
            sync_route(life_dir)
        return (with_route(life_dir, result[0]), *result[1:])

    @app.get("/api/projects/{sid}/canvas", dependencies=auth)
    def _get_canvas(sid: str) -> dict[str, Any]:
        life_dir = ctx.resolve_or_404(sid)
        return with_route(life_dir, store.read_canvas(life_dir))

    @app.post("/api/projects/{sid}/canvas/cards", dependencies=auth)
    def _add_card(sid: str, body: CardIn) -> dict[str, Any]:
        canvas, card = run(
            sid, "card", store.add_card, body.revision, kind=body.kind, text=body.text,
            attachment=body.attachment, position=body.position.model_dump() if body.position else None,
            checkpoint=body.checkpoint,
        )
        if card["attachment"]:
            # A file on the canvas is kept like a sent one, not swept as a draft.
            try:
                unstage(ctx.project_root_or_404(sid), sid, [card["attachment"]["attachment_id"]])
            except (OSError, StagedAttachmentError):
                logger.warning("Could not keep canvas attachment %s", card["attachment"]["attachment_id"])
        return {"canvas": canvas, "card": card}

    @app.patch("/api/projects/{sid}/canvas/cards/{card_id}", dependencies=auth)
    def _update_card(sid: str, card_id: str, body: CardPatch) -> dict[str, Any]:
        life_dir = ctx.resolve_or_404(sid)
        before = next((dict(c) for c in store.read_canvas(life_dir)["cards"] if c["id"] == card_id), None)
        canvas, card = run(sid, "card", store.update_card, body.revision, card_id, text=body.text,
                           kind=body.kind, checkpoint=body.checkpoint)
        say_step_rewording(life_dir, before, card)
        return {"canvas": canvas, "card": card}

    @app.delete("/api/projects/{sid}/canvas/cards/{card_id}", dependencies=auth)
    def _delete_card(sid: str, card_id: str, revision: int = Query(ge=0)) -> dict[str, Any]:
        canvas, card = run(sid, "card", store.delete_card, revision, card_id)
        return {"canvas": canvas, "card": card}

    @app.post("/api/projects/{sid}/canvas/links", dependencies=auth)
    def _add_link(sid: str, body: LinkIn) -> dict[str, Any]:
        canvas, link = run(sid, "link", store.add_link, body.revision, source=body.source, target=body.target, kind=body.kind)
        return {"canvas": canvas, "link": link}

    @app.patch("/api/projects/{sid}/canvas/links/{link_id}", dependencies=auth)
    def _update_link(sid: str, link_id: str, body: LinkPatch) -> dict[str, Any]:
        canvas, link = run(sid, "link", store.update_link, body.revision, link_id, kind=body.kind)
        return {"canvas": canvas, "link": link}

    @app.delete("/api/projects/{sid}/canvas/links/{link_id}", dependencies=auth)
    def _delete_link(sid: str, link_id: str, revision: int = Query(ge=0)) -> dict[str, Any]:
        canvas, link = run(sid, "link", store.delete_link, revision, link_id)
        return {"canvas": canvas, "link": link}

    @app.put("/api/projects/{sid}/canvas/positions", dependencies=auth)
    def _put_positions(sid: str, body: PositionsIn) -> dict[str, Any]:
        positions = {key: value.model_dump() if value else None for key, value in body.positions.items()}
        canvas, _ = run(sid, "positions", store.set_positions, body.revision, positions)
        return {"canvas": canvas}
