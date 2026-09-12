"""Consent-gated request/observable-response records in the operator index.

``Capture`` commits the invocation immediately; feed never performs I/O or waits
for a worker. Only finish stores the bounded, filtered response. An unfinished
invocation remains incomplete after restart. HTTP completion is NOT mission
completion. Trace is a current, separately bounded project view, not evidence
that every source row was caused by this invocation.

List returns metadata summaries; get returns that metadata plus input, frames,
result and (by default) project_trace. Neither API reads another tenant's row.
Retention affects only interactions, never project files or consent receipts.
"""
from __future__ import annotations

import json
import re
import threading
import time

from argus_skill.core.secret_guard import redact_secrets_record
from argus_skill.trial.analytics import AnalyticsError, _safe_row, _sanitize

MAX_BYTES = 1024 * 1024
MAX_FRAMES = 2000
_INPUT_FIELDS = frozenset(
    ("text", "attachments", "route_override", "command", "name", "resources")
)
_PUBLIC_FIELDS = frozenset("""
    kind reply text role type label phase fragment_mode message_id error detail code
    result item items id sid task_id task_ids item_id item_ids backlog_id backlog_ids
    root_task_id dep_task_ids tasks status title objective summary success resolved
    dispatch_state duplicate continuous daemon_alive daemon_control_available
    delivery delivery_id targets path filename mime size size_bytes artifacts
    created ts timestamp started_ts finished_ts
""".split())
_SID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
_PRIVATE_CHANNELS = frozenset({
    "analysis", "thinking", "thoughts", "private", "internal",
    "agent.io.stream", "role.session.turn", "provider_stream", "raw_provider_stream",
})
_SUMMARY = (
    "id,tenant_id,sid,notice_version,path,created_at,finished_at,status_code,"
    "content_type,state,outcome,input_truncated,response_truncated,error_code,"
    "suppressed_frames,task_id,task_accepted"
)


def _require_consent(analytics, tenant_id):
    if not analytics.consented(tenant_id, analytics.notice_version):
        raise AnalyticsError(403, "consent_required")


def _now(analytics):
    return getattr(analytics, "clock", time.time)()


def _schema(analytics):
    with analytics._db() as db:
        # Serialize schema discovery and DDL across threads/processes, avoiding
        # deferred-transaction read-to-write upgrades during concurrent startup.
        db.execute("BEGIN IMMEDIATE")
        db.execute("""
            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL, sid TEXT, notice_version TEXT NOT NULL,
                path TEXT NOT NULL, created_at REAL NOT NULL, finished_at REAL,
                input TEXT NOT NULL, input_truncated INTEGER NOT NULL,
                status_code INTEGER, content_type TEXT,
                state TEXT NOT NULL DEFAULT 'incomplete',
                outcome TEXT NOT NULL DEFAULT 'incomplete',
                response_truncated INTEGER NOT NULL DEFAULT 0,
                error_code TEXT, suppressed_frames INTEGER NOT NULL DEFAULT 0,
                frames TEXT NOT NULL DEFAULT '[]', result TEXT,
                task_id TEXT, task_accepted INTEGER NOT NULL DEFAULT 0
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS interactions_tenant_created
            ON interactions(tenant_id,notice_version,created_at)
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS interactions_created ON interactions(created_at)
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(interactions)")}
        migrate = "task_id" not in columns or "task_accepted" not in columns
        if "task_id" not in columns:
            db.execute("ALTER TABLE interactions ADD COLUMN task_id TEXT")
        if "task_accepted" not in columns:
            db.execute("ALTER TABLE interactions ADD COLUMN task_accepted INTEGER NOT NULL DEFAULT 0")
        if migrate:
            # Rebuild metadata from already consented, retained response copies only.
            # No source files or pre-collection responses are reconstructed.
            rows = db.execute("SELECT id,path,status_code,state,outcome,error_code,response_truncated,"
                              "substr(result,1,?) AS result FROM interactions", (MAX_BYTES + 1,))
            for row in rows:
                try:
                    result = _decode(row["result"]) if row["result"] and len(row["result"]) <= MAX_BYTES else None
                except (ValueError, TypeError, RecursionError):
                    result = None
                task_id, _ = _task_reference(result)
                accepted = _accepted_task(row["path"], result, row["status_code"], row["state"],
                                          row["error_code"], row["response_truncated"])
                outcome = "background_task_dispatched" if accepted else (
                    "task_dispatch_unverified" if row["outcome"] == "background_task_dispatched" else row["outcome"]
                )
                db.execute("UPDATE interactions SET task_id=?,task_accepted=?,outcome=? WHERE id=?",
                           (task_id, int(accepted), outcome, row["id"]))
        db.execute("CREATE INDEX IF NOT EXISTS interactions_task_acceptance "
                   "ON interactions(tenant_id,notice_version,task_accepted,finished_at)")


def _dumps(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _bounded_input(input_data):
    """Bound before recursive redaction; preserve user prose about reasoning."""
    truncated = False
    if isinstance(input_data, (str, bytes, bytearray)):
        if len(input_data) > MAX_BYTES:
            return {"capture_error": "input_truncated"}, True
        try:
            input_data = json.loads(input_data)
        except (ValueError, RecursionError):
            return {"capture_error": "invalid_input_json"}, False
    if not isinstance(input_data, dict):
        return {"capture_error": "invalid_input"}, False

    budget = MAX_BYTES - 1024
    nodes = 10000

    def copy(value, depth=0):
        nonlocal budget, nodes, truncated
        nodes -= 1
        if depth > 20 or budget < 32 or nodes < 0:
            truncated = True
            return None
        budget -= 16
        if isinstance(value, str):
            # Worst-case JSON escaping is six bytes per character. The bounded
            # prefix also prevents allocating an unbounded encoded string.
            prefix = value[:budget]
            encoded = _dumps(prefix)
            if len(encoded) > budget:
                prefix = prefix[:max(0, (budget - 2) // 6)]
                encoded = _dumps(prefix)
            truncated |= len(prefix) < len(value)
            budget -= len(encoded)
            return prefix
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if budget < 64 or nodes < 0:
                    truncated = True
                    break
                if isinstance(key, str):
                    result[copy(key, depth + 1)] = copy(item, depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            result = []
            for item in value:
                if budget < 32 or nodes < 0:
                    truncated = True
                    break
                result.append(copy(item, depth + 1))
            return result
        if value is None or isinstance(value, (bool, float)):
            return value
        if isinstance(value, int):
            if value.bit_length() <= 64:
                return value
            truncated = True
        return None

    selected = {key: input_data[key] for key in _INPUT_FIELDS if key in input_data}
    clean = _sanitize(redact_secrets_record(copy(selected)))
    # Redaction can expand short secret values into redaction markers.
    if len(_dumps(clean)) > MAX_BYTES:
        return {"capture_error": "input_truncated"}, True
    return clean, truncated


def _public_result(value):
    if not isinstance(value, dict):
        return None

    def private_channel(item, depth=0):
        if depth > 30:
            return True
        if isinstance(item, dict):
            return any(
                str(key).lower() in {"analysis", "thinking", "thoughts", "private", "internal"}
                or (key in {"type", "kind", "role", "channel", "field"}
                    and isinstance(val, str) and val.lower() in _PRIVATE_CHANNELS)
                or private_channel(val, depth + 1)
                for key, val in item.items()
            )
        if isinstance(item, list):
            return any(private_channel(val, depth + 1) for val in item)
        if isinstance(item, str):
            if re.search(r"(?i)<thinking>", item):
                return True
            if item.lstrip().startswith(("{", "[")):
                try:
                    return private_channel(_decode(item), depth + 1)
                except (ValueError, RecursionError):
                    pass
        return False

    # Check the original object, including nested/encoded private channels,
    # before projection can erase the identifying marker.
    if private_channel(value) or _safe_row({"result": value}) is None:
        return None

    def select(item):
        if isinstance(item, dict):
            return {key: select(val) for key, val in item.items() if key in _PUBLIC_FIELDS}
        if isinstance(item, list):
            return [select(val) for val in item]
        return item

    safe = _safe_row({"result": select(value)})
    return safe["result"] if safe else None


def _decode(raw):
    return json.loads(raw, parse_constant=lambda _: None)


def _valid_task_id(value):
    return (isinstance(value, str) and _TASK_ID.fullmatch(value) is not None
            and value == _sanitize(redact_secrets_record(value)))


def _task_reference(result):
    """Only explicit, consistent task identifiers in the public response schema."""
    if not isinstance(result, dict):
        return None, []
    values = [(key, result[key]) for key in ("item_id", "task_id") if key in result]
    if isinstance(result.get("item"), dict) and "id" in result["item"]:
        values.append(("item.id", result["item"]["id"]))
    if not values:
        return None, []
    if any(not _valid_task_id(value) for _, value in values):
        return None, ["invalid_task_id"]
    if len({value for _, value in values}) != 1:
        return None, ["conflicting_task_ids"]
    return values[0][1], [key for key, _ in values]


def _accepted_task(path, result, status, state, error, truncated):
    task_id, _ = _task_reference(result)
    if (task_id is None or state != "response_complete" or error or truncated
            or status is None or not 200 <= status < 300):
        return False
    if result.get("kind") in {"chat", "error"} or result.get("error") or result.get("success") is False:
        return False
    if result.get("duplicate") or result.get("dispatch_state") in {
        "already_queued", "planner_pending", "failed", "rejected",
    }:
        return False
    item = result.get("item") if isinstance(result.get("item"), dict) else {}
    if item.get("status") in {"failed", "cancelled", "canceled", "rejected", "orphaned"}:
        return False
    return result.get("kind") == "task" or path.endswith("/tasks")


def _response(raw, is_sse, truncated):
    frames, result = [], None
    error_code, suppressed = None, 0
    if not is_sse:
        if truncated:
            return frames, result, "response_truncated", suppressed
        try:
            value = _decode(raw)
            result = _public_result(value)
        except (ValueError, TypeError, RecursionError):
            return frames, None, "invalid_response_json", suppressed
        if not result:
            return frames, None, "missing_public_result", 1
        if result.get("kind") == "error" or result.get("error") or result.get("success") is False:
            error_code = "response_error"
        return frames, result, error_code, suppressed

    # Only terminated SSE events count. A partial final frame is never invented.
    blocks = re.split(rb"\r\n\r\n|\n\n|\r\r", raw)
    if blocks[-1].strip():
        error_code = "incomplete_sse_frame"
    blocks.pop()
    for index, block in enumerate(blocks):
        if index >= MAX_FRAMES:
            error_code = error_code or "frame_limit"
            break
        data = [
            line[5:].lstrip(b" ") for line in block.splitlines() if line.startswith(b"data:")
        ]
        if not data:
            continue
        try:
            value = _decode(b"\n".join(data))
            if not isinstance(value, dict) or value.get("type") not in {
                "phase", "delta", "done", "error",
            }:
                suppressed += 1
                continue
            clean = _public_result(value)
        except (ValueError, TypeError, RecursionError):
            error_code = error_code or "invalid_sse_json"
            continue
        if not clean:
            suppressed += 1
            continue
        frames.append(clean)
        if clean["type"] == "error":
            error_code = "stream_error"
        elif clean["type"] == "done":
            candidate = clean.get("result")
            if isinstance(candidate, dict) and candidate:
                result = candidate
                if (result.get("kind") == "error" or result.get("error")
                        or result.get("success") is False):
                    error_code = "stream_error"
            else:
                error_code = error_code or "missing_public_result"
    if truncated:
        error_code = error_code or "response_truncated"
    if result is None:
        error_code = error_code or "missing_final_result"
    return frames, result, error_code, suppressed


class Capture:
    """One invocation, safe for feed/finish from different proxy threads."""

    def __init__(self, analytics, tenant_id, sid, path, input_data):
        _require_consent(analytics, tenant_id)
        if sid is not None and (not isinstance(sid, str) or not _SID.fullmatch(sid)):
            raise AnalyticsError(400, "invalid_session_id")
        self.analytics, self.tenant_id, self.sid = analytics, tenant_id, sid
        self.notice_version = analytics.notice_version
        self.path = path.split("?", 1)[0].split("#", 1)[0][:8192]
        # Store only known route names, never arbitrary path/query content.
        match = re.fullmatch(
            r"/api/projects/[^/]+/(message(?:/stream)?|tasks|nudge|note|plan)", self.path,
        )
        self.path = (
            "/api/projects/:sid/" + match[1] if match
            else self.path if self.path in {"/api/projects", "/compute/jobs", "/compute/api/jobs"}
            else "/:other"
        )
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._finished = False
        self._truncated = False
        clean, truncated = _bounded_input(input_data)
        _schema(analytics)
        with analytics._db() as db:
            cursor = db.execute(
                """INSERT INTO interactions
                (tenant_id,sid,notice_version,path,created_at,input,input_truncated)
                VALUES (?,?,?,?,?,?,?)""",
                (tenant_id, sid, self.notice_version, self.path, _now(analytics),
                 _dumps(clean), int(truncated)),
            )
            self.id = cursor.lastrowid

    def feed(self, data: bytes):
        with self._lock:
            if self._finished:
                return
            remaining = MAX_BYTES - len(self._buffer)
            self._buffer.extend(memoryview(data)[:remaining])
            self._truncated |= len(data) > remaining

    def finish(self, status_code: int, completed: bool, content_type: str):
        with self._lock:
            if self._finished:
                return
            _require_consent(self.analytics, self.tenant_id)
            if self.analytics.notice_version != self.notice_version:
                raise AnalyticsError(403, "consent_required")
            media = content_type.split(";", 1)[0].strip().lower()
            is_sse = media == "text/event-stream" and self.path.endswith("/message/stream")
            if is_sse or media == "application/json" or media.endswith("+json"):
                frames, result, error, suppressed = _response(
                    bytes(self._buffer), is_sse, self._truncated,
                )
            else:
                frames, result, error, suppressed = [], None, "unsupported_content_type", 0
            if result and result.get("kind") == "chat" and not isinstance(result.get("reply"), str):
                error = error or "missing_final_reply"
            failure = status_code >= 400 or error in {"stream_error", "response_error"}
            state = "response_error" if failure else (
                "response_complete" if completed else "disconnected"
            )
            if status_code >= 400:
                error = error or "http_error"
            outcome = "response_error" if failure else "incomplete"
            if not failure and completed and not error and not self._truncated and result:
                if result.get("kind") == "task":
                    outcome = "background_task_dispatched"
                elif result.get("kind") == "chat" and isinstance(result.get("reply"), str):
                    outcome = "chat_response"
                else:
                    outcome = "response_observed"
            if not completed:
                error = error or "disconnected"
            frames_json = _dumps(frames)
            result_json = _dumps(result) if result is not None else None
            if len(frames_json) + len(result_json or "") > MAX_BYTES:
                # Persist no oversized or partial JSON, and never claim success.
                frames_json, result_json = "[]", None
                self._truncated = True
                error = error or "response_truncated"
                if not failure:
                    outcome = "incomplete"
            with self.analytics._db() as db:
                task_id, _ = _task_reference(result if result_json is not None else None)
                task_accepted = _accepted_task(
                    self.path, result if result_json is not None else None,
                    status_code, state, error, self._truncated,
                )
                if task_accepted:
                    outcome = "background_task_dispatched"
                elif outcome == "background_task_dispatched":
                    outcome = "task_dispatch_unverified"
                db.execute(
                    """UPDATE interactions SET finished_at=?,status_code=?,content_type=?,
                    state=?,outcome=?,response_truncated=?,error_code=?,suppressed_frames=?,
                    frames=?,result=?,task_id=?,task_accepted=?
                    WHERE id=? AND tenant_id=? AND notice_version=?""",
                    (_now(self.analytics), status_code,
                     media if media in {"application/json", "text/event-stream"} else "other",
                     state, outcome, int(self._truncated), error, suppressed,
                     frames_json, result_json, task_id, int(task_accepted),
                     self.id, self.tenant_id, self.notice_version),
                )
            self._buffer.clear()
            self._finished = True


def _record(row):
    result = dict(row)
    for key in ("input_truncated", "response_truncated", "task_accepted"):
        result[key] = bool(result[key])
    for key in ("input", "frames", "result"):
        if key in result and result[key] is not None:
            result[key] = json.loads(result[key])
    return result


def list_interactions(analytics, tenant_id, limit=100):
    """Newest-first current-notice summaries; no body or project reads."""
    _require_consent(analytics, tenant_id)
    if type(limit) is not int or not 1 <= limit <= 500:
        raise AnalyticsError(400, "invalid_limit")
    _schema(analytics)
    with analytics._db() as db:
        rows = db.execute(
            f"SELECT {_SUMMARY} FROM interactions WHERE tenant_id=? AND notice_version=? "
            "ORDER BY created_at DESC,id DESC LIMIT ?",
            (tenant_id, analytics.notice_version, limit),
        ).fetchall()
    return {"interactions": [_record(row) for row in rows]}


def get_interaction(analytics, tenant_id, id, include_trace=True):
    """Full record plus current project_trace, or an explicit trace error envelope."""
    _require_consent(analytics, tenant_id)
    if type(id) is not int or id < 1:
        raise AnalyticsError(404, "interaction_not_found")
    _schema(analytics)
    with analytics._db() as db:
        row = db.execute(
            "SELECT * FROM interactions WHERE tenant_id=? AND notice_version=? AND id=?",
            (tenant_id, analytics.notice_version, id),
        ).fetchone()
    if row is None:
        raise AnalyticsError(404, "interaction_not_found")
    result = _record(row)
    if include_trace:
        if not result["sid"]:
            result["project_trace"] = {"state": "unavailable", "code": "no_project_id"}
        else:
            try:
                result["project_trace"] = analytics.trace(tenant_id, result["sid"], limit=500)
            except AnalyticsError as exc:
                result["project_trace"] = {
                    "state": "error", "code": exc.code, "status": exc.status,
                }
            except Exception:
                # Never leak exception text (which may contain paths or secrets).
                result["project_trace"] = {"state": "error", "code": "project_trace_unavailable"}
    return result


def prune_interactions(analytics):
    """Delete expired interaction rows only; return the deleted count."""
    _schema(analytics)
    with analytics._db() as db:
        cursor = db.execute(
            "DELETE FROM interactions WHERE created_at < ?",
            (_now(analytics) - analytics.retention_days * 86400,),
        )
        return cursor.rowcount
