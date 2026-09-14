"""Bounded embeddings with explicit wire formats and a text-free cache."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from contextvars import copy_context
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence
from urllib.parse import urlsplit

from ..core.copilot_http import COPILOT_HEADERS
from ..core.json_codec import loads_finite_json
from ..core.run_gateway import current_run_interrupt_reason
from ..core.secret_guard import redact_secrets_text
from .failure_experience_index import EmbeddingUnavailable, _guard_recall_database, _normalized
from .recall_embedding import RecallEmbeddingConfig, _validated

_HTTP_SLOTS = threading.BoundedSemaphore(2)


class HttpEmbeddingAdapter:
    def __init__(self, state_root: Path, config: RecallEmbeddingConfig) -> None:
        self.config = config = _validated(config.to_dict())
        if not config.enabled:
            raise ValueError("HTTP embedding adapter requires explicit enabled configuration")
        self.dimensions = config.dimensions
        identity_fields = {"endpoint": config.endpoint, "model": config.model,
                           "dimensions": config.dimensions, "request_dimensions": config.request_dimensions}
        # Preserve existing OpenAI cache identities. Alternate response contracts
        # are explicitly separated, even when the endpoint/model are unchanged.
        if config.api_format != "openai":
            identity_fields["api_format"] = config.api_format
        identity = json.dumps(identity_fields, sort_keys=True)
        self.identifier = "http-semantic-v1:" + hashlib.sha256(identity.encode()).hexdigest()
        self.path = Path(state_root) / "embedding" / "cache.sqlite3"
        _guard_recall_database(self.path)
        _guard_recall_database(self.path.with_name("usage.sqlite3"))
        self._local = threading.local()

    @contextmanager
    def batch(self) -> Iterator[None]:
        if getattr(self._local, "budget", None) is not None:
            yield
            return
        self._local.budget = {"calls": 0, "deadline": time.monotonic() + self.config.batch_timeout_seconds}
        try:
            yield
        finally:
            self._local.budget = None

    def _open(self) -> sqlite3.Connection:
        _guard_recall_database(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _guard_recall_database(self.path)
        db = sqlite3.connect(self.path, timeout=0.2)
        try:
            db.execute("PRAGMA auto_vacuum=FULL")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("CREATE TABLE IF NOT EXISTS vectors (encoder TEXT NOT NULL, input_digest TEXT NOT NULL, "
                       "vector TEXT NOT NULL, used_at REAL NOT NULL, PRIMARY KEY (encoder, input_digest))")
            db.commit()
            return db
        except BaseException:
            db.close()
            raise

    def _cached(self, digest: str) -> list[float] | None:
        with closing(self._open()) as db, db:
            row = db.execute("SELECT vector FROM vectors WHERE encoder=? AND input_digest=?", (self.identifier, digest)).fetchone()
            if row is None:
                return None
            try:
                result = _normalized(loads_finite_json(row[0]), self.dimensions)
            except (TypeError, ValueError, OverflowError):
                db.execute("DELETE FROM vectors WHERE encoder=? AND input_digest=?", (self.identifier, digest))
                return None
            db.execute("UPDATE vectors SET used_at=? WHERE encoder=? AND input_digest=?", (time.time(), self.identifier, digest))
            return result

    def _reserve(self, size: int) -> None:
        day = datetime.now(timezone.utc).date().isoformat()
        # A disposable vector cache must never own durable spend authority.
        usage_path = self.path.with_name("usage.sqlite3")
        _guard_recall_database(usage_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _guard_recall_database(usage_path)
        with closing(sqlite3.connect(usage_path, timeout=0.2)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS budget (day TEXT PRIMARY KEY, requests INTEGER NOT NULL, input_bytes INTEGER NOT NULL)")
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT requests,input_bytes FROM budget WHERE day=?", (day,)).fetchone()
            calls, used = row if row else (0, 0)
            if type(calls) is not int or type(used) is not int or calls < 0 or used < 0:
                raise EmbeddingUnavailable("embedding usage state is invalid")
            if calls >= self.config.daily_request_budget or used + size > self.config.daily_input_bytes_budget:
                raise EmbeddingUnavailable("embedding daily request/input budget exhausted")
            db.execute("INSERT OR REPLACE INTO budget VALUES (?,?,?)", (day, calls + 1, used + size))
            # Reservations include failures and timeouts. Never refund an
            # ambiguous provider request or reset budgets when the model changes.
            db.execute("DELETE FROM budget WHERE day != ? AND day NOT IN "
                       "(SELECT day FROM budget WHERE day != ? ORDER BY day DESC LIMIT 6)", (day, day))

    def _remember(self, digest: str, vector: list[float]) -> None:
        encoded = json.dumps(vector, allow_nan=False, separators=(",", ":"))
        with closing(self._open()) as db, db:
            db.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?,?)", (self.identifier, digest, encoded, time.time()))
            db.execute("DELETE FROM vectors WHERE rowid NOT IN (SELECT rowid FROM vectors ORDER BY used_at DESC LIMIT ?)",
                       (self.config.cache_entries,))
            rows = list(db.execute("SELECT rowid,length(vector) FROM vectors ORDER BY used_at DESC"))
            used = 0
            for rowid, size in rows:
                used += size
                if used > self.config.cache_max_bytes:
                    db.execute("DELETE FROM vectors WHERE rowid=?", (rowid,))

    def _post(self, body: bytes, credential: str, timeout: float) -> bytes:
        if current_run_interrupt_reason():
            raise EmbeddingUnavailable("embedding request cancelled")
        if not _HTTP_SLOTS.acquire(blocking=False):
            raise EmbeddingUnavailable("embedding HTTP capacity is busy")
        target = urlsplit(self.config.endpoint)
        assert target.hostname is not None  # Checked by configuration validation.
        connection_type = http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
        try:
            connection = connection_type(target.hostname, target.port, timeout=timeout)
        except Exception:
            _HTTP_SLOTS.release()
            raise EmbeddingUnavailable("embedding HTTP transport unavailable") from None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.config.api_format == "copilot":
            headers.update(COPILOT_HEADERS)
            headers["X-Initiator"] = "agent"
        if credential:
            headers["Authorization"] = "Bearer " + credential
        done = threading.Event()
        abandoned = threading.Event()
        result: list[bytes] = []
        active_socket: list[socket.socket] = []
        deadline = time.monotonic() + timeout

        def send() -> None:
            try:
                if abandoned.is_set() or current_run_interrupt_reason() or time.monotonic() >= deadline:
                    return
                connection.connect()
                if connection.sock is not None:
                    active_socket.append(connection.sock)
                # DNS/connect may finish after the caller has returned. Never
                # turn that late connection into a new provider request.
                if abandoned.is_set() or current_run_interrupt_reason() or time.monotonic() >= deadline:
                    return
                connection.request("POST", target.path or "/", body, headers)
                response = connection.getresponse()
                if response.status != 200:
                    return  # No redirects and no provider error bodies enter logs.
                length = response.getheader("Content-Length")
                if length is not None and (not length.isdecimal() or int(length) > self.config.max_response_bytes):
                    return
                chunks: list[bytes] = []
                size = 0
                while time.monotonic() < deadline:
                    chunk = response.read1(min(8192, self.config.max_response_bytes + 1 - size))
                    if not chunk:
                        result.append(b"".join(chunks))
                        return
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > self.config.max_response_bytes:
                        return
            except Exception:
                pass  # Credentials, endpoint details and response bodies stay private.
            finally:
                connection.close()
                _HTTP_SLOTS.release()
                done.set()

        context = copy_context()
        worker = threading.Thread(target=lambda: context.run(send), name="argus-embedding-http", daemon=True)
        try:
            worker.start()
        except RuntimeError:
            _HTTP_SLOTS.release()
            connection.close()
            raise EmbeddingUnavailable("embedding HTTP worker unavailable") from None
        failure = ""
        while not done.is_set():
            if current_run_interrupt_reason():
                failure = "embedding request cancelled"
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "embedding HTTP deadline exceeded"
                break
            done.wait(min(0.05, remaining))
        if not failure and current_run_interrupt_reason():
            failure = "embedding request cancelled"
        if failure:
            abandoned.set()
            # HTTPResponse may retain an fd after HTTPConnection.close().
            # Shutdown wakes that reader as well, including slow response bodies.
            for stream in tuple(active_socket):
                try:
                    stream.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            # The worker owns close/release. Closing its HTTPResponse or joining
            # here could wait on the same blocked reader we are interrupting.
            raise EmbeddingUnavailable(failure)
        if not result:
            raise EmbeddingUnavailable("embedding HTTP response unavailable or oversized")
        return result[0]

    def embed(self, text: str) -> Sequence[float]:
        with self.batch():
            try:
                return self._embed(text)
            except EmbeddingUnavailable:
                raise
            except Exception:
                raise EmbeddingUnavailable("embedding service or cache unavailable") from None

    def redact(self, text: str) -> str:
        credential = os.environ.get(self.config.credential_env, "") if self.config.credential_env else ""
        return redact_secrets_text(text, known_values=(credential,) if credential else ())

    def _embed(self, text: str) -> Sequence[float]:
        if current_run_interrupt_reason():
            raise EmbeddingUnavailable("embedding request cancelled")
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > self.config.max_input_bytes:
            raise EmbeddingUnavailable("embedding input is empty or exceeds its byte limit")
        credential = os.environ.get(self.config.credential_env, "") if self.config.credential_env else ""
        if self.config.credential_env and (not credential or len(credential) > 8192 or "\n" in credential or "\r" in credential):
            raise EmbeddingUnavailable("embedding credential unavailable")
        text = self.redact(text)
        size = len(text.encode("utf-8"))
        if not text.strip() or size > self.config.max_input_bytes:
            raise EmbeddingUnavailable("embedding redacted input exceeds its byte limit")
        digest = hashlib.sha256(text.encode()).hexdigest()
        cached = self._cached(digest)
        if cached is not None:
            return cached
        budget = self._local.budget
        remaining = budget["deadline"] - time.monotonic()
        if budget["calls"] >= self.config.max_requests_per_batch or remaining <= 0:
            raise EmbeddingUnavailable("embedding batch request/time budget exhausted")
        payload: dict[str, Any] = (
            {"input": [text], "model": self.config.model}
            if self.config.api_format == "copilot"
            else {"input": text, "model": self.config.model, "encoding_format": "float"}
        )
        if self.config.request_dimensions:
            payload["dimensions"] = self.dimensions
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        if len(body) > self.config.max_input_bytes + 2048:
            raise EmbeddingUnavailable("embedding request exceeds its byte limit")
        if current_run_interrupt_reason():
            raise EmbeddingUnavailable("embedding request cancelled")
        self._reserve(size)
        budget["calls"] += 1
        raw = self._post(body, credential, min(self.config.timeout_seconds, remaining))
        value = loads_finite_json(raw)
        if not isinstance(value, dict):
            raise EmbeddingUnavailable("embedding response is not an object")
        # Copilot CAPI omits model identity in successful embedding responses.
        # Its opt-in format binds identity to the authenticated request; any
        # explicit conflicting identity is still rejected. OpenAI stays strict.
        if value.get("model") != self.config.model and (
            self.config.api_format != "copilot" or "model" in value
        ):
            raise EmbeddingUnavailable("embedding response model does not match configured identity")
        data = value.get("data")
        if (not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict)
                or type(data[0].get("index")) is not int or data[0].get("index") != 0
                or not isinstance(data[0].get("embedding"), list)):
            raise EmbeddingUnavailable("embedding response does not contain one indexed vector")
        vector = _normalized(data[0]["embedding"], self.dimensions)
        if not any(vector):
            raise EmbeddingUnavailable("embedding response has a zero vector")
        self._remember(digest, vector)
        return vector


__all__ = ["HttpEmbeddingAdapter"]
