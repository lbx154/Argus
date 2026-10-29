"""Small invitation-only trial with isolated native runtimes and a USD meter.

Run this operator service outside tenant namespaces. Native Copilot inside a
tenant receives only that tenant's metered credential, never the provider login.
Deployment credentials, database and tenant directories stay outside Git.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import math
import re
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import httpx
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from websockets.legacy.client import unix_connect

COOKIE = "argus_preview_session"
MODEL = "gpt-6.1-sol"
MICRO = 1_000_000
# Twice the authoritative rates observed from Copilot token_details for this
# model: input/cache-write <= $2.50/M, output/reasoning $10/M. Reserve from
# UTF-8 bytes (an upper bound on text tokens), framing and enforced output cap.
INPUT_CEILING = 5.0 / MICRO
OUTPUT_CEILING = 20.0 / MICRO
MAX_BODY = 2_000_000
MAX_OUTPUT = 16384
MIN_ADMIT = 10_000  # $0.01 in micro-dollars


async def bounded_body(request: Request, maximum: int) -> bytes:
    result = bytearray()
    async for chunk in request.stream():
        if len(result) + len(chunk) > maximum:
            raise ValueError("Request body limit")
        result.extend(chunk)
    return bytes(result)


class QuotaExceeded(Exception):
    pass


class Ledger:
    def __init__(self, path: Path, *, total_usd: float = 50, user_usd: float = 10):
        self.path = path
        self.total = round(total_usd * MICRO)
        self.user = round(user_usd * MICRO)
        with closing(sqlite3.connect(path)) as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS charges (
                  id TEXT PRIMARY KEY, tenant TEXT NOT NULL,
                  amount INTEGER NOT NULL CHECK(amount >= 0),
                  state TEXT NOT NULL, created REAL NOT NULL);
            """)
            db.execute("CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY, total INTEGER, per_user INTEGER)")
            db.execute("INSERT OR IGNORE INTO policy VALUES (1,?,?)", (self.total, self.user))
            stored = db.execute("SELECT total,per_user FROM policy WHERE id=1").fetchone()
            if stored != (self.total, self.user):
                raise ValueError("Existing ledger limits cannot be reset by configuration")
            db.commit()
        path.chmod(0o600)

    def recover(self) -> None:
        # Called only by the exclusively locked meter, never the portal.
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE charges SET state='uncertain' WHERE state='reserved'")
            db.commit()

    def reserve(self, tenant: str, usd: float) -> str:
        if not math.isfinite(usd) or usd < 0:
            raise ValueError("Invalid reservation")
        amount = math.ceil(usd * MICRO)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.execute("BEGIN IMMEDIATE")
            total = db.execute("SELECT COALESCE(SUM(amount),0) FROM charges").fetchone()[0]
            used = db.execute("SELECT COALESCE(SUM(amount),0) FROM charges WHERE tenant=?", (tenant,)).fetchone()[0]
            # The reservation is a ceiling far above a typical call (text bytes as
            # tokens at twice the tariff), so a call that does not fit what is
            # left is still admitted for what is left: it settles at its real
            # cost, which can overdraw by at most that one call. Nothing is
            # admitted once less than MIN_ADMIT remains.
            left = min(self.total - total, self.user - used)
            if left < min(amount, MIN_ADMIT):
                db.rollback()
                raise QuotaExceeded("试用额度已用完，任务已暂停，已有文件都保留着。")
            amount = min(amount, left)
            identity = secrets.token_hex(16)
            db.execute("INSERT INTO charges VALUES(?,?,?,?,?)", (identity, tenant, amount, "reserved", time.time()))
            db.commit()
            return identity

    def settle(self, identity: str, usd: float | None) -> None:
        if usd is not None and (not math.isfinite(usd) or usd < 0):
            raise ValueError("Invalid charge")
        with closing(sqlite3.connect(self.path)) as db:
            if usd is None:
                db.execute("UPDATE charges SET state='uncertain' WHERE id=? AND state='reserved'", (identity,))
            else:
                db.execute("UPDATE charges SET amount=?,state='settled' WHERE id=? AND state='reserved'",
                           (math.ceil(usd * MICRO), identity))
            db.commit()

    def status(self, tenant: str) -> dict:
        with closing(sqlite3.connect(self.path)) as db:
            used = db.execute("SELECT COALESCE(SUM(amount),0) FROM charges WHERE tenant=?", (tenant,)).fetchone()[0]
            total = db.execute("SELECT COALESCE(SUM(amount),0) FROM charges").fetchone()[0]
        return {"model": MODEL, "limit_usd": self.user / MICRO, "used_usd": used / MICRO,
                "remaining_usd": max(0, self.user - used) / MICRO,
                "total_limit_usd": self.total / MICRO,
                "total_remaining_usd": max(0, self.total - total) / MICRO, "concurrency": 2}


def provider_cost(event: dict) -> float | None:
    from argus.provider_integrations.copilot_usage import NANO_AIU_PER_USD

    usage = event.get("copilot_usage")
    if not isinstance(usage, dict):
        return None
    value = usage.get("total_nano_aiu")
    return value / NANO_AIU_PER_USD if type(value) is int and value >= 0 else None


def token_cost(event: dict) -> float | None:
    """Price a terminal Responses event from the usage an OpenAI-compatible
    upstream reports; ``None`` keeps the reservation when usage is missing."""
    from argus.core.pricing import quote_token_usage

    usage = (event.get("response") or {}).get("usage")
    if not isinstance(usage, dict):
        return None
    counts = [usage.get("input_tokens"), usage.get("output_tokens")]
    if any(type(value) is not int or value < 0 for value in counts):
        return None
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens")
    quote = quote_token_usage(MODEL, input_tokens=counts[0], output_tokens=counts[1],
                              cached_input_tokens=cached if type(cached) is int else 0)
    return quote.cost_usd if quote.status == "priced" else None


def admit(payload: dict, *, priced_by_usage: bool) -> tuple[str, set[str]]:
    """Make a request billable or say why it cannot be.

    The reservation is bounded by the request's bytes, so content those bytes
    carry is admissible when the upstream bills it in its reported usage:
    inline (data:) images and files. Remote media and hosted tools are billed
    outside that usage and are refused or dropped; item references cannot be
    resolved with ``store=False`` and are dropped. Copilot's receipts cover
    text and function tools only, so without a usage-priced upstream media is
    refused. Returns the refusal reason ("" to proceed) and what was dropped."""
    dropped: set[str] = set()
    tools = payload.get("tools") or []
    kept = [tool for tool in tools if tool.get("type") in ("function", "custom")]
    dropped.update(f"tool:{tool.get('type')}" for tool in tools if tool not in kept)
    if tools:
        payload["tools"] = kept
        choice = payload.get("tool_choice")
        if isinstance(choice, dict) and choice.get("type") not in (None, "function", "custom"):
            payload.pop("tool_choice")

    def walk(value):
        if isinstance(value, list):
            kept_items = []
            for item in value:
                if isinstance(item, dict) and item.get("type") == "item_reference":
                    dropped.add("item_reference")
                    continue
                reason = walk(item)
                if reason:
                    return reason
                kept_items.append(item)
            value[:] = kept_items
            return ""
        if not isinstance(value, dict):
            return ""
        kind = value.get("type")
        if kind in ("input_image", "input_file", "input_audio"):
            if not priced_by_usage:
                return kind
            source = value.get("image_url") or value.get("file_url") or ""
            if isinstance(source, dict):
                source = source.get("url", "")
            if kind == "input_audio" or (source and not str(source).startswith("data:")) or value.get("file_id"):
                return f"{kind} without inline data"
        for child in value.values():
            reason = walk(child)
            if reason:
                return reason
        return ""

    return walk(payload), dropped


def upstream_request(config: dict) -> tuple[str, dict, object]:
    """Where the meter sends a tenant's request, with which headers, and how a
    terminal event is charged. ``upstream`` names an OpenAI-compatible
    Responses endpoint and a file holding its key; without it the operator's
    Copilot login is used and billed from its receipts."""
    upstream = config.get("upstream")
    if upstream:
        key = Path(upstream["token_file"]).read_text().strip()
        return upstream["base_url"].rstrip("/") + "/responses", {"Authorization": "Bearer " + key}, token_cost
    from argus.agent_cli.copilot_home import _read_managed_config
    from argus.core.copilot_http import COPILOT_BASE_URL, COPILOT_HEADERS

    provider = _read_managed_config(Path(config["copilot_home"]) / "config.json")
    account = provider["lastLoggedInUser"]
    token = provider["authTokens"][f"{account['host']}:{account['login']}"]["token"]
    return (COPILOT_BASE_URL + "/responses",
            {**COPILOT_HEADERS, "Authorization": "Bearer " + token, "X-Initiator": "user"}, provider_cost)


def load_config(path: Path) -> dict:
    value = json.loads(path.read_text())
    if value.get("model") != MODEL or not value.get("tenants"):
        raise ValueError("Preview requires gpt-6.1-sol and configured tenants")
    return value


def model_app(config: dict, ledger: Ledger) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    slots = asyncio.Semaphore(config.get("concurrency", 2))
    tokens = {row["model_token"]: name for name, row in config["tenants"].items()}

    def identify(request: Request) -> str | None:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        return next((name for token, name in tokens.items() if secrets.compare_digest(supplied, token)), None)

    @app.get("/healthz")
    async def health():
        return {"status": "ready", "model": MODEL}

    @app.get("/v1/models")
    async def models(request: Request):
        if identify(request) is None:
            return JSONResponse({"error": {"message": "需要试用授权。"}}, 401)
        return {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "argus-preview"}]}

    @app.post("/v1/responses")
    async def responses(request: Request):
        tenant = identify(request)
        if tenant is None:
            return JSONResponse({"error": {"message": "需要试用授权。"}}, 401)
        try:
            raw = await bounded_body(request, MAX_BODY)
        except ValueError:
            return JSONResponse({"error": {"message": "输入内容太长，请缩短后重试。"}}, 413)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("model") != MODEL:
                raise ValueError
            requested = payload.get("max_output_tokens", MAX_OUTPUT)
            if type(requested) is not int or requested <= 0:
                raise ValueError
            output_limit = min(requested, MAX_OUTPUT)
            tools = payload.get("tools", [])
            if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
                raise ValueError
        except (ValueError, TypeError):
            return JSONResponse({"error": {"message": "此试用版仅支持 GPT-6.1 Sol。"}}, 400)
        was_streaming = payload.get("stream", False)
        payload.update(stream=True, max_output_tokens=output_limit, store=False, background=False)
        payload.pop("previous_response_id", None)
        refusal, dropped = admit(payload, priced_by_usage=bool(config.get("upstream")))
        if dropped:
            print(f"meter: {tenant} request without {', '.join(sorted(dropped))}", flush=True)
        if refusal:
            print(f"meter: {tenant} refused: {refusal}", flush=True)
            return JSONResponse({"error": {"message": "试用版支持文字和代码任务，请把附件内容粘贴为文字。"}}, 400)
        await slots.acquire()
        reservation = None
        client = None
        upstream = None
        submitted = False
        handed_off = False
        try:
            reservation = ledger.reserve(tenant, (len(raw) + 4096) * INPUT_CEILING + output_limit * OUTPUT_CEILING)
            url, headers, charge = upstream_request(config)
            client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15), follow_redirects=False, trust_env=False)
            submitted = True
            upstream = await client.send(client.build_request("POST", url,
                                                             headers=headers, json=payload), stream=True)
            if upstream.status_code != 200:
                status = upstream.status_code
                ledger.settle(reservation, 0 if status in (400, 401, 403, 404, 422, 429) else None)
                reservation = None
                await upstream.aclose()
                upstream = None
                return JSONResponse({"error": {"message": "模型服务未能完成请求。", "code": "provider_unavailable"}}, status)
            handed_off = True
        except QuotaExceeded as exc:
            return JSONResponse({"error": {"code": "trial_budget_exhausted", "message": str(exc)}}, 402)
        except Exception:
            return JSONResponse({"error": {"message": "模型连接暂时不可用，请稍后重试。"}}, 502)
        finally:
            if not handed_off:
                try:
                    if reservation:
                        ledger.settle(reservation, None if submitted else 0)
                finally:
                    slots.release()
                    if upstream:
                        await upstream.aclose()
                    if client:
                        await client.aclose()

        async def stream():
            charged = None
            try:
                async for line in upstream.aiter_lines():
                    if line.startswith("data:"):
                        try:
                            event = json.loads(line[5:])
                            if event.get("type") in ("response.completed", "response.incomplete", "response.failed"):
                                charged = charge(event)
                        except ValueError:
                            pass
                    yield (line + "\n").encode()
            finally:
                try:
                    ledger.settle(reservation, charged)
                finally:
                    slots.release()
                    await upstream.aclose()
                    await client.aclose()

        if was_streaming:
            return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})
        final = None
        async for line in stream():
            if line.startswith(b"data:"):
                try:
                    event = json.loads(line[5:])
                    if event.get("type") in ("response.completed", "response.incomplete"):
                        final = event["response"]
                except ValueError:
                    pass
        return JSONResponse(final or {"error": {"message": "模型没有返回完整结果。"}}, 200 if final else 502)

    return app


LOGIN = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Argus 6.1 试用</title><style>body{font:16px system-ui;background:#f6f7f9;color:#172333;margin:0}main{max-width:420px;margin:12vh auto;padding:32px;background:white;border:1px solid #ddd;border-radius:18px}input,button{box-sizing:border-box;width:100%;padding:13px;font:inherit;border-radius:9px;margin:10px 0;border:1px solid #ddd}button{background:#2069d1;color:white;border:0}p{line-height:1.7;color:#536071}</style>
<main><h1>Argus 6.1 试用</h1><p>告诉它需要做什么，它会实际查资料、写代码和验证结果。</p><p>每个邀请码有独立的工作区和 {user} 美元额度。全站总额度为 {total} 美元，用完后暂停；额度不会自动重置。</p>
<form method="post" action="/login"><label>邀请码</label><input name="invite" autocomplete="off" required placeholder="粘贴你的邀请码"><button>进入试用</button></form></main></html>"""


RECOVERING = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="5">
<title>工作区暂时不可用 · Argus</title><style>body{font:16px system-ui;background:#f6f7f9;color:#172333;margin:0}main{max-width:440px;margin:12vh auto;padding:32px;background:white;border-radius:18px}p{line-height:1.7;color:#536071}a{color:#2069d1}</style>
<main><h1>正在重新连接你的工作区</h1><p>你的文件和试用余额已保留。页面每 5 秒重试连接。</p><p>如果一直无法连接，请联系试用版管理员。</p><a href="/">立即重试</a></main></html>"""


def portal_app(config: dict, ledger: Ledger) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    invites = {row["invite"]: name for name, row in config["tenants"].items()}
    login_html = LOGIN.replace("{user}", f"{ledger.user / MICRO:g}").replace("{total}", f"{ledger.total / MICRO:g}")
    # A session is a random token the portal holds, not a signed claim. Only
    # its hash is kept, next to the ledger, so a portal restart (every deploy)
    # does not sign everyone out and the file cannot be replayed as a cookie.
    sessions_path = Path(config.get("sessions") or ledger.path.with_name("sessions.sqlite3"))
    with closing(sqlite3.connect(sessions_path)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, tenant TEXT NOT NULL, expiry REAL NOT NULL)")
        db.commit()
    sessions_path.chmod(0o600)

    def digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def session(tenant: str) -> str:
        now = time.time()
        token = secrets.token_urlsafe(32)
        with closing(sqlite3.connect(sessions_path, timeout=10)) as db:
            db.execute("DELETE FROM sessions WHERE expiry <= ?", (now,))
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (digest(token), tenant, now + 7 * 86400))
            db.commit()
        return token

    def identify(cookie: str) -> str | None:
        if not cookie:
            return None
        with closing(sqlite3.connect(sessions_path, timeout=10)) as db:
            row = db.execute("SELECT tenant, expiry FROM sessions WHERE digest=?", (digest(cookie),)).fetchone()
        tenant, expiry = row or (None, 0.0)
        return tenant if tenant in config["tenants"] and expiry > time.time() else None

    def origin_ok(request: Request) -> bool:
        origin = request.headers.get("origin")
        return not origin or origin == "https://" + request.headers.get("host", "")

    @app.get("/healthz")
    async def health():
        return {"status": "ready", "model": MODEL, "total_limit_usd": ledger.total / MICRO,
                "revision": config["revision"]}

    @app.get("/login")
    async def login_page():
        return HTMLResponse(login_html, headers={"Cache-Control": "no-store"})

    @app.post("/login")
    async def login(request: Request):
        if not origin_ok(request):
            return JSONResponse({"detail": "请从试用页面登录。"}, 403)
        try:
            form = parse_qs((await bounded_body(request, 4096)).decode("utf-8"), max_num_fields=2)
            token = form.get("invite", [""])[0].strip()
        except (ValueError, UnicodeError):
            return JSONResponse({"detail": "邀请码格式不正确。"}, 400)
        tenant = next((name for invite, name in invites.items() if secrets.compare_digest(token, invite)), None)
        if tenant is None:
            return HTMLResponse(login_html.replace("<h1>", "<p>邀请码不正确，请检查后重试。</p><h1>"), 401)
        response = RedirectResponse("/", 303)
        response.set_cookie(COOKIE, session(tenant), secure=True, httponly=True, samesite="lax", max_age=7 * 86400)
        return response

    @app.get("/trial/budget")
    async def budget(request: Request):
        tenant = identify(request.cookies.get(COOKIE, ""))
        if tenant is None:
            return JSONResponse({"detail": "请先登录。"}, 401)
        return ledger.status(tenant)

    @app.get("/trial-quota.js")
    async def quota_script():
        from fastapi.responses import Response

        return Response("""(()=>{async function update(){const r=await fetch('/trial/budget');if(r.status===401){location.assign('/login');return}if(!r.ok)return;const b=await r.json();const host=document.querySelector('.composer-runtime');if(!host)return;let node=document.getElementById('trial-quota');if(!node){node=document.createElement('span');node.id='trial-quota';node.style.cssText='font-size:11px;color:#2069d1';host.append(node)}node.textContent='试用余额 $'+b.remaining_usd.toFixed(2)+' / $'+b.limit_usd.toFixed(2);node.title='全站剩余 $'+b.total_remaining_usd.toFixed(2)+'；模型 '+b.model}setInterval(update,8000);setTimeout(update,1200)})();""", media_type="text/javascript")

    @app.get("/trial-transport.js")
    async def transport_script():
        from fastapi.responses import Response

        return Response((Path(__file__).parent / "trial-transport.js").read_text(), media_type="text/javascript")

    @app.websocket("/_relay")
    @app.websocket("/trial/stream")
    async def relay(socket: WebSocket):
        browser = socket.url.path == "/trial/stream"
        public_host = socket.headers.get("host", "")
        if secrets.compare_digest(socket.headers.get("authorization", ""), "Bearer " + config["relay_secret"]):
            public_host = socket.headers.get("x-argus-public-host", public_host)
        if browser:
            if identify(socket.cookies.get(COOKIE, "")) is None:
                await socket.close(code=4401)
                return
            if socket.headers.get("origin") != "https://" + public_host:
                await socket.close(code=4403)
                return
        elif not secrets.compare_digest(socket.headers.get("authorization", ""), "Bearer " + config["relay_secret"]):
            await socket.close(code=4401)
            return
        await socket.accept()
        frame = await asyncio.wait_for(socket.receive_json(), 20)
        if browser:
            if frame.get("method") != "POST" or not re.fullmatch(r"/api/projects/[a-zA-Z0-9_-]+/message/stream", frame.get("path", "")):
                await socket.close(code=4403)
                return
            frame["host"] = public_host
            frame["headers"] = {"cookie": socket.headers.get("cookie", ""),
                                "origin": socket.headers["origin"], "content-type": "application/json"}
        raw = base64.b64decode(frame.get("body", ""), validate=True)
        if len(raw) > 16 * 1024 * 1024:
            await socket.close(code=4400)
            return
        request_headers = [(k.lower().encode(), v.encode()) for k, v in frame.get("headers", {}).items()
                           if k.lower() not in ("host", "content-length", "connection", "upgrade", "transfer-encoding")]
        request_headers.extend([(b"host", frame["host"].encode()), (b"content-length", str(len(raw)).encode())])
        path, _, query = frame["path"].partition("?")
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": frame["method"],
                 "scheme": "https", "path": path, "raw_path": path.encode(), "query_string": query.encode(),
                 "headers": request_headers, "client": ("127.0.0.1", 0), "server": (frame["host"], 443)}
        received = False

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": raw, "more_body": False}
            while True:
                message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                await socket.send_json({"type": "start", "status": message["status"],
                                        "headers": [[k.decode(), v.decode()] for k, v in message.get("headers", [])]})
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body:
                    await socket.send_bytes(body)
                if not message.get("more_body"):
                    await socket.send_json({"type": "end"})
        try:
            await app(scope, receive, send)
        finally:
            await socket.close()

    @app.websocket("/api/{path:path}")
    async def websocket_proxy(socket: WebSocket, path: str):
        tenant = identify(socket.cookies.get(COOKIE, ""))
        if tenant is None:
            await socket.close(code=4401)
            return
        origin = socket.headers.get("origin")
        public_host = socket.headers.get("host", "")
        if secrets.compare_digest(socket.headers.get("authorization", ""), "Bearer " + config["relay_secret"]):
            public_host = socket.headers.get("x-argus-public-host", public_host)
        expected = "https://" + public_host
        if origin and origin != expected:
            await socket.close(code=4403)
            return
        row = config["tenants"][tenant]
        await socket.accept()
        query = {key: value for key, value in socket.query_params.items() if key != "token"}
        try:
            async with unix_connect(row["socket"], uri="ws://localhost/api/" + path + "?" + urlencode(query), max_size=8 * 1024 * 1024,
                                    extra_headers={"Authorization": "Bearer " + row["web_token"]}) as backend:
                async def outward():
                    async for message in backend:
                        await socket.send_text(message)
                async def inward():
                    while True:
                        await backend.send(await socket.receive_text())
                tasks = [asyncio.create_task(outward()), asyncio.create_task(inward())]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except OSError:
            await socket.close(code=1013, reason="工作区暂时无法连接，请稍后重试")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
    async def proxy(request: Request, path: str):
        tenant = identify(request.cookies.get(COOKIE, ""))
        if tenant is None:
            return RedirectResponse("/login", 303) if not path.startswith("api/") else JSONResponse({"detail": "请先登录试用版。"}, 401)
        if request.method not in ("GET", "HEAD") and not origin_ok(request):
            return JSONResponse({"detail": "请从试用页面发送请求。"}, 403)
        if path.startswith(("api/runtime", "api/system", "api/plugins", "api/pairing-links")) and request.method not in ("GET", "HEAD"):
            return JSONResponse({"detail": "试用环境由管理员维护。"}, 403)
        if request.method not in ("GET", "HEAD") and (path.endswith("/config/set") or path.endswith("/budget")):
            return JSONResponse({"detail": "试用版固定使用 GPT-6.1，额度由管理员设置。"}, 403)
        try:
            body = await bounded_body(request, 16 * 1024 * 1024)
        except ValueError:
            return JSONResponse({"detail": "文件太大，请缩小后重试。"}, 413)
        row = config["tenants"][tenant]
        headers = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "accept", "accept-language", "range")}
        headers["Authorization"] = "Bearer " + row["web_token"]
        client = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=row["socket"]), timeout=httpx.Timeout(600, connect=10))
        try:
            response = await client.send(client.build_request(request.method, "http://localhost/" + path +
                                                             ("?" + str(request.url.query) if request.url.query else ""),
                                                             headers=headers, content=body), stream=True)
        except httpx.HTTPError:
            await client.aclose()
            headers = {"Retry-After": "5", "Cache-Control": "no-store"}
            if path == "":
                return HTMLResponse(RECOVERING, 503, headers=headers)
            return JSONResponse({"detail": "工作区暂时无法连接，文件和余额已保留，请稍后重试。"}, 503, headers=headers)
        response_headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "transfer-encoding", "connection", "content-encoding")}
        if path == "" and response.status_code == 200:
            from fastapi.responses import Response

            html = (await response.aread()).decode().replace("<head>", '<head><script src="/trial-transport.js"></script>').replace("</body>", '<script src="/trial-quota.js" defer></script></body>')
            await client.aclose()
            # Never serve the shell from cache: a signed-out reader must reach /login.
            response_headers["cache-control"] = "no-store"
            return Response(html, media_type="text/html", headers=response_headers)

        async def chunks():
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()
        return StreamingResponse(chunks(), status_code=response.status_code, headers=response_headers)

    return app


def main():
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["portal", "meter"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument("--uds", type=str)
    args = parser.parse_args()
    config = load_config(args.config)
    ledger = Ledger(Path(config["ledger"]), total_usd=config["total_usd"], user_usd=config["user_usd"])
    meter_lock = None
    if args.mode == "meter":
        import fcntl

        meter_lock = open(config["ledger"] + ".lock", "a")
        fcntl.flock(meter_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger.recover()
    app = model_app(config, ledger) if args.mode == "meter" else portal_app(config, ledger)
    uvicorn.run(app, host="127.0.0.1", port=args.port or 18871, uds=args.uds, access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
