"""per-mission work-item API domain: tasks, nudges, pending-question
answers, notes, plan preview, backlog item read/dispose/stop, mission
abort, and status/journal/transcript reads.

Split out of :mod:`.projects` (see that module's docstring) - this is the
half of the former "projects/sessions" registrar that operates on a single
mission/backlog item rather than the project/session as a whole.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from .context import ServerContext
from .models import (
    AbortMissionIn,
    AnswerIn,
    DecisionIn,
    DisposeIn,
    NoteIn,
    NudgeIn,
    PlanIn,
    RewriteIn,
    TaskIn,
)


def register_workitem_routes(app, ctx: ServerContext, server_mod) -> None:
    @app.post("/api/projects/{sid}/tasks", dependencies=[Depends(ctx.require_auth)])
    async def _post_task(sid: str, body: TaskIn) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty task text")
        project_root = ctx.project_root_or_404(sid)
        try:
            response = ctx.not_found_if_none(
                await run_in_threadpool(
                    server_mod.enqueue_task_command,
                    sid,
                    body.text,
                    autostart_daemon=body.autostart_daemon,
                    global_root=project_root,
                    lifecycle_root=server_mod._global_root(ctx.global_root),
                ),
                sid,
            )
        except server_mod.ManagerHandoffSupersededError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except server_mod.ManagerHandoffError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return response

    @app.post("/api/projects/{sid}/nudge", dependencies=[Depends(ctx.require_auth)])
    def _post_nudge(sid: str, body: NudgeIn) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty nudge text")
        ctx.not_found_if_none(
            server_mod.enqueue_nudge(
                sid, body.text, global_root=ctx.project_root_or_404(sid)
            ),
            sid,
        )
        return {"ok": True}

    @app.post(
        "/api/projects/{sid}/backlog/{item_id}/answer",
        dependencies=[Depends(ctx.require_auth)],
    )
    async def _answer_pending(
        sid: str,
        item_id: str,
        body: AnswerIn,
    ) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty answer")
        project_root = ctx.project_root_or_404(sid)
        result = await run_in_threadpool(
            server_mod.answer_pending_question,
            sid,
            item_id,
            body.text,
            global_root=project_root,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="unknown backlog item")
        if result.get("error"):
            raise HTTPException(status_code=409, detail=result["error"])
        if result.get("resolved"):
            result["daemon"] = await run_in_threadpool(
                server_mod.start_project_daemon,
                sid,
                global_root=project_root,
                reclaim_idle=True,
            )
        return result

    @app.post(
        "/api/projects/{sid}/decisions/{decision_id}/resolve",
        dependencies=[Depends(ctx.require_auth)],
    )
    async def _resolve_decision(
        sid: str,
        decision_id: str,
        body: DecisionIn,
    ) -> dict[str, Any]:
        project_root = ctx.project_root_or_404(sid)
        result = await run_in_threadpool(
            server_mod.resolve_operator_decision,
            sid,
            decision_id,
            body.option_id,
            body.note,
            expected_revision=body.expected_revision,
            global_root=project_root,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="unknown decision")
        if result.get("error"):
            raise HTTPException(status_code=409, detail=result["error"])
        if result.get("resolved") and not result.get("stopped"):
            result["daemon"] = await run_in_threadpool(
                server_mod.start_project_daemon,
                sid,
                global_root=project_root,
                resume_continuous=bool(result.get("continuous")),
                reclaim_idle=True,
            )
        return result

    @app.get("/api/projects/{sid}/status")
    def _status(sid: str) -> dict[str, Any]:
        return ctx.not_found_if_none(
            server_mod.get_status(sid, global_root=ctx.project_root_or_404(sid)), sid
        )

    @app.get("/api/projects/{sid}/journal")
    def _journal(sid: str, n: int = Query(10, ge=1, le=500)) -> dict[str, Any]:
        return {
            "journal": ctx.not_found_if_none(
                server_mod.get_journal(
                    sid, n=n, global_root=ctx.project_root_or_404(sid)
                ),
                sid,
            )
        }

    @app.get("/api/projects/{sid}/transcript")
    def _transcript(sid: str, n: int = Query(20, ge=1, le=500)) -> dict[str, Any]:
        return {
            "turns": ctx.not_found_if_none(
                server_mod.get_transcript(
                    sid, n=n, global_root=ctx.project_root_or_404(sid)
                ),
                sid,
            )
        }

    @app.get("/api/projects/{sid}/backlog/{item_id}")
    def _backlog_item(sid: str, item_id: str) -> dict[str, Any]:
        item = server_mod.get_backlog_item(
            sid, item_id, global_root=ctx.project_root_or_404(sid)
        )
        if item is None:
            raise HTTPException(status_code=404, detail=f"unknown backlog item: {item_id}")
        return {"item": item}

    @app.post("/api/projects/{sid}/note", dependencies=[Depends(ctx.require_auth)])
    def _post_note(sid: str, body: NoteIn) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty note text")
        return {
            "result": ctx.not_found_if_none(
                server_mod.add_project_note(
                    sid, body.text, global_root=ctx.project_root_or_404(sid)
                ),
                sid,
            )
        }

    @app.post("/api/projects/{sid}/plan", dependencies=[Depends(ctx.require_auth)])
    async def _plan_preview(sid: str, body: PlanIn) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty plan objective")
        project_root = ctx.project_root_or_404(sid)
        from ..manager_bridge import manager_plan
        return await run_in_threadpool(
            manager_plan, sid, body.text, global_root=project_root,
        )

    @app.post("/api/projects/{sid}/prompt/rewrite", dependencies=[Depends(ctx.require_auth)])
    async def _rewrite_prompt(sid: str, body: RewriteIn) -> dict[str, Any]:
        """Ask the Manager to restate a short operator draft as a usable brief.

        Preview only — nothing is enqueued and no mission is touched. The
        operator reviews/edits the result before sending it.
        """
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty prompt")
        project_root = ctx.project_root_or_404(sid)
        from ..manager_bridge import manager_rewrite
        return await run_in_threadpool(
            manager_rewrite, sid, body.text, global_root=project_root,
        )

    @app.post("/api/projects/{sid}/backlog/{item_id}/dispose", dependencies=[Depends(ctx.require_auth)])
    def _dispose(sid: str, item_id: str, body: DisposeIn) -> dict[str, Any]:
        if body.op not in ("done", "skip", "rm"):
            raise HTTPException(status_code=400, detail="op must be done|skip|rm")
        item = server_mod.dispose_backlog(
            sid,
            item_id,
            body.op,
            global_root=ctx.project_root_or_404(sid),
        )
        if item is None:
            raise HTTPException(status_code=404, detail=f"unknown backlog item: {item_id}")
        return {"item": item}

    @app.post("/api/projects/{sid}/mission/abort", dependencies=[Depends(ctx.require_auth)])
    def _abort_mission(sid: str, body: AbortMissionIn | None = None) -> dict[str, Any]:
        request = body or AbortMissionIn()
        project_root = ctx.project_root_or_404(sid)
        result = ctx.not_found_if_none(
            server_mod.abort_project_mission(
                sid,
                reason=request.reason,
                requested_by="operator",
                global_root=project_root,
            ),
            sid,
        )
        if result.get("error"):
            raise HTTPException(status_code=500, detail=result["error"])
        return result

    @app.post("/api/projects/{sid}/backlog/{item_id}/stop", dependencies=[Depends(ctx.require_auth)])
    def _stop_item(sid: str, item_id: str) -> dict[str, Any]:
        item = server_mod.stop_backlog_iteration(
            sid, item_id, global_root=ctx.project_root_or_404(sid)
        )
        if item is None:
            raise HTTPException(status_code=404, detail=f"unknown backlog item: {item_id}")
        return {"item": item}
