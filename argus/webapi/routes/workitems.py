"""per-mission work-item API domain: tasks, nudges, pending-question
answers, notes, plan preview, backlog item read/dispose/stop, mission
abort, and status/journal/transcript reads.

Split out of :mod:`.projects` (see that module's docstring) - this is the
half of the former "projects/sessions" registrar that operates on a single
mission/backlog item rather than the project/session as a whole.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from ...manager.front_door import ManagerHandoffError, ManagerHandoffSupersededError
from .. import manager_pending_question, mission_items
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

log = logging.getLogger(__name__)


def register_workitem_routes(app, ctx: ServerContext) -> None:
    @app.post("/api/projects/{sid}/tasks", dependencies=[Depends(ctx.require_auth)])
    async def _post_task(sid: str, body: TaskIn) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty task text")
        project_root = ctx.project_root_or_404(sid)
        try:
            response = ctx.not_found_if_none(
                await run_in_threadpool(
                    mission_items.enqueue_task_command,
                    sid,
                    body.text,
                    autostart_daemon=body.autostart_daemon,
                    global_root=project_root,
                    lifecycle_root=ctx.roots[0],
                    start_daemon=ctx.daemon_services.start,
                ),
                sid,
            )
        except ManagerHandoffSupersededError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ManagerHandoffError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return response

    @app.post("/api/projects/{sid}/nudge", dependencies=[Depends(ctx.require_auth)])
    def _post_nudge(sid: str, body: NudgeIn) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty nudge text")
        result = ctx.not_found_if_none(
            mission_items.enqueue_nudge(sid, body.text, global_root=ctx.project_root_or_404(sid)),
            sid,
        )
        return {"ok": result.get("kind") != "error", **result}

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
            manager_pending_question.manager_answer_pending_question,
            sid,
            item_id,
            body.text,
            global_root=project_root,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="unknown backlog item")
        if result.get("error"):
            raise HTTPException(status_code=409, detail=result["error"])
        if result.get("resolved") and result.get("resume_requested", True):
            result["daemon"] = await run_in_threadpool(
                ctx.daemon_services.start,
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
            manager_pending_question.manager_resolve_operator_decision,
            sid,
            decision_id,
            body.option_id,
            body.note,
            global_root=project_root,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="unknown decision")
        if result.get("error"):
            raise HTTPException(status_code=409, detail=result["error"])
        if (
            result.get("resolved") and not result.get("stopped")
            and result.get("resume_requested", True)
        ):
            result["daemon"] = await run_in_threadpool(
                ctx.daemon_services.start,
                sid,
                global_root=project_root,
                resume_continuous=bool(result.get("continuous")),
                reclaim_idle=True,
            )
        return result

    @app.get(
        "/api/projects/{sid}/status",
        dependencies=[Depends(ctx.require_auth)],
    )
    def _status(sid: str) -> dict[str, Any]:
        return ctx.not_found_if_none(
            mission_items.get_status(
                sid,
                global_root=ctx.project_root_or_404(sid),
                read_status=ctx.daemon_services.read_status,
            ),
            sid,
        )

    @app.get(
        "/api/projects/{sid}/journal",
        dependencies=[Depends(ctx.require_auth)],
    )
    def _journal(sid: str, n: int = Query(10, ge=1, le=500)) -> dict[str, Any]:
        return {
            "journal": ctx.not_found_if_none(
                mission_items.get_journal(
                    sid, n=n, global_root=ctx.project_root_or_404(sid)
                ),
                sid,
            )
        }

    @app.get(
        "/api/projects/{sid}/transcript",
        dependencies=[Depends(ctx.require_auth)],
    )
    def _transcript(sid: str, n: int = Query(20, ge=1, le=500)) -> dict[str, Any]:
        return {
            "turns": ctx.not_found_if_none(
                mission_items.get_transcript(
                    sid, n=n, global_root=ctx.project_root_or_404(sid)
                ),
                sid,
            )
        }

    @app.get(
        "/api/projects/{sid}/backlog/{item_id}",
        dependencies=[Depends(ctx.require_auth)],
    )
    def _backlog_item(sid: str, item_id: str) -> dict[str, Any]:
        item = mission_items.get_backlog_item(
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
                mission_items.add_project_note(
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

    @app.post("/api/projects/{sid}/launch/confirm", dependencies=[Depends(ctx.require_auth)])
    async def _confirm_launch(sid: str) -> dict[str, Any]:
        """Start work the operator held for confirmation before its first run."""
        import time

        from ...core.mission_view import uses_cjk
        from ...core.operator_messages import publish_operator_message
        from ...daemon.commands import daemon_command_execution_lock
        from ..launch_hold import clear_launch_hold, read_launch_hold, release_held_ticket

        life_dir = ctx.resolve_or_404(sid)
        project_root = ctx.project_root_or_404(sid)
        record = read_launch_hold(life_dir)
        if record is None:
            raise HTTPException(status_code=409, detail="Nothing is waiting to start.")

        def _start() -> dict[str, Any]:
            with daemon_command_execution_lock(life_dir, blocking=False) as acquired:
                if not acquired:
                    raise HTTPException(status_code=409, detail="Daemon control is busy; retry shortly.")
                # Start means start: a ticket that says to wait for it now says it came.
                try:
                    release_held_ticket(life_dir, record)
                except Exception:  # noqa: BLE001 - the work still starts as queued
                    log.exception("could not mark the held ticket as started")
                started = ctx.daemon_services.start(
                    sid, global_root=project_root,
                    resume_continuous=bool(record.get("continuous")), reclaim_idle=True,
                )
                clear_launch_hold(life_dir)
            zh = uses_cjk(record.get("request") or record.get("title"))
            title = str(record.get("title") or "").strip()
            text = (f"已开始执行：{title}" if title else "已开始执行。") if zh else (
                f"Started: {title}" if title else "Started.")
            publish_operator_message(life_dir, text=text, message_id=f"launch-{time.time_ns()}")
            return {"ok": True, "daemon": started}

        result = await run_in_threadpool(_start)
        ctx.invalidate_read_caches()
        return result

    @app.post("/api/projects/{sid}/launch/discard", dependencies=[Depends(ctx.require_auth)])
    def _discard_launch(sid: str) -> dict[str, Any]:
        """Drop held, never-started work so the operator can rewrite the request."""
        import time

        from ...core.mission_view import uses_cjk
        from ...core.operator_messages import publish_operator_message
        from ...daemon.life_worker import read_daemon_status
        from ..launch_hold import discard_held_work, read_launch_hold

        life_dir = ctx.resolve_or_404(sid)
        record = read_launch_hold(life_dir)
        if record is None:
            raise HTTPException(status_code=409, detail="Nothing is waiting to start.")
        if read_daemon_status(life_dir).alive:
            raise HTTPException(status_code=409, detail="This work has already started; use Stop instead.")
        result = discard_held_work(life_dir)
        zh = uses_cjk(record.get("request") or record.get("title"))
        publish_operator_message(
            life_dir,
            text="已放弃。可以修改要求后重新发送。" if zh else "Discarded. Edit the request and send it again.",
            message_id=f"launch-discard-{time.time_ns()}",
        )
        ctx.invalidate_read_caches()
        return {"ok": True, **result, "request": str(record.get("request") or "")}

    @app.post("/api/projects/{sid}/backlog/{item_id}/dispose", dependencies=[Depends(ctx.require_auth)])
    def _dispose(sid: str, item_id: str, body: DisposeIn) -> dict[str, Any]:
        if body.op not in ("done", "skip", "rm"):
            raise HTTPException(status_code=400, detail="op must be done|skip|rm")
        item = mission_items.dispose_backlog(
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
            mission_items.abort_project_mission(
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
        item = mission_items.stop_backlog_iteration(
            sid, item_id, global_root=ctx.project_root_or_404(sid)
        )
        if item is None:
            raise HTTPException(status_code=404, detail=f"unknown backlog item: {item_id}")
        return {"item": item}
