"""artifacts/read-only API domain: project artifact listing, artifact detail,
raw artifact file serving, and git-diff.

See :mod:`.meta` for the extraction convention this module follows.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, HTTPException, Query, Response
from starlette.responses import FileResponse

from .context import ServerContext

# The preview page sandboxes itself and denies every network destination, so
# the delivered site's own inline style and script run while it can neither
# reach back into Argus nor call out. Fonts, images and media stay confined to
# the data/blob values the packager already inlined.
PREVIEW_PAGE_CSP = (
    "sandbox allow-scripts allow-downloads; default-src 'none'; "
    "script-src 'unsafe-inline' data: blob:; style-src 'unsafe-inline' data:; "
    "img-src data: blob:; font-src data:; media-src data: blob:; "
    "connect-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'"
)


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

    def html_package(sid: str, path: str):
        from ..artifact_preview import HtmlPackage

        resolved = server_mod._resolved_project_artifact(
            sid, path, global_root=ctx.project_root_or_404(sid)
        )
        if resolved is None or resolved[0]["kind"] != "html":
            raise HTTPException(status_code=404, detail="HTML artifact unavailable or not allowlisted")
        package = HtmlPackage(resolved[1])
        try:
            preview = package.build()
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=413, detail="HTML result is unavailable or too large to preview") from exc
        return package, preview

    @app.get("/api/projects/{sid}/artifact/preview", dependencies=[Depends(ctx.require_auth)])
    def _artifact_preview(sid: str, response: Response, path: str = Query(..., min_length=1)):
        response.headers["Cache-Control"] = "private, no-store"
        _, preview = html_package(sid, path)
        return preview

    # A page loaded into a sandboxed iframe carries its own policy, so its own
    # inline styles and scripts run and the delivered website behaves the way it
    # was built to. The policy sandboxes the document and cuts off every network
    # destination, so nothing here can reach the project directory, the app, or
    # the outside; a token may ride in the query because an iframe cannot send a
    # header (a hosted portal supplies the header on the caller's behalf).
    @app.get("/api/projects/{sid}/artifact/preview/page")
    def _artifact_preview_page(
        sid: str,
        path: str = Query(..., min_length=1),
        token: str | None = Query(default=None),
        authorization: str | None = Header(default=None),
    ):
        ctx.authorize_read(authorization, token)
        _, preview = html_package(sid, path)
        return Response(preview["html"], media_type="text/html; charset=utf-8", headers={
            "Content-Security-Policy": PREVIEW_PAGE_CSP,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
        })

    @app.get("/api/projects/{sid}/artifact/bundle", dependencies=[Depends(ctx.require_auth)])
    def _artifact_bundle(sid: str, path: str = Query(..., min_length=1)):
        from io import BytesIO
        from zipfile import ZIP_DEFLATED, ZipFile

        package, preview = html_package(sid, path)
        output = BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            for name, data in package.files.items():
                archive.writestr(name, data)
            if preview["warnings"]:
                archive.writestr("ARGUS-PREVIEW-NOTES.txt", "\n".join(preview["warnings"]))
        return Response(output.getvalue(), media_type="application/zip", headers={
            "Content-Disposition": 'attachment; filename="argus-website.zip"',
            "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
        })

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
