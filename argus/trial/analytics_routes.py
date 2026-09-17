"""Operator analytics, combined retention, and consent-aware metadata observation."""
from __future__ import annotations

import logging
import sqlite3
import threading
import time

from fastapi import HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

LOG = logging.getLogger(__name__)
HTTP_OBSERVATION = "argus.http_observation"


class RequestObservation:
    """Metadata only; existing response-body captures remain independent."""

    def __init__(self, analytics, tenant, method, path, *, clock=None):
        self.analytics, self.tenant, self.method, self.path = analytics, tenant, method, path
        self.clock = clock or time.monotonic
        self.started = self.clock()
        self.event_id = None
        self._headers_recorded = False
        self._finished = False
        self._lock = threading.Lock()

    def headers(self, status, content_type):
        elapsed = (self.clock() - self.started) * 1000
        media = content_type.split(";", 1)[0].strip().lower()
        response_type = "sse" if media == "text/event-stream" else (
            "json" if media == "application/json" or media.endswith("+json") else "other"
        )
        with self._lock:
            if self._headers_recorded:
                return
            self._headers_recorded = True
            self.event_id = self.analytics.record_request(
                self.tenant, self.method, self.path, status, elapsed,
                header_elapsed_ms=elapsed, response_type=response_type,
            )

    def finish(self, status, completed, content_type):
        # Same callback contract as the existing interaction capture. HTTP
        # status/content type were observed at headers; neither implies EOF.
        with self._lock:
            if self._finished or self.event_id is None:
                return
            self._finished = True
            self.analytics.finish_request(
                self.tenant, self.event_id, (self.clock() - self.started) * 1000, completed,
            )


class HttpResponseObservation:
    """Observe successful ASGI body sends without buffering or parsing the body."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from .web_portal import finish_capture

        completed, status, content_type = False, 500, ""

        async def observed_send(message):
            nonlocal completed, status, content_type
            if message["type"] == "http.response.start":
                status = message["status"]
                content_type = next((value.decode("latin-1") for name, value in message.get("headers", [])
                                     if name.lower() == b"content-type"), "")
                observation = scope.get(HTTP_OBSERVATION)
                if observation is not None:
                    try:
                        await run_in_threadpool(observation.headers, status, content_type)
                    except (sqlite3.Error, OSError):
                        LOG.exception("Trial request observation could not be persisted")
                        message = {**message, "headers": [*message.get("headers", []),
                            (b"x-argus-analytics-error", b"recording-unavailable")]}
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                # Only a successful terminal send establishes server-observed
                # body completion. It does not certify client acknowledgement.
                completed = True
                # Settle before the application runs any response background
                # task, whose duration/failure is not HTTP delivery evidence.
                await finish_capture(scope.get(HTTP_OBSERVATION), status, True, content_type)

        try:
            await self.app(scope, receive, observed_send)
        finally:
            await finish_capture(scope.get(HTTP_OBSERVATION), status, completed, content_type)


def register_analytics(app, analytics, session, *, observe_responses=True) -> None:
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
        identity = await run_in_threadpool(session, request)
        tenant = identity["tenant"] if identity and identity["role"] == "trial" else None
        consented = bool(tenant and await run_in_threadpool(analytics.consented, tenant, analytics.notice_version))
        if tenant and not consented and request.url.path not in {
            "/invite", "/invite/login", "/invite/logout", "/admin/login",
        }:
            if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                return RedirectResponse("/invite", status_code=303)
            return JSONResponse({"detail": "Please confirm the trial data notice"}, status_code=401)
        observation = RequestObservation(analytics, tenant, request.method, request.url.path) if consented else None
        if observation is not None:
            request.scope[HTTP_OBSERVATION] = observation
        return await call_next(request)

    if observe_responses:
        app.add_middleware(HttpResponseObservation)

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
