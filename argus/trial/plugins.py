"""Hosted workbench defaults and the boundary for opening tenant data folders."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

SETUP_ACTIONS = {"health", "repair", "shelx"}


def configure_plugins(root: Path) -> None:
    workspace = root / "crystalpilot-runtime"
    (workspace / "test-projects").mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ["ARGUS_CRYSTALPILOT_WORKSPACE"] = str(workspace.resolve())
    os.environ.setdefault("ARGUS_PLUGINS_PREINSTALL", "crystalpilot")


async def workspace_boundary(request: Request, call_next):
    route = request.url.path.removeprefix("/api/plugins/crystalpilot")
    path = None
    if request.method in {"GET", "HEAD"} and route == "/folders":
        path = request.query_params.get("path")
    elif request.method == "POST" and route == "/projects/open":
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"detail": "Invalid project request"}, status_code=400)
        path = data.get("path") if isinstance(data, dict) else None
        if not path:
            return JSONResponse({"detail": "Project path required"}, status_code=400)
    elif request.method == "POST" and route == "/projects/import-structure":
        # Cache the upload body so the mounted app can still parse the form.
        await request.body()
        async with request.form() as form:
            path = form.get("project")
        if not path:
            return JSONResponse({"detail": "Project path required"}, status_code=400)
    if path is not None:
        root = Path(os.environ["ARGUS_CRYSTALPILOT_WORKSPACE"]).resolve()
        if not isinstance(path, str):
            return JSONResponse({"detail": "Invalid project path"}, status_code=400)
        try:
            target = Path(path).expanduser().resolve()
        except (ValueError, OSError, RuntimeError):
            return JSONResponse({"detail": "Invalid project path"}, status_code=400)
        if target != root and root not in target.parents:
            return JSONResponse(
                {"detail": "Open a data folder inside this invitation's CrystalPilot workspace"},
                status_code=403,
            )
    return await call_next(request)
