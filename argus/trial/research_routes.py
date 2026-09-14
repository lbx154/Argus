"""Invitation-scoped research feedback and separately protected operator controls."""
from __future__ import annotations

from fastapi import HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .analytics import AnalyticsError
from .research_controls import ResearchControls
from .store import TrialError


def register_research(app, analytics, session, *, journal=None):
    from .web_portal import json_body, read_body, require_origin

    controls = ResearchControls(analytics)
    app.state.research_controls = controls
    app.state.journal = journal

    def identity(request: Request, *, admin=False, mutation=False):
        value = session(request)
        if value is None:
            raise HTTPException(401, "Invitation or administrator access required")
        if admin != (value["role"] == "admin"):
            raise HTTPException(403, "Administrator access required" if admin else "Tester access required")
        if mutation:
            require_origin(request)
            if value["readonly"]:
                raise HTTPException(403, "Read-only session")
        if not admin and not analytics.consented(value["tenant"], analytics.notice_version):
            raise HTTPException(403, "Please confirm the trial data notice")
        return value

    async def operation(function, *args, **kwargs):
        try:
            return await run_in_threadpool(function, *args, **kwargs)
        except AnalyticsError as exc:
            raise HTTPException(exc.status, exc.code) from None
        except TrialError as exc:
            raise HTTPException(exc.status, exc.code) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    def recorder():
        if app.state.journal is None:
            raise HTTPException(503, "Server-side replay recorder is not available")
        return app.state.journal

    def project_list(tenant):
        result = analytics.projects(tenant)
        rows = {row["id"]: row for row in result["projects"]}
        with analytics._db() as db:
            for row in db.execute(
                "SELECT sid FROM journey_projects WHERE tenant_id=? AND notice_version=?",
                (tenant, analytics.notice_version),
            ):
                rows.setdefault(row["sid"], {"id": row["sid"], "title": row["sid"]})
            deleted = {row["sid"] for row in db.execute(
                "SELECT sid FROM journey_tombstones WHERE tenant_id=?", (tenant,),
            )}
        result["projects"] = [
            {**row, "research_deleted": sid in deleted} for sid, row in sorted(rows.items())
        ]
        return result

    app.state.research_projects = project_list

    @app.get("/invite/research")
    @app.get("/invite/research.js")
    async def research_page(request: Request):
        identity(request)
        from .research_page import PAGE, SCRIPT

        return (Response(SCRIPT, media_type="application/javascript")
                if request.url.path.endswith(".js") else HTMLResponse(PAGE))

    @app.middleware("http")
    async def audit_operator_access(request: Request, call_next):
        value = session(request)
        audit = request.url.path.startswith("/admin/api/") and value and value["role"] == "admin"
        action = "admin.export" if request.url.path.endswith("/export") else (
            "admin.view" if request.method in {"GET", "HEAD"} else "admin.change"
        )
        if audit:
            await run_in_threadpool(controls.audit, action)
        response = await call_next(request)
        if audit:
            await run_in_threadpool(
                controls.audit, action, outcome="allowed" if response.status_code < 400 else "denied",
            )
        return response

    @app.get("/research/api/projects")
    async def projects(request: Request):
        value = identity(request)
        return await operation(project_list, value["tenant"])

    @app.get("/research/api/projects/{sid}/replay")
    async def replay(request: Request, sid: str,
                     after_sequence: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=500)):
        value = identity(request)
        return await operation(
            recorder().replay, value["tenant"], sid, limit=limit, after_sequence=after_sequence,
        )

    @app.post("/research/api/projects/{sid}/feedback")
    async def feedback(request: Request, sid: str):
        value = identity(request, mutation=True)
        return await operation(
            controls.feedback, value["tenant"], sid, json_body(await read_body(request, 16384)),
        )

    @app.get("/research/api/projects/{sid}/feedback")
    async def tester_feedback(request: Request, sid: str):
        value = identity(request)
        return await operation(controls.feedback_list, value["tenant"], sid)

    @app.delete("/research/api/projects/{sid}")
    async def delete_copies(request: Request, sid: str):
        value = identity(request, mutation=True)
        return await operation(controls.delete_copies, recorder(), value["tenant"], sid)

    @app.get("/admin/api/research/status")
    async def collector_status(request: Request):
        identity(request, admin=True)
        return app.state.research_status

    @app.get("/admin/api/research/testers")
    async def testers(request: Request):
        identity(request, admin=True)
        rows = [await operation(app.state.store.access_info, tenant) for tenant in analytics.tenants]
        return {"testers": rows}

    @app.post("/admin/api/research/testers/{tenant}/access")
    async def access(request: Request, tenant: str):
        identity(request, admin=True, mutation=True)
        if tenant not in analytics.tenants:
            raise HTTPException(404, "Tester not found")
        data = json_body(await read_body(request, 2048))
        if data.keys() != {"enabled", "expires_at"}:
            raise HTTPException(400, "Provide enabled and expires_at")
        await operation(controls.audit, "invitation.access", tenant_id=tenant)
        await operation(app.state.store.set_access, tenant, **data)
        await operation(controls.audit, "invitation.access", tenant_id=tenant, outcome="completed")
        return await operation(app.state.store.access_info, tenant)

    @app.get("/admin/api/research/audit")
    async def audit(request: Request, limit: int = Query(100, ge=1, le=500)):
        identity(request, admin=True)
        return await operation(controls.audit_log, limit)

    @app.get("/admin/api/research/{tenant}/{sid}/replay")
    async def admin_replay(request: Request, tenant: str, sid: str,
                           after_sequence: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=500)):
        identity(request, admin=True)
        await operation(controls.audit, "replay.view", tenant_id=tenant, sid=sid)
        return await operation(recorder().replay, tenant, sid, limit=limit, after_sequence=after_sequence)

    @app.get("/admin/api/research/{tenant}/{sid}/export")
    async def export(request: Request, tenant: str, sid: str,
                     after_sequence: int = Query(0, ge=0)):
        identity(request, admin=True)
        await operation(controls.audit, "replay.export", tenant_id=tenant, sid=sid)
        result = await operation(
            recorder().replay, tenant, sid, limit=500, after_sequence=after_sequence,
        )
        result["annotation"] = await operation(controls.annotation, tenant, sid)
        result["tester_feedback"] = await operation(controls.feedback_list, tenant, sid)
        result["export_scope"] = "One bounded replay page; has_more and next_sequence indicate remaining pages."
        response = JSONResponse(result)
        return Response(response.body, media_type="application/json", headers={
            "Content-Disposition": 'attachment; filename="argus-research-replay.json"',
        })

    @app.get("/admin/api/research/{tenant}/{sid}/annotation")
    async def annotation(request: Request, tenant: str, sid: str):
        identity(request, admin=True)
        return await operation(controls.annotation, tenant, sid)

    @app.get("/admin/api/research/{tenant}/{sid}/feedback")
    async def operator_feedback(request: Request, tenant: str, sid: str):
        identity(request, admin=True)
        return await operation(controls.feedback_list, tenant, sid)

    @app.post("/admin/api/research/{tenant}/{sid}/annotation")
    async def annotate(request: Request, tenant: str, sid: str):
        identity(request, admin=True, mutation=True)
        await operation(controls.audit, "annotation.write", tenant_id=tenant, sid=sid)
        return await operation(controls.annotate, tenant, sid, json_body(await read_body(request, 32768)))
