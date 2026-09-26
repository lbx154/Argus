"""Bounded loopback capabilities tied to one host-owned provider call.

Domain services own dispatch and authorization scope. This transport owns only
an ephemeral bearer capability, finite JSON, byte limits and lifecycle cleanup.
"""
from __future__ import annotations

import hmac
import http.client
import json
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping

from .json_codec import loads_finite_json

MAX_REQUEST_BYTES = 65536
MAX_RESPONSE_BYTES = 393216
MAX_HANDLER_THREADS = 8
MAX_ACTIVE_OPERATIONS = 6  # Leave handler capacity for cancellation/control.


class ToolBridgeBusy(RuntimeError):
    pass


def require_fields(payload: dict[str, Any], allowed: set[str], *, required: set[str] | None = None) -> None:
    if set(payload) - allowed or (required or set()) - set(payload):
        raise ValueError("tool context is host-owned or request fields are incomplete")


class CallBoundBridge:
    def __init__(
        self, dispatch: Callable[[str, dict[str, Any]], dict[str, Any]], *,
        env_prefix: str, timeout_seconds: int = 130,
        on_close: Callable[[], None] | None = None,
        redact: Callable[[str], str] | None = None,
        cancel_operation: str = "cancel",
    ) -> None:
        if not re.fullmatch(r"ARGUS_PLUGIN_[A-Z_]+", env_prefix):
            raise ValueError("invalid role tool environment prefix")
        token = secrets.token_urlsafe(32)
        self._on_close = on_close
        error_text = redact or (lambda text: text)
        handler_slots = threading.BoundedSemaphore(MAX_HANDLER_THREADS)
        operation_slots = threading.BoundedSemaphore(MAX_ACTIVE_OPERATIONS)
        closed = self._closed = threading.Event()

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False
            request_queue_size = MAX_HANDLER_THREADS * 2

            def process_request(self, request: Any, client_address: Any) -> None:
                if not handler_slots.acquire(blocking=False):
                    try:
                        request.settimeout(0.05)
                        body = b'{"status":"busy","error":"role tool is busy; retry shortly"}'
                        request.sendall(
                            f"HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
                        )
                    except OSError:
                        pass
                    self.shutdown_request(request)
                    return
                try:
                    super().process_request(request, client_address)
                except BaseException:
                    handler_slots.release()
                    raise

            def process_request_thread(self, request: Any, client_address: Any) -> None:
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    handler_slots.release()

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(3)

            def handle(self) -> None:
                try:
                    super().handle()
                except (ConnectionError, TimeoutError):
                    # An aborted native tool or a stopped caller can close
                    # during header/body processing; this is normal teardown.
                    pass

            def log_message(self, *_args: Any) -> None:
                pass

            def do_POST(self) -> None:
                self.connection.settimeout(3)
                if not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}"):
                    self.send_error(403)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= MAX_REQUEST_BYTES or self.headers.get("Transfer-Encoding"):
                        raise ValueError("invalid tool request size")
                    raw = self.rfile.read(size)
                    if len(raw) != size:
                        raise ValueError("incomplete tool request")
                    payload = loads_finite_json(raw)
                    if not isinstance(payload, dict) or not re.fullmatch(r"/[a-z_]+", self.path):
                        raise ValueError("invalid tool request")
                    if closed.is_set():
                        raise ToolBridgeBusy("role turn ended")
                    operation = self.path[1:]
                    reserved = operation != cancel_operation
                    if reserved and not operation_slots.acquire(blocking=False):
                        raise ToolBridgeBusy("role tool is busy; retry shortly")
                    try:
                        result, status = dispatch(operation, payload), 200
                    finally:
                        if reserved:
                            operation_slots.release()
                except ToolBridgeBusy as exc:
                    result, status = {"status": "busy", "error": str(exc)}, 503
                except (KeyError, OSError, TypeError, ValueError) as exc:
                    result, status = {"status": "failed", "error": error_text(str(exc))}, 400
                except Exception:
                    result, status = {"status": "failed", "error": "role tool request failed"}, 500
                try:
                    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
                    if len(encoded) > MAX_RESPONSE_BYTES:
                        raise ValueError("oversized response")
                except (TypeError, ValueError):
                    encoded, status = b'{"status":"failed","error":"invalid tool response"}', 500
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                except OSError:
                    pass

        self.server = Server(("127.0.0.1", 0), Handler)
        self.environment = {
            f"{env_prefix}_PORT": str(self.server.server_address[1]),
            f"{env_prefix}_TOKEN": token,
            f"{env_prefix}_TIMEOUT": str(max(1, min(1815, timeout_seconds))),
        }
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    def __enter__(self) -> "CallBoundBridge":
        self.thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self._closed.set()
        try:
            if self._on_close is not None:
                self._on_close()
        finally:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=1)


def bridge_request(
    env_prefix: str, operation: str, payload: dict[str, Any], *, env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    source = os.environ if env is None else env
    port = int(source.get(f"{env_prefix}_PORT", "0"))
    token = source.get(f"{env_prefix}_TOKEN", "")
    if not 0 < port < 65536 or not token:
        raise ValueError("role tool requires a running Argus role turn")
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    if len(body) > MAX_REQUEST_BYTES:
        raise ValueError("role tool request oversized")
    timeout = max(1, min(1815, int(source.get(f"{env_prefix}_TIMEOUT", "130"))))
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("POST", f"/{operation}", body, {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("role tool response oversized")
        try:
            result = loads_finite_json(raw)
        except ValueError as exc:
            raise ValueError("role tool request rejected") from exc
        if not isinstance(result, dict):
            raise ValueError("invalid role tool response")
        if response.status != 200:
            raise ValueError(str(result.get("error") or "role tool request rejected"))
        return result
    except (BrokenPipeError, ConnectionResetError) as exc:
        # Admission can reject before a two-write HTTP client finishes sending
        # its body. Surface that reset as a retryable capacity/closing result.
        raise ValueError("role tool is busy or closing; retry during the active turn") from exc
    finally:
        connection.close()
