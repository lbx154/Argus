"""daemon control API domain: creating daemons/sessions and starting,
stopping, replacing, and upgrading a project's executor, plus continuous
(7x24) mode toggling.

See :mod:`.meta` for the extraction convention this module follows.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from .context import ServerContext
from .models import CommandIn, ContinuousIn, CreateDaemonIn, ReplaceDaemonIn, StopIn


def register_daemon_routes(app, ctx: ServerContext, server_mod) -> None:
    @app.post("/api/daemons", dependencies=[Depends(ctx.require_auth)])
    async def _create_daemon(body: CreateDaemonIn) -> dict[str, Any]:
        """Create a brand-new daemon (session). The objective is OPTIONAL — with
        none, the daemon is idle and the user just talks to the Manager (which
        writes its own objectives). Threadpool: fs writes + optional fork."""
        root = server_mod._global_root(ctx.global_root)
        receipt = await run_in_threadpool(
            server_mod.execute_daemon_command,
            root,
            operation="create",
            args={
                "objective": body.objective,
                "name": body.name,
                "launch_cwd": body.launch_cwd,
                "workdir": body.workdir,
            },
            command_id=body.command_id or None,
            expected_revision=body.expected_revision,
            issuer="webapi",
            handler=lambda: server_mod.create_daemon(
                body.objective,
                name=body.name,
                launch_cwd=body.launch_cwd,
                workdir=body.workdir,
                global_root=ctx.global_root,
            ),
        )
        return server_mod._command_response(receipt)

    @app.post("/api/projects/{sid}/daemon/start", dependencies=[Depends(ctx.require_auth)])
    async def _daemon_start(
        sid: str,
        body: CommandIn | None = None,
    ) -> dict[str, Any]:
        command = body or CommandIn()
        life_dir = ctx.resolve_or_404(sid)
        project_root = ctx.project_root_or_404(sid)
        receipt = await run_in_threadpool(
            server_mod.execute_daemon_command,
            life_dir,
            operation="start",
            args={"resume_continuous": True},
            command_id=command.command_id or None,
            expected_revision=command.expected_revision,
            issuer="webapi",
            handler=lambda: ctx.not_found_if_none(
                server_mod.start_project_daemon(
                    sid,
                    global_root=project_root,
                    resume_continuous=True,
                ),
                sid,
            ),
        )
        return server_mod._command_response(receipt)

    @app.post("/api/projects/{sid}/daemon/stop", dependencies=[Depends(ctx.require_auth)])
    async def _daemon_stop(sid: str, body: StopIn | None = None) -> dict[str, Any]:
        b = body or StopIn()
        life_dir = ctx.resolve_or_404(sid)
        project_root = ctx.project_root_or_404(sid)
        operation = "kill" if b.force else "drain" if b.drain else "stop"
        receipt = await run_in_threadpool(
            server_mod.execute_daemon_command,
            life_dir,
            operation=operation,
            args={"drain": b.drain, "force": b.force},
            command_id=b.command_id or None,
            expected_revision=b.expected_revision,
            issuer="webapi",
            handler=lambda: ctx.not_found_if_none(
                server_mod.stop_project_daemon(
                    sid,
                    drain=b.drain,
                    force=b.force,
                    global_root=project_root,
                ),
                sid,
            ),
        )
        return server_mod._command_response(receipt)

    @app.post("/api/projects/{sid}/daemon/replace", dependencies=[Depends(ctx.require_auth)])
    async def _daemon_replace(sid: str, body: ReplaceDaemonIn) -> dict[str, Any]:
        life_dir = ctx.resolve_or_404(sid)
        project_root = ctx.project_root_or_404(sid)
        receipt = await run_in_threadpool(
            server_mod.execute_daemon_command,
            life_dir,
            operation="replace",
            args={
                "victim_sid": body.victim_sid,
                "resume_continuous": body.resume_continuous,
            },
            command_id=body.command_id or None,
            expected_revision=body.expected_revision,
            issuer="webapi",
            handler=lambda: ctx.not_found_if_none(
                server_mod.replace_project_daemon(
                    sid,
                    body.victim_sid,
                    global_root=project_root,
                    resume_continuous=body.resume_continuous,
                ),
                sid,
            ),
        )
        return server_mod._command_response(receipt)

    @app.post("/api/projects/{sid}/daemon/upgrade", dependencies=[Depends(ctx.require_auth)])
    async def _daemon_upgrade(
        sid: str,
        body: CommandIn | None = None,
    ) -> dict[str, Any]:
        command = body or CommandIn()
        life_dir = ctx.resolve_or_404(sid)
        project_root = ctx.project_root_or_404(sid)
        receipt = await run_in_threadpool(
            server_mod.execute_daemon_command,
            life_dir,
            operation="upgrade",
            args={},
            command_id=command.command_id or None,
            expected_revision=command.expected_revision,
            issuer="webapi",
            handler=lambda: ctx.not_found_if_none(
                server_mod.upgrade_project_daemon(sid, global_root=project_root),
                sid,
            ),
        )
        return server_mod._command_response(receipt)

    @app.post(
        "/api/projects/{sid}/daemon/upgrade-schedule",
        dependencies=[Depends(ctx.require_auth)],
    )
    async def _daemon_upgrade_schedule(
        sid: str,
        body: CommandIn | None = None,
    ) -> dict[str, Any]:
        command = body or CommandIn()
        life_dir = ctx.resolve_or_404(sid)
        project_root = ctx.project_root_or_404(sid)
        receipt = await run_in_threadpool(
            server_mod.execute_daemon_command,
            life_dir,
            operation="upgrade",
            args={"scheduled": True},
            command_id=command.command_id or None,
            expected_revision=command.expected_revision,
            issuer="webapi",
            handler=lambda: ctx.not_found_if_none(
                server_mod.schedule_project_daemon_upgrade(sid, global_root=project_root),
                sid,
            ),
        )
        return server_mod._command_response(receipt)

    @app.post("/api/projects/{sid}/continuous", dependencies=[Depends(ctx.require_auth)])
    async def _post_continuous(sid: str, body: ContinuousIn) -> dict[str, Any]:
        project_root = ctx.project_root_or_404(sid)
        try:
            ctx.not_found_if_none(
                await run_in_threadpool(
                    server_mod.set_continuous,
                    sid,
                    enabled=body.enabled,
                    objective=body.objective,
                    global_root=project_root,
                ),
                sid,
            )
        except server_mod.ManagerHandoffSupersededError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except server_mod.ManagerHandoffError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        response: dict[str, Any] = {"ok": True}
        if body.enabled:
            response["daemon"] = await run_in_threadpool(
                server_mod.start_project_daemon,
                sid,
                global_root=project_root,
                resume_continuous=True,
            )
        return response
