"""Map-note endpoints: pin a short operator note to an Atlas node.

GET  /api/projects/{sid}/map-notes  → {"notes": [...]} (latest 200, oldest first)
POST /api/projects/{sid}/map-notes  → {"note": {...}}

Notes are stored per session (``map_notes.jsonl`` in the life dir, see
:mod:`argus_skill.webapi.map_notes`) and surface to the Planner every cycle
through the current-reality digest.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from ..map_notes import (
    NOTE_AUTHOR_MAX_CHARS,
    NOTE_NODE_ID_MAX_CHARS,
    NOTE_TEXT_MAX_CHARS,
    append_note,
    list_notes,
)


class MapNoteIn(BaseModel):
    node_id: str = Field(min_length=1, max_length=NOTE_NODE_ID_MAX_CHARS)
    text: str = Field(min_length=1, max_length=NOTE_TEXT_MAX_CHARS)
    author: str = Field(default="", max_length=NOTE_AUTHOR_MAX_CHARS)


def register_map_note_routes(app, ctx) -> None:
    @app.get(
        "/api/projects/{sid}/map-notes",
        dependencies=[Depends(ctx.require_auth)],
    )
    def _get_map_notes(sid: str) -> dict[str, Any]:
        return {"notes": list_notes(ctx.resolve_or_404(sid))}

    @app.post(
        "/api/projects/{sid}/map-notes",
        dependencies=[Depends(ctx.require_auth)],
    )
    def _post_map_note(sid: str, body: MapNoteIn) -> dict[str, Any]:
        life_dir = ctx.resolve_or_404(sid)
        try:
            note = append_note(
                life_dir,
                node_id=body.node_id,
                text=body.text,
                author=body.author,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail="the note could not be saved; check disk space and retry",
            ) from exc
        return {"note": note}
