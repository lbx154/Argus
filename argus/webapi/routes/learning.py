"""Observable learning after a mission."""
from fastapi import Depends

from ...life.reflection_status import learning_status
from .context import ServerContext


def register_learning_routes(app, ctx: ServerContext) -> None:
    @app.get("/api/projects/{sid}/learning", dependencies=[Depends(ctx.require_auth)])
    def _status(sid: str) -> dict:
        return learning_status(ctx.project_root_or_404(sid), sid)
