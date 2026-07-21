"""artifacts/read-only API domain: project artifact listing, artifact detail,
raw artifact file serving, and git-diff.

See :mod:`.meta` for the extraction convention this module follows.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Query, Response
from starlette.responses import FileResponse

from .context import ServerContext


def register_artifact_routes(app, ctx: ServerContext, server_mod) -> None:
    @app.get(
        "/api/projects/{sid}/artifacts",
        dependencies=[Depends(ctx.require_auth)],
    )
    def _artifacts(sid: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "private, no-store"
        return {
            "artifacts": ctx.not_found_if_none(
                server_mod.list_project_artifacts(
                    sid, global_root=ctx.project_root_or_404(sid)
                ),
                sid,
            )
        }

    @app.get(
        "/api/projects/{sid}/artifact",
        dependencies=[Depends(ctx.require_auth)],
    )
    def _artifact(
        sid: str,
        response: Response,
        path: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "private, no-store"
        artifact = server_mod.get_project_artifact(
            sid, path, global_root=ctx.project_root_or_404(sid)
        )
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact unavailable or not allowlisted")
        return artifact

    @app.get(
        "/api/projects/{sid}/artifact/raw",
        dependencies=[Depends(ctx.require_auth)],
    )
    def _artifact_raw(
        sid: str,
        path: str = Query(..., min_length=1),
        download: bool = Query(False),
    ):
        resolved = server_mod._resolved_project_artifact(
            sid, path, global_root=ctx.project_root_or_404(sid)
        )
        if resolved is None:
            raise HTTPException(status_code=404, detail="artifact unavailable or not allowlisted")
        info, file_path = resolved
        safe_inline = info["kind"] in {"image", "pdf", "audio", "video"}
        media_type = (
            "application/octet-stream" if download
            else str(info["mime"]) if safe_inline
            else "text/plain; charset=utf-8"
        )
        return FileResponse(
            file_path,
            media_type=media_type,
            filename=str(info["name"]),
            content_disposition_type="attachment" if download else "inline",
            headers={
                # HTML/SVG and unknown binaries intentionally arrive as plain
                # text; prohibit browser MIME sniffing from turning them back
                # into executable content.
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )

    @app.get(
        "/api/projects/{sid}/git-diff",
        dependencies=[Depends(ctx.require_auth)],
    )
    def _git_diff(sid: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "private, no-store"
        return ctx.not_found_if_none(
            server_mod._project_git_diff(sid, global_root=ctx.project_root_or_404(sid)),
            sid,
        )
