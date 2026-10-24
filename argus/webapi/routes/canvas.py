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
current canvas in ``detail.canvas``. Plain reads and writes, no model calls.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import canvas as store
from ..staged_attachments import StagedAttachmentError, unstage

logger = logging.getLogger(__name__)


class Point(BaseModel):
    x: float
    y: float


class CardIn(BaseModel):
    revision: int = Field(ge=0)
    kind: Literal["idea", "file"]
    text: str = Field(default="", max_length=store.MAX_TEXT_CHARS)
    attachment: dict[str, Any] | None = None
    position: Point | None = None


class CardPatch(BaseModel):
    revision: int = Field(ge=0)
    text: str = Field(max_length=store.MAX_TEXT_CHARS)


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


def register_canvas_routes(app, ctx) -> None:
    auth = [Depends(ctx.require_auth)]

    def run(sid: str, operation, *args, **kwargs) -> tuple[dict[str, Any], Any]:
        life_dir = ctx.resolve_or_404(sid)
        try:
            return operation(life_dir, *args, **kwargs)
        except store.CanvasConflict as exc:
            raise HTTPException(409, {"message": str(exc), "canvas": exc.current}) from exc
        except store.CanvasError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except OSError as exc:
            logger.exception("Canvas write failed")
            raise HTTPException(500, "the canvas could not be saved; check disk space and retry") from exc

    @app.get("/api/projects/{sid}/canvas", dependencies=auth)
    def _get_canvas(sid: str) -> dict[str, Any]:
        return store.read_canvas(ctx.resolve_or_404(sid))

    @app.post("/api/projects/{sid}/canvas/cards", dependencies=auth)
    def _add_card(sid: str, body: CardIn) -> dict[str, Any]:
        canvas, card = run(
            sid, store.add_card, body.revision, kind=body.kind, text=body.text,
            attachment=body.attachment, position=body.position.model_dump() if body.position else None,
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
        canvas, card = run(sid, store.update_card, body.revision, card_id, text=body.text)
        return {"canvas": canvas, "card": card}

    @app.delete("/api/projects/{sid}/canvas/cards/{card_id}", dependencies=auth)
    def _delete_card(sid: str, card_id: str, revision: int = Query(ge=0)) -> dict[str, Any]:
        canvas, card = run(sid, store.delete_card, revision, card_id)
        return {"canvas": canvas, "card": card}

    @app.post("/api/projects/{sid}/canvas/links", dependencies=auth)
    def _add_link(sid: str, body: LinkIn) -> dict[str, Any]:
        canvas, link = run(sid, store.add_link, body.revision, source=body.source, target=body.target, kind=body.kind)
        return {"canvas": canvas, "link": link}

    @app.patch("/api/projects/{sid}/canvas/links/{link_id}", dependencies=auth)
    def _update_link(sid: str, link_id: str, body: LinkPatch) -> dict[str, Any]:
        canvas, link = run(sid, store.update_link, body.revision, link_id, kind=body.kind)
        return {"canvas": canvas, "link": link}

    @app.delete("/api/projects/{sid}/canvas/links/{link_id}", dependencies=auth)
    def _delete_link(sid: str, link_id: str, revision: int = Query(ge=0)) -> dict[str, Any]:
        canvas, link = run(sid, store.delete_link, revision, link_id)
        return {"canvas": canvas, "link": link}

    @app.put("/api/projects/{sid}/canvas/positions", dependencies=auth)
    def _put_positions(sid: str, body: PositionsIn) -> dict[str, Any]:
        positions = {key: value.model_dump() if value else None for key, value in body.positions.items()}
        canvas, _ = run(sid, store.set_positions, body.revision, positions)
        return {"canvas": canvas}
