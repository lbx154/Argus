"""Authenticated routes into the shared React data workbench."""
from __future__ import annotations

from fastapi import HTTPException, Request

FRONTEND_BUILD_MISSING = "前端构建不可用，请先构建并配置 frontend_dir。"


def register_data_page(app, session, *, page_renderer=None):
    from .web_portal import canonical_path

    @app.api_route("/admin/data", methods=["GET", "HEAD"])
    @app.api_route("/admin/data/{path:path}", methods=["GET", "HEAD"])
    async def data_page(request: Request):
        identity = session(request)
        if identity is None:
            raise HTTPException(401, "Authentication required")
        if identity["role"] != "admin":
            raise HTTPException(403, "Operator access required")
        if not canonical_path(request.url.path) or request.url.path == "/admin/data/app.js":
            raise HTTPException(404, "Not found")
        if page_renderer is None:
            raise HTTPException(503, FRONTEND_BUILD_MISSING)
        return await page_renderer(request)
