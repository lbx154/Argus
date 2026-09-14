"""The Vertical Store over HTTP: list, refresh, manage, and poll operations.

Contract (``verticals.store.v1``):

* ``GET  /api/verticals`` -> ``{"verticals": [rows], "catalog": {...}, "host": {...}}``
* ``POST /api/verticals/catalog/refresh`` -> the same payload after a forced fetch
* ``POST /api/verticals/{name}/manage/{action}`` with ``action`` in
  ``install|update|enable|disable|uninstall`` and an optional JSON body
  ``{"force": bool}`` -> 202 ``{"name", "action", "operation"}`` for the job
  actions, 200 for enable/disable; a store refusal is 409 ``{"detail"}``,
  an unknown name or action 404
* ``GET  /api/verticals/{name}/operation`` -> the operation record or 404

Every write is same-origin only, like plugin management. On a hosted trial
the store is host-managed: install/update/uninstall answer 403 there.
"""

from __future__ import annotations

import logging
import os
import threading
from urllib.parse import urlparse

from fastapi import Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ...verticals import store


def _same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "Cross-origin vertical management refused")


def register_vertical_routes(app, ctx):
    @app.on_event("startup")
    def _prepare_declared_verticals():
        """Install what the deployment declares, without holding up the interface."""
        names = store.preinstalled_names()
        if not names:
            return
        logger = logging.getLogger("uvicorn.error")
        logger.info("Preparing the verticals this deployment declares: %s", ", ".join(names))
        threading.Thread(
            target=store.preinstall,
            args=(ctx.global_root,),
            kwargs={"logger": logger, "wait": True},
            daemon=True,
            name="vertical-preinstall",
        ).start()

    @app.get("/api/verticals", dependencies=[Depends(ctx.require_auth)])
    def list_verticals():
        return store.overview(ctx.global_root)

    @app.post("/api/verticals/catalog/refresh", dependencies=[Depends(ctx.require_auth)])
    def refresh_catalog(request: Request):
        _same_origin(request)
        return store.overview(ctx.global_root, refresh=True)

    @app.post("/api/verticals/{name}/manage/{action}", dependencies=[Depends(ctx.require_auth)])
    def manage(name: str, action: str, request: Request, payload: dict = Body(default_factory=dict)):
        if action not in store.ACTIONS:
            raise HTTPException(404, f"unknown vertical action: {action}")
        if action in store.JOB_ACTIONS and os.environ.get("ARGUS_TRIAL_HARNESS"):
            raise HTTPException(403, "Vertical installation is managed by the service")
        _same_origin(request)
        if set(payload) - {"force"} or type(payload.get("force", False)) is not bool:
            raise HTTPException(409, "Unknown vertical management field")
        force = bool(payload.get("force", False))
        try:
            if action == "install":
                operation = store.install(name, ctx.global_root)
            elif action == "update":
                operation = store.update(name, ctx.global_root)
            elif action == "uninstall":
                operation = store.uninstall(name, ctx.global_root, force=force)
            else:
                store.set_enabled(name, action == "enable", ctx.global_root)
                return {"name": name.strip().lower(), "action": action, "operation": None}
        except store.UnknownVerticalError as exc:
            raise HTTPException(404, str(exc)) from exc
        except store.VerticalStoreError as exc:
            raise HTTPException(409, str(exc)) from exc
        return JSONResponse(
            {"name": name.strip().lower(), "action": action, "operation": operation}, status_code=202
        )

    @app.get("/api/verticals/{name}/operation", dependencies=[Depends(ctx.require_auth)])
    def read_operation(name: str):
        operation = store.operation(name, ctx.global_root)
        if operation is None:
            raise HTTPException(404, f"no operation recorded for vertical {name}")
        return operation
