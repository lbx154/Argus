"""Durable diagnostics for the Manager vertical-classification contract.

The streak belongs in ``.argus/PIPELINE_STATE.json`` because that is already
the Manager's authoritative, session-scoped state for vertical routing.  A
separate diagnostic file could drift from the route state during migration or
session deletion.

Three consecutive contract failures are treated as a model/role capability
mismatch.  One or two failures can still be an unlucky generation; three is
small enough to stop an operator losing hours to a deterministic mismatch.
Provider transport/auth failures are deliberately absent from this module.
"""
from __future__ import annotations

from typing import Any

from ..core.pipeline_state import read_pipeline_state, write_pipeline_state

MANAGER_CONTRACT_MISMATCH_THRESHOLD = 3
_STATE_KEY = "manager_classification_contract_failures"
_SCHEMA_VERSION = 1

STRUCTURED_DECISION_CLAUSE = "structured existing/new decision event"
REPOSITORY_TOOL_CLAUSE = (
    "repository tool inspection for a repository-sensitive decision"
)


def _models(payload: dict[str, Any]) -> dict[str, Any]:
    stored = payload.get(_STATE_KEY)
    if not isinstance(stored, dict):
        return {}
    models = stored.get("models")
    return dict(models) if isinstance(models, dict) else {}


def record_contract_failure(
    project_root: object,
    *,
    model_id: str,
    clause: str,
) -> int:
    """Increment and persist one model's consecutive contract-failure streak."""
    payload = read_pipeline_state(project_root)
    models = _models(payload)
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
    payload[_STATE_KEY] = {
        "schema_version": _SCHEMA_VERSION,
        "models": models,
    }
    write_pipeline_state(project_root, payload)
    return count


def reset_contract_failures(project_root: object, *, model_id: str) -> None:
    """Reset one model's streak after a successful Manager classification."""
    payload = read_pipeline_state(project_root)
    models = _models(payload)
    if model_id not in models:
        return
    models.pop(model_id, None)
    if models:
        payload[_STATE_KEY] = {
            "schema_version": _SCHEMA_VERSION,
            "models": models,
        }
    else:
        payload.pop(_STATE_KEY, None)
    write_pipeline_state(project_root, payload)


def contract_failure_count(project_root: object, *, model_id: str) -> int:
    """Return one model's current streak (primarily for status/tests)."""
    record = _models(read_pipeline_state(project_root)).get(model_id)
    if not isinstance(record, dict):
        return 0
    return max(0, int(record.get("consecutive_count", 0) or 0))


__all__ = [
    "MANAGER_CONTRACT_MISMATCH_THRESHOLD",
    "REPOSITORY_TOOL_CLAUSE",
    "STRUCTURED_DECISION_CLAUSE",
    "contract_failure_count",
    "record_contract_failure",
    "reset_contract_failures",
]
