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
import hmac
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
            if total + amount > self.total or used + amount > self.user:
                db.rollback()
                raise QuotaExceeded("试用额度不足，任务已暂停。请联系试用版管理员。")
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


def load_config(path: Path) -> dict:
    value = json.loads(path.read_text())
    if value.get("model") != MODEL or not value.get("tenants"):
        raise ValueError("Preview requires gpt-6.1-sol and configured tenants")
    return value


def model_app(config: dict, ledger: Ledger) -> FastAPI:
    from argus.agent_cli.copilot_home import _read_managed_config
    from argus.core.copilot_http import COPILOT_BASE_URL, COPILOT_HEADERS

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    slots = asyncio.Semaphore(2)
    tokens = {row["model_token"]: name for name, row in config["tenants"].items()}

    def identify(request: Request) -> str | None:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        return next((name for token, name in tokens.items() if hmac.compare_digest(supplied, token)), None)

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
        # The byte-based reservation covers text/function tools only. Hosted
        # tools and remote media have different billing and cannot bypass it.
        def supported(value):
            if isinstance(value, dict):
                if value.get("type") in ("input_image", "input_file", "input_audio", "item_reference"):
                    return False
                return all(supported(child) for child in value.values())
            return not isinstance(value, list) or all(supported(child) for child in value)
        if not supported(payload) or any(tool.get("type") not in ("function", "custom") for tool in payload.get("tools", [])):
            return JSONResponse({"error": {"message": "试用版支持文字和代码任务，请把附件内容粘贴为文字。"}}, 400)
        await slots.acquire()
        reservation = None
        client = None
        upstream = None
        submitted = False
        handed_off = False
        try:
            reservation = ledger.reserve(tenant, (len(raw) + 4096) * INPUT_CEILING + output_limit * OUTPUT_CEILING)
            provider = _read_managed_config(Path(config["copilot_home"]) / "config.json")
            account = provider["lastLoggedInUser"]
            token = provider["authTokens"][f"{account['host']}:{account['login']}"]["token"]
            headers = {**COPILOT_HEADERS, "Authorization": "Bearer " + token, "X-Initiator": "user"}
            client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15), follow_redirects=False, trust_env=False)
            submitted = True
            upstream = await client.send(client.build_request("POST", COPILOT_BASE_URL + "/responses",
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
                                charged = provider_cost(event)
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
<main><h1>Argus 6.1 试用</h1><p>告诉它需要做什么，它会实际查资料、写代码和验证结果。</p><p>每个邀请码有独立的工作区和 10 美元额度。全站总额度为 50 美元，用完后暂停；额度不会自动重置。</p>
<form method="post" action="/login"><label>邀请码</label><input name="invite" autocomplete="off" required placeholder="粘贴你的邀请码"><button>进入试用</button></form></main></html>"""


def portal_app(config: dict, ledger: Ledger) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    sessions = config["session_secret"].encode()
    invites = {row["invite"]: name for name, row in config["tenants"].items()}

    def session(tenant: str) -> str:
        value = f"{tenant}:{int(time.time()) + 7 * 86400}"
        return value + ":" + hmac.new(sessions, value.encode(), hashlib.sha256).hexdigest()

    def identify(cookie: str) -> str | None:
        parts = cookie.split(":")
        if len(parts) != 3 or parts[0] not in config["tenants"]:
            return None
        value = ":".join(parts[:2])
        if not hmac.compare_digest(parts[2], hmac.new(sessions, value.encode(), hashlib.sha256).hexdigest()):
            return None
        try:
            return parts[0] if int(parts[1]) > time.time() else None
        except ValueError:
            return None

    def origin_ok(request: Request) -> bool:
        origin = request.headers.get("origin")
        return not origin or origin == "https://" + request.headers.get("host", "")

    @app.get("/healthz")
    async def health():
        return {"status": "ready", "model": MODEL, "total_limit_usd": 50,
                "revision": config["revision"]}

    @app.get("/login")
    async def login_page():
        return HTMLResponse(LOGIN, headers={"Cache-Control": "no-store"})

    @app.post("/login")
    async def login(request: Request):
        if not origin_ok(request):
            return JSONResponse({"detail": "请从试用页面登录。"}, 403)
        try:
            form = parse_qs((await bounded_body(request, 4096)).decode("utf-8"), max_num_fields=2)
            token = form.get("invite", [""])[0].strip()
        except (ValueError, UnicodeError):
            return JSONResponse({"detail": "邀请码格式不正确。"}, 400)
        tenant = next((name for invite, name in invites.items() if hmac.compare_digest(token, invite)), None)
        if tenant is None:
            return HTMLResponse(LOGIN.replace("<h1>", "<p>邀请码不正确，请检查后重试。</p><h1>"), 401)
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

        return Response("""(()=>{async function update(){const r=await fetch('/trial/budget');if(!r.ok)return;const b=await r.json();const host=document.querySelector('.composer-runtime');if(!host)return;let node=document.getElementById('trial-quota');if(!node){node=document.createElement('span');node.id='trial-quota';node.style.cssText='font-size:11px;color:#2069d1';host.append(node)}node.textContent='试用余额 $'+b.remaining_usd.toFixed(2)+' / $'+b.limit_usd.toFixed(2);node.title='全站剩余 $'+b.total_remaining_usd.toFixed(2)+'；模型 GPT-6.1 Sol'}setInterval(update,8000);setTimeout(update,1200)})();""", media_type="text/javascript")

    @app.get("/trial-transport.js")
    async def transport_script():
        from fastapi.responses import Response

        return Response((Path(__file__).parent / "trial-transport.js").read_text(), media_type="text/javascript")

    @app.websocket("/_relay")
    @app.websocket("/trial/stream")
    async def relay(socket: WebSocket):
        browser = socket.url.path == "/trial/stream"
        public_host = socket.headers.get("host", "")
        if hmac.compare_digest(socket.headers.get("authorization", ""), "Bearer " + config["relay_secret"]):
            public_host = socket.headers.get("x-argus-public-host", public_host)
        if browser:
            if identify(socket.cookies.get(COOKIE, "")) is None:
                await socket.close(code=4401)
                return
            if socket.headers.get("origin") != "https://" + public_host:
                await socket.close(code=4403)
                return
        elif not hmac.compare_digest(socket.headers.get("authorization", ""), "Bearer " + config["relay_secret"]):
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
        if hmac.compare_digest(socket.headers.get("authorization", ""), "Bearer " + config["relay_secret"]):
            public_host = socket.headers.get("x-argus-public-host", public_host)
        expected = "https://" + public_host
        if origin and origin != expected:
            await socket.close(code=4403)
            return
        row = config["tenants"][tenant]
        await socket.accept()
        query = dict(socket.query_params)
        query["token"] = row["web_token"]
        async with unix_connect(row["socket"], uri="ws://localhost/api/" + path + "?" + urlencode(query), max_size=8 * 1024 * 1024) as backend:
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
            return JSONResponse({"detail": "你的工作区正在准备，请稍后重试。"}, 503)
        response_headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "transfer-encoding", "connection", "content-encoding")}
        if path == "" and response.status_code == 200:
            from fastapi.responses import Response

            html = (await response.aread()).decode().replace("<head>", '<head><script src="/trial-transport.js"></script>').replace("</body>", '<script src="/trial-quota.js" defer></script></body>')
            await client.aclose()
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
