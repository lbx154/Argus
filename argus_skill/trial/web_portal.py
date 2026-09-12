"""Cookie-authenticated browser entry point for the separately provisioned trial.

Run ``python -m argus_skill.trial.web_portal --config /absolute/portal.json``.
``create_app()`` also reads ``ARGUS_WEB_TRIAL_CONFIG``; ``create_app(config)``
accepts the same JSON object or its path. Required fields are ``state_dir``
(containing an existing usage.sqlite3), ``key_file``, ``tenants`` (exactly
trial-01 through trial-11, each with url/token). Older ten-tenant configurations
remain readable; ``admin`` (url/token) is an optional legacy configuration alias
and is never routed as a frontend identity.
``token_limit`` defaults to 10,000,000 lifetime input-plus-output tokens per
web invitation. It changes the allowance, never the ledger's recorded usage;
the shared Store class retains its separate desktop-trial default.
Public administrator login is disabled unless ``admin_login_token`` contains
a separate private credential. ``admin.token`` is forwarding-only: old demo
links redirect to the invitation page. Rotating/removing admin_login_token
invalidates administrator cookies without changing tenant credentials or usage.
A tenant may also specify ``uds``: an absolute, private Unix socket path. In
that case ``url`` is the HTTP origin (typically http://localhost), not a TCP
destination. Every tenant must have a distinct socket or TCP origin.
Optional ``compute_url`` and ``compute_uds`` enable the separate compute API
bridge. It uses the authenticated invitation credential, never a web token;
admin sessions have no compute identity. Authenticated tenants can open
``/invite/compute``; its PAGE/SCRIPT assets come from ``compute_page``.
Only local HTTP testing should set ``secure_cookie: false``.

Terminate HTTPS in a trusted reverse proxy, suppress query-string access logs
there (legacy admin links carry a token), and never publish tenant backends.
If a TLS-terminating tunnel preserves Host but omits the HTTPS scheme, configure
``public_origin`` with its exact HTTPS browser origin (no path/query/fragment).
The exception is bound to that request Host and reads no forwarded headers;
native same-origin local requests retain their existing behavior. A hostname
change requires updating this pinned configuration and restarting the portal.
This service neither provisions keys nor recovers/locks the model gateway.
Container isolation, model quota enforcement, and secret-free backend artifacts
remain deployment responsibilities. Separate administrator and invitation cookies allow both sessions in one browser.
Logout clears only the corresponding cookie; a copied
tenant cookie remains valid for at most seven days (there is no session-revocation DB).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import anyio
import httpx
from cryptography.fernet import InvalidToken
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles
from websockets.exceptions import WebSocketException
from websockets.legacy.client import connect as websocket_connect
from websockets.legacy.client import unix_connect as websocket_unix_connect

from . import WEB_TOKEN_LIMIT
from .secrets import Vault
from .store import Store, TrialError

COOKIE = "argus_web_session"
ADMIN_COOKIE = "argus_admin_session"
SESSION_SECONDS = 7 * 24 * 3600
RESEARCH_POLL_SECONDS = 5
MAX_BODY_BYTES = 16 * 1024 * 1024
COOKIE_DOMAIN = b"argus-web-invitation-session-v1\x00"
ADMIN_COOKIE_DOMAIN = b"argus-web-private-admin-session-v2\x00"
TENANT_IDS = {f"trial-{number:02d}" for number in range(1, 12)}
LEGACY_TENANT_IDS = TENANT_IDS - {"trial-11"}
SAFE_METHODS = {"GET", "HEAD"}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
SECURITY_HEADERS = {
    "cache-control": "no-store",
    # Preserve Origin on same-origin HTML form POSTs while omitting the
    # referrer entirely for cross-origin navigation.
    "referrer-policy": "same-origin",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "content-security-policy": CSP,
    "x-accel-buffering": "no",
}
REQUEST_HEADERS = {
    "accept", "accept-language", "content-type", "range", "if-range",
    "if-match", "if-none-match", "if-modified-since", "if-unmodified-since",
    "last-event-id",
}
RESPONSE_HEADERS = {
    "content-type", "content-length", "content-encoding", "content-disposition",
    "accept-ranges", "content-range", "etag", "last-modified",
}
HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}
# The actual public runtime controls live in webapi/routes/{meta,daemon,projects}.
# Deny them for admin cookies too: administration happens through a private path.
BLOCKED = re.compile(
    r"^/(?:api/(?:runtime|system|metrics)(?:/|$)|metrics(?:/|$)|"
    r"(?:docs|redoc|openapi\.json)(?:/|$))|"
    r"^/api/projects/[^/]+/(?:config|skills|identity|doctor|workdir|launch-cwd)(?:/|$)|"
    r"^/api/projects/[^/]+/daemon/(?:upgrade|upgrade-schedule|replace)(?:/|$)"
)
PROJECT_WRITES = re.compile(
    r"^/api/projects/[^/]+/(?:attachments|message(?:/stream)?|tasks|nudge|note|"
    r"plan|prompt/rewrite|reset|continuous|daemon/(?:start|stop)|mission/abort|"
    r"backlog/[^/]+/(?:answer|dispose|stop)|decisions/[^/]+/resolve|"
    r"reviews/final|map-notes)$"
)
PLUGIN_WRITES = re.compile(
    r"^/api/plugins/crystalpilot/(?:launch|preferences|config|"
    r"manage/(?:health|repair|shelx)|"
    r"projects/(?:open|settings|restart_engine|mcp_status|upload|import-structure)|"
    r"threads/(?:send|interrupt|steer|rename|compact|fork)|approvals/decide|"
    r"system/pick_folder|ui/diagnostics|"
    r"wb/refine/analysis/jobs(?:/[^/]+/(?:cancel|release))?)$"
)
WS_ROUTE = re.compile(r"^/api/projects/[^/]+/stream$")
# The website preview page ships its own content-security-policy that sandboxes
# the delivered site and denies it every network destination; its policy is kept
# on the response so the site's own styles and scripts run inside the sandbox.
PREVIEW_PAGE_ROUTE = re.compile(r"^/api/projects/[^/]+/artifact/preview/page$")
# The websocket library can log its token-bearing upgrade URL at DEBUG.
WS_LOGGER = logging.Logger("argus.trial.private.websocket", level=logging.CRITICAL + 1)


@dataclass(frozen=True, repr=False)
class Endpoint:
    url: str
    uds: str | None = None

    @classmethod
    def load(cls, data: dict) -> Endpoint:
        url = data["url"]
        if not isinstance(url, str):
            raise ValueError("Invalid backend configuration")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Backend requires an HTTP origin")
        # Validate the port without including a secret-bearing URL in errors.
        try:
            parsed.port
        except ValueError:
            raise ValueError("Invalid backend port") from None
        uds = data.get("uds")
        if "uds" in data:
            if not isinstance(uds, str) or not Path(uds).is_absolute() or "\x00" in uds:
                raise ValueError("uds must be an absolute Unix socket path")
            uds = str(Path(uds).resolve())
        return cls(url.rstrip("/"), uds)


@dataclass(frozen=True, repr=False)
class Backend:
    url: str
    token: str
    uds: str | None = None

    @classmethod
    def load(cls, data: dict) -> Backend:
        if (
            not isinstance(data, dict) or not {"url", "token"} <= data.keys()
            or data.keys() - {"url", "token", "uds"}
        ):
            raise ValueError("Each backend requires url/token and optional uds")
        endpoint = Endpoint.load(data)
        token = data["token"]
        if (
            not isinstance(token, str) or not token or not token.isascii()
            or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in token)
        ):
            raise ValueError("Backend requires a private token")
        return cls(endpoint.url, token, endpoint.uds)


@dataclass(frozen=True, repr=False)
class Settings:
    state_dir: Path
    key_file: Path
    tenants: dict[str, Backend]
    admin: Backend | None = None
    secure_cookie: bool = True
    compute: Endpoint | None = None
    admin_login_token: str | None = None
    token_limit: int | None = WEB_TOKEN_LIMIT
    frontend_dir: Path | None = None
    public_origin: str | None = None
    team_training_policy: dict | None = None

    @classmethod
    def load(cls, source: dict | str | Path | None = None) -> Settings:
        if source is None:
            source = os.environ.get("ARGUS_WEB_TRIAL_CONFIG")
            if not source:
                raise ValueError("Set ARGUS_WEB_TRIAL_CONFIG or pass --config")
        data = source if isinstance(source, dict) else json.loads(Path(source).read_text())
        required = {"state_dir", "key_file", "tenants"}
        if (
            not isinstance(data, dict) or not required <= data.keys()
            or data.keys() - required - {
                "admin", "secure_cookie", "compute_url", "compute_uds", "admin_login_token", "token_limit",
                "frontend_dir", "public_origin", "team_training_policy",
            }
        ):
            raise ValueError("Invalid web portal configuration fields")
        state, key = Path(data["state_dir"]), Path(data["key_file"])
        if not state.is_absolute() or not key.is_absolute():
            raise ValueError("state_dir and key_file must be absolute paths")
        if not (state / "usage.sqlite3").is_file() or not key.is_file():
            raise ValueError("An existing trial ledger and master key are required")
        if not isinstance(data["tenants"], dict) or set(data["tenants"]) not in (TENANT_IDS, LEGACY_TENANT_IDS):
            raise ValueError("Configure exactly trial-01 through trial-11 (legacy trial-01 through trial-10 is supported)")
        secure = data.get("secure_cookie", True)
        if type(secure) is not bool:
            raise ValueError("secure_cookie must be a boolean")
        token_limit = data.get("token_limit", WEB_TOKEN_LIMIT)
        if token_limit is not None and (type(token_limit) is not int or token_limit <= 0):
            raise ValueError("token_limit must be a positive integer")
        tenants = {name: Backend.load(value) for name, value in data["tenants"].items()}
        admin = Backend.load(data["admin"]) if data.get("admin") is not None else None
        backends = list(tenants.values())
        if admin is not None and admin not in backends:
            backends.append(admin)
        if len({backend.token for backend in backends}) != len(backends):
            raise ValueError("Each backend must have its own private token")
        if len({("uds", backend.uds) if backend.uds else ("tcp", backend.url)
                for backend in backends}) != len(backends):
            raise ValueError("Each backend must have its own socket or HTTP origin")
        admin_login_token = data.get("admin_login_token")
        if admin_login_token is not None and (
            not isinstance(admin_login_token, str) or not admin_login_token
            or not admin_login_token.isascii()
            or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in admin_login_token)
            or admin_login_token.startswith("argus_trial_")
            or admin_login_token in {backend.token for backend in backends}
        ):
            raise ValueError("admin_login_token must be a distinct private login credential")
        compute = None
        if "compute_uds" in data and "compute_url" not in data:
            raise ValueError("compute_uds requires compute_url")
        if "compute_url" in data:
            endpoint = {"url": data["compute_url"]}
            if "compute_uds" in data:
                endpoint["uds"] = data["compute_uds"]
            compute = Endpoint.load(endpoint)
        frontend = None
        if "frontend_dir" in data:
            frontend = Path(data["frontend_dir"])
            if not frontend.is_absolute() or not (frontend / "index.html").is_file():
                raise ValueError("frontend_dir must name an absolute built frontend directory")
            frontend = frontend.resolve()
        public_origin = data.get("public_origin")
        if public_origin is not None:
            if not isinstance(public_origin, str) or not public_origin.isascii():
                raise ValueError("public_origin must be an exact HTTPS origin")
            try:
                parsed = urlsplit(public_origin)
                hostname, port = parsed.hostname, parsed.port
                if (parsed.scheme != "https" or not hostname or parsed.username is not None
                        or parsed.password is not None or parsed.path or "?" in public_origin or "#" in public_origin
                        or parsed.netloc.endswith(":")
                        or any(char.isspace() or ord(char) < 33 for char in public_origin)
                        or (port is not None and not 1 <= port <= 65535)):
                    raise ValueError
                if ":" in hostname:
                    ipaddress.IPv6Address(hostname)
                    authority = "[" + hostname.lower() + "]"
                else:
                    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", hostname):
                        raise ValueError
                    if any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                           for label in hostname.split(".")):
                        raise ValueError
                    authority = hostname.lower()
                public_origin = "https://" + authority + (f":{port}" if port not in (None, 443) else "")
            except ValueError:
                raise ValueError("public_origin must be an exact HTTPS origin") from None
        team_training_policy = data.get("team_training_policy")
        if team_training_policy is not None:
            from .training_data import validate_team_training_policy

            team_training_policy = validate_team_training_policy(team_training_policy, tenants)
        return cls(state, key, tenants, admin, secure, compute, admin_login_token, token_limit, frontend,
                   public_origin, team_training_policy)


class BackendTransport(httpx.AsyncBaseTransport):
    """Select a private connection pool by the authenticated server-side identity."""

    def __init__(self, settings: Settings):
        endpoints: dict[str, Backend | Endpoint] = dict(settings.tenants)
        if settings.compute:
            endpoints["compute"] = settings.compute
        self.transports = {
            name: httpx.AsyncHTTPTransport(
                uds=backend.uds, trust_env=False,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
            for name, backend in endpoints.items()
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.transports[request.extensions["argus_backend"]].handle_async_request(request)

    async def aclose(self):
        await asyncio.gather(*(transport.aclose() for transport in self.transports.values()))


class SecurityHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def secured(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                policy = headers.get(b"content-security-policy", CSP.encode())
                headers.update({key.encode(): value.encode() for key, value in SECURITY_HEADERS.items()})
                headers[b"content-security-policy"] = policy
                if (
                    message["status"] in {200, 304}
                    and scope.get("method") in SAFE_METHODS
                    and re.fullmatch(
                        r"/assets/[\w.-]+-[A-Za-z0-9_-]{8}\.(?:js|mjs|css|woff2?|ttf)",
                        scope.get("path", ""),
                    )
                ):
                    headers[b"cache-control"] = b"private, max-age=31536000, immutable"
                message["headers"] = list(headers.items())
            await send(message)
        await self.app(scope, receive, secured)


async def finish_capture(capture, status, completed, content_type):
    if capture is None:
        return
    with anyio.CancelScope(shield=True):
        try:
            await run_in_threadpool(capture.finish, status, completed, content_type)
        except (sqlite3.Error, OSError):
            logging.getLogger(__name__).exception("Could not settle request journey observation")


class ProxyResponse(StreamingResponse):
    def __init__(self, upstream: httpx.Response, headers: dict, capture=None):
        self.upstream = upstream
        self.capture = capture
        super().__init__(self.chunks(), status_code=upstream.status_code, headers=headers)

    async def chunks(self):
        completed = False
        try:
            async for chunk in self.upstream.aiter_raw():
                if self.capture is not None:
                    self.capture.feed(chunk)
                yield chunk
            completed = True
        finally:
            await finish_capture(
                self.capture, self.upstream.status_code, completed,
                self.upstream.headers.get("content-type", ""),
            )

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Disconnect cancellation must not leak a pooled connection.
            with anyio.CancelScope(shield=True):
                await self.upstream.aclose()


def same_origin(request: Request | WebSocket) -> bool:
    origin = request.headers.get("origin", "")
    scheme = {"ws": "http", "wss": "https"}.get(request.url.scheme, request.url.scheme)
    # Some TLS-terminating tunnels preserve Host but omit the HTTPS scheme.
    # Only an operator-pinned browser origin may bridge that mismatch, and only
    # for that exact request authority. Never infer it from forwarded headers.
    app = request.scope.get("app")
    settings = getattr(getattr(app, "state", None), "settings", None)
    public_origin = getattr(settings, "public_origin", None)
    if public_origin and origin == public_origin:
        public = urlsplit(public_origin)
        authority = request.url.netloc.lower()
        if authority == public.netloc or (public.port is None and authority == public.netloc + ":443"):
            return True
    return origin == f"{scheme}://{request.url.netloc}"


def require_origin(request: Request | WebSocket):
    if not same_origin(request):
        raise HTTPException(403, "Same-origin request required")


def canonical_path(path: str) -> bool:
    return (
        path.startswith("/") and "//" not in path and "\\" not in path and "%" not in path
        and not any(segment in {".", ".."} for segment in path.split("/"))
        and not any(ord(c) < 32 or ord(c) == 127 for c in path)
    )


def permitted(path: str, method: str) -> bool:
    if not canonical_path(path):
        return False
    if method in SAFE_METHODS and re.fullmatch(r"/api/projects/[^/]+/config", path):
        return True
    if BLOCKED.search(path):
        return False
    if method in SAFE_METHODS:
        return True
    if method == "POST":
        return bool(
            PROJECT_WRITES.fullmatch(path) or PLUGIN_WRITES.fullmatch(path) or path == "/api/daemons"
            or re.fullmatch(r"/api/map-copy/(?:project|dataset)/[^/]+", path)
            or re.fullmatch(r"/api/trash/[^/]+/restore", path)
        )
    return method in {"PATCH", "DELETE"} and bool(re.fullmatch(r"/api/projects/[^/]+", path))


def compute_permitted(path: str, method: str) -> bool:
    if method in SAFE_METHODS:
        return bool(re.fullmatch(r"/compute/(?:status|jobs(?:/[0-9]+(?:/logs)?)?)", path))
    return method == "POST" and bool(re.fullmatch(r"/compute/jobs(?:/[0-9]+/cancel)?", path))


def clean_query(request: Request | WebSocket, *, readonly: bool = False) -> str:
    return urlencode([
        (key, value) for key, value in request.query_params.multi_items()
        if key.lower() != "token" and not (readonly and key == "prewarm")
    ])


def filtered_headers(headers: httpx.Headers, allowed: set[str]) -> dict[str, str]:
    nominated = {part.strip().lower() for part in headers.get("connection", "").split(",")}
    return {
        key: value for key, value in headers.items()
        if key.lower() in allowed - HOP_HEADERS - nominated
    }


async def read_body(request: Request, limit: int = MAX_BODY_BYTES) -> bytes:
    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) < 0 or int(length) > limit:
                raise HTTPException(413, "Request too large")
        except ValueError:
            raise HTTPException(400, "Invalid request") from None
    body = bytearray()
    try:
        async with asyncio.timeout(60):
            async for chunk in request.stream():
                if len(body) + len(chunk) > limit:
                    raise HTTPException(413, "Request too large")
                body.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, "Request timed out") from None
    return bytes(body)


def json_body(body: bytes) -> dict:
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Invalid request") from None
    if not isinstance(value, dict):
        raise HTTPException(400, "Invalid request")
    return value


def login_page(nonce: str, token_limit: int | None = WEB_TOKEN_LIMIT,
               notice_version: str | None = None, *, defer_notice: bool = False) -> str:
    if token_limit is None:
        quota_copy = "每个邀请码不设累计 token 上限，输入与输出仍按实际用量记录；上游服务限流仍然适用。"
    else:
        quota = f"{token_limit // 10_000}万" if token_limit % 10_000 == 0 else f"{token_limit:,}"
        quota_copy = "每个邀请码终身享有 " + quota + " tokens（输入与输出合计），不会按月重置。"
    notice = (
        '<label><input id="data-notice" type="checkbox" required data-version="'
        + html.escape(notice_version, quote=True) + '"> '
        '我已阅读并同意团队训练内测告知：从本次确认起，允许运营方记录任务输入、可见回复、'
        '统筹、规划、执行、审查四个角色的真实过程、产物引用、明确反馈和资源使用信息，'
        '用于团队内部模型训练、训练数据准备、人工复盘与产品改进。'
        '过程采集包括模型实际输入（含应用的系统提示和开发者指令）、可见输出、'
        '工具的真实定义、调用参数和执行结果，可能包含代码、文件内容、工作区路径以及你输入或工具读取到的敏感信息。'
        '无工具调用、失败或未结束的过程也会保留；不会因疑似敏感内容或格式不完整而整段排除，采集与质量验收分别记录。'
        '模型提供的结构化隐藏推理和签名字段会被移除。'
        '关闭浏览器后，服务器仍会持续采集；不回填授权前的历史记录。'
        '请仅提交团队有权用于本次内测与内部训练的材料。'
        '研究内容最多保留30天，管理员查看和导出会留审计记录。'
        '可在“研究记录与反馈”中删除整个项目的研究副本并停止该项目后续采集，'
        '但不会删除运行中任务、工作区、源日志或计费账本。'
        '同意凭据、采集游标和删除标记单独保留以防重新导入；研究库不由本功能创建备份，'
        '既有主机快照及已下载导出不能在此保证即时删除，须联系运营方处理。'
        '本项包含内部训练用途，不包含对外或商业分享；旧版同意不能替代本次确认。</label>'
        '<label><input id="external-sharing-notice" type="checkbox"> '
        '另外授权运营方将今后符合对外分享授权范围的记录向第三方提供，包括商业供应或出售（可选）。'
        '记录可能包含可识别信息；可在研究记录页面撤回未来导出授权，已交付副本不能自动召回。</label>'
        if notice_version else ""
    )
    if notice:
        notice = (
            '<fieldset id="data-notice-fields" style="border:0;padding:0;margin:0;min-width:0"'
            + (" disabled hidden" if defer_notice else "") + ">" + notice + "</fieldset>"
        )
    return """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Argus · 邀请码入口</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;
font:16px/1.7 system-ui,sans-serif;color:#e7edf8;background:radial-gradient(ellipse at top,#203050,#0a1020)}
main{width:min(92vw,480px);padding:42px;background:#111c30;border:1px solid #30425e;
border-radius:24px;box-shadow:0 24px 90px #0006}small{color:#9eb5d3;letter-spacing:.14em}
h1{font-size:30px;margin:12px 0}p{color:#b5c5dc}label{display:block;margin-top:20px}
input[type=password]{width:100%;padding:14px;margin-top:8px;border:1px solid #526583;
border-radius:10px;color:#fff;background:#0b1424;font-size:16px}
button{width:100%;padding:14px;border:0;border-radius:10px;margin-top:24px;
background:#9dc2ff;color:#101a2e;font-size:16px;font-weight:700;cursor:pointer}
button:disabled{opacity:.5}#error{color:#ffb5b5;min-height:1.7em}
footer{margin-top:20px;color:#8fa4c0;font-size:13px}
footer nav{margin-top:14px}a{color:#9dc2ff;text-underline-offset:3px}</style>
<main><small>ARGUS · PRIVATE TRIAL</small><h1>把想法交给 Argus</h1>
<p>输入专属邀请码，进入独立研究空间。工作空间与历史记录按邀请码隔离，
""" + quota_copy + """</p>
<form id="login"><label for="code">专属邀请码</label>
<input id="code" type="password" autocomplete="off" spellcheck="false" required
placeholder="请输入邀请码" maxlength="76">
<label><input id="readonly" type="checkbox"> 只读浏览（不可提交或修改任务）</label>
""" + notice + """
<button id="submit" type="submit">进入工作区 →</button>
<div id="error" role="alert" aria-live="polite"></div></form>
<footer>仅需邀请码，无需注册或其他身份验证。同一邀请码可在不同设备继续进入同一独立工作空间。<br>
浏览器会保留 7 天安全访问会话，不保存邀请码原文。请勿共享邀请码。
<nav aria-label="邀请码与计算"><a href="/invite/status">邀请码额度（进入后查看）</a>
 · <a href="/invite/compute">GPU任务队列（进入后查看）</a></nav></footer></main>
<script nonce='""" + nonce + """'>
const form=document.querySelector('#login'),code=document.querySelector('#code');
form.addEventListener('submit',async event=>{
event.preventDefault();const button=document.querySelector('#submit');
button.disabled=true;document.querySelector('#error').textContent='';
const value=code.value.trim();code.value='';
const payload={code:value,readonly:document.querySelector('#readonly').checked};
const notice=document.querySelector('#data-notice');
const noticeFields=document.querySelector('#data-notice-fields');
if(notice&&!noticeFields.disabled){payload.data_notice_accepted=notice.checked;payload.notice_version=notice.dataset.version;
payload.external_sharing_accepted=document.querySelector('#external-sharing-notice').checked;}
try{const response=await fetch('/invite/login',{method:'POST',credentials:'same-origin',
headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
const result=await response.json();
if(response.status===403&&noticeFields?.disabled&&result.detail==='Please confirm the trial data notice'){
noticeFields.hidden=false;noticeFields.disabled=false;code.value=value;
document.querySelector('#error').textContent='请确认当前试用告知后继续。';return;}
if(!response.ok)throw Error();location.replace(result.redirect);
}catch{document.querySelector('#error').textContent='邀请码验证未成功，请检查后重试。'}
finally{button.disabled=false;}});
</script></html>"""


def admin_login_page(nonce: str) -> str:
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Argus · 数据后台登录</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f7fa;color:#303345;font:14px/1.7 system-ui,sans-serif}main{width:min(84vw,380px);padding:34px;background:white;border:1px solid #e7e8ef;border-radius:16px}h1{font-size:23px;margin:8px 0}small,p{color:#9195a4}label{display:block;margin:24px 0 8px}input,button{box-sizing:border-box;width:100%;border-radius:8px;padding:12px;font:inherit}input{border:1px solid #dfe1e9}button{margin-top:16px;border:0;background:#635bca;color:white;cursor:pointer}a{color:#77719f;text-decoration:none;font-size:12px}#error{color:#b86161;font-size:12px;min-height:20px}button:disabled{opacity:.5}</style></head>
<body><main><small>ARGUS / 数据工作台</small><h1>登录数据后台</h1><p>使用专用管理员密钥查看团队过程数据。</p>
<form id="admin-login"><label for="admin-key">管理员密钥</label><input id="admin-key" type="password" autocomplete="current-password" required autofocus><button type="submit">进入数据后台</button><p id="error" role="alert"></p></form><a href="/invite">进入用户工作区 →</a></main>
<script nonce="__LOGIN_NONCE__">
document.getElementById('admin-login').addEventListener('submit',async event=>{event.preventDefault();const button=event.currentTarget.querySelector('button');button.disabled=true;document.getElementById('error').textContent='';try{const response=await fetch('/admin/login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_login_token:document.getElementById('admin-key').value})});if(!response.ok)throw Error();document.getElementById('admin-key').value='';const result=await response.json();location.replace(result.redirect);}catch{document.getElementById('error').textContent='登录未成功，请检查管理员密钥后重试。';}finally{button.disabled=false;}});
</script></body></html>""".replace("__LOGIN_NONCE__", html.escape(nonce, quote=True))


def launcher_page(identity: dict, compute_enabled: bool, analytics_enabled: bool = False) -> str:
    workspace = "/?kiosk=1" if identity["readonly"] else "/"
    role = "只读浏览：可查看任务与额度，不可提交或修改任务。" if identity["readonly"] else "交互会话：继续使用当前独立工作空间。"
    compute_links = ""
    if identity["role"] == "trial":
        compute_links = (
            '<a href="/invite/compute">GPU任务队列 →</a>'
            '<a href="/invite/compute">模型与GPU额度 →</a>'
            if compute_enabled else '<p>GPU任务队列尚未配置。</p>'
        )
        if analytics_enabled:
            compute_links += '<a href="/invite/research">研究记录与反馈 / 删除研究副本 →</a>'
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Argus · 我的研究空间</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0a1020;
color:#e7edf8;font:16px/1.7 system-ui,sans-serif}}main{{width:min(86vw,520px);padding:32px;
border:1px solid #30425e;background:#111c30;border-radius:20px}}h1{{margin:12px 0}}
small,p{{color:#b5c5dc}}nav{{display:grid;gap:12px;margin:24px 0}}a,button{{padding:12px 18px;
border:1px solid #526583;border-radius:10px;color:#b5d2ff;background:#0b1424;font:inherit}}
a{{text-decoration:none}}button{{cursor:pointer}}a:focus-visible,button:focus-visible{{outline:2px solid #b5d2ff}}</style>
<main><small>ARGUS · RESEARCH CLOUD / {identity["tenant"]}</small>
<h1>我的研究空间</h1><p>{role}</p>
<p>从研究想法、代码与实验，到任务队列和结果：同一邀请码可跨设备继续同一研究过程。</p>
<nav aria-label="研究服务"><a href="{workspace}">进入工作区 →</a>{compute_links}
<a href="/invite/status">邀请码与模型额度明细 →</a></nav>
<form method="post" action="/invite/logout"><button type="submit">退出当前空间</button></form>
<p><small>共享算力按任务排队，工作空间与历史记录按邀请码隔离。</small></p></main></html>"""


def create_app(config: dict | str | Path | Settings | None = None, *,
               transport: httpx.AsyncBaseTransport | None = None, analytics=None) -> FastAPI:
    """Build the portal; ``transport`` is solely an in-process HTTP test seam."""
    settings = config if isinstance(config, Settings) else Settings.load(config)
    frontend = StaticFiles(directory=settings.frontend_dir) if settings.frontend_dir else None
    frontend_index = settings.frontend_dir / "index.html" if settings.frontend_dir else None
    frontend_cache = (None, None)

    def frontend_document():
        nonlocal frontend_cache
        modified = frontend_index.stat().st_mtime_ns
        if frontend_cache[0] != modified:
            frontend_cache = (modified, frontend_index.read_text())
        return frontend_cache[1]

    @asynccontextmanager
    async def lifespan(app):
        vault = Vault(settings.key_file, settings.state_dir / "github-token.enc")
        store = Store(settings.state_dir / "usage.sqlite3", token_limit=settings.token_limit,
                      key_limit=len(settings.tenants))
        # Check provisioned identities, without issuing keys, recovering active
        # requests, or acquiring the model gateway's process ownership lock.
        for key_id in settings.tenants:
            with store.transaction() as db:
                found = db.execute("SELECT credential_hash FROM trial_keys WHERE key_id=?", (key_id,)).fetchone()
            if found is None or not hmac.compare_digest(
                found[0], hashlib.sha256(vault.credential(key_id).encode()).hexdigest(),
            ):
                raise ValueError("Provisioned trial identities do not match the master key")
        app.state.vault, app.state.store = vault, store
        # Bind admin cookies to the current private login authority. Previously
        # issued public-demo cookies and cookies from a rotated login cannot pass.
        app.state.admin_binding = (
            hmac.digest(
                vault.signing_key,
                b"argus-web-private-admin-session-v1\x00" + settings.admin_login_token.encode(),
                "sha256",
            ).hex()
            if settings.admin_login_token is not None else None
        )
        async with httpx.AsyncClient(
            transport=transport if transport is not None else BackendTransport(settings),
            trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(connect=10, read=300, write=60, pool=10),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        ) as client:
            app.state.client = client
            worker = None
            training_bridges = None
            if analytics is not None:
                from .training_bridge import start_training_bridges

                await run_in_threadpool(analytics.open_storage)
                try:
                    training_bridges = await run_in_threadpool(
                        start_training_bridges, app.state.training_data, settings.tenants,
                    )
                except BaseException:
                    await run_in_threadpool(analytics.close_storage)
                    raise
                async def collect():
                    from .interaction_capture import prune_interactions

                    while True:
                        try:
                            result = await run_in_threadpool(app.state.journal.poll)
                            await run_in_threadpool(analytics.prune)
                            await run_in_threadpool(prune_interactions, analytics)
                            await run_in_threadpool(app.state.research_controls.prune)
                            await run_in_threadpool(app.state.training_data.capture.prune)
                            app.state.research_status = {
                                "last_poll_at": time.time(), "state": "running", **result,
                                "tool_capture": training_bridges.status(),
                            }
                        except (sqlite3.Error, OSError):
                            # Do not log private source paths or exception payloads.
                            logging.getLogger(__name__).error("Research collection/storage unavailable")
                            app.state.research_status = {
                                "state": "storage_error", "tool_capture": training_bridges.status(),
                            }
                        await asyncio.sleep(RESEARCH_POLL_SECONDS)

                worker = asyncio.create_task(collect())
            try:
                yield
            finally:
                if worker:
                    worker.cancel()
                    with suppress(asyncio.CancelledError):
                        await worker
                if training_bridges is not None:
                    await run_in_threadpool(training_bridges.close)
                if analytics is not None:
                    await run_in_threadpool(analytics.close_storage)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityHeaders)

    def read_session(request: Request | WebSocket, *, admin: bool = False) -> dict | None:
        cookie = request.cookies.get(ADMIN_COOKIE if admin else COOKIE, "")
        if not cookie or len(cookie) > 4096:
            return None
        domain = ADMIN_COOKIE_DOMAIN if admin else COOKIE_DOMAIN
        try:
            raw = app.state.vault.cipher.decrypt(cookie.encode(), ttl=SESSION_SECONDS)
            if not raw.startswith(domain):
                return None
            value = json.loads(raw[len(domain):])
            if not isinstance(value, dict):
                return None
            fields = {"tenant", "role", "readonly", "exp"}
            if admin:
                fields.add("admin_binding")
            if (
                set(value) != fields
                or type(value["readonly"]) is not bool or type(value["exp"]) is not int
                or value["exp"] <= time.time() or value["exp"] > time.time() + SESSION_SECONDS
                or value["role"] != ("admin" if admin else "trial")
                or not isinstance(value["tenant"], str)
                or (not admin and value["tenant"] not in settings.tenants)
                or (admin and value["tenant"] != "admin")
            ):
                return None
            if admin and (
                app.state.admin_binding is None or not isinstance(value["admin_binding"], str)
                or not hmac.compare_digest(value["admin_binding"], app.state.admin_binding)
            ):
                return None
            if not admin:
                try:
                    app.state.store.check_access(value["tenant"])
                except TrialError:
                    return None
            return value
        except (InvalidToken, ValueError, UnicodeError, TypeError):
            return None

    def session(request: Request | WebSocket) -> dict | None:
        path = request.url.path
        return read_session(request, admin=path == "/admin" or path.startswith("/admin/"))

    def set_session(response, tenant: str, readonly: bool, role: str = "trial"):
        admin = role == "admin"
        value = {"tenant": tenant, "role": role, "readonly": readonly,
                 "exp": int(time.time()) + SESSION_SECONDS}
        if admin:
            value["admin_binding"] = app.state.admin_binding
        domain = ADMIN_COOKIE_DOMAIN if admin else COOKIE_DOMAIN
        cookie = app.state.vault.cipher.encrypt(domain + json.dumps(value).encode()).decode()
        response.set_cookie(ADMIN_COOKIE if admin else COOKIE, cookie, max_age=SESSION_SECONDS,
                            secure=settings.secure_cookie, httponly=True, samesite="strict",
                            path="/admin" if admin else "/")

    app.state.session = session
    app.state.settings = settings
    app.state.analytics = analytics
    if settings.team_training_policy is not None and analytics is None:
        raise ValueError("team_training_policy requires configured analytics")
    if analytics is not None:
        analytics.token_limit = settings.token_limit
        from .analytics_routes import register_analytics
        from .journey_journal import Journal
        from .research_routes import register_research
        from .training_data import COMBINED_NOTICE_VERSION
        from .training_routes import register_training_routes

        # Expanded collection must never inherit an old consent, even if a
        # deployment still supplies the old config's notice version.
        analytics.notice_version = COMBINED_NOTICE_VERSION
        analytics.retention_days = min(30, analytics.retention_days)
        register_analytics(app, analytics, session)
        register_research(app, analytics, session, journal=Journal(analytics))
        register_training_routes(
            app, analytics, session, journal=app.state.journal,
            controls=app.state.research_controls,
        )
        if settings.team_training_policy is not None:
            for tenant in settings.team_training_policy["tenant_ids"]:
                app.state.training_data.apply_offline_team_authorization(
                    tenant, team_policy=settings.team_training_policy,
                )
        app.state.research_status = {"state": "starting"}

    @app.middleware("http")
    async def administrator_entry(request: Request, call_next):
        if request.method == "GET" and request.url.path in {"/admin", "/admin/data"} and session(request) is None:
            return RedirectResponse("/admin/login", status_code=303)
        return await call_next(request)

    @app.get("/admin/login", response_class=HTMLResponse)
    async def administrator_login_page(request: Request):
        if session(request) is not None:
            return RedirectResponse("/admin/data", status_code=303)
        nonce = secrets.token_urlsafe(24)
        return HTMLResponse(admin_login_page(nonce), headers={
            "content-security-policy": CSP.replace("script-src 'self'", f"script-src 'nonce-{nonce}'"),
        })

    @app.get("/admin/status")
    async def administrator_status(request: Request):
        identity = session(request)
        if identity is None:
            raise HTTPException(401, "请先登录数据后台")
        return {"key_id": "admin", "role": "admin", "readonly": identity["readonly"],
                "expires_at": identity["exp"]}

    @app.post("/admin/logout")
    async def administrator_logout(request: Request):
        require_origin(request)
        if session(request) is None:
            raise HTTPException(401, "请先登录数据后台")
        response = (RedirectResponse("/admin/login", status_code=303)
                    if "text/html" in request.headers.get("accept", "")
                    else JSONResponse({"redirect": "/admin/login"}))
        response.delete_cookie(ADMIN_COOKIE, path="/admin", secure=settings.secure_cookie,
                               httponly=True, samesite="strict")
        return response

    @app.post("/admin/login")
    async def operator_login(request: Request):
        require_origin(request)
        data = json_body(await read_body(request, 2048))
        token, readonly = data.get("admin_login_token"), data.get("readonly", False)
        if (data.keys() - {"admin_login_token", "readonly"}
                or not isinstance(token, str) or type(readonly) is not bool):
            raise HTTPException(400, "Invalid administrator sign-in")
        if (settings.admin_login_token is None
                or not hmac.compare_digest(token.encode(), settings.admin_login_token.encode())):
            raise HTTPException(401, "Invalid administrator credentials")
        if analytics is not None:
            await run_in_threadpool(
                app.state.research_controls.audit, "admin.login", outcome="completed",
            )
        response = JSONResponse({"redirect": "/admin/data"})
        set_session(response, "admin", readonly, "admin")
        return response

    @app.get("/invite", response_class=HTMLResponse)
    async def invitation(request: Request):
        identity = session(request)
        if identity is not None and (
            analytics is None or await run_in_threadpool(analytics.consented, identity["tenant"], analytics.notice_version)
        ):
            return HTMLResponse(launcher_page(
                identity, settings.compute is not None, analytics is not None,
            ))
        nonce = secrets.token_urlsafe(24)
        return HTMLResponse(login_page(
            nonce, settings.token_limit, analytics.notice_version if analytics else None,
            defer_notice=settings.team_training_policy is not None,
        ), headers={
            "content-security-policy": CSP.replace("script-src 'self'", f"script-src 'nonce-{nonce}'")
        })

    @app.get("/invite/compute")
    @app.get("/invite/compute.js")
    async def compute_dashboard(request: Request):
        script = request.url.path == "/invite/compute.js"
        identity = session(request)
        if identity is None:
            if not script and "text/html" in request.headers.get("accept", ""):
                return RedirectResponse("/invite", status_code=303)
            raise HTTPException(401, "请先输入邀请码")
        if identity["role"] != "trial":
            raise HTTPException(403, "需要有效邀请码")
        if settings.compute is None:
            raise HTTPException(404, "Compute is not configured")
        from .compute_page import PAGE, SCRIPT

        return Response(SCRIPT, media_type="application/javascript") if script else HTMLResponse(PAGE)

    @app.post("/invite/login")
    async def login(request: Request):
        require_origin(request)
        data = json_body(await read_body(request, 2048))
        code, readonly = data.get("code"), data.get("readonly", False)
        if (
            data.keys() - {"code", "readonly", "notice_version", "data_notice_accepted",
                           "external_sharing_accepted"}
            or type(readonly) is not bool
            or type(data.get("external_sharing_accepted", False)) is not bool
            or not isinstance(code, str) or not re.fullmatch(r"argus_trial_[0-9a-f]{64}", code)
        ):
            raise HTTPException(400, "请输入有效的邀请码")
        try:
            tenant = await run_in_threadpool(app.state.store.authenticate, code)
        except TrialError:
            raise HTTPException(401, "邀请码无效，请重新输入") from None
        if tenant not in settings.tenants:
            raise HTTPException(401, "邀请码无效，请重新输入")
        if analytics is not None:
            offline_member = (
                settings.team_training_policy is not None
                and tenant in settings.team_training_policy["tenant_ids"]
            )
            explicit_notice = data.keys() & {
                "data_notice_accepted", "notice_version", "external_sharing_accepted",
            }
            if not offline_member or explicit_notice:
                if (data.get("data_notice_accepted") is not True
                        or data.get("notice_version") != analytics.notice_version):
                    raise HTTPException(403, "Please confirm the trial data notice")
                from .analytics import AnalyticsError

                try:
                    await run_in_threadpool(
                        app.state.training_data.accept_onboarding, tenant, analytics.notice_version,
                        accepted=True, external_sharing=data.get("external_sharing_accepted", False),
                        record_research=True,
                    )
                except AnalyticsError as exc:
                    raise HTTPException(exc.status, exc.code) from None
            await run_in_threadpool(app.state.journal.poll, tenant)
        await run_in_threadpool(app.state.store.tester_id, tenant)
        response = JSONResponse({"redirect": "/invite"})
        set_session(response, tenant, readonly)
        return response

    @app.get("/invite/status")
    async def status(request: Request):
        identity = session(request)
        if identity is None:
            raise HTTPException(401, "请先输入邀请码")
        result = {"key_id": identity["tenant"], "role": identity["role"],
                  "readonly": identity["readonly"], "expires_at": identity["exp"]}
        if identity["role"] == "trial":
            quota = await run_in_threadpool(app.state.store.status, identity["tenant"])
            result.update({key: quota[key] for key in
                           ("token_limit", "tokens_used", "tokens_remaining",
                            "tokens_settled", "tokens_reserved", "tokens_uncertain",
                            "tokens_unattributed", "token_unlimited")})
            result["tester_id"] = await run_in_threadpool(app.state.store.tester_id, identity["tenant"])
        return result

    @app.post("/invite/logout")
    async def logout(request: Request):
        require_origin(request)
        if session(request) is None:
            raise HTTPException(401, "请先输入邀请码")
        response = (
            RedirectResponse("/invite", status_code=303)
            if "text/html" in request.headers.get("accept", "")
            else JSONResponse({"redirect": "/invite"})
        )
        response.delete_cookie(COOKIE, path="/", secure=settings.secure_cookie,
                               httponly=True, samesite="strict")
        return response

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"])
    async def proxy(request: Request, path: str):
        path = "/" + path
        # Exchange before serving any frontend code so its legacy token adoption
        # logic never sees or stores the administrator credential.
        if path == "/" and request.method == "GET" and "token" in request.query_params:
            values = request.query_params.getlist("token")
            if settings.admin is not None and len(values) == 1 and hmac.compare_digest(values[0].encode(), settings.admin.token.encode()):
                return RedirectResponse("/invite", status_code=303)
            if (
                len(values) != 1
                or settings.admin_login_token is None
                or not hmac.compare_digest(values[0].encode(), settings.admin_login_token.encode())
            ):
                raise HTTPException(401, "入口凭证无效")
            if request.headers.get("origin") and not same_origin(request):
                raise HTTPException(403, "Same-origin request required")
            response = RedirectResponse("/admin/data", status_code=303)
            set_session(response, "admin", "1" in request.query_params.getlist("kiosk"), "admin")
            return response
        if path == "/admin" or path.startswith("/admin/"):
            raise HTTPException(404, "Administrator route not available")
        identity = session(request)
        if identity is None:
            if request.method == "GET" and not path.startswith(("/api/", "/compute/")) and (
                path in {"/", "/index.html"} or "text/html" in request.headers.get("accept", "")
            ):
                return RedirectResponse("/invite", status_code=303)
            raise HTTPException(401, "请先输入邀请码")
        if path.startswith("/invite"):
            raise HTTPException(405 if path == "/invite/logout" else 404, "Not available")
        if request.method not in SAFE_METHODS:
            require_origin(request)
            if identity["readonly"]:
                raise HTTPException(403, "Read-only session")
        if (
            frontend is not None
            and request.method in SAFE_METHODS and canonical_path(path)
        ):
            if path in {"/", "/index.html"}:
                nonce = secrets.token_urlsafe(24)
                document = await run_in_threadpool(frontend_document)
                content = re.sub(r"<script(?=[\s>])", f'<script nonce="{nonce}"', document)
                return HTMLResponse("" if request.method == "HEAD" else content, headers={
                    "content-security-policy": CSP.replace(
                        "script-src 'self'", f"script-src 'self' 'nonce-{nonce}'",
                    ),
                })
            if path.startswith("/assets/") or path in {
                "/favicon.svg", "/favicon-dark.svg", "/manifest.webmanifest",
                "/apple-touch-icon.png", "/apple-touch-icon-dark.png",
            }:
                try:
                    return await frontend.get_response(path.lstrip("/"), request.scope)
                except StarletteHTTPException as exc:
                    if exc.status_code != 404:
                        raise
                    # Already-open tabs may still request the container's previous bundle.
        route_key = identity["tenant"]
        if path == "/compute" or path.startswith("/compute/"):
            if identity["role"] == "admin":
                raise HTTPException(403, "需要有效邀请码")
            if settings.compute is None:
                raise HTTPException(404, "Compute is not configured")
            if not compute_permitted(path, request.method):
                raise HTTPException(403, "Route not available")
            backend = settings.compute
            credential = app.state.vault.credential(identity["tenant"])
            route_key = "compute"
        else:
            if not permitted(path, request.method):
                raise HTTPException(403, "Route not available")
            backend = settings.tenants[identity["tenant"]]
            credential = backend.token
        body = await read_body(request)
        if path == "/api/daemons" and request.method == "POST":
            data = json_body(body)
            if data.get("workdir") or data.get("launch_cwd"):
                raise HTTPException(403, "Workspace selection is not available")
        query = clean_query(request, readonly=identity["readonly"])
        url = backend.url + quote(path, safe="/:@-._~!$&'()*+,;=") + ("?" + query if query else "")
        headers = filtered_headers(httpx.Headers(request.headers), REQUEST_HEADERS)
        headers["authorization"] = "Bearer " + credential
        if route_key == "compute":
            headers["x-argus-readonly"] = str(identity["readonly"]).lower()
        # Construct directly rather than build_request: the shared client's
        # cookie jar must never send an upstream cookie to a different tenant.
        upstream_request = httpx.Request(
            request.method, url, headers=headers, content=body,
            extensions={"argus_backend": route_key},
        )
        capture = None
        interaction = re.fullmatch(r"/api/projects/([^/]+)/(?:message(?:/stream)?|tasks)", path)
        if analytics is not None and identity["role"] == "trial" and request.method == "POST" and interaction:
            capture = await run_in_threadpool(
                app.state.research_controls.capture,
                identity["tenant"], interaction.group(1), path, json_body(body),
            )
        try:
            upstream = await app.state.client.send(upstream_request, stream=True)
        except httpx.TimeoutException:
            await finish_capture(capture, 504, False, "application/json")
            raise HTTPException(504, "Workspace timed out") from None
        except httpx.HTTPError:
            await finish_capture(capture, 502, False, "application/json")
            raise HTTPException(502, "Workspace unavailable") from None
        if upstream.status_code >= 400:
            await finish_capture(capture, upstream.status_code, True, "application/json")
            if route_key == "compute" and upstream.status_code < 500:
                error_body = bytearray()
                async for chunk in upstream.aiter_bytes():
                    error_body.extend(chunk)
                    if len(error_body) > 8192:
                        break
                await upstream.aclose()
                if len(error_body) <= 8192 and credential.encode() not in error_body:
                    try:
                        error = json.loads(error_body).get("error")
                    except (ValueError, AttributeError):
                        error = None
                    if (isinstance(error, dict) and set(error) == {"code", "message"}
                            and all(isinstance(value, str) for value in error.values())):
                        return JSONResponse({"error": error}, status_code=upstream.status_code)
            await upstream.aclose()
            raise HTTPException(upstream.status_code, "Workspace request failed")
        if 300 <= upstream.status_code < 400 and upstream.status_code != 304:
            location = urlsplit(upstream.headers.get("location", ""))
            await upstream.aclose()
            if (
                location.scheme or location.netloc or not canonical_path(location.path)
                or credential in location.path
            ):
                raise HTTPException(502, "Workspace redirect unavailable")
            pairs = [(key, value) for key, value in parse_qsl(location.query, keep_blank_values=True)
                     if key.lower() != "token"]
            if any(credential in value for _, value in pairs):
                raise HTTPException(502, "Workspace redirect unavailable")
            target = location.path + ("?" + urlencode(pairs) if pairs else "")
            return RedirectResponse(target, status_code=upstream.status_code)
        headers = filtered_headers(upstream.headers, RESPONSE_HEADERS)
        headers = {key: value for key, value in headers.items() if credential not in value}
        if PREVIEW_PAGE_ROUTE.fullmatch(path) and "content-security-policy" in upstream.headers:
            headers["content-security-policy"] = upstream.headers["content-security-policy"]
        if (
            path.startswith("/plugins/crystalpilot/")
            and request.method == "GET"
            and upstream.headers.get("content-type", "").startswith("text/html")
        ):
            document = (await upstream.aread()).decode("utf-8")
            await upstream.aclose()
            nonce = secrets.token_urlsafe(24)
            content = re.sub(r"<script(?=[\s>])", f'<script nonce="{nonce}"', document)
            return HTMLResponse(content, headers={
                "content-security-policy": CSP.replace(
                    "script-src 'self'", f"script-src 'self' 'nonce-{nonce}'",
                ),
            })
        return ProxyResponse(upstream, headers, capture)

    @app.websocket("/{path:path}")
    async def websocket_proxy(ws: WebSocket, path: str):
        identity = session(ws)
        if identity is None:
            await ws.close(code=4401)
            return
        if (analytics is not None and identity["role"] == "trial"
                and not await run_in_threadpool(analytics.consented, identity["tenant"], analytics.notice_version)):
            await ws.close(code=4401)
            return
        if not same_origin(ws) or not WS_ROUTE.fullmatch("/" + path) or not canonical_path("/" + path):
            await ws.close(code=4403)
            return
        backend = settings.tenants[identity["tenant"]]
        query = clean_query(ws, readonly=identity["readonly"])
        query += ("&" if query else "") + urlencode({"token": backend.token})
        url = backend.url.replace("http", "ws", 1) + "/" + quote(path, safe="/") + "?" + query
        accepted = False
        try:
            options = {
                "extra_headers": {"Authorization": "Bearer " + backend.token},
                "open_timeout": 10, "close_timeout": 5, "ping_interval": 20, "ping_timeout": 20,
                "max_size": MAX_BODY_BYTES, "max_queue": 16, "logger": WS_LOGGER,
            }
            connection = (
                websocket_unix_connect(backend.uds, uri=url, **options) if backend.uds
                else websocket_connect(url, **options)
            )
            async with connection as upstream:
                await ws.accept()
                accepted = True

                async def from_browser():
                    while True:
                        message = await ws.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        if identity["readonly"]:
                            await ws.close(code=4403, reason="Read-only session")
                            return
                        payload = message.get("text")
                        if payload is None:
                            payload = message.get("bytes", b"")
                        if len(payload.encode() if isinstance(payload, str) else payload) > MAX_BODY_BYTES:
                            await ws.close(code=1009)
                            return
                        await upstream.send(payload)

                async def to_browser():
                    async for message in upstream:
                        if isinstance(message, str):
                            await ws.send_text(message)
                        else:
                            await ws.send_bytes(message)

                tasks = [asyncio.create_task(from_browser()), asyncio.create_task(to_browser())]
                try:
                    done, _ = await asyncio.wait(
                        tasks, timeout=max(0, identity["exp"] - time.time()),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        task.result()
                    if not done:
                        await ws.close(code=4401)
                finally:
                    for task in tasks:
                        task.cancel()
                    with anyio.CancelScope(shield=True):
                        await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            return
        except (OSError, TimeoutError, WebSocketException, WebSocketDisconnect):
            if ws.client_state.name != "DISCONNECTED" and ws.application_state.name != "DISCONNECTED":
                await ws.close(code=1011 if accepted else 4402)
        else:
            if ws.client_state.name != "DISCONNECTED" and ws.application_state.name != "DISCONNECTED":
                await ws.close(code=1000)

    return app


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--analytics-config", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8897)
    parser.add_argument("--forwarded-allow-ips", default="127.0.0.1")
    args = parser.parse_args()
    analytics = None
    if args.analytics_config is not None:
        from .analytics import Analytics

        analytics = Analytics(**json.loads(args.analytics_config.read_text()))
    uvicorn.run(create_app(args.config, analytics=analytics), host=args.host, port=args.port,
                access_log=False, forwarded_allow_ips=args.forwarded_allow_ips,
                ws_max_size=MAX_BODY_BYTES)


if __name__ == "__main__":
    main()
