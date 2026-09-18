"""Lossless accounting validation. Diagnostic projections must not authorize spend."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from .json_codec import is_finite_number
from .safety_io import durable_json as durable_json
from .safety_io import fsync_directory as fsync_directory
from .safety_io import loads_strict_json as loads_accounting_json


class AccountingIntegrityError(RuntimeError):
    """An accounting source cannot be used as complete admission evidence."""

    def __init__(self, path: Path, offset: int, raw: bytes, reason: str):
        self.path = path
        self.offset = offset
        self.length = len(raw)
        self.sha256 = hashlib.sha256(raw).hexdigest()
        super().__init__(f"accounting integrity: {path.name} byte {offset}, "
                         f"length={len(raw)}, sha256={self.sha256}: {reason}")


def validate_numbers(row: dict[str, Any], *, allow_null: bool = True) -> None:
    integers = {"input_tokens", "cached_input_tokens", "cache_write_tokens",
                "output_tokens", "reasoning_output_tokens", "total_nano_aiu",
                "observed_tokens", "duration_ms", "usage_event_id", "turn_index", "pid"}
    numbers = {"cost_usd", "amount_usd", "observed_cost_usd", "liability_usd",
               "premium_requests", "premium_request_cost_usd", "request_multiplier",
               "started_at", "completed_at", "updated_at", "acknowledged_at"}
    for key, value in row.items():
        if value is None:
            if not allow_null and key in integers | numbers:
                raise ValueError(f"null numeric obligation field {key}")
            continue
        if key in integers and (type(value) is not int or value < 0):
            raise ValueError(f"invalid non-negative integer {key}")
        if key in numbers and (not is_finite_number(value) or value < 0):
            raise ValueError(f"invalid non-negative number {key}")


def validate_usage_row(row: dict[str, Any]) -> None:
    if not isinstance(row, dict):
        raise ValueError("usage must be an object")
    for key in ("call_id", "project_id", "provider"):
        if not isinstance(row.get(key), str) or not row[key]:
            raise ValueError(f"missing usage identity {key}")
    if type(row.get("schema_version", 1)) is not int or row.get("schema_version", 1) not in {1, 2}:
        raise ValueError("unsupported usage schema")
    if row.get("status") not in {"completed", "error", "denied"}:
        raise ValueError("invalid usage status")
    if row.get("pricing_status") not in {"priced", "partial", "unpriced", "not_billed"}:
        raise ValueError("invalid pricing status")
    for key in ("started_at", "completed_at"):
        if not is_finite_number(row.get(key)) or row[key] < 0:
            raise ValueError("missing usage timestamp")
    validate_numbers(row)
    if row["status"] == "denied":
        if (row["pricing_status"] != "not_billed" or row.get("cost_usd") != 0
                or row.get("model_usage")
                or any(row.get(key) not in (None, 0) for key in (
                    "input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens",
                    "reasoning_output_tokens", "total_nano_aiu", "premium_requests",
                    "premium_request_cost_usd"))):
            raise ValueError("denial contradicts known or unknown charge evidence")
    model_projection(row)  # aggregate nano-AIU and USD must also agree
    for key in ("mission_id", "thread_id", "model", "run_label", "source", "error", "cost_basis", "pricing_tier"):
        if row.get(key) is not None and not isinstance(row[key], str):
            raise ValueError(f"invalid text {key}")
    models = row.get("model_usage", [])
    if not isinstance(models, list):
        raise ValueError("invalid model usage")
    validate_model_usage(models, row=row)
    history = row.get("accounting_history", [])
    if not isinstance(history, list):
        raise ValueError("invalid reconciliation history")
    for previous in history:
        if not isinstance(previous, dict) or "accounting_history" in previous:
            raise ValueError("invalid prior receipt")
        validate_usage_row(previous)
        for key in ("call_id", "project_id", "provider", "mission_id", "run_label",
                    "status", "source", "started_at", "completed_at"):
            if previous.get(key) != row.get(key):
                raise ValueError("reconciliation changed immutable identity")
        if previous.get("thread_id") and previous["thread_id"] != row.get("thread_id"):
            raise ValueError("reconciliation changed bound provider session")


# These are exactly the fields projected by the monetary/token fold. A model
# detail list replaces the call aggregate, so it must preserve every known
# aggregate lower bound, not turn absent details into a zero-priced call.
FOLD_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_tokens",
               "output_tokens", "reasoning_output_tokens", "total_nano_aiu", "cost_usd")


def model_event_identity(model: dict[str, Any]) -> tuple[str, int] | None:
    session = model.get("session_id")
    event = model.get("usage_event_id")
    if session is not None and (not isinstance(session, str) or not session.strip()):
        raise ValueError("invalid provider session identity")
    if event is not None and (type(event) is not int or event < 0):
        raise ValueError("invalid provider event identity")
    return (session.strip(), event) if session is not None and event is not None else None


def _same_money(left: float, right: float) -> bool:
    # Permit only binary representation rounding, not a relative percentage of
    # the bill (which would forgive dollars on large canonical receipts).
    return math.isclose(left, right, rel_tol=0.0,
                        abs_tol=max(math.ulp(left), math.ulp(right)))


def model_projection(model: dict[str, Any]) -> dict[str, Any]:
    projection = {key: model.get(key) for key in FOLD_FIELDS}
    nano = projection["total_nano_aiu"]
    if nano is not None:
        from ..provider_integrations.copilot_usage import NANO_AIU_PER_USD
        cost = nano / NANO_AIU_PER_USD
        if projection["cost_usd"] is None:
            projection["cost_usd"] = cost
        elif not _same_money(projection["cost_usd"], cost):
            raise ValueError("model cost contradicts nano-AIU receipt")
    return projection


def unique_model_usage(models, *, seen=None):
    """Validated call-local unique projections, before global contribution dedup."""
    events = {} if seen is None else seen
    local = set()
    unique = []
    for model in models:
        if not isinstance(model, dict):
            raise ValueError("invalid model receipt")
        validate_numbers(model)
        identity = model_event_identity(model)
        projection = model_projection(model)
        if identity is not None:
            if identity in events and events[identity] != projection:
                raise ValueError("conflicting model receipt identity")
            events[identity] = projection
            if identity in local:
                continue
            local.add(identity)
        unique.append((identity, projection))
    return unique


def validate_model_usage(models, *, row=None, seen=None) -> None:
    """Check the same event identities as the fold, before any deduplication.

    Equality is of the monetary/token evidence, not call-local display metadata.
    Missing and explicit zero evidence are deliberately different.
    """
    unique = [projection for _, projection in unique_model_usage(models, seen=seen)]
    if row is None or not models:
        return
    if row.get("pricing_status") == "priced" and any(m["cost_usd"] is None for m in unique):
        raise ValueError("priced receipt has incomplete model cost detail")
    if any(not any(value is not None for value in m.values()) for m in unique):
        raise ValueError("empty model monetary/token detail")
    for field in FOLD_FIELDS:
        bound = row.get(field)
        if bound is None or bound == 0:
            continue
        values = [m[field] for m in unique]
        if any(value is None for value in values):
            if field in {"cost_usd", "total_nano_aiu"}:
                raise ValueError(f"model detail missing known aggregate {field}")
            # Token-only residuals retain the call's known lower bound in the
            # fold with explicit provenance. They never invent a dollar bill.
            continue
        total = math.fsum(values) if field == "cost_usd" else sum(values)
        if total < bound and not (field == "cost_usd" and _same_money(total, bound)):
            raise ValueError(f"model detail loses aggregate {field}")


def validate_usage_rows(rows: list[dict[str, Any]]) -> None:
    seen = {}
    events = {}
    for row in rows:
        validate_usage_row(row)
        validate_model_usage(row.get("model_usage", []), seen=events)
        key = row["call_id"]
        if key in seen and seen[key] != row:
            raise ValueError("conflicting duplicate call identity")
        seen[key] = row


def strict_jsonl(path: Path, *, require_call_id: bool = False) -> list[dict[str, Any]]:
    """Read every physical line or fail, including old/out-of-window damage.

    A non-newline-terminated tail is unsafe to append to, even if its JSON is
    complete. Never return partial records on read/decoding/schema failure.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise AccountingIntegrityError(path, 0, b"", "unreadable source") from exc
    rows = []
    seen = {}
    events = {}
    offset = 0
    for raw in data.splitlines(keepends=True):
        try:
            if not raw.endswith(b"\n"):
                raise ValueError("unterminated accounting tail")
            row = loads_accounting_json(raw)
            if not isinstance(row, dict):
                raise ValueError("expected object")
            if require_call_id and not isinstance(row.get("call_id"), str):
                raise ValueError("missing call identity")
            if require_call_id and not row["call_id"]:
                raise ValueError("empty call identity")
        except (ValueError, UnicodeError) as exc:
            raise AccountingIntegrityError(path, offset, raw, "invalid JSONL record") from exc
        if require_call_id:
            try:
                validate_usage_row(row)
                validate_model_usage(row.get("model_usage", []), seen=events)
                previous = seen.get(row["call_id"])
                if previous is not None and previous != row:
                    raise ValueError("conflicting duplicate call identity")
                seen[row["call_id"]] = row
            except (ValueError, TypeError) as exc:
                raise AccountingIntegrityError(path, offset, raw, str(exc)) from exc
        rows.append(row)
        offset += len(raw)
    return rows
