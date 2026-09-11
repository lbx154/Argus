"""Server-side, consent-gated research replay; never a runtime log writer.

The portal owns authentication and must call ``poll`` repeatedly in its persistent
service (not in a browser). All tables are ``journey_*`` in ``analytics.path``,
accessed through Analytics._db. No provider, artifact-content or network reads.

API:
* poll() -> {projects, inserted_events, tenants_without_consent, discovery,
  errors}; discovery/errors are bounded per configured tenant/project.
* replay(tenant_id, sid, limit=500, after_sequence=0) -> {tenant_id, sid,
  notice_version, events, next_sequence, has_more, completeness, sources,
  lifecycle, artifact_references}. Events have {id, sequence, tenant_id, sid,
  notice_version, source_kind, source, source_timestamp, ingested_at, kind,
  task_id, association, lifecycle, artifact_references, payload, warnings}.
  Lifecycle/artifact lists summarize ONLY the returned page, not project state.
* delete_project(tenant_id, sid) -> {events, checkpoints, http_observations,
  tombstone: {tenant_id, sid, deleted_at}}. Deletes this journal's copies only.
  The portal's research_copies=True additionally deletes interactions, feedback
  and annotations in the same transaction, under its capture/deletion lock.
* prune() -> {events}; applies min(30, analytics.retention_days) days from ingest
  time and a per-project row cap. Source timestamps are untrusted observations.
* append_user_event(tenant_id, sid, kind, payload, task_id=None,
  idempotency_key=None) -> one event. Supported kinds: correction/feedback/download.
  Oversized user payloads fail with 413; conflicting retained keys fail with 409.

Poll processes at most one 256 KiB/200-line runtime batch (plus a bounded
validation reread/fingerprints) and ten captured HTTP rows per listed project
per call; Analytics bounds discovery to 100 projects/1000 entries per tenant.
Replay is at most 500 rows/1 MiB with an exclusive sequence cursor. Individual
payloads are at most 16 KiB; HTTP bodies above 128 KiB are replaced by warnings.
Public methods raise AnalyticsError for safe authorization/input failures; poll
returns source errors without exception text. Database failures propagate so the
service can alert/retry rather than silently report successful recording.

Sequence is global SQLite AUTOINCREMENT ingest order, NOT causal/runtime time.
Source identity+generation+byte offset+hash provides durable poll idempotence;
HTTP request and response are two separate observations, not runtime duplicates.
Offsets, minimal HTTP receipts and deletion tombstones outlive content retention
to prevent reimport. Idempotency keys for explicit user events last only as long
as their retained event. No authoritative task ID is inferred from nearby rows.

Only current events.jsonl is read. Rotated generations, pre-instrumentation
history, signal-mode omissions, transient deliveries and invisible tools cannot
be reconstructed. Completeness is never asserted for the entire user journey.
The v2 portal does not backfill any existing bytes on initial source observation
or replacement; each such conservative collection boundary is recorded as a gap.
Prefix/tail checkpoint fingerprints detect common truncate/rewrite cases, not
arbitrary edits in the already-read middle of a file. No filesystem snapshot or
independent proof of task success is implied by a lifecycle observation.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import uuid

from argus_skill.core.secret_guard import redact_secrets_record
from argus_skill.trial.analytics import (
    _ID,
    AnalyticsError,
    _safe_row,
    _sanitize,
    _server_replay_notice,
)
from argus_skill.trial.interaction_capture import (
    _bounded_input,
    _public_result,
    _require_consent,
    _task_reference,
    _valid_task_id,
)

MAX_READ_BYTES = 256 * 1024
MAX_LINE_BYTES = 64 * 1024
MAX_BATCH_ROWS = 200
MAX_EVENT_BYTES = 16 * 1024
MAX_REPLAY_BYTES = 1024 * 1024
MAX_RETAINED_EVENTS = 10_000
MAX_HTTP_ROWS = 10
MAX_HTTP_BYTES = 128 * 1024
REPLAY_NOTICE_VERSION = "operator-analytics-v2"
_TYPE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){0,8}\Z")
_SCALARS = frozenset("""
    type kind item_id task_id continuation_item_id message_id event_id id ts timestamp
    status success title objective text reply question answer summary reason next_action
    code error_code error_category stop_kind outcome_class from_state to_state
    tool_name exit_code duration_ms elapsed_ms elapsed_seconds actor agent_layer label
    phase fragment_mode resolved dispatch_state execution_status review_status turn_id role
    stage_certification interruption_kind resumable overall_complete campaign_continues
    steps_incomplete
    decision_id decision_revision resolution_id manager_decision answered_item_id
    application_status resume_requested answer_intent
""".split())
_REF_FIELDS = frozenset("path filename mime size size_bytes label delivery_id".split())
_REF_CONTAINERS = ("artifacts", "targets", "delivery_candidates", "attachments")
_PUBLIC_TYPES = frozenset("""
    ui.operator ui.argus engineer.progress life.lifecycle.transition life.lifecycle.block
    life.budget.pause life.inbox.queued life.inbox.drained life.letter.written
    project.completed project.completion_refused operator_alert daemon.parked manager.activity
    round.review.completed
""".split())
_PREFIXES = (
    "life.mission.", "life.operator_question.", "life.planner.task_",
    "team.", "task.", "life.team.", "life.task.",
)
_SPEECH = frozenset({"assistant_message", "agent_message", "message", "progress"})
_LIMITATIONS = [
    "Only currently instrumented observable sources; full journey completeness is unverified.",
    "Rotated archives, transient/signal-filtered events and pre-instrumentation history are not read.",
    "Private reasoning, provider streams, raw tool output and attachment contents are excluded.",
    "Artifact references are declarations, not verified files or successful downloads.",
    "Source timestamps are untrusted; sequence is ingest order, not causal ordering.",
    "HTTP response completion is not task completion; page summaries are not project state.",
    "Public stages are bounded metadata, not full tool schemas/calls/results or hidden reasoning.",
    "HTTP interaction copies may omit stages; runtime stage records are separate observations.",
]


def _json(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _scalar(value):
    return value is None or isinstance(value, (str, bool, int, float))


def _references(value, depth=0):
    """Metadata projection only, never follow a reference or preserve a URL query."""
    refs = []
    if not isinstance(value, dict) or depth > 4:
        return refs
    containers = [value]
    if isinstance(value.get("delivery"), dict):
        containers.append(value["delivery"])
    for container in containers:
        for key in _REF_CONTAINERS:
            items = container.get(key, [])
            if not isinstance(items, list):
                continue
            for item in items[:32]:
                if isinstance(item, str):
                    item = {"path": item}
                if not isinstance(item, dict):
                    continue
                ref = {
                    field: val for field, val in item.items()
                    if field in _REF_FIELDS and _scalar(val)
                }
                for field in ("path", "filename"):
                    if isinstance(ref.get(field), str):
                        ref[field] = ref[field].split("?", 1)[0].split("#", 1)[0][:1024]
                        if "://" in ref[field] or ref[field].startswith(("data:", "//")):
                            ref.pop(field)
                if ref:
                    refs.append(ref)
    for key in ("result", "input"):
        refs.extend(_references(value.get(key), depth + 1))
    return refs[:32]


def _public_steps(value):
    if not isinstance(value, list):
        return [], ["invalid_public_steps"]
    steps, warnings = [], set()
    if len(value) > 80:
        warnings.add("public_steps_truncated")
    fields = {"kind", "label", "status", "started_ts", "ended_ts", "tool", "tool_kind", "call_id"}
    for step in value[:80]:
        if not isinstance(step, dict) or step.get("kind") not in {
            "activity", "tool_use", "command_execution", "file_change", "tool_result",
        }:
            warnings.add("public_step_suppressed")
            continue
        selected = {key: val for key, val in step.items() if key in fields and _scalar(val)}
        for key in ("label", "tool", "tool_kind", "call_id"):
            if isinstance(selected.get(key), str) and len(selected[key]) > 240:
                selected[key] = selected[key][:240]
                warnings.add("public_step_metadata_truncated")
        if step.keys() - fields:
            warnings.add("public_step_details_excluded")
        if not isinstance(step.get("status"), str) or step.get("status") in {"", "unconfirmed"}:
            warnings.add("public_step_completion_unconfirmed")
        steps.append(selected)
    return steps, sorted(warnings)


def _projection(value, *, public_text=True, depth=0):
    if not isinstance(value, dict) or depth > 4:
        return {}
    # Tool frames may embed stdout in text/summary; retain only typed diagnostics.
    tool_frame = value.get("kind") in {
        "command_execution", "tool_use", "tool_call", "tool_result", "file_change",
    }
    fields = _SCALARS
    if not public_text or tool_frame:
        fields = fields - {
            "text", "reply", "summary", "reason", "objective", "title", "question",
            "answer", "next_action",
        }
    result = {
        key: val for key, val in value.items() if key in fields and _scalar(val)
    }
    for key in ("result", "outcome"):
        if isinstance(value.get(key), dict):
            result[key] = _projection(value[key], public_text=public_text, depth=depth + 1)
    if isinstance(value.get("item"), dict):
        item = _projection(value["item"], public_text=public_text, depth=depth + 1)
        if "id" in item and not _valid_task_id(item["id"]):
            item["id"] = None
        result["item"] = item
    if "steps" in value:
        result["steps"], result["steps_warnings"] = _public_steps(value["steps"])
        if set(result["steps_warnings"]) & {
            "invalid_public_steps", "public_steps_truncated", "public_step_suppressed",
        }:
            result["steps_incomplete"] = True
    refs = _references(value)
    if refs:
        result["artifacts"] = refs
    return result


def _bounded(value):
    """Bound before recursive shared sanitizers, and flag every lossy projection."""
    nodes, budget, truncated = 1000, MAX_EVENT_BYTES // 2, False

    def copy(item, depth=0):
        nonlocal nodes, budget, truncated
        nodes -= 1
        if nodes < 0 or budget < 32 or depth > 12:
            truncated = True
            return None
        budget -= 16
        if isinstance(item, str):
            limit = min(4096, max(0, budget // 6))
            truncated |= len(item) > limit
            item = item[:limit]
            budget -= len(_json(item))
            return item
        if isinstance(item, dict):
            output = {}
            for key, val in item.items():
                if nodes < 0 or budget < 64:
                    truncated = True
                    break
                if isinstance(key, str):
                    output[key[:80]] = copy(val, depth + 1)
            return output
        if isinstance(item, list):
            output = []
            for val in item:
                if nodes < 0 or budget < 32:
                    truncated = True
                    break
                output.append(copy(val, depth + 1))
            return output
        if isinstance(item, int) and item.bit_length() > 64:
            truncated = True
            return None
        return item if _scalar(item) else None

    clean = _sanitize(redact_secrets_record(copy(value)))
    if len(_json(clean)) > MAX_EVENT_BYTES:
        return {}, True
    return clean, truncated


def _association(payload):
    candidates = []
    invalid = []
    turn = {}
    turn_id = payload.get("turn_id")
    if (isinstance(turn_id, str) and _ID.fullmatch(turn_id)
            and turn_id == _sanitize(redact_secrets_record(turn_id))):
        turn = {"turn_id": turn_id, "turn_evidence": ["turn_id"]}
    for prefix, container in (("", payload), ("result.", payload.get("result", {}))):
        if not isinstance(container, dict):
            continue
        task_id, evidence = _task_reference(container)
        if task_id is not None:
            candidates.extend((prefix + key, task_id) for key in evidence)
        else:
            invalid.extend(evidence)
    if candidates and not invalid and len({val for _, val in candidates}) == 1:
        return candidates[0][1], {
            "kind": "authoritative", "evidence": [key for key, _ in candidates],
            **turn,
        }
    return None, {
        "kind": "unassigned",
        "evidence": sorted(set(invalid)) or ["conflicting_task_ids" if candidates else "no_explicit_task_id"],
        **turn,
    }


def _lifecycle(kind, payload):
    """A mission.completed envelope can actually represent a resumable pause."""
    if kind.startswith(("http.", "user.")):
        return None
    if not (kind.startswith(("life.mission.", "task.", "life.task."))
            or kind == "life.lifecycle.transition"):
        return None
    status = payload.get("status") or payload.get("to_state")
    if isinstance(status, str):
        status = status.lower()
        if status.startswith("paused") or status in {"blocked", "infra_blocked"}:
            return "pause"
        if status in {"cancelled", "canceled", "aborted"}:
            return "cancel"
        if status in {"failed", "error", "orphaned"}:
            return "fail"
        if status in {"continue", "running", "in_progress", "replan_requested"}:
            return "resume" if kind.endswith((".resumed", ".requeued")) else "continue"
        if status in {"done", "completed", "success"}:
            outcome = payload.get("outcome")
            outcome = outcome if isinstance(outcome, dict) else {}
            if (payload.get("success") is False
                    or payload.get("outcome_class") in {"incomplete", "blocked", "failed", "stalled"}
                    or outcome.get("resumable") is True
                    or outcome.get("interruption_kind") not in (None, "", "none")):
                return "incomplete"
            return "complete"
    suffix = kind.rsplit(".", 1)[-1]
    return {
        "started": "start", "paused": "pause", "resumed": "resume",
        "requeued": "resume", "failed": "fail", "orphaned": "fail",
        "cancelled": "cancel", "canceled": "cancel",
        "completed": "settled_unknown",
    }.get(suffix)


class Journal:
    """A bounded batch collector. Caller authentication is outside this module."""

    def __init__(self, analytics):
        self.analytics = analytics
        with analytics._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                CREATE TABLE IF NOT EXISTS journey_projects (
                    tenant_id TEXT NOT NULL, sid TEXT NOT NULL, notice_version TEXT NOT NULL,
                    checkpoint TEXT NOT NULL DEFAULT '{}', polled_at REAL,
                    pruned_through INTEGER NOT NULL DEFAULT 0,
                    pruned_events INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (tenant_id,sid,notice_version)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS journey_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE, tenant_id TEXT NOT NULL, sid TEXT NOT NULL,
                    notice_version TEXT NOT NULL, ingested_at REAL NOT NULL,
                    record TEXT NOT NULL, gap INTEGER NOT NULL
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS journey_project_sequence
                ON journey_events(tenant_id,sid,notice_version,sequence)
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS journey_ingested ON journey_events(ingested_at)
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS journey_tombstones (
                    tenant_id TEXT NOT NULL, sid TEXT NOT NULL, deleted_at REAL NOT NULL,
                    PRIMARY KEY (tenant_id,sid)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS journey_http (
                    tenant_id TEXT NOT NULL, sid TEXT NOT NULL, notice_version TEXT NOT NULL,
                    interaction_id INTEGER NOT NULL, finished INTEGER NOT NULL,
                    projection_version INTEGER NOT NULL DEFAULT 2,
                    PRIMARY KEY (tenant_id,sid,notice_version,interaction_id)
                )
            """)
            if "projection_version" not in {row[1] for row in db.execute("PRAGMA table_info(journey_http)")}:
                db.execute("ALTER TABLE journey_http ADD COLUMN projection_version INTEGER NOT NULL DEFAULT 0")

    def _key(self, tenant, sid):
        return tenant, sid, self.analytics.notice_version

    def _authorize(self, db, tenant, sid):
        self.analytics._tenant(tenant)
        if not isinstance(sid, str) or not _ID.fullmatch(sid):
            raise AnalyticsError(400, "invalid_session_id")
        if db.execute(
            "SELECT 1 FROM consents WHERE tenant_id=? AND version=?",
            (tenant, self.analytics.notice_version),
        ).fetchone() is None:
            raise AnalyticsError(403, "consent_required")
        if db.execute(
            "SELECT 1 FROM journey_tombstones WHERE tenant_id=? AND sid=?", (tenant, sid),
        ).fetchone():
            raise AnalyticsError(410, "journal_deleted")

    def _project(self, db, tenant, sid):
        self._authorize(db, tenant, sid)
        db.execute(
            "INSERT OR IGNORE INTO journey_projects(tenant_id,sid,notice_version) VALUES(?,?,?)",
            self._key(tenant, sid),
        )
        return dict(db.execute(
            "SELECT * FROM journey_projects WHERE tenant_id=? AND sid=? AND notice_version=?",
            self._key(tenant, sid),
        ).fetchone())

    def _insert(self, db, tenant, sid, source_kind, source, kind, payload,
                *, warnings=(), source_timestamp=None):
        task, association = _association(payload)
        if source_kind == "http_interaction":
            association["interaction_id"] = source["interaction_id"]
        clean, truncated = _bounded(payload)
        warnings = list(warnings)
        if truncated:
            warnings.append("payload_truncated")
        pending = [payload]
        while pending:
            part = pending.pop()
            if isinstance(part, dict):
                warnings.extend(
                    warning for warning in (part.get("steps_warnings") or [])
                    if isinstance(warning, str)
                )
                if part.get("steps_incomplete") is True:
                    warnings.append("public_steps_incomplete")
                pending.extend(part.values())
            elif isinstance(part, list):
                pending.extend(part)
        warnings = sorted(set(warnings))
        identity = _hash(_json([*self._key(tenant, sid), source_kind, source]).encode())
        if not (_scalar(source_timestamp) and not isinstance(source_timestamp, bool)):
            source_timestamp = None
        if isinstance(source_timestamp, str):
            source_timestamp = _sanitize(redact_secrets_record(source_timestamp[:80]))
        if isinstance(source_timestamp, (int, float)):
            if not math.isfinite(source_timestamp) or abs(source_timestamp) > 1e15:
                source_timestamp = None
        record = {
            "id": identity, "tenant_id": tenant, "sid": sid,
            "notice_version": self.analytics.notice_version, "source_kind": source_kind,
            "source": source, "source_timestamp": source_timestamp,
            "ingested_at": self.analytics.clock(), "kind": kind, "task_id": task,
            "association": association, "lifecycle": _lifecycle(kind, clean),
            "artifact_references": _references(clean), "payload": clean, "warnings": warnings,
        }
        cursor = db.execute(
            "INSERT OR IGNORE INTO journey_events"
            "(id,tenant_id,sid,notice_version,ingested_at,record,gap) VALUES(?,?,?,?,?,?,?)",
            (identity, *self._key(tenant, sid), record["ingested_at"], _json(record),
             int(bool(warnings) or source_kind == "journal_gap")),
        )
        if source_kind == "http_interaction" and source.get("phase") == "response" and task:
            self._link_http_request(db, tenant, sid, source["interaction_id"], task, association, identity)
        return cursor.rowcount

    def _link_http_request(self, db, tenant, sid, interaction_id, task_id, association, response_id):
        """Enrich only the request sharing the exact durable interaction receipt."""
        source = {"name": "interactions", "interaction_id": interaction_id, "phase": "request"}
        identity = _hash(_json([*self._key(tenant, sid), "http_interaction", source]).encode())
        row = db.execute("SELECT record FROM journey_events WHERE id=?", (identity,)).fetchone()
        if row is None:
            return
        record = json.loads(row["record"])
        record["task_id"] = task_id
        record["association"] = {
            "kind": "authoritative", "evidence": ["response." + item for item in association["evidence"]],
            "interaction_id": interaction_id, "response_event_id": response_id,
        }
        db.execute("UPDATE journey_events SET record=? WHERE id=?", (_json(record), identity))

    def _repair_http_response(self, db, tenant, sid, source, payload, warnings):
        """Upgrade a retained projection, never resurrect expired/deleted records."""
        identity = _hash(_json([*self._key(tenant, sid), "http_interaction", source]).encode())
        row = db.execute("SELECT record FROM journey_events WHERE id=?", (identity,)).fetchone()
        if row is None:
            return
        record = json.loads(row["record"])
        task_id, association = _association(payload)
        association["interaction_id"] = source["interaction_id"]
        clean, truncated = _bounded(payload)
        record.update(payload=clean, task_id=task_id, association=association)
        record["warnings"] = sorted(set(record["warnings"] + warnings + (["payload_truncated"] if truncated else [])))
        db.execute("UPDATE journey_events SET record=?,gap=? WHERE id=?",
                   (_json(record), int(bool(record["warnings"])), identity))
        if task_id:
            self._link_http_request(db, tenant, sid, source["interaction_id"], task_id, association, identity)

    def _gap(self, db, tenant, sid, code, source):
        return self._insert(
            db, tenant, sid, "journal_gap", {**source, "code": code},
            "journal.gap", {"code": code}, warnings=[code],
        )

    @staticmethod
    def _fingerprint(handle, offset):
        width = min(offset, 512)
        return _hash(os.pread(handle, width, 0) + os.pread(handle, width, offset - width))

    def _runtime(self, db, tenant, sid, directory, checkpoint):
        try:
            handle = os.open(
                "events.jsonl", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
        except FileNotFoundError:
            inserted = 0
            if checkpoint.get("state") != "missing":
                inserted = self._gap(db, tenant, sid, "source_missing", {
                    "name": "events.jsonl", "generation": checkpoint.get("generation"),
                    "offset": checkpoint.get("offset", 0),
                })
            return {**checkpoint, "state": "missing"}, inserted
        try:
            info = os.fstat(handle)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise AnalyticsError(400, "unsafe_path")
            identity = f"{info.st_dev}:{info.st_ino}"
            offset, inserted = checkpoint.get("offset", 0), 0
            reason = None
            if checkpoint.get("identity") not in (None, identity):
                reason = "source_rotated"
            elif checkpoint.get("identity") and (
                info.st_size < offset
                or self._fingerprint(handle, offset) != checkpoint.get("fingerprint")
            ):
                reason = "source_truncated_or_rewritten"
            if not checkpoint.get("identity") or reason:
                if reason:
                    inserted += self._gap(db, tenant, sid, reason, {
                        "name": "events.jsonl", "generation": checkpoint["generation"],
                        "offset": offset,
                    })
                checkpoint = {"generation": uuid.uuid4().hex, "identity": identity}
                offset = 0
                if _server_replay_notice(self.analytics.notice_version):
                    # Never backfill bytes that predate this collector's boundary.
                    offset = info.st_size
                    inserted += self._gap(db, tenant, sid, "collection_boundary_no_backfill", {
                        "name": "events.jsonl", "generation": checkpoint["generation"],
                        "offset": offset,
                    })
                    checkpoint.update(
                        offset=offset, fingerprint=self._fingerprint(handle, offset),
                        dropping=bool(offset and os.pread(handle, 1, offset - 1) != b"\n"),
                        state="caught_up", observed_bytes=offset,
                    )
                    return checkpoint, inserted
            start = offset
            old_fingerprint = self._fingerprint(handle, start)
            raw = os.pread(handle, MAX_READ_BYTES, start)
            positions, pos, dropping = [], 0, checkpoint.get("dropping", False)
            for _ in range(MAX_BATCH_ROWS):
                if pos >= len(raw):
                    break
                end = raw.find(b"\n", pos)
                if end < 0:
                    if dropping or len(raw) - pos > MAX_LINE_BYTES:
                        if not dropping:
                            positions.append((pos, None, "oversized_line"))
                        dropping = True
                        pos = len(raw)
                    break
                line = raw[pos:end + 1]
                if not dropping:
                    positions.append((
                        pos, line if len(line) <= MAX_LINE_BYTES else None,
                        "oversized_line" if len(line) > MAX_LINE_BYTES else None,
                    ))
                dropping = False
                pos = end + 1
            offset += pos
            # Do not commit a mixed generation if rotation/truncation raced this read.
            final = os.stat("events.jsonl", dir_fd=directory, follow_symlinks=False)
            if (final.st_dev, final.st_ino) != (info.st_dev, info.st_ino) or (
                os.fstat(handle).st_size < start + len(raw)
                or self._fingerprint(handle, start) != old_fingerprint
                or os.pread(handle, len(raw), start) != raw
            ):
                raise AnalyticsError(409, "source_changed_during_read")
            for relative, line, error in positions:
                source = {
                    "name": "events.jsonl", "generation": checkpoint["generation"],
                    "offset": start + relative, "sha256": _hash(line) if line else None,
                }
                if error:
                    inserted += self._gap(db, tenant, sid, error, source)
                    continue
                try:
                    row = json.loads(line, parse_constant=lambda _: None)
                    if not isinstance(row, dict):
                        raise ValueError
                    kind = row.get("type", "")
                    if not isinstance(kind, str) or not _TYPE.fullmatch(kind):
                        raise ValueError
                    allowed = kind in _PUBLIC_TYPES or kind.startswith(_PREFIXES)
                    # Reuse capture's original-object private-channel check before projection.
                    safe = _public_result(row) if allowed else None
                    if safe is None or _safe_row(row) is None:
                        inserted += self._gap(db, tenant, sid, "suppressed_event", source)
                        continue
                    speech = kind != "engineer.progress" or row.get("kind") in _SPEECH
                    payload = _projection(row, public_text=speech)
                    inserted += self._insert(
                        db, tenant, sid, "runtime_event", source, kind, payload,
                        source_timestamp=row.get("ts", row.get("timestamp")),
                    )
                except (ValueError, TypeError, RecursionError, OverflowError):
                    inserted += self._gap(db, tenant, sid, "invalid_event", source)
            checkpoint.update(
                offset=offset, fingerprint=self._fingerprint(handle, offset), dropping=dropping,
                state=("oversized_line_pending" if dropping else
                       "awaiting_newline" if offset < start + len(raw)
                       and start + len(raw) == final.st_size and b"\n" not in raw[pos:]
                       else "backlog" if offset < final.st_size else "caught_up"),
                observed_bytes=final.st_size,
            )
            return checkpoint, inserted
        finally:
            os.close(handle)

    def _http(self, db, tenant, sid):
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='interactions'",
        ).fetchone():
            return {"state": "not_instrumented", "pending": False}, 0
        key = self._key(tenant, sid)
        rows = db.execute("""
            SELECT i.id,i.path,i.created_at,i.finished_at,i.state,i.outcome,i.status_code,
                i.error_code,i.input_truncated,i.response_truncated,i.suppressed_frames,
                substr(i.input,1,?) AS input,substr(i.frames,1,?) AS frames,
                substr(i.result,1,?) AS result,h.interaction_id AS seen,h.finished AS seen_finished,
                h.projection_version
            FROM interactions i LEFT JOIN journey_http h
                ON h.tenant_id=i.tenant_id AND h.sid=i.sid
                AND h.notice_version=i.notice_version AND h.interaction_id=i.id
            WHERE i.tenant_id=? AND i.sid=? AND i.notice_version=?
                AND (h.interaction_id IS NULL OR h.projection_version<2
                    OR (h.finished=0 AND i.finished_at IS NOT NULL))
            ORDER BY i.id LIMIT ?
        """, (MAX_HTTP_BYTES + 1,) * 3 + key + (MAX_HTTP_ROWS + 1,)).fetchall()
        inserted = 0
        for row in rows[:MAX_HTTP_ROWS]:
            phases = (["request"] if row["seen"] is None else [])
            if row["finished_at"] is not None:
                phases.append("response")
            for phase in phases:
                warnings = []
                payload = {"path": row["path"]}
                names = ("input",) if phase == "request" else ("frames", "result")
                for name in names:
                    raw = row[name]
                    if raw is None:
                        continue
                    if len(raw) > MAX_HTTP_BYTES:
                        warnings.append("http_body_oversized")
                        continue
                    try:
                        value = json.loads(raw)
                        if name == "input":
                            value, truncated = _bounded_input(value)
                            if truncated:
                                warnings.append("http_input_truncated")
                            payload[name] = _projection(value)
                        elif name == "frames":
                            if not isinstance(value, list):
                                raise ValueError
                            frames = []
                            for frame in value[:64]:
                                safe = _public_result(frame)
                                if safe is None:
                                    warnings.append("http_frame_suppressed")
                                else:
                                    frames.append(_projection(frame))
                            payload[name] = frames
                            if len(value) > 64:
                                warnings.append("http_frames_truncated")
                        else:
                            safe = _public_result(value)
                            if safe is None:
                                warnings.append("http_result_suppressed")
                            else:
                                payload[name] = _projection(value)
                    except (ValueError, TypeError, RecursionError):
                        warnings.append("invalid_http_body")
                if phase == "response":
                    payload.update({key: row[key] for key in (
                        "state", "outcome", "status_code", "error_code",
                    )})
                    if row["outcome"] == "incomplete" or row["error_code"]:
                        warnings.append("http_response_incomplete_or_error")
                    if row["suppressed_frames"]:
                        warnings.append("http_frames_suppressed")
                if row["input_truncated"] or (phase == "response" and row["response_truncated"]):
                    warnings.append("http_capture_truncated")
                source = {"name": "interactions", "interaction_id": row["id"], "phase": phase}
                if row["seen_finished"] and row["projection_version"] < 2 and phase == "response":
                    self._repair_http_response(db, tenant, sid, source, payload, sorted(set(warnings)))
                else:
                    inserted += self._insert(
                        db, tenant, sid, "http_interaction", source,
                        f"http.{phase}", payload, warnings=sorted(set(warnings)),
                        source_timestamp=row["created_at"] if phase == "request" else row["finished_at"],
                    )
            db.execute(
                "INSERT INTO journey_http(tenant_id,sid,notice_version,interaction_id,finished,projection_version) "
                "VALUES(?,?,?,?,?,2) ON CONFLICT "
                "(tenant_id,sid,notice_version,interaction_id) DO UPDATE SET "
                "finished=excluded.finished,projection_version=excluded.projection_version",
                (*key, row["id"], int(row["finished_at"] is not None)),
            )
        unfinished = db.execute("""
            SELECT 1 FROM journey_http WHERE tenant_id=? AND sid=? AND notice_version=?
                AND finished=0 LIMIT 1
        """, key).fetchone() is not None
        return {"state": "observed", "pending": len(rows) > MAX_HTTP_ROWS,
                "unfinished_observations": unfinished}, inserted

    def poll(self, tenant_id=None):
        self.prune()
        result = {"projects": 0, "inserted_events": 0, "tenants_without_consent": 0,
                  "discovery": [], "errors": []}
        for tenant in sorted(self.analytics.tenants):
            if tenant_id is not None and tenant != tenant_id:
                continue
            if not self.analytics.consented(tenant, self.analytics.notice_version):
                result["tenants_without_consent"] += 1
                continue
            try:
                listing = self.analytics.projects(tenant)
            except AnalyticsError as exc:
                result["errors"].append({"tenant_id": tenant, "code": exc.code})
                continue
            result["discovery"].append({
                "tenant_id": tenant, "state": listing["state"],
                "truncated": listing["truncated"], "skipped": listing["skipped"],
            })
            for project in listing["projects"]:
                sid = project["id"]
                try:
                    with self.analytics._project(tenant, sid) as directory:
                        with self.analytics._db() as db:
                            db.execute("BEGIN IMMEDIATE")
                            state = self._project(db, tenant, sid)
                            checkpoint = json.loads(state["checkpoint"])
                            runtime, count = self._runtime(
                                db, tenant, sid, directory, checkpoint.get("runtime", {}),
                            )
                            http, http_count = self._http(db, tenant, sid)
                            db.execute(
                                "UPDATE journey_projects SET checkpoint=?,polled_at=? "
                                "WHERE tenant_id=? AND sid=? AND notice_version=?",
                                (_json({"runtime": runtime, "http": http}), self.analytics.clock(),
                                 *self._key(tenant, sid)),
                            )
                    result["projects"] += 1
                    result["inserted_events"] += count + http_count
                except (AnalyticsError, OSError) as exc:
                    code = exc.code if isinstance(exc, AnalyticsError) else "source_unreadable"
                    result["errors"].append({"tenant_id": tenant, "sid": sid, "code": code})
                    if code != "journal_deleted":
                        self._record_source_error(tenant, sid, code)
        self.prune()
        return result

    def _record_source_error(self, tenant, sid, code):
        with self.analytics._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                state = self._project(db, tenant, sid)
            except AnalyticsError as exc:
                if exc.code in {"consent_required", "journal_deleted"}:
                    return
                raise
            checkpoint = json.loads(state["checkpoint"])
            runtime = checkpoint.setdefault("runtime", {})
            self._gap(db, tenant, sid, code, {
                "name": "events.jsonl", "generation": runtime.get("generation"),
                "offset": runtime.get("offset", 0),
            })
            runtime["state"] = code
            db.execute(
                "UPDATE journey_projects SET checkpoint=?,polled_at=? "
                "WHERE tenant_id=? AND sid=? AND notice_version=?",
                (_json(checkpoint), self.analytics.clock(), *self._key(tenant, sid)),
            )

    def replay(self, tenant_id, sid, limit=500, after_sequence=0):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise AnalyticsError(400, "invalid_limit")
        if type(after_sequence) is not int or not 0 <= after_sequence < 2**63:
            raise AnalyticsError(400, "invalid_sequence")
        _require_consent(self.analytics, tenant_id)
        self.prune()
        with self.analytics._db() as db:
            db.execute("BEGIN")
            self._authorize(db, tenant_id, sid)
            key = self._key(tenant_id, sid)
            project = db.execute(
                "SELECT * FROM journey_projects WHERE tenant_id=? AND sid=? AND notice_version=?",
                key,
            ).fetchone()
            if project is None:
                raise AnalyticsError(404, "journal_not_found")
            rows = db.execute(
                "SELECT sequence,record FROM journey_events WHERE tenant_id=? AND sid=? "
                "AND notice_version=? AND sequence>? ORDER BY sequence LIMIT ?",
                (*key, after_sequence, limit + 1),
            ).fetchall()
            gaps = db.execute(
                "SELECT count(*) FROM journey_events WHERE tenant_id=? AND sid=? "
                "AND notice_version=? AND gap=1", key,
            ).fetchone()[0]
        events, used = [], 0
        for row in rows[:limit]:
            event = {**json.loads(row["record"]), "sequence": row["sequence"]}
            cost = len(_json(event))
            if used + cost > MAX_REPLAY_BYTES // 2:
                break
            events.append(event)
            used += cost
        sources = json.loads(project["checkpoint"])
        runtime, http = sources.get("runtime", {}), sources.get("http", {})
        warnings = []
        if runtime.get("state") != "caught_up":
            warnings.append(runtime.get("state", "not_polled"))
        if http.get("pending"):
            warnings.append("http_backlog")
        if http.get("unfinished_observations"):
            warnings.append("unfinished_http_observations")
        if http.get("state") != "observed":
            warnings.append("http_not_instrumented")
        if project["pruned_events"]:
            warnings.append("retention_or_capacity_pruned")
        if gaps:
            warnings.append("recording_gaps")
        return {
            "tenant_id": tenant_id, "sid": sid, "notice_version": self.analytics.notice_version,
            "events": events, "next_sequence": events[-1]["sequence"] if events else after_sequence,
            "has_more": len(rows) > len(events), "sources": sources,
            "completeness": {
                "complete": False, "state": "incomplete" if warnings else "coverage_unverified",
                "warnings": warnings, "gap_events": gaps,
                "pruned_events": project["pruned_events"],
                "pruned_through_sequence": project["pruned_through"],
                "last_polled_at": project["polled_at"], "as_of_last_poll_only": True,
                "limitations": list(_LIMITATIONS),
            },
            "lifecycle": [
                {"sequence": event["sequence"], "task_id": event["task_id"],
                 "association": event["association"], "state": event["lifecycle"]}
                for event in events if event["lifecycle"]
            ],
            "artifact_references": [
                {"sequence": event["sequence"], "task_id": event["task_id"], "reference": ref}
                for event in events for ref in event["artifact_references"]
            ],
        }

    def append_user_event(self, tenant_id, sid, kind, payload, task_id=None, idempotency_key=None):
        if not isinstance(kind, str) or kind not in {"correction", "feedback", "download"}:
            raise AnalyticsError(400, "invalid_user_event_kind")
        if not isinstance(payload, dict):
            raise AnalyticsError(400, "invalid_payload")
        if task_id is not None and (not isinstance(task_id, str) or not _ID.fullmatch(task_id)):
            raise AnalyticsError(400, "invalid_task_id")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 256
        ):
            raise AnalyticsError(400, "invalid_idempotency_key")
        _require_consent(self.analytics, tenant_id)
        bounded, truncated = _bounded(payload)
        if truncated:
            raise AnalyticsError(413, "user_payload_too_large")
        if _public_result(bounded) is None:
            raise AnalyticsError(400, "private_payload")
        clean, projected_truncated = _bounded(_projection(bounded))
        truncated |= projected_truncated
        clean.pop("task_id", None)
        clean.pop("item_id", None)
        if task_id is not None:
            clean["task_id"] = task_id
        source = {"name": "user_event", "key": _hash(
            (idempotency_key if idempotency_key is not None else uuid.uuid4().hex).encode(),
        )}
        identity = _hash(_json([*self._key(tenant_id, sid), "user_observation", source]).encode())
        self.prune()
        with self.analytics._project(tenant_id, sid) as directory:
            meta, _ = self.analytics._meta(directory, sid)
            if meta is None:
                raise AnalyticsError(422, "invalid_project_metadata")
            with self.analytics._db() as db:
                db.execute("BEGIN IMMEDIATE")
                self._project(db, tenant_id, sid)
                self._insert(
                    db, tenant_id, sid, "user_observation", source, f"user.{kind}", clean,
                    warnings=["payload_truncated"] if truncated else [],
                )
                row = db.execute(
                    "SELECT sequence,record FROM journey_events WHERE id=?", (identity,),
                ).fetchone()
                event = {**json.loads(row["record"]), "sequence": row["sequence"]}
                if event["kind"] != f"user.{kind}" or event["payload"] != clean:
                    raise AnalyticsError(409, "idempotency_conflict")
        self.prune()
        return event

    def delete_project(self, tenant_id, sid, *, research_copies=False):
        self.analytics._tenant(tenant_id)
        if not isinstance(sid, str) or not _ID.fullmatch(sid):
            raise AnalyticsError(400, "invalid_session_id")
        with self.analytics._db() as db:
            db.execute("BEGIN IMMEDIATE")
            known = db.execute(
                "SELECT 1 FROM journey_projects WHERE tenant_id=? AND sid=? "
                "UNION ALL SELECT 1 FROM journey_tombstones WHERE tenant_id=? AND sid=? LIMIT 1",
                (tenant_id, sid, tenant_id, sid),
            ).fetchone()
            if known is None:
                try:
                    with self.analytics._project(tenant_id, sid) as directory:
                        meta, _ = self.analytics._meta(directory, sid)
                        if meta is None:
                            raise AnalyticsError(404, "journal_not_found")
                except AnalyticsError as exc:
                    if exc.status == 404:
                        raise AnalyticsError(404, "journal_not_found") from None
                    raise
            db.execute(
                "INSERT OR IGNORE INTO journey_tombstones VALUES(?,?,?)",
                (tenant_id, sid, self.analytics.clock()),
            )
            result = {}
            for key, table in (
                ("events", "journey_events"), ("checkpoints", "journey_projects"),
                ("http_observations", "journey_http"),
            ):
                result[key] = db.execute(
                    f"DELETE FROM {table} WHERE tenant_id=? AND sid=?", (tenant_id, sid),
                ).rowcount
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("interactions", "research_annotations", "research_feedback"):
                if research_copies and table in tables:
                    result[table] = db.execute(
                        f"DELETE FROM {table} WHERE tenant_id=? AND sid=?", (tenant_id, sid),
                    ).rowcount
            result["tombstone"] = dict(db.execute(
                "SELECT * FROM journey_tombstones WHERE tenant_id=? AND sid=?", (tenant_id, sid),
            ).fetchone())
        return result

    def prune(self):
        cutoff = self.analytics.clock() - min(30, self.analytics.retention_days) * 86400
        with self.analytics._db() as db:
            db.execute("BEGIN IMMEDIATE")
            # SQL bounds content without loading an entire project into Python.
            db.execute("""
                CREATE TEMP TABLE journey_expired AS
                SELECT sequence,tenant_id,sid,notice_version FROM (
                    SELECT sequence,tenant_id,sid,notice_version,ingested_at,
                        row_number() OVER (
                            PARTITION BY tenant_id,sid,notice_version ORDER BY sequence DESC
                        ) AS ordinal FROM journey_events
                ) WHERE ingested_at < ? OR ordinal > ?
            """, (cutoff, MAX_RETAINED_EVENTS))
            db.execute("""
                UPDATE journey_projects SET
                    pruned_events=pruned_events+(
                        SELECT count(*) FROM journey_expired e WHERE
                        e.tenant_id=journey_projects.tenant_id AND e.sid=journey_projects.sid
                        AND e.notice_version=journey_projects.notice_version),
                    pruned_through=max(pruned_through,coalesce((
                        SELECT max(sequence) FROM journey_expired e WHERE
                        e.tenant_id=journey_projects.tenant_id AND e.sid=journey_projects.sid
                        AND e.notice_version=journey_projects.notice_version),0))
            """)
            count = db.execute(
                "DELETE FROM journey_events WHERE sequence IN (SELECT sequence FROM journey_expired)",
            ).rowcount
        return {"events": count}
