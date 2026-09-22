"""Observable answer learning and retry without repeating the user's task."""
from fastapi import Depends, HTTPException

from ...life.answer_learning import learning_status, resume_learning, retry_learning
from .context import ServerContext


def register_learning_routes(app, ctx: ServerContext) -> None:
    @app.get("/api/projects/{sid}/learning", dependencies=[Depends(ctx.require_auth)])
    def _status(sid: str) -> dict:
        return learning_status(ctx.project_root_or_404(sid), sid)

    @app.post("/api/projects/{sid}/learning/{job_id}/retry", dependencies=[Depends(ctx.require_auth)])
    def _retry(sid: str, job_id: str) -> dict:
        root = ctx.project_root_or_404(sid)
        if not retry_learning(root, sid, job_id):
            raise HTTPException(status_code=409, detail="Only failed learning can be retried")
        return learning_status(root, sid)

    @app.on_event("startup")
    def _resume() -> None:
        for root in ctx.roots:
            if (root / "answer-learning.sqlite3").exists():
                resume_learning(root)
