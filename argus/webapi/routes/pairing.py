"""Short-lived, one-use links that bootstrap the existing browser pairing."""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, Response
from starlette.responses import HTMLResponse, RedirectResponse

from .context import ServerContext

_TTL_SECONDS = 3600
_MAX_LINKS = 32
_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
}


def register_pairing_routes(app, ctx: ServerContext) -> None:
    # Only hashes are retained, bounded in count/time and invalidated on restart.
    pending: dict[str, float] = {}
    lock = threading.Lock()

    def valid(code: str, *, consume: bool = False) -> bool:
        digest = hashlib.sha256(code.encode()).hexdigest()
        with lock:
            expiry = pending.get(digest, 0)
            if consume or expiry <= time.monotonic():
                pending.pop(digest, None)
            return expiry > time.monotonic()

    @app.post("/api/pairing-links", dependencies=[Depends(ctx.require_auth)])
    def issue_link(response: Response) -> dict[str, object]:
        if not ctx.token:
            raise HTTPException(409, "This server does not require browser pairing")
        now = time.monotonic()
        with lock:
            for digest, expiry in list(pending.items()):
                if expiry <= now:
                    del pending[digest]
            if len(pending) >= _MAX_LINKS:
                raise HTTPException(429, "Too many unexpired pairing links")
            code = secrets.token_urlsafe(24)
            pending[hashlib.sha256(code.encode()).hexdigest()] = now + _TTL_SECONDS
        response.headers.update(_HEADERS)
        return {"path": f"/pair/{code}", "expires_in": _TTL_SECONDS}

    @app.get("/pair/{code}", response_class=HTMLResponse)
    def pairing_page(code: str, request: Request) -> HTMLResponse:
        # GET never consumes a link: chat previews and browser prefetches are safe.
        available = valid(code)
        chinese = request.headers.get("accept-language", "zh").lower().startswith("zh")
        title = "连接 Argus" if chinese else "Connect to Argus"
        detail = ("点击后，这个浏览器会记住配对。" if chinese else "This browser will remember the pairing.") if available else (
            "链接已使用或过期。已配对的浏览器可直接打开首页；否则请领取新的配对链接。" if chinese else
            "This link was used or expired. Paired browsers can open the home page; otherwise request a new pairing link."
        )
        action = (f'<form method="post" action="/pair/{quote(code, safe="")}"><button>'
                  + ("连接并进入" if chinese else "Connect and open") + "</button></form>") if available else '<a href="/">Argus</a>'
        return HTMLResponse(
            '<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title}</title><style>body{{font:16px system-ui;margin:12vh auto;padding:24px;max-width:420px;line-height:1.6}}'
            'button{font:inherit;padding:10px 20px;cursor:pointer}p{color:#555}</style>'
            f'<h1>{title}</h1><p>{detail}</p>{action}</html>',
            status_code=200 if available else 410, headers=_HEADERS,
        )

    @app.post("/pair/{code}")
    def redeem_link(code: str) -> RedirectResponse:
        if not valid(code, consume=True):
            raise HTTPException(410, "Pairing link was used or expired", headers=_HEADERS)
        # Reuse native token adoption/storage and immediate URL scrubbing.
        return RedirectResponse(f"/?token={quote(str(ctx.token), safe='')}", status_code=303, headers=_HEADERS)
