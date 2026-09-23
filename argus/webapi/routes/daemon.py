"""HTTP daemon creation, start/stop, replacement, upgrades, and continuous mode."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from ...core.workspace_lease import canonical_workdir
from ...daemon import commands as daemon_commands
from ...life.memory import LifeMemory
from ...manager import front_door as manager_front_door
from .. import daemon_lifecycle, daemon_upgrade, project_state
from .context import ServerContext
from .models import CommandIn, ContinuousIn, CreateDaemonIn, ReplaceDaemonIn, StopIn


async def _execute_command(
    path: Path, command: CommandIn, *, operation: str, args: dict[str, Any],
    handler: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    receipt = await run_in_threadpool(
        daemon_commands.execute_daemon_command, path,
        operation=operation, args=args, handler=handler,
        command_id=command.command_id or None,
        expected_revision=command.expected_revision, issuer="webapi",
    )
    result = dict(receipt.result)
    if receipt.status in {"failed", "rejected"}:
        result.setdefault("rc", 3)
        result.setdefault("error", receipt.error)
    result.update(
        {
            "command_id": receipt.command_id,
            "command_status": receipt.status,
            "command_revision": receipt.revision,
            "command": receipt.to_jsonable(),
        }
    )
    return result


def _resume_provider_fences_after_start(life_dir, result, *, enabled=True):
    # Only an authenticated, explicit start/continue command reaches this
    # callback. Automatic supervision/restarts never clear this boundary.
    # Account attention and unknown-cost acknowledgements are separate gates.
    if enabled and type(result.get("rc")) is int and result["rc"] == 0:
        LifeMemory.open(life_dir).backlog.resume_paused_statuses({"paused_provider_fence"})
    return result


def register_daemon_routes(app, ctx: ServerContext) -> None:
    @app.post("/api/daemons", dependencies=[Depends(ctx.require_auth)])
    async def _create_daemon(body: CreateDaemonIn) -> dict[str, Any]:
        """Create a brand-new daemon (session). The objective is OPTIONAL — with
        none, the daemon is idle and the user just talks to the Manager (which
        writes its own objectives). Threadpool: fs writes + optional fork."""
        root = project_state.resolve_global_root(ctx.global_root)
        resolved_paths: dict[str, str] = {}
        for label, value in (
            ("workdir", body.workdir),
            ("launch cwd", body.launch_cwd),
        ):
            if not value:
                resolved_paths[label] = ""
                continue
            try:
                resolved_paths[label] = str(canonical_workdir(value))
            except (OSError, RuntimeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"{label} is unavailable: {value}",
                ) from exc
        return await _execute_command(
            root, body,
            operation="create",
            args={
                "objective": body.objective,
                "name": body.name,
                "launch_cwd": resolved_paths["launch cwd"],
                "workdir": resolved_paths["workdir"],
            },
            handler=lambda: daemon_lifecycle.create_daemon(
                body.objective,
                name=body.name,
                launch_cwd=resolved_paths["launch cwd"],
                workdir=resolved_paths["workdir"],
                global_root=ctx.global_root,
            ),
        )

    @app.post("/api/projects/{sid}/daemon/start", dependencies=[Depends(ctx.require_auth)])
    async def _daemon_start(
        sid: str,
        body: CommandIn | None = None,
    ) -> dict[str, Any]:
        command = body or CommandIn()
        life_dir = ctx.resolve_or_404(sid)
        project_root = ctx.project_root_or_404(sid)

        def start_and_resume() -> dict[str, Any]:
            result = ctx.not_found_if_none(
                ctx.daemon_services.start(
                    sid,
                    global_root=project_root,
                    resume_continuous=True,
                ),
                sid,
            )
            return _resume_provider_fences_after_start(life_dir, result)

        return await _execute_command(
            life_dir, command,
            operation="start",
            args={"resume_continuous": True},
            handler=start_and_resume,
        )

    @app.post("/api/projects/{sid}/daemon/stop", dependencies=[Depends(ctx.require_auth)])
    async def _daemon_stop(sid: str, body: StopIn | None = None) -> dict[str, Any]:
        b = body or StopIn()
        life_dir = ctx.resolve_or_404(sid)
        project_root = ctx.project_root_or_404(sid)
        operation = "kill" if b.force else "drain" if b.drain else "stop"
        return await _execute_command(
            life_dir, b,
            operation=operation,
            args={"drain": b.drain, "force": b.force},
            handler=lambda: ctx.not_found_if_none(
                daemon_lifecycle.stop_project_daemon(
                    sid,
                    drain=b.drain,
                    force=b.force,
                    global_root=project_root,
                ),
                sid,
            ),
        )

    @app.post("/api/projects/{sid}/daemon/replace", dependencies=[Depends(ctx.require_auth)])
    async def _daemon_replace(sid: str, body: ReplaceDaemonIn) -> dict[str, Any]:
        life_dir = ctx.resolve_or_404(sid)
        project_root = ctx.project_root_or_404(sid)

        def replace_and_resume() -> dict[str, Any]:
            result = ctx.not_found_if_none(
                daemon_lifecycle.replace_project_daemon(
                    sid,
                    body.victim_sid,
                    global_root=project_root,
                    resume_continuous=body.resume_continuous,
                ),
                sid,
            )
            return _resume_provider_fences_after_start(
                life_dir, result, enabled=body.resume_continuous,
            )

        return await _execute_command(
            life_dir, body,
            operation="replace",
            args={
                "victim_sid": body.victim_sid,
                "resume_continuous": body.resume_continuous,
            },
            handler=replace_and_resume,
        )

    @app.post("/api/projects/{sid}/daemon/upgrade", dependencies=[Depends(ctx.require_auth)])
    async def _daemon_upgrade(
        sid: str,
        body: CommandIn | None = None,
    ) -> dict[str, Any]:
        command = body or CommandIn()
        life_dir = ctx.resolve_or_404(sid)
        project_root = ctx.project_root_or_404(sid)
        return await _execute_command(
            life_dir, command,
            operation="upgrade",
            args={},
            handler=lambda: ctx.not_found_if_none(
                daemon_upgrade.upgrade_project_daemon(sid, global_root=project_root),
                sid,
            ),
        )

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
        return await _execute_command(
            life_dir, command,
            operation="upgrade",
            args={"scheduled": True},
            handler=lambda: ctx.not_found_if_none(
                daemon_upgrade.schedule_project_daemon_upgrade(sid, global_root=project_root),
                sid,
            ),
        )

    @app.post("/api/projects/{sid}/continuous", dependencies=[Depends(ctx.require_auth)])
    async def _post_continuous(sid: str, body: ContinuousIn) -> dict[str, Any]:
        project_root = ctx.project_root_or_404(sid)
        from ..project_crud import apply_continuous_update, start_continuous_update

        try:
            receipt = ctx.not_found_if_none(
                await run_in_threadpool(
                    apply_continuous_update,
                    sid,
                    enabled=body.enabled,
                    objective=body.objective,
                    global_root=project_root,
                ),
                sid,
            )
            response: dict[str, Any] = {"ok": True}
            if body.enabled:
                response["daemon"] = await run_in_threadpool(
                    start_continuous_update, sid, receipt,
                    start=ctx.daemon_services.start, global_root=project_root,
                )
            return response
        except manager_front_door.ManagerHandoffSupersededError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except manager_front_door.ManagerHandoffError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
