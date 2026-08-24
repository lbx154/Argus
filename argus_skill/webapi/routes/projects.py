"""projects/sessions API domain: project listing/CRUD, trash, and per-project
snapshot/event reads.

Per-session work-item endpoints (tasks, nudges, pending question answers,
notes, plan preview, backlog item read/dispose/stop, mission abort,
status/journal/transcript reads) live in the sibling :mod:`.workitems`
module — this file stayed too big as a single registrar (breaching the
per-function size budget) so it was split along a real seam: project-level
listing/CRUD vs. per-mission work-item operations.

See :mod:`.meta` for the extraction convention this module follows.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from .context import ServerContext
from .models import LaunchCwdIn, ProjectUpdateIn, WorkdirIn


def register_project_routes(app, ctx: ServerContext, server_mod) -> None:
    @app.get("/api/projects", dependencies=[Depends(ctx.require_auth)])
    def _projects(
        limit: int = Query(100, ge=1, le=2000),
        include_empty: bool = Query(False),
    ) -> dict[str, Any]:
        return {
            "projects": ctx.machine_projects(limit=limit, include_empty=include_empty),
            "local_cwd": "",
        }

    @app.get("/api/projects/costs", dependencies=[Depends(ctx.require_auth)])
    def _project_costs(
        limit: int = Query(100, ge=1, le=2000),
    ) -> dict[str, Any]:
        return {
            "projects": ctx.machine_project_costs(limit=limit),
            "generated_at": server_mod.time.time(),
        }

    @app.get("/api/trash", dependencies=[Depends(ctx.require_auth)])
    def _trash(
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        query: str = Query("", max_length=200),
    ) -> dict[str, Any]:
        entries = ctx.machine_trash()
        needle = query.strip().casefold()
        if needle:
            entries = [
                entry
                for entry in entries
                if needle
                in " ".join(
                    (
                        str(entry.get("sid") or ""),
                        str(entry.get("label") or ""),
                        str(entry.get("launch_cwd") or ""),
                        str(entry.get("trash_path") or ""),
                    )
                ).casefold()
            ]
        return {
            "entries": entries[offset : offset + limit],
            "total": len(entries),
            "offset": offset,
            "limit": limit,
        }

    @app.post(
        "/api/trash/{trash_id:path}/restore",
        dependencies=[Depends(ctx.require_auth)],
    )
    async def _restore_trash(trash_id: str) -> dict[str, Any]:
        prefix, separator, relative = trash_id.partition(":")
        if not separator or not prefix.isdigit():
            raise HTTPException(status_code=404, detail="unknown trash entry")
        index = int(prefix)
        if index >= len(ctx.roots):
            raise HTTPException(status_code=404, detail="unknown trash entry")
        entry = next(
            (
                item
                for item in server_mod.list_trashed_projects(global_root=ctx.roots[index])
                if item["trash_path"] == relative
            ),
            None,
        )
        if entry is None:
            raise HTTPException(status_code=404, detail="unknown trash entry")
        if any(
            server_mod.project_life_dir(str(entry["sid"]), global_root=root) is not None
            for root in ctx.roots
        ):
            raise HTTPException(
                status_code=409,
                detail="a session with this id already exists",
            )
        result = await run_in_threadpool(
            server_mod.restore_trashed_project,
            relative,
            global_root=ctx.roots[index],
            existing_roots=ctx.roots,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="unknown trash entry")
        if not result.get("ok"):
            raise HTTPException(status_code=409, detail=result.get("error"))
        return result

    @app.post("/api/projects/{sid}/launch-cwd", dependencies=[Depends(ctx.require_auth)])
    async def _set_launch_cwd(sid: str, body: LaunchCwdIn) -> dict[str, bool]:
        updated = await run_in_threadpool(
            server_mod.set_project_launch_cwd,
            sid,
            body.launch_cwd,
            global_root=ctx.project_root_or_404(sid),
        )
        if updated is None:
            raise HTTPException(status_code=404, detail=f"unknown project: {sid}")
        if not updated:
            raise HTTPException(
                status_code=400,
                detail="launch_cwd must be an existing directory",
            )
        return {"ok": True}

    @app.post("/api/projects/{sid}/workdir", dependencies=[Depends(ctx.require_auth)])
    async def _set_workdir(sid: str, body: WorkdirIn) -> dict[str, Any]:
        result = await run_in_threadpool(
            server_mod.set_project_workdir,
            sid,
            body.workdir,
            global_root=ctx.project_root_or_404(sid),
        )
        if result is None:
            raise HTTPException(status_code=404, detail=f"unknown project: {sid}")
        if not result.get("ok"):
            raise HTTPException(
                status_code=409, detail=str(result.get("error") or "workdir update failed")
            )
        return result

    @app.patch("/api/projects/{sid}", dependencies=[Depends(ctx.require_auth)])
    async def _update_project(sid: str, body: ProjectUpdateIn) -> dict[str, Any]:
        return ctx.not_found_if_none(
            await run_in_threadpool(
                server_mod.update_project,
                sid,
                name=body.name,
                global_root=ctx.project_root_or_404(sid),
            ),
            sid,
        )

    @app.delete("/api/projects/{sid}", dependencies=[Depends(ctx.require_auth)])
    async def _delete_project(sid: str) -> dict[str, Any]:
        result = ctx.not_found_if_none(
            await run_in_threadpool(
                server_mod.delete_project,
                sid,
                global_root=ctx.project_root_or_404(sid),
                lifecycle_root=server_mod._global_root(ctx.global_root),
            ),
            sid,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=409, detail=result["error"])
        return result

    @app.get(
        "/api/projects/{sid}/snapshot",
        dependencies=[Depends(ctx.require_auth)],
    )
    def _snapshot(
        sid: str,
        events_limit: int = Query(80, ge=1, le=500),
        compact: bool = Query(False),
        prewarm: bool = Query(False),
    ) -> dict[str, Any]:
        root = ctx.project_root_or_404(sid)
        if prewarm:
            try:
                from ..manager_state import schedule_manager_prewarm

                schedule_manager_prewarm(sid, global_root=root)
            except Exception:  # noqa: BLE001 - snapshot must remain read-available
                pass

        def _build_snapshot() -> dict[str, Any] | None:
            return server_mod.build_snapshot(
                sid,
                global_root=root,
                events_limit=events_limit,
                compact=compact,
            )

        return ctx.not_found_if_none(
            ctx.snapshot_cache.get(
                ("project_snapshot", sid, events_limit, compact),
                _build_snapshot,
            ),
            sid,
        )

    @app.get(
        "/api/projects/{sid}/events",
        dependencies=[Depends(ctx.require_auth)],
    )
    def _events(
        sid: str,
        limit: int = Query(80, ge=1, le=1000),
        view: str = Query("full", pattern="^(full|ui)$"),
    ) -> dict[str, Any]:
        life_dir = ctx.resolve_or_404(sid)
        if view == "ui":
            events = server_mod._read_jsonl_tail_history(
                life_dir / server_mod.EVENT_FILE,
                limit,
                predicate=server_mod._event_visible_in_web_ui,
            )
        else:
            events = server_mod._read_recent_project_events(life_dir, limit=limit)
        return {"events": events}
