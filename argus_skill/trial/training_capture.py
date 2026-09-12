"""Affirmatively authorized Pi capture of received agent observations.

The retain-observed-v2 policy appends each public event independently, including
the application's actual system/developer instructions. Collection is separate
from training-quality validation; interruption and format mismatch never erase
the events already received. Private structured thinking/signature blocks are
excluded. Per-event IPC gaps are recorded while later collection continues.

The hosted-workspace profile is integrated through training_runtime.py and the
private tenant-bound training_bridge.py. Its read-only Pi extension checks the
actual outgoing provider payload, preserves provider schemas and model arguments,
and validates the pinned Pi strict-schema/optional-null execution transformations.
Legacy public episodes omitted system messages and structured private thinking.
Their existing validation and approved source records remain unchanged.

The legacy PI_EXTENSION_SOURCE remains a separately authorized, non-document
tool profile for existing integrations. Neither profile exposes a public capture
endpoint or accepts a client's claim that an observer is verified. Every event
reauthorizes before projection and persistence; outages leave runtime work alone.

Verified local Pi 0.85.1 sources: coding-agent/docs/extensions.md,
src/core/extensions/types.ts, extensions/runner.ts, agent-session.ts and sdk.ts.
context is before convertToLlm; mutable tool hooks run in load order. Hence the
verified runtime profile is mandatory, not an inferred property of event order.
No system prompt or private blocks are stored. Human context review must confirm
the non-system episode is self-contained before it can become an SFT sample.
"""
from __future__ import annotations

import json
import re

from jsonschema import Draft202012Validator, SchemaError

from argus_skill.core.secret_guard import redact_secrets_record

from .analytics import AnalyticsError, _sanitize
from .research_controls import SID

MAX_EPISODE_BYTES = 128 * 1024
MAX_CAPTURE_BYTES = 16 * 1024 * 1024
MAX_EPISODES = 128
MAX_OBSERVATIONS = 64
MAX_RESULT_CHARS = 4096
PROFILE = "pi-0.85.1-final-observer-v1"
HOSTED_PROFILE = "pi-0.85.1-hosted-workspace-v1"
HOSTED_EPISODE_BYTES = 16 * 1024 * 1024
HOSTED_PAYLOAD_BYTES = 16 * 1024 * 1024
HOSTED_RESULT_CHARS = 64 * 1024
HOSTED_TEXT_CHARS = 256 * 1024
HOSTED_OBSERVATIONS = 512
HOSTED_TOOLS = frozenset({"read", "write", "edit", "grep", "find", "ls", "bash"})
OBSERVED_POLICY = "retain-observed-v2"
DOCUMENT_TOOLS = frozenset({"read", "write", "edit", "grep", "find", "ls", "bash", "powershell"})
INIT_FAILURE_REASONS = frozenset({
    "capture_init_authorize_failed", "capture_init_authorize_timeout",
    "capture_init_begin_failed", "capture_init_begin_timeout",
    "capture_init_reply_invalid", "runtime_profile_changed",
})


class TrainingCapture:
    def __init__(self, training):
        self.training = training
        self.analytics, self.controls = training.analytics, training.controls
        with self.analytics._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS training_tool_episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL, sid TEXT NOT NULL, session_id TEXT NOT NULL,
                    started_at REAL NOT NULL, updated_at REAL NOT NULL,
                    grants TEXT NOT NULL, allowed_tools TEXT NOT NULL, state TEXT NOT NULL, reason TEXT,
                    record TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS training_tool_project
                    ON training_tool_episodes(tenant_id,sid,started_at);
                CREATE TABLE IF NOT EXISTS training_observed_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    episode_id INTEGER NOT NULL,
                    sequence INTEGER NOT NULL, kind TEXT NOT NULL,
                    observed_at REAL NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(episode_id,sequence)
                );
                CREATE INDEX IF NOT EXISTS training_observed_episode
                    ON training_observed_events(episode_id,sequence);
                CREATE TRIGGER IF NOT EXISTS training_observed_episode_delete
                AFTER DELETE ON training_tool_episodes BEGIN
                    DELETE FROM training_observed_events WHERE episode_id=OLD.id;
                END;
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(training_tool_episodes)")}
            if "runtime_profile" not in columns:
                db.execute("ALTER TABLE training_tool_episodes ADD COLUMN runtime_profile TEXT NOT NULL DEFAULT 'pi-0.85.1-final-observer-v1'")
            if "runtime_metadata" not in columns:
                db.execute("ALTER TABLE training_tool_episodes ADD COLUMN runtime_metadata TEXT NOT NULL DEFAULT '{}'")
            if "diagnostic" not in columns:
                db.execute("ALTER TABLE training_tool_episodes ADD COLUMN diagnostic TEXT NOT NULL DEFAULT '{}'")
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='journey_tombstones'",
            ).fetchone():
                db.executescript("""
                    CREATE TRIGGER IF NOT EXISTS training_tool_delete
                    AFTER INSERT ON journey_tombstones BEGIN
                        DELETE FROM training_tool_episodes
                        WHERE tenant_id=NEW.tenant_id AND sid=NEW.sid;
                    END;
                    CREATE TRIGGER IF NOT EXISTS training_tool_delete_again
                    AFTER UPDATE ON journey_tombstones BEGIN
                        DELETE FROM training_tool_episodes
                        WHERE tenant_id=NEW.tenant_id AND sid=NEW.sid;
                    END;
                """)

    def _authorization(self, db, tenant, sid):
        from .training_data import NOTICE_VERSION, PURPOSES, _sensitive

        self.analytics._tenant(tenant)
        if not isinstance(sid, str) or not SID.fullmatch(sid) or _sensitive(sid):
            raise AnalyticsError(400, "invalid_session_id")
        grants = {}
        for purpose in PURPOSES:
            stamp, reason = self.training._grant(db, tenant, sid, purpose)
            if reason is None:
                grants[purpose] = stamp
        exists = db.execute(
            "SELECT 1 FROM journey_projects WHERE tenant_id=? AND sid=? AND notice_version=?",
            (tenant, sid, self.analytics.notice_version),
        ).fetchone()
        return {"enabled": bool(grants and exists), "notice_version": NOTICE_VERSION, "grants": grants}

    def authorize(self, tenant, sid):
        with self.analytics._db() as db:
            return self._authorization(db, tenant, sid)

    def prune(self):
        cutoff = self.analytics.clock() - min(30, self.analytics.retention_days) * 86400
        with self.controls.capture_lock, self.analytics._db() as db:
            return db.execute("DELETE FROM training_tool_episodes WHERE started_at<?", (cutoff,)).rowcount

    @staticmethod
    def iter_events(db, episode_id, *, after_sequence=-1, limit=None):
        """Read stored observations in order; caller supplies the authorized DB scope."""
        row = db.execute(
            "SELECT runtime_metadata,record FROM training_tool_episodes WHERE id=?", (episode_id,),
        ).fetchone()
        if row is None:
            raise AnalyticsError(404, "training_episode_not_found")
        if json.loads(row["runtime_metadata"]).get("capture_policy") == OBSERVED_POLICY:
            sql = "SELECT id,sequence,kind,observed_at,payload FROM training_observed_events WHERE episode_id=? AND sequence>? ORDER BY sequence"
            args = (episode_id, after_sequence)
            if limit is not None:
                sql += " LIMIT ?"
                args += (limit,)
            for event in db.execute(sql, args):
                yield {"id": f"observation-{event['id']}", "sequence": event["sequence"],
                       "kind": event["kind"], "observed_at": event["observed_at"], "payload": json.loads(event["payload"])}
        else:
            events = json.loads(row["record"])
            selected = (event for event in events if event.get("sequence", -1) > after_sequence)
            for index, event in enumerate(selected):
                if limit is not None and index >= limit:
                    break
                yield event

    @classmethod
    def events(cls, db, episode_id, *, after_sequence=-1, limit=None):
        return list(cls.iter_events(db, episode_id, after_sequence=after_sequence, limit=limit))

    def begin(self, tenant, sid, session_id, *, observer_verified=False, allowed_tools=(),
              runtime_profile=PROFILE, runtime_metadata=None):
        from .training_data import _json, _sensitive

        if observer_verified is not True:
            raise AnalyticsError(409, "training_runtime_observer_not_verified")
        if runtime_profile not in {PROFILE, HOSTED_PROFILE}:
            raise AnalyticsError(409, "training_runtime_profile_unknown")
        hosted = runtime_profile == HOSTED_PROFILE
        observed = hosted and (runtime_metadata or {}).get("capture_policy") == OBSERVED_POLICY
        if (not isinstance(allowed_tools, (list, tuple)) or not (0 if observed else 1) <= len(allowed_tools) <= (1024 if observed else 32)
                or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)
                       or (not observed and (name not in HOSTED_TOOLS if hosted else name.lower() in DOCUMENT_TOOLS))
                       for name in allowed_tools)):
            raise AnalyticsError(409, "training_non_document_tool_allowlist_required")
        if (not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", session_id)
                or _sensitive(session_id)):
            raise ValueError("A genuine Pi session identifier is required")
        self.prune()
        with self.controls.capture_lock, self.analytics._db() as db:
            db.execute("BEGIN IMMEDIATE")
            access = self._authorization(db, tenant, sid)
            if not access["enabled"]:
                raise AnalyticsError(403, "training_tool_capture_consent_required")
            if not observed and db.execute("SELECT count(*) FROM training_tool_episodes").fetchone()[0] >= MAX_EPISODES:
                raise AnalyticsError(413, "training_capture_capacity")
            if not observed and db.execute(
                "SELECT 1 FROM training_tool_episodes WHERE tenant_id=? AND sid=? AND session_id=?",
                (tenant, sid, session_id),
            ).fetchone():
                raise AnalyticsError(409, "training_capture_requires_fresh_session")
            now = self.analytics.clock()
            key = db.execute("""
                INSERT INTO training_tool_episodes
                (tenant_id,sid,session_id,started_at,updated_at,grants,allowed_tools,state,
                 runtime_profile,runtime_metadata)
                VALUES (?,?,?,?,?,?,?,'capturing',?,?)
            """, (tenant, sid, session_id, now, now, _json(access["grants"]), _json(allowed_tools),
                  runtime_profile, _json(runtime_metadata or {}))).lastrowid
        self.training.audit("capture", outcome="requested", actor="system")
        return {"episode_id": key, "profile": runtime_profile, "allowed_tools": list(allowed_tools)}

    def event(self, tenant, sid, episode_id, kind, payload):
        """Trusted bridge only; V2 appends observations before quality review."""
        from .training_data import _hash, _json, _sensitive

        if type(episode_id) is not int or kind not in {
            "context", "provider_request", "tool_call", "tool_result", "agent_end", "settled", "quarantine", "capture_warning",
        }:
            raise ValueError("Invalid training capture event")
        with self.analytics._db() as db:
            metadata = db.execute(
                "SELECT runtime_profile,runtime_metadata FROM training_tool_episodes WHERE id=? AND tenant_id=? AND sid=?",
                (episode_id, tenant, sid),
            ).fetchone()
        if (metadata is not None and metadata["runtime_profile"] == HOSTED_PROFILE
                and json.loads(metadata["runtime_metadata"]).get("capture_policy") == OBSERVED_POLICY):
            return self._observed_event(tenant, sid, episode_id, kind, payload)
        self.prune()
        with self.controls.capture_lock, self.analytics._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM training_tool_episodes WHERE id=? AND tenant_id=? AND sid=?",
                (episode_id, tenant, sid),
            ).fetchone()
            if row is None:
                raise AnalyticsError(404, "training_episode_not_found")
            if row["state"] != "capturing":
                raise AnalyticsError(409, "training_episode_closed")
            hosted = row["runtime_profile"] == HOSTED_PROFILE
            maximum = HOSTED_EPISODE_BYTES if hosted else MAX_EPISODE_BYTES
            payload_maximum = HOSTED_PAYLOAD_BYTES if hosted else MAX_EPISODE_BYTES
            mission = json.loads(row["runtime_metadata"]).get("mission_id") if hosted else None
            access = self._authorization(db, tenant, sid)
            reason = None
            diagnostic = None
            if not access["enabled"] or access["grants"] != json.loads(row["grants"]):
                reason = "training_capture_consent_changed"
            observations = json.loads(row["record"])
            if reason is None:
                try:
                    if not isinstance(payload, dict) or len(_json(payload)) > payload_maximum:
                        raise ValueError("capture_payload_oversized")
                    fields = {
                        "context": {"messages", "tools"}, "agent_end": {"messages"},
                        "tool_call": {"toolCallId", "toolName", "input"},
                        "tool_result": {"toolCallId", "toolName", "input", "content", "isError", "output_complete"},
                        "settled": set(), "quarantine": {"reason"},
                    }
                    if hosted:
                        fields["provider_request"] = {"messages", "tools", "model"}
                        fields["agent_end"] = {"messages", "private_blocks_excluded"}
                    if kind not in fields:
                        raise ValueError("invalid_capture_order")
                    if set(payload) != fields[kind]:
                        raise ValueError("private_or_nontext_context")
                    if kind in {"context", "agent_end"}:
                        public_messages = payload["messages"]
                        if not isinstance(public_messages, list) or len(public_messages) > (256 if hosted else 32):
                            raise ValueError("private_or_nontext_context")
                        for message in public_messages:
                            if (not isinstance(message, dict) or message.get("role") not in {
                                "user", "assistant", "toolResult",
                            } or message.keys() - {
                                "role", "content", "timestamp", "stopReason", "toolCallId", "toolName", "isError",
                            }):
                                raise ValueError("private_or_nontext_context")
                            self._public_blocks(message.get("content"), maximum=HOSTED_TEXT_CHARS if hosted else MAX_RESULT_CHARS)
                    if kind == "tool_result":
                        self._public_blocks(payload["content"], result=True,
                                            maximum=HOSTED_RESULT_CHARS if hosted else MAX_RESULT_CHARS)
                    if kind in {"tool_call", "tool_result"} and payload["toolName"] not in json.loads(row["allowed_tools"]):
                        raise ValueError("unreviewed_tool_or_document_access")
                    if kind == "tool_result" and payload["output_complete"] is not True:
                        raise ValueError("tool_result_excerpt_truncated")
                    # Scrub in memory first. Never store a modified ideal answer or
                    # silently rewrite arguments; changed/sensitive episodes fail.
                    if hosted:
                        from .training_public_assets import check_public_skill_event

                        try:
                            check_public_skill_event(kind, payload)
                        except ValueError:
                            diagnostic = {"kind": kind, "field": "payload.content" if kind == "tool_result" else "payload.input",
                                          "detector": "public_skill_digest"}
                            raise
                    redacted = redact_secrets_record(payload)
                    clean = _sanitize(redacted)
                    sensitive = (_hosted_sensitive(clean, sid=sid, mission_id=mission)
                                 if hosted else _sensitive(clean))
                    if clean != payload or sensitive:
                        diagnostic = _content_diagnostic(kind, payload, sid=sid, mission_id=mission)
                        raise ValueError("sensitive_capture_content")
                    if kind == "quarantine":
                        reason = payload.get("reason")
                        if reason not in {
                            "private_or_nontext_context", "tool_result_excerpt_truncated",
                            "capture_projection_failed", "capture_transport_failed",
                            "unreviewed_tool_or_document_access",
                            "runtime_call_unsettled", "runtime_profile_changed", "provider_context_mismatch",
                            "session_compacted_or_reused", *INIT_FAILURE_REASONS,
                        }:
                            reason = "capture_projection_failed"
                    elif kind != "settled":
                        observations.append({
                            "id": _hash(_json(["pi_capture", episode_id, len(observations)]).encode()),
                            "sequence": len(observations), "kind": kind,
                            "observed_at": self.analytics.clock(), "payload": clean,
                        })
                    if len(observations) > (HOSTED_OBSERVATIONS if hosted else MAX_OBSERVATIONS) or len(_json(observations)) > maximum:
                        raise ValueError("capture_episode_oversized")
                    if kind == "settled":
                        self._sample(observations, max(access["grants"].values()), hosted=hosted)
                except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
                    safe_codes = {
                        "capture_payload_oversized", "sensitive_capture_content", "capture_episode_oversized",
                        "fresh_public_context_required", "context_or_schema_changed", "invalid_tool_schema",
                        "unmatched_tool_call_result", "incomplete_tool_episode", "tool_result_excerpt_truncated",
                        "invalid_tool_arguments", "private_or_nontext_context", "invalid_capture_order",
                        "unreviewed_tool_or_document_access",
                        "provider_context_mismatch", "runtime_profile_changed", "session_compacted_or_reused",
                        "unverified_public_skill_content",
                    }
                    reason = str(exc) if str(exc) in safe_codes else "malformed_tool_episode"
            state = "quarantined" if reason else "complete" if kind == "settled" else "capturing"
            diagnostic = (diagnostic or {"kind": kind, "field": "payload", "detector": "validation"}) if reason else {}
            record = "[]" if reason else _json(observations)
            size = db.execute(
                "SELECT coalesce(sum(length(record)),0) FROM training_tool_episodes WHERE id!=?",
                (episode_id,),
            ).fetchone()[0]
            if size + len(record) > (128 * 1024 * 1024 if hosted else MAX_CAPTURE_BYTES):
                state, reason, record = "quarantined", "training_capture_capacity", "[]"
            db.execute(
                "UPDATE training_tool_episodes SET state=?,reason=?,record=?,diagnostic=?,updated_at=? WHERE id=?",
                (state, reason, record, _json(diagnostic), self.analytics.clock(), episode_id),
            )
        if state != "capturing":
            self.training.audit("capture", outcome="completed" if state == "complete" else "denied",
                                actor="system", counts={"quarantined": int(state == "quarantined")})
        return {"episode_id": episode_id, "state": state, "reason": reason, "diagnostic": diagnostic}

    def _observed_event(self, tenant, sid, episode_id, kind, payload):
        """Append real observations; formatting and quality never erase history.

        V2 stores each event once in its own row. There is no episode/global row
        count or cumulative payload ceiling. The IPC-sized single-event bound
        produces a visible warning and allows subsequent events to continue.
        """
        from .training_data import _json

        self.prune()
        original_kind = kind
        excluded = 0
        try:
            if not isinstance(payload, dict):
                raise ValueError("invalid_public_payload")
            clean, excluded = self._observed_public_payload(kind, payload)
            encoded = _json(clean)
            if len(encoded.encode()) > HOSTED_PAYLOAD_BYTES:
                raise ValueError("capture_payload_oversized")
        except (TypeError, ValueError, RecursionError, OverflowError) as exc:
            reason = str(exc) if str(exc) in {"invalid_public_payload", "capture_payload_oversized"} else "invalid_public_payload"
            kind, clean = "capture_warning", {"reason": reason, "event_kind": original_kind}
            encoded = _json(clean)
        with self.controls.capture_lock, self.analytics._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM training_tool_episodes WHERE id=? AND tenant_id=? AND sid=?", (episode_id, tenant, sid),
            ).fetchone()
            if row is None:
                raise AnalyticsError(404, "training_episode_not_found")
            if row["state"] != "capturing":
                raise AnalyticsError(409, "training_episode_closed")
            access = self._authorization(db, tenant, sid)
            if not access["enabled"] or access["grants"] != json.loads(row["grants"]):
                raise AnalyticsError(403, "training_capture_consent_changed")
            sequence = db.execute(
                "SELECT coalesce(max(sequence),-1)+1 FROM training_observed_events WHERE episode_id=?", (episode_id,),
            ).fetchone()[0]
            now = self.analytics.clock()
            db.execute(
                "INSERT INTO training_observed_events(episode_id,sequence,kind,observed_at,payload) VALUES(?,?,?,?,?)",
                (episode_id, sequence, kind, now, encoded),
            )
            if excluded:
                db.execute(
                    "INSERT INTO training_observed_events(episode_id,sequence,kind,observed_at,payload) VALUES(?,?,?,?,?)",
                    (episode_id, sequence + 1, "capture_warning", now,
                     _json({"reason": "private_blocks_excluded", "event_kind": original_kind, "count": excluded})),
                )
            state = "complete" if kind == "settled" else "interrupted" if kind == "quarantine" else "capturing"
            reason = clean.get("reason") if kind in {"capture_warning", "quarantine"} else row["reason"]
            if not isinstance(reason, str):
                reason = "capture_interrupted" if kind == "quarantine" else None
            diagnostic = {"kind": kind, "field": "payload", "detector": "observation_warning"} if reason else {}
            db.execute(
                "UPDATE training_tool_episodes SET state=?,reason=?,diagnostic=?,updated_at=? WHERE id=?",
                (state, reason, _json(diagnostic), now, episode_id),
            )
        if state != "capturing":
            self.training.audit("capture", outcome="completed", actor="system")
        return {"episode_id": episode_id, "state": state, "reason": reason, "diagnostic": diagnostic}

    @staticmethod
    def _observed_public_payload(kind, payload):
        """Keep application instructions and IO; omit structured private blocks.

        This deliberately does not scan prose, paths, tool arguments, source
        code, public skill files, or message contents for sensitive-looking words.
        System/developer messages are the observed application's actual inputs.
        """
        excluded = 0
        private_types = {"thinking", "analysis", "reasoning", "redacted_thinking", "signature"}
        private_fields = {"thinking", "analysis", "reasoning", "reasoning_content", "signature", "thinkingSignature"}

        def blocks(content):
            nonlocal excluded
            if not isinstance(content, list):
                return content
            result = []
            for block in content:
                if isinstance(block, dict) and block.get("type") in private_types:
                    excluded += 1
                else:
                    result.append(block)
            return result

        clean = dict(payload)
        if kind in {"context", "provider_request", "agent_end"} and isinstance(payload.get("messages"), list):
            messages = []
            for message in payload["messages"]:
                if not isinstance(message, dict):
                    messages.append(message)
                    continue
                if message.get("role") in private_types or message.get("channel") in private_types:
                    excluded += 1
                    continue
                selected = {key: value for key, value in message.items() if key not in private_fields}
                excluded += len(message) - len(selected)
                if "content" in selected:
                    selected["content"] = blocks(selected["content"])
                messages.append(selected)
            clean["messages"] = messages
        if kind == "tool_result" and "content" in clean:
            clean["content"] = blocks(clean["content"])
        return clean, excluded

    @staticmethod
    def _public_blocks(blocks, *, result=False, maximum=MAX_RESULT_CHARS):
        if not isinstance(blocks, list) or len(blocks) > 32:
            raise ValueError("private_or_nontext_context")
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text" and set(block) == {"type", "text"}:
                if not isinstance(block["text"], str):
                    raise ValueError("private_or_nontext_context")
                if len(block["text"]) > maximum:
                    raise ValueError("tool_result_excerpt_truncated" if result else "capture_payload_oversized")
            elif result or not isinstance(block, dict) or block.get("type") != "toolCall" or set(block) != {
                "type", "id", "name", "arguments",
            }:
                raise ValueError("private_or_nontext_context")

    @staticmethod
    def _sample(events, grant, *, hosted=False):
        """Validate full non-system context, exact schemas and every call/result."""
        from .training_data import _json

        contexts = [e["payload"] for e in events if e["kind"] == "context"]
        endings = [e["payload"] for e in events if e["kind"] == "agent_end"]
        if not contexts or len(endings) != 1 or events[-1]["kind"] != "agent_end":
            raise ValueError("incomplete_tool_episode")
        tools = contexts[0].get("tools")
        if not isinstance(tools, list) or not 1 <= len(tools) <= 32:
            raise ValueError("invalid_tool_schema")
        schemas = {}
        for tool in tools:
            if (not isinstance(tool, dict) or set(tool) != {"name", "description", "parameters"}
                    or not isinstance(tool["name"], str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tool["name"])
                    or tool["name"] in schemas or not isinstance(tool["description"], str)
                    or not isinstance(tool["parameters"], dict)
                    or tool["parameters"].get("type") != "object"
                    or re.search(r'"\$(?:ref|dynamicRef)"\s*:', _json(tool["parameters"]))):
                raise ValueError("invalid_tool_schema")
            try:
                Draft202012Validator.check_schema(tool["parameters"])
            except SchemaError:
                raise ValueError("invalid_tool_schema") from None
            schemas[tool["name"]] = Draft202012Validator(tool["parameters"])

        def messages(value):
            if not isinstance(value, list) or not 1 <= len(value) <= (256 if hosted else 32):
                raise ValueError("private_or_nontext_context")
            result = []
            for message in value:
                if not isinstance(message, dict) or message.get("role") not in {"user", "assistant", "toolResult"}:
                    raise ValueError("private_or_nontext_context")
                role = message["role"]
                allowed = {"role", "content", "timestamp", "stopReason", "toolCallId", "toolName", "isError"}
                if message.keys() - allowed:
                    raise ValueError("private_or_nontext_context")
                content = message.get("content")
                if isinstance(content, str) and role == "user":
                    content = [{"type": "text", "text": content}]
                if not isinstance(content, list):
                    raise ValueError("private_or_nontext_context")
                texts, calls = [], []
                for block in content:
                    if not isinstance(block, dict):
                        raise ValueError("private_or_nontext_context")
                    if block.get("type") == "text" and set(block) == {"type", "text"} and isinstance(block["text"], str):
                        texts.append(block["text"])
                    elif role == "assistant" and block.get("type") == "toolCall" and (
                        set(block) == {"type", "id", "name", "arguments"}
                        and isinstance(block["id"], str) and re.fullmatch(r"[A-Za-z0-9_.:|-]{1,160}", block["id"])
                        and block["name"] in schemas and isinstance(block["arguments"], dict)
                    ):
                        arguments = block["arguments"]
                        if hosted:
                            from .training_schema import pi_execution_arguments

                            arguments = pi_execution_arguments(arguments, schemas[block["name"]].schema)
                        if not schemas[block["name"]].is_valid(arguments):
                            raise ValueError("invalid_tool_arguments")
                        calls.append({"id": block["id"], "type": "function",
                                      "function": {"name": block["name"], "arguments": block["arguments"]}})
                    else:
                        raise ValueError("private_or_nontext_context")
                if len(texts) > 1 or (not texts and not calls):
                    raise ValueError("private_or_nontext_context")
                item = {"role": "tool" if role == "toolResult" else role}
                if texts:
                    item["content"] = texts[0]
                if calls:
                    item["tool_calls"] = calls
                if role == "toolResult":
                    if ((type(message.get("isError")) is not bool if hosted else message.get("isError") is not False)
                            or not texts or len(texts[0]) > (HOSTED_RESULT_CHARS if hosted else MAX_RESULT_CHARS)):
                        raise ValueError("tool_result_excerpt_truncated")
                    item.update(tool_call_id=message.get("toolCallId"), name=message.get("toolName"))
                if role == "assistant" and message.get("stopReason") != ("toolUse" if calls else "stop"):
                    raise ValueError("incomplete_tool_episode")
                result.append(item)
            return result

        first = contexts[0].get("messages")
        if (not isinstance(first, list) or len(first) != 1 or first[0].get("role") != "user"
                or type(first[0].get("timestamp")) not in (int, float)
                or not grant <= first[0]["timestamp"] / 1000 <= events[0]["observed_at"]):
            raise ValueError("fresh_public_context_required")
        final = messages(endings[0].get("messages"))
        if (final[0] != messages(first)[0] or final[-1].get("role") != "assistant"
                or final[-1].get("tool_calls") or not final[0].get("content", "").strip()
                or not final[-1].get("content", "").strip()):
            raise ValueError("incomplete_tool_episode")
        prefixes = [final[:i] for i, message in enumerate(final) if message["role"] == "assistant"]
        if len(contexts) != len(prefixes):
            raise ValueError("context_or_schema_changed")
        for context, prefix in zip(contexts, prefixes):
            if context.get("tools") != tools or messages(context.get("messages")) != prefix:
                raise ValueError("context_or_schema_changed")
        calls, results = {}, {}
        pending = set()
        for index, message in enumerate(final):
            if message["role"] == "user":
                if index:
                    raise ValueError("fresh_public_context_required")
            elif message["role"] == "assistant":
                if pending:
                    raise ValueError("unmatched_tool_call_result")
                for call in message.get("tool_calls", []):
                    if call["id"] in calls:
                        raise ValueError("unmatched_tool_call_result")
                    calls[call["id"]] = call["function"]
                    pending.add(call["id"])
            elif message["role"] == "tool":
                key = message["tool_call_id"]
                if key not in pending or message["name"] != calls[key]["name"]:
                    raise ValueError("unmatched_tool_call_result")
                pending.remove(key)
                results[key] = message["content"]
        if not calls or pending:
            raise ValueError("unmatched_tool_call_result")
        observed_calls, observed_results = {}, {}
        turn, allowed_calls = -1, set()
        for event in events:
            if event["kind"] == "context":
                if not allowed_calls <= observed_results.keys():
                    raise ValueError("invalid_capture_order")
                turn += 1
                allowed_calls = {
                    call["id"] for call in final[len(prefixes[turn])].get("tool_calls", [])
                }
            if event["kind"] not in {"tool_call", "tool_result"}:
                continue
            payload = event["payload"]
            key = payload.get("toolCallId")
            expected_input = calls[key]["arguments"] if key in calls else None
            if hosted and key in calls:
                from .training_schema import pi_execution_arguments

                expected_input = pi_execution_arguments(expected_input, schemas[calls[key]["name"]].schema)
            if (key not in allowed_calls or key not in calls or payload.get("toolName") != calls[key]["name"]
                    or payload.get("input") != expected_input):
                raise ValueError("unmatched_tool_call_result")
            target = observed_calls if event["kind"] == "tool_call" else observed_results
            if key in target:
                raise ValueError("unmatched_tool_call_result")
            if event["kind"] == "tool_result":
                if (key not in observed_calls or (type(payload.get("isError")) is not bool if hosted else payload.get("isError") is not False)
                        or payload.get("output_complete") is not True or payload.get("content") != [
                    {"type": "text", "text": results[key]},
                ]):
                    raise ValueError("unmatched_tool_call_result")
            target[key] = True
        if set(observed_calls) != set(calls) or set(observed_results) != set(calls):
            raise ValueError("unmatched_tool_call_result")
        sample = {"messages": final, "tools": [
            {"type": "function", "function": tool} for tool in tools
        ]}
        if hosted:
            requests = [event["payload"] for event in events if event["kind"] == "provider_request"]
            if len(requests) != len(prefixes):
                raise ValueError("provider_context_mismatch")
            # These are the actual outgoing provider payloads, projected before
            # transport: exclude only system/private fields, never reconstruct a
            # schema or tool result from a UI activity label.
            for request, prefix in zip(requests, prefixes):
                provider_tools = request["tools"]
                if not isinstance(provider_tools, list):
                    raise ValueError("provider_context_mismatch")
                if len(provider_tools) != len(tools):
                    raise ValueError("provider_context_mismatch")
                runtime_by_name = {tool["name"]: tool for tool in tools}
                provider_names = set()
                for tool in provider_tools:
                    if (not isinstance(tool, dict) or set(tool) != {"type", "function"}
                            or tool["type"] != "function" or not isinstance(tool["function"], dict)
                            or tool["function"].keys() - {"name", "description", "parameters", "strict"}
                            or ("strict" in tool["function"] and type(tool["function"]["strict"]) is not bool)):
                        raise ValueError("provider_context_mismatch")
                    from .training_schema import pi_schema_equal, pi_strict_schema

                    function = tool["function"]
                    name = function.get("name")
                    if not isinstance(name, str) or name not in runtime_by_name or name in provider_names:
                        raise ValueError("provider_context_mismatch")
                    provider_names.add(name)
                    runtime_tool = runtime_by_name[name]
                    expected_parameters = (pi_strict_schema(runtime_tool["parameters"])
                                           if function.get("strict") is True else runtime_tool["parameters"])
                    if (function.get("name") != runtime_tool["name"]
                            or function.get("description") != runtime_tool["description"]
                            or not pi_schema_equal(function.get("parameters"), expected_parameters)):
                        raise ValueError("provider_context_mismatch")
                    validator = Draft202012Validator(function["parameters"])
                    if any(call["name"] == function["name"] and not validator.is_valid(call["arguments"])
                           for call in calls.values()):
                        raise ValueError("invalid_tool_arguments")
                if request["messages"] != prefix:
                    raise ValueError("provider_context_mismatch")
                if provider_tools != requests[0]["tools"]:
                    raise ValueError("provider_context_mismatch")
            expected_order = []
            for event in events:
                if event["kind"] in {"context", "provider_request"}:
                    expected_order.append(event["kind"])
            if expected_order != [item for _ in prefixes for item in ("context", "provider_request")]:
                raise ValueError("provider_context_mismatch")
            sample["tools"] = requests[0]["tools"]
        return sample

    def collect(self, db, tenant, sid, purpose, grant, approved, context_approved):
        from .training_data import NOTICE_VERSION, _hash, _json

        cutoff = self.analytics.clock() - min(30, self.analytics.retention_days) * 86400
        rows = db.execute(
            "SELECT * FROM training_tool_episodes WHERE tenant_id=? AND sid=? AND started_at>=? ORDER BY id",
            (tenant, sid, max(grant, cutoff)),
        ).fetchall()
        candidates, trajectories, provenance = [], [], []
        for row in rows:
            if json.loads(row["grants"]).get(purpose) != grant:
                continue
            key = _hash(_json(["pi_episode", tenant, sid, row["id"]]).encode())
            origin = {
                "event_id": key, "tenant_id": tenant, "sid": sid, "episode_id": row["id"],
                "session_id": row["session_id"], "source": row["runtime_profile"],
                "runtime": json.loads(row["runtime_metadata"]),
                "consent": {"purpose": purpose, "notice_version": NOTICE_VERSION, "granted_at": grant},
                "disposition": "diagnostic",
            }
            provenance.append(origin)
            if row["state"] != "complete":
                origin.update(disposition="quarantined", reason=row["reason"] or "capture_not_settled",
                              diagnostic=json.loads(row["diagnostic"]))
                continue
            events = json.loads(row["record"])
            try:
                from .training_data import _sensitive

                hosted = row["runtime_profile"] == HOSTED_PROFILE
                sensitive = (_hosted_sensitive(events, sid=sid, mission_id=origin["runtime"].get("mission_id"))
                             if hosted else _sensitive(events))
                if sensitive:
                    raise ValueError("sensitive_capture_content")
                sample = self._sample(events, grant, hosted=hosted)
            except (ValueError, TypeError, KeyError, IndexError):
                origin.update(disposition="quarantined", reason="malformed_tool_episode")
                continue
            trajectories.append({"id": key, "kind": "pi.training_episode", "tenant_id": tenant,
                                 "sid": sid, "session_id": row["session_id"], "events": events,
                                 "complete": False, "public_episode_complete": True,
                                 "runtime_profile": row["runtime_profile"],
                                 "runtime": json.loads(row["runtime_metadata"]),
                                 "private_blocks_excluded": bool(hosted),
                                 "model_context_complete": False})
            quality = key in approved and context_approved is True
            runtime = json.loads(row["runtime_metadata"])
            candidates.append({
                "tenant_id": tenant, "sid": sid, "event_id": key,
                "event_ids": [event["id"] for event in events], "task_id": runtime.get("mission_id"),
                "runtime": runtime,
                "sample": sample, "quality_approved": quality,
                "quality_evidence": {"kind": "operator_tool_context_review", "event_id": key} if quality else None,
                "sample_complete": True, "global_complete": False,
                "scope": "fresh_pi_public_tool_episode", "context_review_required": True,
                "runtime_profile": row["runtime_profile"],
                "private_blocks_excluded": bool(hosted), "model_context_complete": False,
            })
        return candidates, trajectories, provenance


def _content_diagnostic(kind, payload, *, sid, mission_id):
    """Fixed detector names and structural fields only, never matched text."""
    from .training_data import _SENSITIVE

    known = {"messages", "content", "text", "tools", "parameters", "properties", "description", "input",
             "path", "command", "pattern", "glob", "oldText", "newText", "edits", "function", "arguments",
             "tool_calls", "name", "toolName", "toolCallId", "model", "role", "type", "payload"}

    def walk(value, field="payload", depth=0):
        if depth > 30:
            return None
        if isinstance(value, str):
            if redact_secrets_record(value) != value:
                return {"kind": kind, "field": field, "detector": "secret_redactor"}
            if _sanitize(value) != value:
                return {"kind": kind, "field": field, "detector": "analytics_sanitizer"}
            match = _SENSITIVE.search(_public_path_scan_text(value, sid, mission_id))
            if match:
                detector = "private_path" if re.search(r"/home/|/Users/|/data/|/root/|/mnt/|[A-Z]:\\|\\\\", match[0], re.I) else "sensitive_pattern"
                return {"kind": kind, "field": field, "detector": detector}
        elif isinstance(value, dict):
            for key, item in value.items():
                child = field + "." + (key if key in known else "*")
                if _sanitize({key: "public-probe"}) != {key: "public-probe"}:
                    return {"kind": kind, "field": child, "detector": "analytics_sanitizer"}
                result = walk(str(key), field + ".*", depth + 1) or walk(item, child, depth + 1)
                if result:
                    return result
        elif isinstance(value, list):
            for item in value:
                result = walk(item, field + "[]", depth + 1)
                if result:
                    return result
        return None

    return walk(payload) or {"kind": kind, "field": "payload", "detector": "content_filter"}


def _workspace_path_scan_text(value, workspace):
    """Mask bound POSIX path literals, leaving other paths and all text intact.

    This is lexical, never a filesystem/symlink lookup or a shell expansion.
    Quotes delimit a literal; ordinary POSIX filename characters (including
    spaces, Unicode and globs) are not an ASCII allowlist. Ambiguous traversal
    and encoded/Windows separators retain the original private-home marker.
    """
    output = list(value)
    plain_end = 0
    plain_unsafe = -1
    unsafe_path = re.compile(r"(?:^|[/\\])\.{1,2}(?=[/\\]|$)|\\|[\x00-\x1f\x7f]|%(?:2e|2f|5c)", re.I)
    new_absolute = re.compile(r"[\s,:=(\[{]/")
    root = re.compile(r"(?<![\w/.\\%+-])" + re.escape(workspace)
                      + r"(?=/|$|[\s\"'`<>;,|&])")
    for match in root.finditer(value):
        start, end = match.span()
        if end < len(value) and value[end] == "/":
            initial_quote = value[start - 1] if start and value[start - 1] in "\"'`" else None
            quote = initial_quote
            saw_quote = bool(quote)
            cached_plain = end < plain_end and not quote
            cursor = plain_end if cached_plain else end
            while not cached_plain and cursor < len(value):
                char = value[cursor]
                if char in "\r\n":
                    break
                if quote:
                    if char == quote:
                        if initial_quote:
                            break
                        quote = None
                elif char in "\"'`":
                    quote = char
                    saw_quote = True
                elif char in ";|&<>":
                    break
                cursor += 1
            # Check before splitting on spaces/punctuation. Otherwise a path
            # such as "workdir/a [1]/../../private" could lose its home marker
            # while its traversal survives as an apparently relative string.
            if not saw_quote:
                if not cached_plain:
                    plain_end = cursor
                    plain_unsafe = max((m.start() for m in unsafe_path.finditer(value, end, cursor)), default=-1)
                unsafe = plain_unsafe >= end
            else:
                components = value[end:cursor].translate(str.maketrans("", "", "\"'`"))
                unsafe = unsafe_path.search(components) is not None
            if unsafe:
                continue
            # A second absolute path must remain visible to the private-path
            # detector, including colon-separated path lists and diagnostics.
            boundary = new_absolute.search(value, end, cursor)
            if boundary:
                cursor = boundary.start()
            if not initial_quote:
                inner_quote = re.search(r"[\"'`]", value[end:cursor])
                if inner_quote:
                    cursor = end + inner_quote.start()
            end = cursor
        for index in range(start, end):
            if value[index] == "/":
                output[index] = " "
    return "".join(output)
def _public_path_scan_text(value, sid, mission_id):
    """Exempt only bound platform path literals in a disposable scan copy.

    Neither the stored bytes nor tool access changes. In particular, raw model
    logs and other project/home files never acquire an exemption. Path suffixes
    remain in the scan so secret/PII-shaped filenames are still rejected.
    """
    if not isinstance(sid, str) or not re.fullmatch(r"s-[a-f0-9]{8}", sid):
        return value
    workspace = f"/tenant/home/.argus-skill/workspaces/{sid}"
    value = _workspace_path_scan_text(value, workspace)
    # The normal role-library and durable-learning templates publish these
    # directory names. This exempts the names only, never arbitrary skill
    # files, their bodies, another project, or the surrounding home directory.
    public_directories = {
        "/tenant/home/.argus-skill",
        f"/tenant/home/.argus-skill/projects/{sid}/skills",
        f"/tenant/home/.argus-skill/projects/{sid}/skills/engineer",
        f"/tenant/home/.argus-skill/projects/{sid}/skills/reviewer",
    }
    handoffs = set()
    if isinstance(mission_id, str) and re.fullmatch(r"[a-f0-9]{12}", mission_id):
        root = f"/tenant/home/.argus-skill/projects/{sid}/handoffs/{mission_id}"
        handoffs = {root + "/" + name for name in (
            "mission.json", "latest.json", "frontier.json", "CHECKPOINT.md",
            "role-sessions/engineer.json", "role-sessions/reviewer.json",
        )}

    def replace(match):
        from .training_public_assets import public_skill_literal

        path = match[0]
        if path in handoffs or path in public_directories or public_skill_literal(path):
            # Keep all segment text for the credential/PII detectors while
            # removing the private-home interpretation of this exact path.
            return path.replace("/", " ")
        return path

    return re.sub(r"(?<![\w/.\\%+-])/tenant/home/\.argus-skill(?:/[^\s\"'`<>;,|]+)?"
                  r"(?=$|[\s\"'`<>;,|])", replace, value)


def _hosted_sensitive(value, *, sid=None, mission_id=None):
    """Inspect the public projection, not words like 'reasoning' in user text.

    Private content is excluded structurally by the read-only producer. Public
    research questions may discuss reasoning or include code using such names.
    Credential/PII matches still quarantine rather than rewriting training text.
    """
    from .training_data import _SENSITIVE

    if isinstance(value, str):
        return bool(_SENSITIVE.search(_public_path_scan_text(value, sid, mission_id)))
    if isinstance(value, dict):
        return any(_hosted_sensitive(str(key), sid=sid, mission_id=mission_id)
                   or _hosted_sensitive(item, sid=sid, mission_id=mission_id) for key, item in value.items())
    if isinstance(value, list):
        return any(_hosted_sensitive(item, sid=sid, mission_id=mission_id) for item in value)
    return False


# Factory for a parent-owned trusted-local transport, deliberately not autoloaded.
# No file/network transport, credentials, headers or system prompts are embedded.
PI_EXTENSION_SOURCE = r"""
export function trainingExtension(submit) {
  async function request(action, value) {
    let timer;
    try {
      return await Promise.race([
        submit(action, value),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("capture_timeout")), 1000); })
      ]);
    } finally { clearTimeout(timer); }
  }
  return function(pi) {
    let episode = null;
    let allowed = new Set();
    const project = async (kind, makePayload) => {
      if (episode === null) return;
      try {
        const access = await request("authorize", {});
        if (!access.enabled) { episode = null; return; }
        let nodes = 8192;
        function bounded(value, depth = 0) {
          if (--nodes < 0 || depth > 20) throw new Error("bounded");
          if (typeof value === "string" && value.length > 4096) throw new Error("bounded");
          if (Array.isArray(value)) return value.map(item => bounded(item, depth + 1));
          if (value && typeof value === "object") return Object.fromEntries(
            Object.entries(value).map(([key, item]) => [key, bounded(item, depth + 1)]));
          return value;
        }
        const payload = bounded(makePayload());
        if (JSON.stringify(payload).length > 131072) throw new Error("bounded");
        const answer = await request("event", {episode_id: episode, kind, payload});
        if (answer.state !== "capturing") episode = null;
      } catch (error) {
        const reason = String(error?.message).includes("private") ? "private_or_nontext_context"
          : kind === "tool_result" && String(error?.message).includes("bounded")
            ? "tool_result_excerpt_truncated" : "capture_projection_failed";
        try { await request("event", {episode_id: episode, kind: "quarantine",
                                   payload: {reason}}); } catch (_) {}
        episode = null;
      }
    };
    function content(blocks) {
      if (!Array.isArray(blocks)) throw new Error("nontext");
      return blocks.map(block => {
        if (block.type === "text") {
          if (typeof block.text !== "string" || block.text.length > 4096) throw new Error("bounded");
          return {type: "text", text: block.text};
        }
        if (block.type === "toolCall") {
          if (block.namespace || block.thoughtSignature) throw new Error("private");
          if (!allowed.has(block.name)) throw new Error("document_or_unreviewed_tool");
          return {type: "toolCall", id: block.id, name: block.name, arguments: block.arguments};
        }
        // Never copy private thinking, signatures, images or arbitrary details.
        throw new Error("private_or_nontext");
      });
    }
    function messages(items) {
      if (!Array.isArray(items) || items.length > 32) throw new Error("bounded");
      return items.map(message => {
        if (!["user", "assistant", "toolResult"].includes(message.role)) throw new Error("private");
        const body = typeof message.content === "string"
          ? content([{type: "text", text: message.content}]) : content(message.content);
        const value = {role: message.role, content: body, timestamp: message.timestamp};
        if (message.role === "assistant") value.stopReason = message.stopReason;
        if (message.role === "toolResult") Object.assign(value, {
          toolCallId: message.toolCallId, toolName: message.toolName, isError: message.isError
        });
        return value;
      });
    }
    pi.on("agent_start", async (_event, ctx) => {
      try {
        if (episode !== null) {
          await project("quarantine", () => ({reason: "capture_projection_failed"}));
          return;
        }
        const access = await request("authorize", {});
        if (!access.enabled) return;
        const begun = await request("begin", {session_id: ctx.sessionManager.getSessionId()});
        episode = begun.episode_id;
        allowed = new Set(begun.allowed_tools);
      } catch (_) { episode = null; }
    });
    pi.on("context", async event => {
      await project("context", () => {
        const active = pi.getActiveTools();
        if (active.length > 32) throw new Error("bounded");
        const tools = pi.getAllTools().filter(tool => active.includes(tool.name)).map(tool => ({
          name: tool.name, description: tool.description, parameters: tool.parameters
        }));
        if (tools.length !== active.length) throw new Error("incomplete_schema_snapshot");
        return {messages: messages(event.messages), tools};
      });
    });
    pi.on("tool_call", async event => {
      if (!allowed.has(event.toolName)) {
        await project("quarantine", () => ({reason: "unreviewed_tool_or_document_access"}));
        return;
      }
      await project("tool_call", () => ({
        toolCallId: event.toolCallId, toolName: event.toolName, input: event.input
      }));
    });
    pi.on("tool_result", async event => {
      if (!allowed.has(event.toolName)) {
        await project("quarantine", () => ({reason: "unreviewed_tool_or_document_access"}));
        return;
      }
      await project("tool_result", () => ({
        toolCallId: event.toolCallId, toolName: event.toolName, input: event.input,
        content: content(event.content), isError: event.isError,
        output_complete: !event.details?.truncation?.truncated && !event.details?.fullOutputPath
      }));
    });
    pi.on("agent_end", async event => {
      await project("agent_end", () => ({messages: messages(event.messages)}));
    });
    pi.on("agent_settled", async () => { await project("settled", () => ({})); });
  };
}
"""
