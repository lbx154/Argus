"""Operator analytics, combined retention, and consent-aware metadata observation."""
from __future__ import annotations

import logging
import sqlite3
import time

from fastapi import HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

LOG = logging.getLogger(__name__)


def register_analytics(app, analytics, session) -> None:
    from .analytics import AnalyticsError
    from .web_portal import require_origin

    def admin(request: Request, *, mutation: bool = False):
        identity = session(request)
        if identity is None:
            raise HTTPException(401, "Administrator access required")
        if identity["role"] != "admin":
            raise HTTPException(403, "Administrator access required")
        if mutation:
            require_origin(request)
            if identity["readonly"]:
                raise HTTPException(403, "Read-only administrator session")

    async def operation(function, *args, **kwargs):
        try:
            return await run_in_threadpool(function, *args, **kwargs)
        except AnalyticsError as exc:
            raise HTTPException(exc.status, exc.code) from None
        except FileNotFoundError:
            raise HTTPException(404, "Analytics record not found") from None
        except PermissionError:
            raise HTTPException(403, "Consent or data boundary prevents access") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.middleware("http")
    async def observe(request: Request, call_next):
        identity = session(request)
        tenant = identity["tenant"] if identity and identity["role"] == "trial" else None
        consented = bool(tenant and analytics.consented(tenant, analytics.notice_version))
        if tenant and not consented and request.url.path not in {
            "/invite", "/invite/login", "/invite/logout", "/admin/login",
        }:
            if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                return RedirectResponse("/invite", status_code=303)
            return JSONResponse({"detail": "Please confirm the trial data notice"}, status_code=401)
        started = time.monotonic()
        response = await call_next(request)
        if consented:
            try:
                await run_in_threadpool(
                    analytics.record_request, tenant, request.method, request.url.path,
                    response.status_code, (time.monotonic() - started) * 1000,
                )
            except (sqlite3.Error, OSError):
                LOG.exception("Trial request observation could not be persisted")
                response.headers["X-Argus-Analytics-Error"] = "recording-unavailable"
        return response

    @app.get("/admin")
    @app.get("/admin/app.js")
    async def dashboard_page(request: Request):
        admin(request)
        from .admin_page import PAGE, SCRIPT

        return (
            Response(SCRIPT, media_type="application/javascript")
            if request.url.path.endswith(".js") else HTMLResponse(PAGE)
        )

    @app.get("/admin/api/dashboard")
    async def dashboard(request: Request, days: int = Query(1, ge=1, le=30),
                        include_internal: bool = False):
        admin(request)
        return await operation(analytics.dashboard, days=days, include_internal=include_internal)

    @app.get("/admin/api/tenants/{tenant}/projects")
    async def projects(request: Request, tenant: str):
        admin(request)
        if hasattr(app.state, "research_projects"):
            return await operation(app.state.research_projects, tenant)
        return await operation(analytics.projects, tenant)

    @app.get("/admin/api/tenants/{tenant}/projects/{sid}")
    async def trace(request: Request, tenant: str, sid: str,
                    limit: int = Query(500, ge=1, le=1000)):
        admin(request)
        if getattr(app.state, "journal", None) is not None:
            return await operation(app.state.journal.replay, tenant, sid, limit=min(500, limit))
        return await operation(analytics.trace, tenant, sid, limit=limit)

    @app.get("/admin/api/tenants/{tenant}/projects/{sid}/export")
    async def export(request: Request, tenant: str, sid: str):
        admin(request)
        if getattr(app.state, "journal", None) is not None:
            return RedirectResponse(
                f"/admin/api/research/{tenant}/{sid}/export", status_code=307,
            )
        result = await operation(analytics.export_trace, tenant, sid)
        return Response(result, media_type="application/json", headers={
            "Content-Disposition": 'attachment; filename="argus-task-trace.json"',
        })

    @app.post("/admin/api/prune")
    async def prune(request: Request):
        admin(request, mutation=True)
        from .interaction_capture import prune_interactions

        deleted_events = await operation(analytics.prune)
        deleted_interactions = await operation(prune_interactions, analytics)
        result = {
            "deleted_events": deleted_events,
            "deleted_interactions": deleted_interactions,
        }
        if getattr(app.state, "journal", None) is not None:
            result["deleted_journal"] = await operation(app.state.journal.prune)
            result["deleted_research"] = await operation(app.state.research_controls.prune)
        return result

    @app.get("/admin/api/tenants/{tenant}/interactions")
    async def interactions(request: Request, tenant: str):
        admin(request)
        from .interaction_capture import list_interactions

        return await operation(list_interactions, analytics, tenant)

    @app.get("/admin/api/tenants/{tenant}/interactions/{interaction_id}")
    async def interaction(request: Request, tenant: str, interaction_id: int):
        admin(request)
        from .interaction_capture import get_interaction

        return await operation(
            get_interaction, analytics, tenant, interaction_id,
            include_trace=getattr(app.state, "journal", None) is None,
        )
