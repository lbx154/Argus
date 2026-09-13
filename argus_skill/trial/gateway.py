"""Private Copilot forwarding with persistent per-key quota and 10 slots."""
from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal

import httpx
import portalocker
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.background import BackgroundTask
from starlette.staticfiles import StaticFiles

from . import CLIENT_MODEL, MAX_CONCURRENCY, MAX_OUTPUT_TOKENS, MODEL, TOKEN_LIMIT
from .copilot import Copilot
from .gateway_accounting import GatewayAccounting, RequestMonitor
from .gateway_observation import GatewayAttempt, GatewayStreamingResponse
from .model_catalog import configured_model_ids, select_model
from .responses import chat_chunks, completion, request_payload
from .secrets import Vault
from .store import Store, TrialError

MAX_BODY_BYTES = 2_000_000


def provider_retry_after(value: str | None) -> int:
    """Normalize the provider's retry delay without forwarding other headers."""
    value = (value or "").strip()
    if not value or len(value) > 64:
        return 60
    if value.isascii() and value.isdecimal():
        return max(1, int(value)) if len(value) <= 10 else 60
    try:
        deadline = parsedate_to_datetime(value)
        if deadline.tzinfo is not None:
            return max(1, math.ceil(deadline.timestamp() - time.time()))
    except (ValueError, TypeError, OverflowError):
        pass
    return 60


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
    token_limit: int | None = TOKEN_LIMIT
    models: tuple[str, ...] | None = None


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


class SimpleResponseFormat(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["text", "json_object"]


class JsonSchemaOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    schema_: dict = Field(alias="schema")
    strict: bool | None = None
    description: str | None = None


class JsonSchemaResponseFormat(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["json_schema"]
    json_schema: JsonSchemaOutput


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
    response_format: SimpleResponseFormat | JsonSchemaResponseFormat | None = None
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


def prepare(data: dict, model: str, *, models: tuple[str, ...] | None = None) -> tuple[dict, int]:
    try:
        selected = select_model(data.get("model"), model, configured_model_ids(model, models))
    except ValueError:
        raise TrialError(400, "model_not_enabled", "The selected model is not enabled for this trial.") from None
    data = {**data, "model": MODEL}
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
    payload = parsed.model_dump(exclude_none=True, by_alias=True)
    output = payload.pop("max_completion_tokens", None) or payload.pop("max_tokens", None) or MAX_OUTPUT_TOKENS
    payload.update(model=selected, max_tokens=output)
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
    catalog = configured_model_ids(settings.model, settings.models)
    @asynccontextmanager
    async def lifespan(app):
        settings.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        settings.state_dir.chmod(0o700)
        # Exactly one gateway process owns this database. This prevents another
        # worker from refunding/recovering requests that are still executing.
        with portalocker.Lock(str(settings.state_dir / "gateway.lock"), timeout=0):
            vault = Vault(settings.key_file, settings.state_dir / "github-token.enc")
            store = Store(settings.state_dir / "usage.sqlite3", token_limit=settings.token_limit)
            store.recover()
            async with httpx.AsyncClient(
                transport=transport, timeout=settings.timeout, follow_redirects=False,
                trust_env=False, limits=httpx.Limits(max_connections=11),
            ) as client:
                app.state.store, app.state.vault = store, vault
                app.state.copilot = Copilot(client, vault)
                app.state.request_slots = asyncio.Semaphore(MAX_CONCURRENCY)
                app.state.accounting = GatewayAccounting(store, MAX_CONCURRENCY)
                try:
                    yield
                finally:
                    # Retain the gateway's process lock until every owned
                    # worker and late-reservation cleanup has finished.
                    await app.state.accounting.close()

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
        if app.state.accounting.failure_count:
            return JSONResponse({"status": "billing_recovery_required"}, 503)
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
        return {"object": "list", "data": [{"id": name, "object": "model", "owned_by": "argus"} for name in (MODEL, *catalog)]}

    @app.post("/v1/chat/completions")
    async def complete(request: Request):
        key_id = trial_key(request)
        request_data = await read_json(request, MAX_BODY_BYTES)
        payload, reserve = prepare(request_data, settings.model, models=catalog)
        response_model = MODEL if request_data.get("model") == MODEL else payload["model"]
        store, copilot = app.state.store, app.state.copilot
        accounting = app.state.accounting
        monitor = RequestMonitor(request, accounting)
        attempt = GatewayAttempt(store, key_id, reserve, dispatch=accounting.observations.submit)
        slot_acquired = False

        def release_slot():
            nonlocal slot_acquired
            if slot_acquired:
                app.state.request_slots.release()
                slot_acquired = False

        request_id = None
        actual: int | None = 0  # No generation has been submitted yet.
        response = None
        handed_off = False
        lease = None
        detached = False
        successful_result = False
        terminal: dict = {}

        def select_outcome(outcome, **fields):
            terminal.clear()
            terminal.update(outcome=outcome, **fields)

        billing_deadline = None
        try:
            last_tpm_error = None
            try:
                # Slot and TPM waiting share one admission deadline. Keep TPM
                # waiters inside the existing slot limit and charge only once
                # reservation succeeds. Poll so disconnected clients leave.
                async with asyncio.timeout(settings.timeout):
                    while not slot_acquired:
                        await monitor.check()
                        try:
                            async with asyncio.timeout(1):
                                if app.state.request_slots.locked():
                                    attempt.waiting_for_slot()
                                await app.state.request_slots.acquire()
                                slot_acquired = True
                                attempt.acquired_slot()
                        except TimeoutError:
                            continue
                    lease = await accounting.acquire(monitor)
                    while True:
                        await monitor.check()
                        try:
                            request_id = await monitor.wait(lease.reserve(key_id, reserve))
                            lease.request_id = request_id
                            attempt.admitted(request_id)
                            break
                        except TrialError as exc:
                            if exc.code != "trial_tpm_exceeded":
                                raise
                            last_tpm_error = exc
                            attempt.waiting_for_tpm()
                        await asyncio.sleep(1)
            except TimeoutError:
                detached = True
                if last_tpm_error is not None:
                    raise last_tpm_error from None
                raise TrialError(429, "trial_busy", "Model request queue timed out; try again later.") from None
            async with asyncio.timeout(settings.timeout) as provider_timeout:
                billing_deadline = provider_timeout.when()
                await monitor.check()
                monitor.start_disconnect_watch()
                base_url, headers = await copilot.authorization()
                await monitor.check()
                await monitor.wait(lease.submit())
                await monitor.check()
                actual = None  # Ambiguous network failures must not refund usage.
                lease.actual = actual
                upstream = copilot.client.build_request("POST", base_url + "/responses", headers=headers, json=payload)
                attempt.upstream()
                response = await copilot.client.send(upstream, stream=True)
                lease.response = response
                await monitor.check()
                attempt.upstream_response(response.status_code)
                if response.status_code != 200:
                    # Keep the rate-limit signal, without forwarding provider content.
                    if response.status_code in {400, 401, 403, 404, 422, 429}:
                        actual = 0
                        lease.actual = actual
                    if response.status_code in {400, 422}:
                        raise TrialError(400, "provider_rejected_request", "Trial provider rejected the request format; retrying unchanged will not help.")
                    if response.status_code == 429:
                        retry_after = provider_retry_after(response.headers.get("retry-after"))
                        raise TrialError(429, "provider_rate_limited",
                                         f"Model provider is rate limiting requests. Retry after {retry_after} seconds.",
                                         retry_after=retry_after)
                    raise TrialError(502, "provider_unavailable", "Trial provider could not complete the request.")
                if payload["stream"]:
                    # StreamingResponse owns downstream disconnect handling
                    # after handoff. Stop our upstream-header watcher synchronously;
                    # no await may split handed_off from response ownership.
                    monitor.stop_disconnect_watch()
                    handed_off = True
                    attempt.streaming()
                    return GatewayStreamingResponse(
                        stream_response(response, lease, release_slot, response_model, attempt), media_type="text/event-stream",
                        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
                        background=BackgroundTask(finish_stream, lease, release_slot, attempt),
                    )
                body = await response.aread()
                data = json.loads(body)
                result = completion(data, model_id=response_model)
                actual = usage_total(result)
                lease.actual = actual
                if actual is None:
                    raise TrialError(502, "provider_usage_missing", "Provider did not report token usage; reservation retained.")
                # Preserve authoritative usage already in the completed body
                # before observing a cancellation swallowed by its transport.
                await monitor.check()
                result_response = JSONResponse(result, headers={"Cache-Control": "no-store"})
                successful_result = True
                return result_response
        except TrialError as exc:
            detached = detached or exc.code == "client_disconnected"
            outcome = "disconnected" if exc.code == "client_disconnected" else "rejected" if request_id is None else "error"
            select_outcome(outcome, selected_status=exc.status,
                           error_code=exc.code, retry_after=exc.retry_after if exc.status == 429 else None)
            raise
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            detached = isinstance(exc, (TimeoutError, httpx.TimeoutException))
            select_outcome("error", selected_status=502, error_code="provider_connection_failed")
            raise TrialError(502, "provider_connection_failed", "Trial provider connection failed.") from None
        except (ValueError, TypeError, KeyError, AttributeError):
            select_outcome("error", selected_status=502, error_code="provider_protocol_error")
            raise TrialError(502, "provider_protocol_error", "Invalid provider completion response.") from None
        except asyncio.CancelledError:
            detached = True
            if monitor.disconnect_seen:
                select_outcome("disconnected", selected_status=499, error_code="client_disconnected")
                raise TrialError(499, "client_disconnected", "Client disconnected during model request.") from None
            select_outcome("cancelled")
            raise
        except sqlite3.Error:
            select_outcome("error", selected_status=503, error_code="billing_unavailable")
            raise TrialError(503, "billing_unavailable", "Trial billing is temporarily unavailable.") from None
        finally:
            if not handed_off:
                release_slot()
                try:
                    if lease is not None:
                        lease.actual = actual
                        lease.finish()
                        if not detached and not monitor.interrupted:
                            try:
                                deadline = billing_deadline or asyncio.get_running_loop().time() + settings.timeout
                                async with asyncio.timeout_at(deadline):
                                    # Lease cleanup owns and logs response-close
                                    # errors without replacing the HTTP result.
                                    await lease.wait_settled(monitor)
                            except TimeoutError:
                                select_outcome("error", selected_status=503, error_code="billing_unavailable")
                                raise TrialError(503, "billing_unavailable", "Trial billing is still finalizing; reservation retained.") from None
                            except TrialError as exc:
                                select_outcome("disconnected" if exc.code == "client_disconnected" else "error",
                                               selected_status=exc.status, error_code=exc.code)
                                raise
                            except asyncio.CancelledError:
                                select_outcome("cancelled")
                                raise
                finally:
                    try:
                        if not terminal:
                            if successful_result:
                                select_outcome("completed", selected_status=200)
                            else:
                                select_outcome("unknown")
                        attempt.finish(**terminal)
                    finally:
                        monitor.done()

    async def finish_stream(lease, release_slot, attempt):
        # Also covers a disconnect before the async generator starts.
        try:
            attempt.finish("interrupted")
            release_slot()
            lease.finish()
        finally:
            lease.monitor.done()
        # This may run while ASGI send is unwinding a disconnect, before the
        # generator ever starts. Register cleanup before yielding and never
        # hold that cancelled request on a database write.
        await asyncio.sleep(0)

    async def stream_response(response, lease, release_slot, model_id, attempt):
        actual = None
        complete = False
        try:
            async with asyncio.timeout(settings.timeout):
                async for chunk in chat_chunks(response, MAX_BODY_BYTES, model_id=model_id):
                    if chunk is None:
                        if actual is None:
                            raise TrialError(502, "provider_usage_missing", "Provider did not report token usage; reservation retained.")
                        complete = True
                        lease.actual = actual
                        await lease.wait_settled(lease.monitor)
                        attempt.finish("completed")
                        yield "data: [DONE]\n\n"
                        return
                    reported = usage_total(chunk)
                    if reported is not None:
                        actual = reported
                    yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
        except (httpx.HTTPError, TimeoutError, OSError, ValueError, TypeError, KeyError, AttributeError, TrialError) as exc:
            # The error response is final. Its termination must not wait for
            # a SQLite writer, regardless of which upstream failure caused it.
            lease.monitor.interrupted = True
            code = exc.code if isinstance(exc, TrialError) else "provider_stream_failed"
            attempt.finish("stream_error", error_code=code)
            yield "data: " + json.dumps({"error": {"code": code, "message": "Trial stream failed; retry after checking remaining quota."}}) + "\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            lease.monitor.interrupted = True
            attempt.finish("cancelled")
            raise
        finally:
            attempt.finish("interrupted")
            # Disconnects and truncated streams retain reservations even if a
            # partial usage object was seen. Cancellation cannot skip settlement.
            release_slot()
            if not complete:
                lease.actual = None
            lease.finish()
            if not lease.monitor.interrupted:
                await lease.wait_settled(lease.monitor)

    if settings.site_dir is not None:
        app.mount("/trial/downloads", TrialFiles(directory=settings.site_dir / "trial/downloads"))
    return app
