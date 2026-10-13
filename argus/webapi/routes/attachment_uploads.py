"""Authenticated small-request upload protocol for the existing attachments."""
from __future__ import annotations

import asyncio
import errno
import logging
from contextlib import suppress

from anyio import from_thread
from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..attachment_uploads import CHUNK_BYTES, JSON_TOKEN_BYTES, AttachmentUploads, UploadError
from ..attachments import attachment_limits


class UploadIn(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    mime: str = Field(default="", max_length=128)
    size_bytes: int = Field(ge=0)


def register_attachment_upload_routes(app, ctx):
    uploads = AttachmentUploads(ctx.roots)
    app.state.attachment_uploads = uploads
    logger = logging.getLogger(__name__)

    async def cleanup_loop():
        while True:
            try:
                await run_in_threadpool(uploads.cleanup)
            except (OSError, ValueError):
                logger.exception("Temporary attachment cleanup failed")
            await asyncio.sleep(60 * 60)

    @app.on_event("startup")
    async def start_cleanup():
        app.state.attachment_cleanup = asyncio.create_task(cleanup_loop())

    @app.on_event("shutdown")
    async def stop_cleanup():
        app.state.attachment_cleanup.cancel()
        with suppress(asyncio.CancelledError):
            await app.state.attachment_cleanup

    async def call(function, *args, **kwargs):
        try:
            return await run_in_threadpool(function, *args, **kwargs)
        except UploadError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "upload not found for this project") from exc
        except TimeoutError as exc:
            raise HTTPException(429, "upload storage is busy; retry shortly") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            logger.exception("Attachment storage operation failed")
            status = 507 if exc.errno in {errno.ENOSPC, errno.EDQUOT} else 500
            raise HTTPException(status, "attachment storage failed; upload has not been committed") from exc

    @app.get("/api/projects/{sid}/attachments/limits", dependencies=[Depends(ctx.require_auth)])
    def limits(sid: str):
        ctx.project_root_or_404(sid)
        return {**attachment_limits(), "chunk_bytes": CHUNK_BYTES,
                "max_json_token_bytes": JSON_TOKEN_BYTES, "max_json_depth": 128}

    @app.post("/api/projects/{sid}/attachments/uploads", dependencies=[Depends(ctx.require_auth)])
    async def begin(sid: str, body: UploadIn):
        return await call(uploads.begin, ctx.project_root_or_404(sid), sid, body.name, body.mime, body.size_bytes)

    @app.get("/api/projects/{sid}/attachments/uploads/{attachment_id}", dependencies=[Depends(ctx.require_auth)])
    async def status(sid: str, attachment_id: str):
        return await call(uploads.operate, ctx.project_root_or_404(sid), sid, attachment_id, "status")

    @app.put("/api/projects/{sid}/attachments/uploads/{attachment_id}", dependencies=[Depends(ctx.require_auth)])
    async def append(sid: str, attachment_id: str, request: Request, offset: int = Query(ge=0)):
        root = ctx.project_root_or_404(sid)
        if not uploads.slots.acquire(blocking=False):
            raise HTTPException(429, "two upload chunks are already in flight; retry shortly")
        try:
            if request.headers.get("content-type", "").split(";", 1)[0] != "application/octet-stream":
                raise HTTPException(415, "upload chunks require application/octet-stream")
            # The middleware bounds each request before this allocation.
            content = await request.body()
            return await call(uploads.operate, root, sid, attachment_id, "append", offset=offset, content=content)
        finally:
            uploads.slots.release()

    @app.post("/api/projects/{sid}/attachments/uploads/{attachment_id}/complete", dependencies=[Depends(ctx.require_auth)])
    async def complete(sid: str, attachment_id: str, request: Request):
        return await call(uploads.operate, ctx.project_root_or_404(sid), sid, attachment_id, "complete",
                          cancelled=lambda: from_thread.run(request.is_disconnected))

    @app.delete("/api/projects/{sid}/attachments/uploads/{attachment_id}", dependencies=[Depends(ctx.require_auth)])
    async def cancel(sid: str, attachment_id: str):
        return await call(uploads.operate, ctx.project_root_or_404(sid), sid, attachment_id, "cancel")
