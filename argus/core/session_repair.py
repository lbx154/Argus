"""Narrow operator repair using stored original evidence, never uploaded events.

The bounded v1 contract accepts only an unowned, non-resumed cold session. It
binds identity; it does not certify billing completeness or release admission.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from pathlib import Path
from uuid import UUID

from ..provider_integrations.copilot_usage import capture_copilot_usage_cursor
from .paths import session_states_root
from .provider_sessions import read_bindings, write_bindings
from .usage import UsageLedger

_MAX_BYTES = 64 * 1024 * 1024
_MAX_SESSIONS = 2000
_RECEIPT_TYPES = {"model.call_start", "model.call_finished"}


class SessionRepairRejected(ValueError):
    """Bounded, public-safe repair refusal reason (never raw evidence)."""


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _read_original(path: Path) -> bytes:
    # Paths are derived exclusively from trusted configured roots and UUIDs.
    # Reject symlink redirection and nonregular files; no user path is accepted.
    if any(parent.is_symlink() for parent in [path, *path.parents]):
        raise SessionRepairRejected("symlink evidence is not supported")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES:
            raise SessionRepairRejected("evidence file is nonregular or exceeds repair limit")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES or (raw and not raw.endswith(b"\n")):
            raise SessionRepairRejected("oversized or truncated original evidence")
        return raw
    finally:
        os.close(fd)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SessionRepairRejected("duplicate keys in original evidence")
        result[key] = value
    return result


def _loads(raw):
    return json.loads(raw, object_pairs_hook=_object)


def _rows(raw: bytes) -> list[dict]:
    rows = [_loads(line) for line in raw.splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise SessionRepairRejected("non-object original evidence")
    return rows


def _session_events(home: Path, session_id: str) -> tuple[bytes, list[dict]]:
    raw = _read_original(home / "session-state" / session_id / "events.jsonl")
    events = _rows(raw)
    starts = [e for e in events if e.get("type") == "session.start"]
    if len(starts) != 1 or starts[0].get("data", {}).get("sessionId") != session_id:
        raise SessionRepairRejected("missing or contradictory provider session.start")
    return raw, events


def _ownership(project_root: Path, call_id: str, session_id: str) -> None:
    projects = session_states_root()
    if project_root.resolve().parent != projects.resolve():
        raise SessionRepairRejected("repair supports only registered project storage")
    for project in projects.iterdir():
        if not project.is_dir() or project.is_symlink():
            continue
        rows = (_rows(_read_original(project / "usage.jsonl"))
                if (project / "usage.jsonl").exists() else [])
        rows += [{**item, "call_id": row.get("call_id")} for row in list(rows)
                 for item in row.get("model_usage", []) if isinstance(item, dict)]
        rows += read_bindings(project)["decisions"]
        for row in rows:
            if str(row.get("thread_id") or row.get("session_id") or "") != session_id:
                continue
            if project != project_root or row.get("call_id") != call_id:
                raise SessionRepairRejected("session has another owner; resumed-session repair is unsupported")


def _evidence(project_root: Path, call_id: str, session_id: str) -> dict:
    raw = _read_original(project_root / "usage.jsonl")
    matches = [(line, _loads(line)) for line in raw.splitlines(keepends=True)
               if line.strip() and _loads(line).get("call_id") == call_id]
    if len(matches) != 1:
        raise LookupError("expected exactly one original call record")
    original, row = matches[0]
    if row.get("provider") != "copilot" or row.get("status") != "error" or row.get("thread_id"):
        raise SessionRepairRejected("repair requires an original failed Copilot row with missing session")
    if row.get("model_usage") or row.get("pricing_status") == "not_billed":
        raise SessionRepairRejected("record already has receipt ownership or a nonbilling classification")
    if "provider_session_identity_conflict" in str(row.get("error") or ""):
        raise SessionRepairRejected("conflicting reported identity cannot be repaired")
    history_raw = _read_original(project_root / "events.jsonl")
    history = [e for e in _rows(history_raw) if e.get("call_id") == call_id]
    starts = [e for e in history if e.get("type") == "agent.io.start"]
    ends = [e for e in history if e.get("type") == "agent.io.complete"]
    if (len(starts) != 1 or len(ends) != 1 or starts[0].get("backend") != "copilot"
            or starts[0].get("resume_thread_id") or ends[0].get("thread_id")):
        raise SessionRepairRejected("missing, ambiguous or resumed original call boundaries")
    stream_raw = _read_original(project_root / "agent_io.jsonl")
    envelopes = [e for e in _rows(stream_raw) if e.get("call_id") == call_id]
    if not any(e.get("type") == "agent.io.start" for e in envelopes):
        raise SessionRepairRejected("truncated original call transcript")
    events = [_loads(e["line"]) for e in envelopes
              if e.get("type") == "agent.io.stream" and e.get("stream") == "stdout"]
    for event in events:
        if not isinstance(event, dict):
            raise SessionRepairRejected("non-object provider stream")
        if event.get("agentId"):
            raise SessionRepairRejected("delegated-session historical attribution is unsupported")
        reported = event.get("sessionId")
        if event.get("type") == "session.start" and isinstance(event.get("data"), dict):
            reported = event["data"].get("sessionId")
        if reported and reported != session_id:
            raise SessionRepairRejected("original stream reported a conflicting session")
    original_events = {e["id"]: e for e in events
                       if isinstance(e, dict) and isinstance(e.get("id"), str)}
    if len(original_events) != sum(isinstance(e, dict) and isinstance(e.get("id"), str) for e in events):
        raise SessionRepairRejected("duplicate original event identities")
    for identity in original_events:
        if str(UUID(identity)) != identity:
            raise SessionRepairRejected("canonical original event UUID required")
    receipt_ids = {i for i, e in original_events.items() if e.get("type") in _RECEIPT_TYPES}
    if not any(e.get("type") == "model.call_finished" for e in original_events.values()):
        raise SessionRepairRejected("no exact original provider receipt events")
    cursor = capture_copilot_usage_cursor()
    if cursor is None:
        raise SessionRepairRejected("original provider store unavailable")
    home = cursor.db_path.parent
    provider_raw, provider_events = _session_events(home, session_id)
    by_id = {e["id"]: e for e in provider_events if isinstance(e.get("id"), str)}
    if len(by_id) != sum(isinstance(e.get("id"), str) for e in provider_events):
        raise SessionRepairRejected("duplicate provider event identities")
    if any(_canonical(by_id.get(i)) != _canonical(event) for i, event in original_events.items()):
        raise SessionRepairRejected("original event identity/payload mismatch")
    provider_receipts = {i for i, e in by_id.items() if e.get("type") in _RECEIPT_TYPES}
    if provider_receipts != receipt_ids:
        raise SessionRepairRejected("provider has unattributed requests; resumed or incomplete transcript")
    directories = list((home / "session-state").iterdir())
    if len(directories) > _MAX_SESSIONS:
        raise SessionRepairRejected("provider store exceeds bounded repair scan; offline review required")
    # Exact IDs must select one original provider session, not just the UUID
    # nominated by the operator. Reject duplicates even with matching payloads.
    for directory in directories:
        if directory.name == session_id or not directory.is_dir():
            continue
        try:
            UUID(directory.name)
        except ValueError:
            continue
        other = _rows(_read_original(directory / "events.jsonl"))
        if any(e.get("id") in receipt_ids for e in other):
            raise SessionRepairRejected("ambiguous original event ownership across sessions")
    _ownership(project_root, call_id, session_id)
    return {"call_id": call_id, "session_id": session_id,
            "expected_row_hash": _hash(original),
            "evidence_hash": _hash(_canonical({"history": _hash(history_raw),
                                               "stream": _hash(stream_raw),
                                               "provider": _hash(provider_raw)})),
            "event_ids": sorted(original_events), "original_row": original.decode(),
            "accounting_pending": "cancelled_tail_unverified"}


def repair_session(project_root: Path, *, call_id: str, session_id: str,
                   dry_run: bool = True, expected_row_hash: str | None = None,
                   expected_evidence_hash: str | None = None, reason: str = "") -> dict:
    """Preview by default. Apply identity and audit as one idempotent decision."""
    if str(UUID(session_id)) != session_id:
        raise SessionRepairRejected("canonical session UUID required")
    if not dry_run and (not expected_row_hash or not expected_evidence_hash or not reason.strip()):
        raise SessionRepairRejected("apply requires preview hashes and an operator reason")
    if not call_id or len(call_id) > 128:
        raise SessionRepairRejected("invalid call identity")
    if project_root.resolve().parent != session_states_root().resolve():
        raise SessionRepairRejected("repair supports only registered project storage")
    # Same scope lock as dispatch binding prevents a concurrently admitted
    # resume from acquiring this quarantined identity during an operator repair.
    with UsageLedger(project_root.parent, migrate_legacy=False)._locked():
        with UsageLedger(project_root, migrate_legacy=False)._locked():
            payload = read_bindings(project_root)
            existing = [d for d in payload["decisions"]
                        if d["call_id"] == call_id and d["kind"] == "repair"]
            if existing:
                decision = existing[0]
                if (decision["session_id"] != session_id
                        or (expected_row_hash and decision["expected_row_hash"] != expected_row_hash)
                        or (expected_evidence_hash and decision["evidence_hash"] != expected_evidence_hash)):
                    raise SessionRepairRejected("repair conflicts with committed decision")
                current = [line for line in _read_original(project_root / "usage.jsonl").splitlines(keepends=True)
                           if _loads(line).get("call_id") == call_id]
                if len(current) != 1 or _hash(current[0]) != decision["expected_row_hash"]:
                    raise SessionRepairRejected("original row changed after repair")
                return _public(decision, applied=True)
            evidence = _evidence(project_root, call_id, session_id)
            if dry_run:
                return _public(evidence, applied=False)
            if (expected_row_hash != evidence["expected_row_hash"]
                    or expected_evidence_hash != evidence["evidence_hash"]):
                raise SessionRepairRejected("stale evidence or row; preview again")
            if not reason.strip() or len(reason) > 1000:
                raise SessionRepairRejected("bounded operator reason required")
            decision = {**evidence, "kind": "repair", "reason": reason,
                        "created_at": time.time()}
            payload["decisions"].append(decision)
            write_bindings(project_root, payload)
    # Deliberately no liability acceptance and no fake completion. The normal
    # ledger reader projects the binding and keeps it unresolved; monetary
    # attribution is deferred until a provider-specific completeness contract.
    return _public(decision, applied=True)


def _public(decision: dict, *, applied: bool) -> dict:
    return {k: decision[k] for k in ("call_id", "session_id", "expected_row_hash",
                                    "evidence_hash", "accounting_pending")} | {
        "applied": applied, "matched_events": len(decision["event_ids"]),
        "billing_reconciled": False,
        "limitation": "Identity only: cancelled-tail and per-receipt attribution remain unverified; no admission release authorized.",
    }
