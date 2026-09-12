"""Safe failure provenance and reader feedback for Manager supervision."""
from __future__ import annotations

import re
from concurrent.futures import CancelledError
from typing import Any

from ..core.stop_kinds import (
    normalize_stop_kind,
    stop_kind_clause,
    stop_kind_from_external_interrupt,
)
from ._helpers import _manager_backend_failure


def _provider_failure_metadata(result: Any, exc: Exception) -> dict[str, str]:
    """Keep only canonical stop kinds and proven, allowlisted diagnostic codes."""
    metadata: dict[str, str] = {}
    failed, diagnostic = _manager_backend_failure(result) if result is not None else (False, "")
    if failed:
        kind = normalize_stop_kind(getattr(result, "stop_kind", None))
        quota = bool(re.search(r"\btrial_quota_exceeded\b", diagnostic, re.IGNORECASE))
        # Legacy runners may omit stop_kind. Do not reclassify arbitrary prose:
        # only trusted control prefixes and this explicit provider code qualify.
        kind = kind or stop_kind_from_external_interrupt(diagnostic)
        if kind is None and quota:
            kind = "provider_fence"
        if kind is not None:
            metadata["stop_kind"] = kind
        if kind == "provider_fence" and quota:
            metadata["error_code"] = "trial_quota_exceeded"
        elif kind in {"daemon_shutdown", "operator_pause", "operator_abort"}:
            metadata["error_code"] = "cancelled"
        elif kind == "transient_error" and re.search(
            r"\b(?:timed out|timeout|wall-clock (?:time )?limit)\b", diagnostic, re.IGNORECASE,
        ):
            metadata["error_code"] = "timeout"
    if isinstance(exc, TimeoutError):
        metadata.update(stop_kind="transient_error", error_code="timeout")
    elif isinstance(exc, CancelledError):
        metadata["error_code"] = "cancelled"
    return metadata


def _failure_reason(stage: str, metadata: dict[str, Any], *, issued: bool = False) -> str:
    """Receipt and event text never interpolate backend or exception diagnostics."""
    code = str(metadata.get("error_code") or "")
    if code == "observation_incomplete":
        return "Required project facts could not be fully read; this check cannot justify further team changes."
    if stage == "commit":
        if metadata.get("status") == "superseded":
            if code == "timeout":
                return "The control commit timed out and this decision expired. Any recorded effects remain auditable."
            cause = "Cancellation" if code == "cancelled" else "Newer control or evidence"
            return f"{cause} superseded this decision. Any recorded effects remain auditable."
        reason = {
            "timeout": "The control commit timed out. ",
            "cancelled": "The control commit was cancelled. ",
        }.get(code, "")
        return reason + (
            "Delivery is incomplete and will be recovered from this issued receipt; some recorded effects may already be durable."
            if issued else
            "The Manager control commit did not complete cleanly; inspect this receipt for any recorded effects before retrying."
        )
    if code == "trial_quota_exceeded":
        return "The trial quota was insufficient for this Manager check; prior controls remain authoritative."
    if code == "timeout":
        return "The Manager evidence check timed out; prior controls remain authoritative."
    if code == "cancelled":
        return "The Manager evidence check was cancelled; prior controls remain authoritative."
    if code == "superseded":
        return "Newer control, evidence, or a foreground conversation superseded this check; the older Manager decision was not applied."
    clause = stop_kind_clause(metadata.get("stop_kind"))
    if clause:
        return f"The Manager evidence check stopped because {clause}; prior controls remain authoritative."
    if stage == "decision":
        if metadata.get("incomplete_requirements"):
            return "The Manager check could not fully observe required project facts; inspect observation_limitations before retrying."
        return "The Manager decision or its cited evidence could not be validated; prior controls remain authoritative."
    return "The model call did not complete the Manager evidence check; prior controls remain authoritative."
