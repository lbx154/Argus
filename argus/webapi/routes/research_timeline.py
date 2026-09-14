"""Forecast previews and project-confined revision storage for the workbench."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException

from .context import ServerContext


def register_research_timeline_routes(app, ctx: ServerContext) -> None:
    def workspace(sid: str) -> Path:
        from ..artifacts import project_workspace

        root = project_workspace(sid, global_root=ctx.project_root_or_404(sid))
        if root is None:
            raise HTTPException(status_code=409, detail="Project has no bound workspace")
        root = root.resolve()
        directory = root / ".argus" / "timeline"
        paths = [directory, directory / ".lock", *directory.glob("*.json")]
        if any(not path.resolve().is_relative_to(root) for path in paths):
            raise HTTPException(
                status_code=409, detail="Timeline path leaves the project workspace"
            )
        return root

    @app.get("/api/research/timeline/example", dependencies=[Depends(ctx.require_auth)])
    def _example() -> dict[str, Any]:
        source = files("argus.core").joinpath("timeline_example.json")
        return json.loads(source.read_text(encoding="utf-8"))

    @app.post("/api/research/timeline/estimate", dependencies=[Depends(ctx.require_auth)])
    def _estimate(body: dict[str, Any]) -> dict[str, Any]:
        from ...core.timeline import estimate

        try:
            return estimate(body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/projects/{sid}/research/timeline", dependencies=[Depends(ctx.require_auth)])
    def _latest(sid: str) -> dict[str, Any]:
        from ...core.timeline_store import latest

        root = workspace(sid)
        try:
            return {"latest": latest(root)}
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=409, detail=f"Cannot read saved timeline: {exc}"
            ) from exc

    @app.post("/api/projects/{sid}/research/timeline", dependencies=[Depends(ctx.require_auth)])
    def _record(sid: str, body: dict[str, Any]) -> dict[str, Any]:
        from ...core.timeline_store import TimelineVersionConflict, record

        root = workspace(sid)
        try:
            payload = body.get("input")
            expected_version = body.get("expected_version")
            reason = body.get("reason")
            if not isinstance(payload, dict):
                raise ValueError("input must be an object")
            if isinstance(expected_version, bool) or not isinstance(expected_version, int):
                raise ValueError("expected_version must be a nonnegative integer")
            if not isinstance(reason, str):
                raise ValueError("revision reason must be nonempty text")
            return record(
                root,
                payload,
                expected_version=expected_version,
                reason=reason,
            )
        except TimelineVersionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (OSError, TimeoutError) as exc:
            raise HTTPException(status_code=503, detail=f"Cannot save timeline: {exc}") from exc
