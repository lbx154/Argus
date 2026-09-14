"""Durable, independently locked Manager classification diagnostics.

Diagnostic counters must not wait behind a research campaign's pipeline lock,
or rewrite its stage/goal state from an unrelated frontend thread. They live
beside the canonical pipeline file and migrate legacy counters read-only on the
first diagnostic write. The sidecar moves/deletes with the same project.
"""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..core.file_lock import exclusive_file_lock
from ..core.pipeline_state import primary_pipeline_state_path, read_pipeline_state

MANAGER_CONTRACT_MISMATCH_THRESHOLD = 3
_STATE_KEY = "manager_classification_contract_failures"
_SCHEMA_VERSION = 1
_DIAGNOSTIC_LOCK_SECONDS = 5.0

STRUCTURED_DECISION_CLAUSE = "structured existing/new decision event"
REPOSITORY_TOOL_CLAUSE = (
    "repository tool inspection for a repository-sensitive decision"
)


def classification_state_path(project_root: object) -> Path:
    return primary_pipeline_state_path(project_root).with_name("manager-classification-contract.json")


def _read(project_root: object) -> dict[str, Any]:
    path = classification_state_path(project_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        legacy = read_pipeline_state(project_root).get(_STATE_KEY, {})
        models = legacy.get("models", {}) if isinstance(legacy, dict) else {}
        return {"schema_version": _SCHEMA_VERSION,
                "models": dict(models) if isinstance(models, dict) else {}}
    if (not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION
            or not isinstance(payload.get("models"), dict)):
        raise ValueError("invalid Manager classification diagnostics")
    return payload


@contextmanager
def _locked(project_root: object) -> Iterator[Path]:
    path = classification_state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as handle:
        with exclusive_file_lock(handle, timeout_seconds=_DIAGNOSTIC_LOCK_SECONDS,
                                 lock_name="Manager classification diagnostics"):
            yield path


def _write(path: Path, payload: dict[str, Any]) -> None:
    fd, name = tempfile.mkstemp(prefix=".manager-classification-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def record_contract_failure(
    project_root: object,
    *,
    model_id: str,
    clause: str,
) -> int:
    """Increment one model's streak without touching authoritative pipeline state."""
    with _locked(project_root) as path:
        payload = _read(project_root)
        models = payload["models"]
        prior = models.get(model_id)
        prior = prior if isinstance(prior, dict) else {}
        count = max(0, int(prior.get("consecutive_count", 0) or 0)) + 1
        clause_counts = prior.get("clause_counts")
        clause_counts = dict(clause_counts) if isinstance(clause_counts, dict) else {}
        clause_counts[clause] = max(0, int(clause_counts.get(clause, 0) or 0)) + 1
        models[model_id] = {
            "consecutive_count": count,
            "last_failed_clause": clause,
            "clause_counts": clause_counts,
        }
        _write(path, payload)
        return count


def reset_contract_failures(project_root: object, *, model_id: str) -> None:
    """Clear only this model; an empty sidecar prevents stale legacy re-import."""
    with _locked(project_root) as path:
        payload = _read(project_root)
        previous = payload["models"].pop(model_id, None)
        if previous is not None or not path.exists():
            _write(path, payload)


def contract_failure_count(project_root: object, *, model_id: str) -> int:
    """Read the atomic diagnostic snapshot without acquiring a pipeline lock."""
    record = _read(project_root)["models"].get(model_id)
    if not isinstance(record, dict):
        return 0
    return max(0, int(record.get("consecutive_count", 0) or 0))


__all__ = [
    "MANAGER_CONTRACT_MISMATCH_THRESHOLD",
    "REPOSITORY_TOOL_CLAUSE",
    "STRUCTURED_DECISION_CLAUSE",
    "classification_state_path",
    "contract_failure_count",
    "record_contract_failure",
    "reset_contract_failures",
]
