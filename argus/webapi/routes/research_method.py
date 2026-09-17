"""Read-only view of a research project's method card for human readers.

The hand-written METHOD.md lives at the project root and is written once by
the agent. Everything volatile next to it (per-component test status, reused
code, hyperparameters, change log) is derived by the research vertical at zero
model cost; this route only resolves the workspace safely and hands the
derivation result to the Atlas web UI. Nothing here gates a stage or admits a
task.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException

from .context import ServerContext

METHOD_FILENAME = "METHOD.md"
ERROR_DETAIL_LIMIT = 200


def register_research_method_routes(app, ctx: ServerContext) -> None:
    def method_workspace(sid: str) -> tuple[Path, Path]:
        """Resolved workspace root and the METHOD.md path inside it (409 when it escapes)."""
        from ..artifacts import project_workspace

        root = project_workspace(sid, global_root=ctx.project_root_or_404(sid))
        if root is None:
            raise HTTPException(status_code=409, detail="Project has no bound workspace")
        root = root.resolve()
        path = root / METHOD_FILENAME
        try:
            resolved = path.resolve()
        except OSError as exc:
            raise HTTPException(
                status_code=409, detail=f"Cannot resolve {METHOD_FILENAME}: {exc}"
            ) from exc
        if not resolved.is_relative_to(root):
            raise HTTPException(
                status_code=409, detail=f"{METHOD_FILENAME} path leaves the project workspace"
            )
        return root, path

    @app.get("/api/projects/{sid}/research/method", dependencies=[Depends(ctx.require_auth)])
    def _method(sid: str) -> dict[str, Any]:
        root, path = method_workspace(sid)
        if not path.is_file():
            return {"exists": False}
        try:
            from ...verticals.research_bridge import derive_method_card

            payload = derive_method_card(root)
        except Exception as exc:  # a broken derivation must not turn into a 500
            detail = f"{type(exc).__name__}: {exc}".strip(": ")
            return {"exists": False, "error": detail[:ERROR_DETAIL_LIMIT]}
        if not isinstance(payload, dict):
            return {"exists": False, "error": "derive_method_card returned a non-object"}
        return payload
