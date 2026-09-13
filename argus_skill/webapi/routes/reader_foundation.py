"""Explicit question-foundation creation; later reads use the artifacts API."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from .. import reader_foundation
from .model_stream import model_stream_response


class ReaderFoundationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    question: str = Field(min_length=1, max_length=12000)
    locale: Literal["zh-CN", "en-US"] = "zh-CN"
    source_task_id: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("question")
    @classmethod
    def nonempty_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be empty")
        return value


def _foundation_error(exc: Exception) -> HTTPException:
    if isinstance(exc, reader_foundation.FoundationConflict):
        return HTTPException(409, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(422, "question foundation request or content is invalid")
    return HTTPException(503, "question foundation is temporarily unavailable")


def register_reader_foundation_routes(app, ctx) -> None:
    @app.post("/api/projects/{sid}/reader-foundation", dependencies=[Depends(ctx.require_auth)])
    async def create_foundation(
        sid: str, body: ReaderFoundationIn, response: Response, stream: bool = False,
    ):
        root = ctx.project_root_or_404(sid)

        def generate(on_progress=None):
            try:
                # Register only after this actual worker has started. A request
                # cancelled before scheduling cannot leave a live-owner record
                # for generation that was never dispatched.
                record, created = reader_foundation.reserve_foundation(
                    root, sid, **body.model_dump(mode="json"),
                )
                if not created:
                    return reader_foundation.foundation_artifact(record)
                return reader_foundation.generate_foundation(
                    root, sid, record, on_progress=on_progress,
                )
            except (ValueError, OSError, TimeoutError, RuntimeError) as exc:
                raise _foundation_error(exc) from exc

        if stream:
            return model_stream_response(
                generate, thread_name="reader-foundation-stream",
                unavailable_message="question foundation is temporarily unavailable",
            )
        response.headers["Cache-Control"] = "private, no-store"
        return await run_in_threadpool(generate)
