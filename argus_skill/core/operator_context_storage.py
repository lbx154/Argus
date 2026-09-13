"""Atomic checkpoints for one operator-context revision namespace.

Checkpoint records keep their original revisions. The closed prefix prevents
discarded revoked/consumed history from being reintroduced by a stale tail.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FORMAT = "operator-context-v2"
DELIVERY_FORMAT = "operator-context-v3"
DELIVERY_STATE_KEY = "delivery_replay"
MAX_OPERATOR_DELIVERY_STREAMS = 64
MAX_OPERATOR_DELIVERY_RECEIPTS = 256
MAX_OPERATOR_DELIVERY_COUNTER = (1 << 63) - 1
MAX_SOURCE_BYTES = 16 * 1024 * 1024

_STREAM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def encode(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode()


def operator_delivery_identity(value: Any, *, prefix: bool = False) -> dict[str, Any]:
    """Validate a host-issued stream identity without coercing malformed counters."""
    keys = {"stream", "generation", "sequence"} | (set() if prefix else {"digest"})
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("operator delivery identity has invalid fields")
    stream = value["stream"]
    if not isinstance(stream, str) or not _STREAM_ID.fullmatch(stream):
        raise ValueError("operator delivery stream is invalid")
    for key in ("generation", "sequence"):
        number = value[key]
        if type(number) is not int or not 1 <= number <= MAX_OPERATOR_DELIVERY_COUNTER:
            raise ValueError("operator delivery counter is invalid")
    if not prefix and (not isinstance(value["digest"], str) or not _SHA256.fullmatch(value["digest"])):
        raise ValueError("operator delivery digest is invalid")
    return {key: value[key] for key in sorted(keys)}


def validate_operator_delivery_state(state: dict[str, Any], revision: int) -> None:
    """Reject corrupt or unbounded replay metadata before projecting authority."""
    if DELIVERY_STATE_KEY not in state:
        return
    replay = state[DELIVERY_STATE_KEY]
    if (
        not isinstance(replay, dict) or set(replay) != {"version", "streams"}
        or type(replay["version"]) is not int or replay["version"] != 1
        or not isinstance(replay["streams"], dict)
        or len(replay["streams"]) > MAX_OPERATOR_DELIVERY_STREAMS
    ):
        raise ValueError("invalid operator delivery replay state")
    receipt_count = 0
    for stream, row in replay["streams"].items():
        if not isinstance(stream, str) or not _STREAM_ID.fullmatch(stream):
            raise ValueError("invalid operator delivery replay stream")
        if not isinstance(row, dict) or set(row) != {"generation", "closed_sequence", "receipts"}:
            raise ValueError("invalid operator delivery stream state")
        generation, closed = row["generation"], row["closed_sequence"]
        if (
            type(generation) is not int or not 1 <= generation <= MAX_OPERATOR_DELIVERY_COUNTER
            or type(closed) is not int or not 0 <= closed <= MAX_OPERATOR_DELIVERY_COUNTER
            or not isinstance(row["receipts"], dict)
        ):
            raise ValueError("invalid operator delivery stream counters")
        receipt_count += len(row["receipts"])
        if receipt_count > MAX_OPERATOR_DELIVERY_RECEIPTS:
            raise ValueError("operator delivery receipts exceed their bounded capacity")
        for sequence, receipt in row["receipts"].items():
            if not isinstance(receipt, dict) or set(receipt) != {
                "format", "identity", "target_root", "effect_digest", "revision",
            }:
                raise ValueError("invalid operator delivery receipt")
            identity = operator_delivery_identity(receipt["identity"])
            if (
                identity["stream"] != stream or identity["generation"] != generation
                or str(identity["sequence"]) != sequence or identity["sequence"] <= closed
                or receipt["format"] != "operator-delivery-receipt-v1"
                or not isinstance(receipt["target_root"], str)
                or not Path(receipt["target_root"]).is_absolute()
                or len(receipt["target_root"]) > 4096
                or not isinstance(receipt["effect_digest"], str)
                or not _SHA256.fullmatch(receipt["effect_digest"])
                or type(receipt["revision"]) is not int
                or not 0 <= receipt["revision"] <= revision
            ):
                raise ValueError("operator delivery receipt does not match its stream")


def preference_key(row: dict[str, Any]) -> str:
    roles = row.get("applies_to_roles", "all")
    normalized = "all" if roles == "all" else sorted(set(roles))
    return json.dumps([row["kind"], row["scope"], normalized], ensure_ascii=False)


@dataclass
class ContextDocument:
    records: list[dict[str, Any]] = field(default_factory=list)
    revision: int = 0
    base_revision: int = 0
    state: dict[str, Any] = field(default_factory=dict)
    checkpointed: bool = False
    needs_checkpoint: bool = False

    def absorb_records(self) -> None:
        heads = dict(self.state.get("preference_heads") or {})
        bounded = dict(self.state.get("bounded_missions") or {})
        for row in self.records:
            if row.get("type") == "preference":
                key = preference_key(row)
                heads[key] = max(int(heads.get(key, 0)), int(row["revision"]))
            if row.get("type") == "directive" and row.get("lifetime") == "bounded_increment":
                revision = str(row["revision"])
                bounded.setdefault(revision, str(row.get("mission_id") or "__no_mission__"))
        self.state["preference_heads"] = heads
        self.state["bounded_missions"] = bounded


def read_document(
    path: Path,
    *,
    legacy_state: dict[str, Any],
    normalize_record: Any = None,
) -> ContextDocument | None:
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        return None
    with handle:

        def lines():
            while raw := handle.readline(MAX_SOURCE_BYTES + 1):
                if len(raw) > MAX_SOURCE_BYTES:
                    raise ValueError("operator context record exceeds the migration byte bound")
                yield raw

        stream = lines()
        first_raw = next(stream, None)
        if first_raw is None:
            return ContextDocument()
        try:
            first = json.loads(first_raw)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("operator context header/first row is corrupt") from exc
        if not isinstance(first, dict):
            raise ValueError("operator context row must be an object")
        source_format = first.get("format")
        is_checkpoint = source_format == FORMAT or source_format == DELIVERY_FORMAT
        if (
            any(key in first for key in ("format", "base_revision", "checkpoint_digest"))
            and not is_checkpoint
        ):
            raise ValueError("operator context checkpoint header is corrupt")
        if is_checkpoint:
            payload = dict(first)
            signature = payload.pop("checkpoint_digest", None)
            if hashlib.sha256(encode(payload)).hexdigest() != signature:
                raise ValueError("operator context checkpoint checksum mismatch")
            base = int(first["base_revision"])
            if (
                base < 0
                or not isinstance(first.get("records"), list)
                or not isinstance(first.get("state"), dict)
            ):
                raise ValueError("invalid operator context checkpoint")
            doc = ContextDocument(list(first["records"]), base, base, dict(first["state"]), True)
            if (source_format == DELIVERY_FORMAT) != (DELIVERY_STATE_KEY in doc.state):
                raise ValueError("operator delivery replay requires its fenced checkpoint format")
            validate_operator_delivery_state(doc.state, doc.revision)
            if normalize_record is not None:
                doc.records = [normalize_record(row) for row in doc.records]
            revisions = [int(row["revision"]) for row in doc.records]
            if revisions != sorted(set(revisions)) or any(
                not 0 < revision <= base for revision in revisions
            ):
                raise ValueError("invalid retained operator-context revisions")
            tail = stream
        else:
            from itertools import chain

            doc = ContextDocument(state=dict(legacy_state))
            tail = chain((first_raw,), stream)
        expected = doc.revision + 1
        retained_bytes = sum(len(encode(row)) for row in doc.records)
        for raw in tail:
            try:
                row = json.loads(raw)
                if normalize_record is not None:
                    row = normalize_record(row)
                revision = int(row["revision"])
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError("invalid operator context tail record") from exc
            if revision != expected:
                raise ValueError(
                    f"operator context revision gap: expected {expected}, got {revision}"
                )
            if row.get("type") == "revoke" and int(row["target_revision"]) >= revision:
                raise ValueError("revocation must target an earlier revision")
            doc.records.append(row)
            doc.revision = revision
            expected += 1
            retained_bytes += len(raw)
            if len(doc.records) > 512 or retained_bytes > MAX_SOURCE_BYTES // 2:
                compact_document(doc)
                doc.needs_checkpoint = True
                retained_bytes = sum(len(encode(row)) for row in doc.records)
                if len(doc.records) > 4096 or retained_bytes > MAX_SOURCE_BYTES // 2:
                    raise ValueError(
                        "live operator authority exceeds the bounded migration capacity"
                    )
        if any(not 0 < int(value) <= doc.revision for value in doc.state.get("consumed_once", [])):
            raise ValueError("invalid consumed operator-context revision")
        if any(
            not 0 <= int(value) <= doc.revision
            for value in (doc.state.get("acknowledged_revisions") or {}).values()
        ):
            raise ValueError("invalid acknowledged operator-context revision")
        doc.absorb_records()
        validate_operator_delivery_state(doc.state, doc.revision)
        if any(
            not 0 < int(value) <= doc.revision for value in doc.state["preference_heads"].values()
        ):
            raise ValueError("invalid preference-head revision")
        return doc


def compact_document(doc: ContextDocument) -> None:
    """Prune only records that can no longer take effect, preserving live authority."""
    doc.absorb_records()
    revoked = {int(row["target_revision"]) for row in doc.records if row.get("type") == "revoke"}
    revoked.update(int(value) for value in doc.state.get("revoked", []))
    consumed = {int(value) for value in doc.state.get("consumed_once", [])}
    heads = doc.state["preference_heads"]
    kept = []
    for row in doc.records:
        revision = int(row["revision"])
        if row["type"] == "revoke" or revision in revoked:
            continue
        if row["type"] == "directive" and row.get("lifetime") == "once" and revision in consumed:
            continue
        if row["type"] == "preference" and revision != int(heads[preference_key(row)]):
            continue
        kept.append(row)
    doc.records = kept
    retained = {int(row["revision"]) for row in kept}
    # Removed revisions belong to the permanently closed checkpoint prefix.
    # Only retained identities need individual flags, keeping state bounded.
    doc.state["revoked"] = sorted(
        value for value in revoked if value in retained or value > doc.revision
    )
    doc.state["consumed_once"] = sorted(
        value for value in consumed if value in retained or value > doc.revision
    )
    doc.state["bounded_missions"] = {
        key: value
        for key, value in doc.state["bounded_missions"].items()
        if int(key) in retained or int(key) > doc.revision
    }


def checkpoint_bytes(doc: ContextDocument) -> bytes:
    validate_operator_delivery_state(doc.state, doc.revision)
    payload = {
        "format": DELIVERY_FORMAT if DELIVERY_STATE_KEY in doc.state else FORMAT,
        "base_revision": doc.revision,
        "records": doc.records,
        "state": doc.state,
    }
    payload["checkpoint_digest"] = hashlib.sha256(encode(payload)).hexdigest()
    return encode(payload)


def write_checkpoint(path: Path, doc: ContextDocument) -> None:
    payload = checkpoint_bytes(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".operator-context-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
    doc.base_revision = doc.revision
    doc.checkpointed = True
    doc.needs_checkpoint = False
