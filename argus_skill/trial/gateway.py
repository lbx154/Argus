"""Private Copilot forwarding with persistent per-key quota and 10 slots."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
import portalocker
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.background import BackgroundTask
from starlette.staticfiles import StaticFiles

from . import CLIENT_MODEL, MAX_OUTPUT_TOKENS, MODEL
from .copilot import Copilot
from .responses import chat_chunks, completion, request_payload
from .secrets import Vault
from .store import Store, TrialError

MAX_BODY_BYTES = 2_000_000


class TrialFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.update({"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                 "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"})
        return response


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    key_file: Path
    model: str = CLIENT_MODEL
    timeout: float = 300
    site_dir: Path | None = None


class TextPart(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["text"]
    text: str


class FunctionCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    arguments: str


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    type: Literal["function"]
    function: FunctionCall


class CustomCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    input: str


class CustomToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    type: Literal["custom"]
    custom: CustomCall


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str | list[TextPart] | None = None
    refusal: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall | CustomToolCall] | None = None


class Snippy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool


class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: Literal["argus-trial"]
    messages: list[Message] = Field(min_length=1, max_length=4096)
    stream: bool = False
    stream_options: dict | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=MAX_OUTPUT_TOKENS)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=MAX_OUTPUT_TOKENS)
    tools: list[dict] | None = Field(default=None, max_length=256)
    tool_choice: str | dict | None = None
    parallel_tool_calls: bool | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh"] | None = None
    snippy: Snippy | None = None
    stop: str | list[str] | None = None
    # Accepted for OpenAI-compatible clients; never allow storage or n>1.
    store: Literal[False] | None = None
    n: Literal[1] | None = None


async def read_json(request: Request, limit: int) -> dict:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise TrialError(413, "request_too_large", "Trial request is too large.")
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        raise TrialError(400, "invalid_json", "Expected a JSON object.") from None
    if not isinstance(data, dict):
        raise TrialError(400, "invalid_json", "Expected a JSON object.")
    return data


def prepare(data: dict, model: str) -> tuple[dict, int]:
    try:
        parsed = Completion.model_validate(data)
    except ValidationError:
        # Pydantic errors include input values; do not reflect user input.
        raise TrialError(400, "invalid_request", "Unsupported trial request; use text/tool Chat Completions with model argus-trial.") from None
    if parsed.max_tokens is not None and parsed.max_completion_tokens is not None:
        raise TrialError(400, "invalid_request", "Specify only one output token limit.")
    for tool in parsed.tools or []:
        kind = tool.get("type")
        if kind not in ("function", "custom") or not isinstance(tool.get(kind), dict):
            raise TrialError(400, "invalid_tools", "Only local function and custom tools are supported.")
    if isinstance(parsed.tool_choice, dict) and parsed.tool_choice.get("type") not in ("function", "custom"):
        raise TrialError(400, "invalid_tools", "Only local function and custom tool choices are supported.")
    payload = parsed.model_dump(exclude_none=True)
    output = payload.pop("max_completion_tokens", None) or payload.pop("max_tokens", None) or MAX_OUTPUT_TOKENS
    payload.update(model=model, max_tokens=output)
    # Text-only requests: reserve UTF-8 bytes plus protocol/tool framing and
    # the enforced output maximum. Refund only from authoritative upstream
    # usage. This deliberately conservative estimate is not a tokenizer.
    try:
        payload = request_payload(payload)
    except (KeyError, TypeError):
        raise TrialError(400, "invalid_tools", "Invalid local function call or tool choice.") from None
    reserve = len(json.dumps(payload, ensure_ascii=False).encode()) + output
    reserve += 4096 + 64 * len(parsed.messages) + 256 * len(parsed.tools or [])
    return payload, reserve


def usage_total(data: dict) -> int | None:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if type(prompt) is not int or type(completion) is not int or min(prompt, completion) < 0:
        return None
    total = prompt + completion
    if usage.get("total_tokens", total) != total:
        return None
    # Cached input is already included in prompt_tokens; never double count it.
    return total


def create_app(settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        settings.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        settings.state_dir.chmod(0o700)
        # Exactly one gateway process owns this database. This prevents another
        # worker from refunding/recovering requests that are still executing.
        with portalocker.Lock(str(settings.state_dir / "gateway.lock"), timeout=0):
            vault = Vault(settings.key_file, settings.state_dir / "github-token.enc")
            store = Store(settings.state_dir / "usage.sqlite3")
            store.recover()
            async with httpx.AsyncClient(
                transport=transport, timeout=settings.timeout, follow_redirects=False,
                trust_env=False, limits=httpx.Limits(max_connections=11),
            ) as client:
                app.state.store, app.state.vault = store, vault
                app.state.copilot = Copilot(client, vault)
                yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(TrialError)
    async def trial_error(request, exc):
        headers = {"Cache-Control": "no-store"}
        if exc.status == 429:
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, exc.status, headers=headers)

    def trial_key(request: Request) -> str:
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer ") or len(authorization) > 256:
            raise TrialError(401, "invalid_trial_key", "Trial credential required.")
        return app.state.store.authenticate(authorization[7:])

    @app.get("/healthz")
    async def health():
        try:
            app.state.vault.read()
        except ValueError:
            return JSONResponse({"status": "awaiting_provider_login"}, 503)
        return {"status": "configured"}

    @app.get("/trial/status")
    async def status(request: Request):
        return JSONResponse(app.state.store.status(trial_key(request)), headers={"Cache-Control": "no-store"})

    @app.get("/v1/models")
    async def models(request: Request):
        trial_key(request)
        return {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "argus"}]}

    @app.post("/v1/chat/completions")
    async def complete(request: Request):
        key_id = trial_key(request)
        payload, reserve = prepare(await read_json(request, MAX_BODY_BYTES), settings.model)
        store, copilot = app.state.store, app.state.copilot
        request_id = store.reserve(key_id, reserve)
        actual: int | None = 0  # No generation has been submitted yet.
        response = None
        handed_off = False
        try:
            async with asyncio.timeout(settings.timeout):
                base_url, headers = await copilot.authorization()
                actual = None  # Ambiguous network failures must not refund usage.
                upstream = copilot.client.build_request("POST", base_url + "/responses", headers=headers, json=payload)
                response = await copilot.client.send(upstream, stream=True)
                if response.status_code != 200:
                    # Do not return upstream bodies, cookies, headers, or auth errors.
                    if response.status_code in {400, 401, 403, 404, 422, 429}:
                        actual = 0
                    raise TrialError(503 if response.status_code == 429 else 502, "provider_unavailable", "Trial provider could not complete the request.")
                if payload["stream"]:
                    handed_off = True
                    return StreamingResponse(
                        stream_response(response, request_id), media_type="text/event-stream",
                        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
                        background=BackgroundTask(finish_stream, response, request_id),
                    )
                body = await response.aread()
                data = json.loads(body)
                result = completion(data)
                actual = usage_total(result)
                if actual is None:
                    raise TrialError(502, "provider_usage_missing", "Provider did not report token usage; reservation retained.")
                return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except (httpx.HTTPError, TimeoutError):
            raise TrialError(502, "provider_connection_failed", "Trial provider connection failed.") from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise TrialError(502, "provider_protocol_error", "Invalid provider completion response.") from None
        finally:
            if not handed_off:
                store.settle(request_id, actual)
                if response is not None:
                    await response.aclose()

    async def finish_stream(response, request_id):
        # Also covers a disconnect before the async generator starts.
        app.state.store.settle(request_id, None)
        await response.aclose()

    async def stream_response(response, request_id):
        actual = None
        complete = False
        try:
            async with asyncio.timeout(settings.timeout):
                async for chunk in chat_chunks(response, MAX_BODY_BYTES):
                    if chunk is None:
                        if actual is None:
                            raise TrialError(502, "provider_usage_missing", "Provider did not report token usage; reservation retained.")
                        complete = True
                        app.state.store.settle(request_id, actual)
                        yield "data: [DONE]\n\n"
                        return
                    reported = usage_total(chunk)
                    if reported is not None:
                        actual = reported
                    yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError, AttributeError, TrialError) as exc:
            code = exc.code if isinstance(exc, TrialError) else "provider_stream_failed"
            yield "data: " + json.dumps({"error": {"code": code, "message": "Trial stream failed; retry after checking remaining quota."}}) + "\n\n"
        finally:
            # Disconnects and truncated streams retain reservations even if a
            # partial usage object was seen. Cancellation cannot skip settlement.
            app.state.store.settle(request_id, actual if complete else None)
            await response.aclose()

    if settings.site_dir is not None:
        app.mount("/trial/downloads", TrialFiles(directory=settings.site_dir / "trial/downloads"))
    return app
