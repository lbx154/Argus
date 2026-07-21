"""config/diagnostics API domain: ``/api/meta``, metrics, per-project config,
identity, doctor, and the operator config/budget/identity/reset/skills
command endpoints.

Registered by :func:`argus_skill.webapi.server.create_app`. Every handler
below is a straight extraction of the corresponding nested function that used
to live inside ``create_app`` — bodies are unchanged; only the enclosing
scope moved from a closure over ``create_app`` locals to parameters passed
in explicitly (``ctx`` for shared auth/root helpers, ``server_mod`` for the
module-level functions ``create_app`` re-exports/defines).
"""

from __future__ import annotations

import shlex
from typing import Any

from fastapi import Depends, Header, HTTPException, Response

from .context import ServerContext
from .models import BudgetSetIn, ConfigSetIn, IdentitySetIn, SkillsIn


def register_meta_routes(app, ctx: ServerContext, server_mod) -> None:
    token = ctx.token
    api_meta = ctx.api_meta

    @app.get("/api/meta")
    def _meta(
        response: Response,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        expected = "Bearer " + str(token)
        if not token or authorization == expected:
            return api_meta
        runtime = {
            **api_meta["runtime"],
            "source_root": "<redacted>",
            "configured_source_root": None,
            "source_root_matches_config": None,
            "executable": "<redacted>",
        }
        return {**api_meta, "runtime": runtime}

    @app.get("/api/metrics", dependencies=[Depends(ctx.require_auth)])
    def _metrics() -> dict[str, Any]:
        return server_mod.metrics_snapshot(root=server_mod._global_root(ctx.global_root))

    @app.get("/metrics", dependencies=[Depends(ctx.require_auth)])
    def _prometheus_metrics() -> Response:
        snapshot = server_mod.metrics_snapshot(root=server_mod._global_root(ctx.global_root))
        return Response(
            server_mod.render_prometheus(snapshot),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get(
        "/api/projects/{sid}/config",
        dependencies=[Depends(ctx.require_auth)],
    )
    def _config(sid: str) -> dict[str, Any]:
        return server_mod.get_config(
            project_state_dir=ctx.resolve_or_404(sid),
            global_root=ctx.project_root_or_404(sid),
        )

    @app.get(
        "/api/projects/{sid}/identity",
        dependencies=[Depends(ctx.require_auth)],
    )
    def _identity(sid: str) -> dict[str, Any]:
        return {
            "identity": ctx.not_found_if_none(
                server_mod.get_identity(sid, global_root=ctx.project_root_or_404(sid)), sid
            )
        }

    @app.get("/api/projects/{sid}/doctor")
    def _doctor(sid: str) -> dict[str, Any]:
        return ctx.not_found_if_none(
            server_mod.get_doctor(sid, global_root=ctx.project_root_or_404(sid)), sid
        )

    @app.post("/api/projects/{sid}/config/set", dependencies=[Depends(ctx.require_auth)])
    def _config_set(sid: str, body: ConfigSetIn) -> dict[str, Any]:
        project_state_dir = ctx.resolve_or_404(sid)
        root = ctx.project_root_or_404(sid)
        try:
            return server_mod.set_operator_config(
                body.name,
                body.value,
                project_state_dir=project_state_dir,
                global_root=root,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post(
        "/api/projects/{sid}/config/budget",
        dependencies=[Depends(ctx.require_auth)],
    )
    def _budget_set(sid: str, body: BudgetSetIn) -> dict[str, Any]:
        project_state_dir = ctx.resolve_or_404(sid)
        root = ctx.project_root_or_404(sid)
        try:
            return server_mod.set_budget_config(
                body.values,
                project_state_dir=project_state_dir,
                global_root=root,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/projects/{sid}/identity", dependencies=[Depends(ctx.require_auth)])
    def _identity_set(sid: str, body: IdentitySetIn) -> dict[str, Any]:
        ctx.not_found_if_none(
            server_mod.set_identity(
                sid, body.text, global_root=ctx.project_root_or_404(sid)
            ),
            sid,
        )
        return {"ok": True}

    @app.post("/api/projects/{sid}/reset", dependencies=[Depends(ctx.require_auth)])
    def _manager_reset(sid: str) -> dict[str, Any]:
        project_root = ctx.project_root_or_404(sid)
        from ..manager_bridge import reset_manager_context

        return {"ok": reset_manager_context(sid, global_root=project_root)}

    @app.post("/api/projects/{sid}/skills", dependencies=[Depends(ctx.require_auth)])
    def _skills(sid: str, body: SkillsIn) -> dict[str, Any]:
        ctx.project_root_or_404(sid)
        try:
            tokens = shlex.split(body.args)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid skill arguments: {exc}") from exc
        return {"text": server_mod.run_skill_command(tokens)}
