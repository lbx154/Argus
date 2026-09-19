"""Durable call/session provenance, serialized by the usage ledger lock.

This is an identity journal, never a second source of prices. A single atomic
snapshot holds the append-only decisions so identity and audit cannot diverge.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .usage import UsageLedger

JOURNAL = "usage.provider-sessions.json"


def read_bindings(project_root: Path) -> dict[str, Any]:
    path = project_root / JOURNAL
    if not path.exists():
        return {"version": 1, "decisions": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("decisions"), list):
        raise ValueError("invalid provider session journal")
    return payload


def write_bindings(project_root: Path, payload: dict[str, Any]) -> None:
    """Caller holds usage.lock. A failed pre-rename write exposes no decision."""
    path = project_root / JOURNAL
    fd, name = tempfile.mkstemp(prefix=f".{JOURNAL}.", dir=project_root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        if os.name != "nt":
            directory = os.open(project_root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def bind_before_dispatch(project_root: Path, *, call_id: str, session_id: str,
                         resumed: bool) -> None:
    ledger = UsageLedger(project_root, migrate_legacy=False)
    with ledger._locked():
        payload = read_bindings(project_root)
        own = [d for d in payload["decisions"] if d["call_id"] == call_id]
        if own:
            if any(d["session_id"] != session_id for d in own):
                raise ValueError("provider session binding conflict")
            return
        if not resumed and any(d["session_id"] == session_id for d in payload["decisions"]):
            raise ValueError("new provider session already owned")
        payload["decisions"].append({"call_id": call_id, "session_id": session_id,
                                     "kind": "dispatch", "resumed": resumed,
                                     "created_at": time.time()})
        write_bindings(project_root, payload)
