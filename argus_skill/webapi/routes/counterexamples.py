"""Counterexample research dashboard API."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException

from .context import ServerContext


def register_counterexample_routes(app, ctx: ServerContext, server_mod) -> None:
    @app.get(
        "/api/projects/{sid}/counterexamples",
        dependencies=[Depends(ctx.require_auth)],
    )
    def _counterexamples(sid: str) -> dict[str, Any]:
        root = ctx.project_root_or_404(sid)
        workspace = server_mod._project_workspace(sid, global_root=root)
        if workspace is None:
            raise HTTPException(status_code=404, detail=f"project workspace unavailable: {sid}")
        return server_mod.build_counterexample_dashboard(workspace)


__all__ = ["register_counterexample_routes"]
