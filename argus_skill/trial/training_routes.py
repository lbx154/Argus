"""Session-scoped revocable permissions and operator-only dataset downloads."""
from __future__ import annotations

from fastapi import HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .analytics import AnalyticsError
from .collaboration_data import CollaborationData
from .training_data import TrainingData


def register_training_routes(app, analytics, session, *, journal=None, controls=None, page_renderer=None):
    """Register before the portal catch-all; reuse existing ResearchControls."""
    from .data_page import register_data_page
    from .research_controls import ResearchControls
    from .web_portal import json_body, read_body, require_origin

    controls = controls or ResearchControls(analytics)
    training = TrainingData(analytics, journal, controls)
    app.state.training_data = training
    collaboration = CollaborationData(training)
    app.state.collaboration_data = collaboration
    register_data_page(app, session, page_renderer=page_renderer)

    def identity(request, *, admin=False, mutation=False):
        value = session(request)
        if value is None:
            raise HTTPException(401, "Authentication required")
        if value["role"] != ("admin" if admin else "trial"):
            raise HTTPException(403, "Operator access required" if admin else "Tester access required")
        if mutation:
            require_origin(request)
            if value["readonly"]:
                raise HTTPException(403, "Read-only session")
        return value

    async def operation(function, *args, **kwargs):
        try:
            return await run_in_threadpool(function, *args, **kwargs)
        except AnalyticsError as exc:
            raise HTTPException(exc.status, exc.code) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/trial/data-permissions")
    async def permissions(request: Request):
        value = identity(request)
        result = await operation(training.permissions, value["tenant"])
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.put("/trial/data-permissions")
    async def set_permissions(request: Request):
        value = identity(request, mutation=True)
        return await operation(
            training.set_permissions, value["tenant"], json_body(await read_body(request, 2048)),
        )

    @app.get("/admin/api/training/preview")
    async def preview(request: Request, purpose: str = "internal_training",
                      offset: int = Query(0, ge=0, le=2_147_483_647), tenant: str | None = None,
                      query: str = Query("", max_length=160)):
        try:
            identity(request, admin=True)
        except HTTPException:
            await operation(training.audit, "preview", purpose, outcome="denied")
            raise
        result = await operation(training.preview, purpose, offset=offset, tenant=tenant, query=query)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/admin/api/training/audit")
    async def training_audit(request: Request, limit: int = Query(40, ge=1, le=100)):
        identity(request, admin=True)
        return await operation(training.review_audit, limit)

    @app.get("/admin/api/training/collaboration")
    async def collaboration_overview(request: Request, purpose: str = "internal_training",
                                     offset: int = Query(0, ge=0, le=2_147_483_647),
                                     tenant: str | None = None,
                                     query: str = Query("", max_length=160)):
        identity(request, admin=True)
        result = await operation(
            collaboration.overview, purpose, offset=offset, tenant=tenant, query=query,
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/admin/api/training/collaboration/{tenant}/{sid}")
    async def collaboration_detail(request: Request, tenant: str, sid: str,
                                   purpose: str = "internal_training",
                                   task_id: str | None = Query(None, max_length=80)):
        identity(request, admin=True)
        result = await operation(collaboration.detail, purpose, tenant, sid, task_id)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/admin/api/training/observations/{tenant}/{sid}")
    async def retained_observations(request: Request, tenant: str, sid: str,
                                    purpose: str = "internal_training",
                                    task_id: str | None = Query(None, max_length=80),
                                    cursor: str | None = Query(None, max_length=1024),
                                    limit: int = Query(200, ge=1, le=500)):
        identity(request, admin=True)
        result = await operation(
            training.observations, purpose, tenant, sid, task_id, cursor=cursor, limit=limit,
        )
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.post("/admin/api/training/export-observations")
    async def export_observations(request: Request):
        purpose = None
        try:
            identity(request, admin=True, mutation=True)
            data = json_body(await read_body(request, 192 * 1024))
            purpose = data.get("purpose")
            if set(data) - {"purpose", "projects"}:
                raise HTTPException(400, "Unknown export fields")
        except HTTPException:
            await operation(training.audit, "export_observations", purpose, outcome="denied")
            raise
        content, filename = await operation(
            training.export_observations, purpose, data.get("projects"),
        )
        return StreamingResponse(content, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        })

    @app.post("/admin/api/training/export")
    async def export(request: Request):
        purpose = None
        try:
            identity(request, admin=True, mutation=True)
            data = json_body(await read_body(request, 192 * 1024))
            purpose = data.get("purpose")
            if set(data) - {"purpose", "projects", "review"}:
                raise HTTPException(400, "Unknown export fields")
        except HTTPException:
            await operation(training.audit, "export", purpose, outcome="denied")
            raise
        content, filename = await operation(
            training.export, purpose, data.get("projects"), review=data.get("review"),
        )
        return Response(content, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        })

    return training
