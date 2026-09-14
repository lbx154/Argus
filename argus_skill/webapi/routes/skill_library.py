"""Authenticated, read-only access to global, vertical and project Skills."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Query

from ...skills import catalog as skill_library
from ...skills.vertical_select import resolve_skill_scope
from ..artifacts import project_workspace
from .context import ServerContext


def register_skill_library_routes(app, ctx: ServerContext) -> None:
    def libraries(sid: str | None):
        if sid is None:
            return skill_library.library_roots(ctx.roots[0]), ""
        root = ctx.project_root_or_404(sid)
        state = ctx.resolve_or_404(sid)
        workspace = project_workspace(sid, global_root=root)
        return (skill_library.library_roots(root, state, workspace),
                resolve_skill_scope(workspace) if workspace is not None else "")

    @app.get("/api/skill-library", dependencies=[Depends(ctx.require_auth)])
    def _catalog(sid: str | None = Query(None, max_length=128)) -> dict[str, Any]:
        roots, active = libraries(sid)
        return skill_library.catalog(roots, active_vertical=active)

    @app.get("/api/skill-library/document", dependencies=[Depends(ctx.require_auth)])
    def _document(
        library: str = Query(..., max_length=256),
        path: str = Query(..., max_length=1024),
        sid: str | None = Query(None, max_length=128),
    ) -> dict[str, Any]:
        roots, _ = libraries(sid)
        try:
            return skill_library.read_document(roots, library, path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (OSError, UnicodeError) as exc:
            raise HTTPException(status_code=409, detail="Cannot read Skill document") from exc
        except ValueError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
