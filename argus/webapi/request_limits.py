"""Bound request bytes before JSON parsing or multipart temporary-file writes."""
from __future__ import annotations

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse

MESSAGE_MAX_CHARS = 32_000
JSON_BODY_MAX_BYTES = 256 * 1024
UPLOAD_BODY_MAX_BYTES = 26 * 1024 * 1024  # 25 MiB files plus multipart headers
TOO_LARGE = "内容过长，请缩短消息或拆分附件后重试。 / Request too large; shorten the message or split attachments."


class RequestSizeLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        multipart = headers.get("content-type", "").lower().startswith("multipart/form-data")
        limit = UPLOAD_BODY_MAX_BYTES if multipart else JSON_BODY_MAX_BYTES
        try:
            length = int(headers.get("content-length", "0"))
            if length < 0:
                raise ValueError
        except ValueError:
            return await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
        if length > limit:
            return await JSONResponse({"detail": TOO_LARGE}, status_code=413)(scope, receive, send)
        consumed = 0

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    if multipart:
                        # Starlette closes every spooled upload on this error.
                        raise MultiPartException(TOO_LARGE)
                    raise HTTPException(413, TOO_LARGE)
            return message

        return await self.app(scope, limited_receive, send)
