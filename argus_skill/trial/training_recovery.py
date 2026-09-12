"""Explicit recovery of cleared legacy Pi sessions; never run during startup.

Native message logs cannot reconstruct provider requests or tool schemas. This
entry point only retains their public messages, in file order, under the saved
episode identity. It neither prunes other episodes nor approves training data.
"""
from __future__ import annotations

import json
import math
import os
import re
import stat
from collections import Counter
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

from .analytics import AnalyticsError, _directory
from .collaboration_data import _role
from .training_capture import HOSTED_PROFILE, OBSERVED_POLICY
from .training_data import _json

_SESSION_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_SESSION_PARTS = ("home", ".argus-skill", "pi-sessions")
_MESSAGE_FIELDS = frozenset({
    "role", "content", "timestamp", "api", "provider", "model", "usage",
    "stopReason", "errorMessage", "toolCallId", "toolName", "isError",
})
_BLOCK_FIELDS = {
    "text": {"type", "text"},
    "image": {"type", "data", "mimeType"},
    "toolCall": {"type", "id", "name", "arguments"},
}


class _Skip(Exception):
    pass


def _number(value):
    return type(value) in {int, float} and math.isfinite(value)


def _timestamp(value):
    if not isinstance(value, str):
        raise _Skip("invalid_source_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        stamp = parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        raise _Skip("invalid_source_timestamp") from None
    if not _number(stamp):
        raise _Skip("invalid_source_timestamp")
    return stamp


def _json_object(line):
    def reject_constant(_value):
        raise ValueError

    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        entry = json.loads(line, parse_constant=reject_constant, object_pairs_hook=unique_fields)
    except (ValueError, UnicodeError, RecursionError):
        raise _Skip("invalid_source_jsonl") from None
    if not isinstance(entry, dict):
        raise _Skip("invalid_source_jsonl")
    return entry


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class _SessionSource:
    """Open only the configured tenant's native directory, without symlinks."""

    def __init__(self, root, session_id, *, parts=_SESSION_PARTS):
        self.root = Path(root).absolute()
        self.session_id = session_id
        self.parts = parts
        self.stack = ExitStack()

    def __enter__(self):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            self.directories = []
            descriptor = self.stack.enter_context(_directory(self.root))
            self.directories.append((descriptor, os.fstat(descriptor)))
            for part in self.parts:
                descriptor = os.open(part, flags, dir_fd=descriptor)
                self.stack.callback(os.close, descriptor)
                self.directories.append((descriptor, os.fstat(descriptor)))
            names = self._matches()
            if not names:
                raise _Skip("session_file_missing")
            if len(names) != 1:
                raise _Skip("session_file_ambiguous")
            self.name = names[0]
            self.fd = os.open(self.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
            self.stack.callback(os.close, self.fd)
            self.info = os.fstat(self.fd)
            if not stat.S_ISREG(self.info.st_mode) or self.info.st_nlink != 1:
                raise _Skip("unsafe_session_file")
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def _matches(self):
        suffix = f"_{self.session_id}.jsonl"
        return [name for name in os.listdir(self.directories[-1][0]) if name.endswith(suffix)]

    def check(self):
        try:
            current = os.stat(self.root, follow_symlinks=False)
            for index, (descriptor, original) in enumerate(self.directories):
                if (current.st_dev, current.st_ino, current.st_mode) != (
                    original.st_dev, original.st_ino, original.st_mode,
                ):
                    raise _Skip("session_file_changed")
                if index < len(self.parts):
                    current = os.stat(self.parts[index], dir_fd=descriptor, follow_symlinks=False)
            if self._matches() != [self.name]:
                raise _Skip("session_file_changed")
            current = os.stat(self.name, dir_fd=self.directories[-1][0], follow_symlinks=False)
            if _fingerprint(current) != _fingerprint(self.info) or _fingerprint(os.fstat(self.fd)) != _fingerprint(self.info):
                raise _Skip("session_file_changed")
        except OSError:
            raise _Skip("session_file_changed") from None

    def read(self, training, floor, now):
        messages, ignored, omitted = [], Counter(), Counter()
        header, source_count, excluded, unsupported = None, 0, 0, 0
        with os.fdopen(os.dup(self.fd), "rb") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    raise _Skip("invalid_source_jsonl")
                entry = _json_object(line)
                if line_number == 1:
                    if entry.get("type") != "session" or entry.get("id") != self.session_id:
                        raise _Skip("session_header_mismatch")
                    if _timestamp(entry.get("timestamp")) > now:
                        raise _Skip("invalid_source_timestamp")
                    header = entry
                    continue
                if entry.get("type") == "session":
                    raise _Skip("session_header_mismatch")
                if entry.get("type") != "message":
                    kind = entry.get("type")
                    ignored[kind if kind in {"model_change", "thinking_level_change", "compaction", "branch_summary"} else "other"] += 1
                    continue
                source_count += 1
                stamp = _timestamp(entry.get("timestamp"))
                message = entry.get("message")
                if not isinstance(message, dict):
                    raise _Skip("invalid_source_message")
                original_stamp = message.get("timestamp")
                if original_stamp is not None and not _number(original_stamp):
                    raise _Skip("invalid_source_timestamp")
                if stamp < floor or (original_stamp is not None and original_stamp / 1000 < floor):
                    omitted["before_authorization_or_retention"] += 1
                    continue
                if stamp > now or (original_stamp is not None and original_stamp / 1000 > now):
                    omitted["future_timestamp"] += 1
                    continue
                clean, removed = training.capture._observed_public_payload("context", {"messages": [message]})
                excluded += removed
                if not clean["messages"]:
                    omitted["private_message"] += 1
                    continue
                public = clean["messages"][0]
                if public.get("role") not in {"user", "assistant", "toolResult", "system", "developer"}:
                    omitted["unsupported_message_role"] += 1
                    continue
                excluded += int("thoughtSignature" in public)
                public = {key: value for key, value in public.items() if key in _MESSAGE_FIELDS}
                content = public.get("content")
                if isinstance(content, list):
                    blocks = []
                    for block in content:
                        fields = _BLOCK_FIELDS.get(block.get("type")) if isinstance(block, dict) else None
                        if fields is None:
                            unsupported += 1
                            continue
                        # Project provider block fields only. Arguments are public
                        # business data: a key named thoughtSignature there survives.
                        excluded += sum(key in {"thoughtSignature", "thinkingSignature", "signature"} for key in block)
                        blocks.append({key: value for key, value in block.items() if key in fields})
                    public["content"] = blocks
                elif not isinstance(content, str):
                    raise _Skip("invalid_source_message")
                payload = {"messages": [public], "source_kind": "pi_session_jsonl", "native_line": line_number}
                if isinstance(entry.get("id"), str):
                    payload["native_entry_id"] = entry["id"]
                if isinstance(entry.get("parentId"), str) or entry.get("parentId") is None:
                    payload["native_parent_id"] = entry.get("parentId")
                messages.append((stamp, payload))
        self.check()
        if header is None:
            raise _Skip("invalid_source_jsonl")
        if not messages:
            raise _Skip("no_eligible_public_messages")
        metadata = {
            "source": "pi_session_jsonl", "source_file": str(Path(*_SESSION_PARTS, self.name)),
            "source_session_id": self.session_id, "source_session_timestamp": header["timestamp"],
            "source_bytes": self.info.st_size,
            "source_mtime_ns": self.info.st_mtime_ns,
            "source_device": self.info.st_dev, "source_inode": self.info.st_ino,
            "source_message_count": source_count, "retained_message_count": len(messages),
            "retained_first_observed_at": min(stamp for stamp, _ in messages),
            "retained_last_observed_at": max(stamp for stamp, _ in messages),
            "excluded_private_fields_or_blocks": excluded,
            "unsupported_content_blocks_excluded": unsupported,
            "omitted_messages": dict(omitted), "ignored_entries": dict(ignored),
            "scope": "native_session_message_entries_in_file_order",
            "includes_resumed_turns_and_branches": True,
            "original_episode_context_available": False,
            "provider_requests_available": False, "tool_schemas_available": False,
        }
        return messages, metadata


def _eligible(training, db, episode_id):
    row = db.execute("SELECT * FROM training_tool_episodes WHERE id=?", (episode_id,)).fetchone()
    if row is None:
        raise _Skip("episode_not_found")
    row = dict(row)
    try:
        runtime, grants = json.loads(row["runtime_metadata"]), json.loads(row["grants"])
        record = json.loads(row["record"])
    except (TypeError, ValueError):
        raise _Skip("invalid_episode_metadata") from None
    if not isinstance(runtime, dict) or not isinstance(grants, dict):
        raise _Skip("invalid_episode_metadata")
    try:
        access = training.capture._authorization(db, row["tenant_id"], row["sid"])
    except AnalyticsError:
        raise _Skip("episode_authorization_unavailable") from None
    if not access["enabled"] or access["grants"] != grants:
        raise _Skip("training_capture_consent_changed")
    now = training.analytics.clock()
    if not _number(now) or not grants or any(not _number(stamp) for stamp in grants.values()):
        raise _Skip("invalid_episode_timestamp")
    floor = max(now - min(30, training.analytics.retention_days) * 86400, *grants.values())
    if any(not _number(row[field]) or not floor <= row[field] <= now for field in ("started_at", "updated_at")):
        raise _Skip("episode_outside_authorized_retention")
    if runtime.get("capture_policy") == OBSERVED_POLICY and isinstance(runtime.get("recovery"), dict):
        raise _Skip("already_recovered")
    if (row["state"] != "quarantined" or record != [] or "capture_policy" in runtime
            or "recovery" in runtime or row["runtime_profile"] != HOSTED_PROFILE):
        raise _Skip("episode_not_eligible")
    if _role(runtime.get("run_label")) not in {"manager", "planner", "engineer", "reviewer"}:
        raise _Skip("unsupported_legacy_role")
    if not isinstance(row["session_id"], str) or not _SESSION_ID.fullmatch(row["session_id"]):
        raise _Skip("invalid_native_session_id")
    if db.execute("SELECT 1 FROM training_observed_events WHERE episode_id=? LIMIT 1", (episode_id,)).fetchone():
        raise _Skip("observations_already_exist")
    return row, runtime, floor, now


def recover_episode(training, episode_id):
    """Recover one authorized cleared episode from any of the four roles, atomically.

    Call explicitly with the owning TrainingData instance. Results contain only
    status/counts; no source message text. Ineligible or changing sources leave
    the database untouched. Repeating a successful call cannot append duplicates.
    """
    if type(episode_id) is not int or not 0 < episode_id < 2**63:
        raise ValueError("A positive episode ID is required")
    try:
        with training.analytics._db() as db:
            original, runtime, floor, now = _eligible(training, db, episode_id)
        tenant = training.analytics._tenant(original["tenant_id"])
        root = tenant.get("global_root", tenant["data_dir"] / "home/.argus-skill")
        with _SessionSource(root, original["session_id"], parts=("pi-sessions",)) as source:
            messages, metadata = source.read(training, floor, now)
            with training.controls.capture_lock, training.analytics._db() as db:
                db.execute("BEGIN IMMEDIATE")
                current, _, current_floor, recovered_at = _eligible(training, db, episode_id)
                if current != original:
                    raise _Skip("episode_changed")
                if any(stamp < current_floor or stamp > recovered_at
                       or (payload["messages"][0].get("timestamp") is not None
                           and not current_floor <= payload["messages"][0]["timestamp"] / 1000 <= recovered_at)
                       for stamp, payload in messages):
                    raise _Skip("authorized_retention_changed")
                source.check()
                metadata.update({
                    "recovered_at": recovered_at, "original_state": original["state"],
                    "original_reason": original["reason"],
                    "original_started_at": original["started_at"], "original_updated_at": original["updated_at"],
                    "binding": {"episode_id": episode_id, "tenant_id": original["tenant_id"],
                                "sid": original["sid"], "session_id": original["session_id"],
                                "mission_id": runtime.get("mission_id")},
                })
                runtime.update(capture_policy=OBSERVED_POLICY, source_kind="pi_session_jsonl", recovery=metadata)
                db.executemany(
                    "INSERT INTO training_observed_events(episode_id,sequence,kind,observed_at,payload) VALUES(?,?,'session_message',?,?)",
                    [(episode_id, sequence, stamp, _json(payload)) for sequence, (stamp, payload) in enumerate(messages)],
                )
                db.execute(
                    "UPDATE training_tool_episodes SET state='interrupted',reason='native_session_recovered',runtime_metadata=?,updated_at=? WHERE id=?",
                    (_json(runtime), max(original["updated_at"], metadata["retained_last_observed_at"]), episode_id),
                )
                source.check()
        return {"episode_id": episode_id, "status": "recovered", "state": "interrupted", "event_count": len(messages)}
    except _Skip as exc:
        reason = str(exc)
        return {"episode_id": episode_id, "status": "already_recovered" if reason == "already_recovered" else "skipped", "reason": reason}
    except OSError:
        return {"episode_id": episode_id, "status": "skipped", "reason": "session_source_unavailable"}
