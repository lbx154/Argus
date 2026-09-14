"""Read-only forecast previews for research proposal editors."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException

from .context import ServerContext


def register_research_timeline_routes(app, ctx: ServerContext) -> None:
    @app.post("/api/research/timeline/estimate", dependencies=[Depends(ctx.require_auth)])
    def _estimate(body: dict[str, Any]) -> dict[str, Any]:
        from ...verticals.research.timeline import estimate

        try:
            return estimate(body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
