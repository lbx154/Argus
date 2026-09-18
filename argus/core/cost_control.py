"""Host-global settled and observed in-flight cost admission.

``usage.jsonl`` remains the authoritative settled ledger. This module protects
the global admission check and unresolved-price policy across concurrent
daemons. Calls publish observed provider spend while running; they do not
receive or consume a speculative fixed per-call USD hold. Explicit operator
risk provisions for unknown settlements are accounted separately from usage.
"""

from __future__ import annotations

import calendar
import json
import math
import os
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import portalocker

from .accounting_integrity import (
    AccountingIntegrityError,
    durable_json,
    loads_accounting_json,
    strict_jsonl,
    validate_numbers,
    validate_usage_row,
    validate_usage_rows,
)
from .daemon_lock import is_pid_running
from .dispatch_safety import assert_project_dispatch
from .event_catalog import EventType, new_event
from .finalization_intents import FinalizationIntent, assert_finalization_integrity
from .json_codec import is_finite_number
from .knobs import resolve_budget_caps, resolve_knob
from .paths import session_states_root
from .usage import UsageLedger, UsageRecord, UsageSummary, summarize_usage, usage_pricing_reason

COST_CONTROL_STATE_FILE = "cost-control.json"
COST_CONTROL_LOCK_FILE = "cost-control.lock"
COST_CONTROL_AUDIT_FILE = "cost-control.jsonl"

# Version 3 requires durable finalizer obligations. Older writers must refuse
# it rather than admitting without the lease/finalization fence.
_STATE_VERSION = 3
# Direct callers retain their project guard. The backend supplies its separately
# resolved execution owner; an accounting/log destination is not that authority.
_USE_ACCOUNTING_PROJECT = object()
_CALL_STATE_LOCK_TIMEOUT_SECONDS = 0.25
_THREAD_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = (
    weakref.WeakValueDictionary()
)
_THREAD_LOCKS_GUARD = threading.Lock()
class CostControlStateError(AccountingIntegrityError):
    def __init__(self, message: str):
        super().__init__(Path(COST_CONTROL_STATE_FILE), 0, b"", message)


class CostControlLockBusyError(CostControlStateError):
    """Raised when a bounded read cannot acquire the host-global lock."""


def _local_day(timestamp: float) -> str:
    local = time.localtime(timestamp)
    return f"{local.tm_year:04d}-{local.tm_mon:02d}-{local.tm_mday:02d}"


def _local_day_start(timestamp: float) -> float:
    local = time.localtime(timestamp)
    midnight = (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
    try:
        return time.mktime(midnight)
    except (OverflowError, OSError, ValueError):
        offset = getattr(local, "tm_gmtoff", None)
        if offset is None:
            offset = -(
                time.altzone if local.tm_isdst > 0 else time.timezone
            )
        utc_midnight = calendar.timegm(
            (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0)
        )
        return float(utc_midnight - int(offset))


def _global_root(value: Path | str | None) -> Path:
    if value is not None:
        return Path(value).expanduser()
    from .paths import global_root

    return global_root()


def _default_state(timestamp: float) -> dict[str, Any]:
    return {
        "version": _STATE_VERSION,
        "day": _local_day(timestamp),
        "reservations": [],
        "unresolved": [],
        "project_roots": [],
        "acknowledgements": {},
        "updated_at": timestamp,
    }


def _read_state(root: Path, timestamp: float) -> dict[str, Any]:
    path = root / COST_CONTROL_STATE_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _default_state(timestamp)
    except (OSError, UnicodeError) as exc:
        raise CostControlStateError(f"cannot read {path}: {exc}") from exc
    try:
        payload = loads_accounting_json(raw)
    except ValueError as exc:
        raise CostControlStateError(f"invalid {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CostControlStateError(f"invalid {path}: expected an object")
    version = payload.get("version")
    if type(version) is not int:
        raise CostControlStateError("version must be an integer")
    if version not in {1, 2, _STATE_VERSION}:
        raise CostControlStateError(
            f"unsupported cost-control state version {payload.get('version')!r}"
        )
    # Midnight resets the spend window, never outstanding debt or provenance.
    reservations = payload.get("reservations")
    unresolved = payload.get("unresolved")
    if not isinstance(reservations, list) or not isinstance(unresolved, list):
        raise CostControlStateError(
            f"invalid {path}: reservations and unresolved must be arrays"
        )
    acknowledgements = payload.get("acknowledgements", {})
    if not isinstance(acknowledgements, dict):
        raise CostControlStateError("invalid cost acknowledgements: expected an object")
    for call_id, row in acknowledgements.items():
        if not isinstance(row, dict):
            raise CostControlStateError("invalid cost acknowledgement")
        amount = row.get("liability_usd")
        if (not call_id or not is_finite_number(amount) or amount <= 0
                or not isinstance(row.get("project_id"), str) or not row["project_id"]
                or not isinstance(row.get("reason"), str) or not row["reason"].strip()):
            raise CostControlStateError("invalid acknowledged cost liability")
    if any(not isinstance(row, dict) for row in [*reservations, *unresolved]):
        raise CostControlStateError("invalid reservation or unresolved row")
    try:
        validate_numbers(payload, allow_null=False)
        if payload.get("updated_at") is None:
            raise ValueError("missing updated timestamp")
        if not isinstance(payload.get("day"), str) or not payload["day"]:
            raise ValueError("missing state day")
        roots = payload.get("project_roots", [])
        if not isinstance(roots, list) or any(not isinstance(p, str) or not p for p in roots):
            raise ValueError("invalid project roots")
        seen_calls, seen_ids = {}, {}
        for kind, rows in (("reservations", reservations), ("unresolved", unresolved)):
            for row in rows:
                validate_numbers(row, allow_null=False)
                for key in ("amount_usd", "observed_cost_usd", "observed_tokens", "created_at", "pid"):
                    if key in row and row[key] is None:
                        raise ValueError(f"null state field {key}")
                if not isinstance(row.get("call_id"), str) or not row["call_id"]:
                    raise ValueError("missing state call identity")
                if version == 3:
                    required = ("id", "project_root", "project_id", "provider", "model", "run_label", "created_at", "pid") if kind == "reservations" else ("project_id", "provider", "model", "created_at")
                    if any(key not in row or row[key] is None for key in required):
                        raise ValueError("missing v3 obligation fields")
                if kind == "reservations" and (not isinstance(row.get("id"), str) or not row["id"]):
                    raise ValueError("missing reservation identity")
                for key in ("project_root", "project_id", "provider", "model", "mission_id", "run_label", "reason"):
                    if row.get(key) is not None and not isinstance(row[key], str):
                        raise ValueError("invalid state identity/text")
                if "created_at" in row:
                    validate_numbers({"started_at": row["created_at"]})
                if "blocking" in row and type(row["blocking"]) is not bool:
                    raise ValueError("invalid blocking flag")
                call = row["call_id"]
                if (kind, call) in seen_calls:
                    raise ValueError("duplicate state call identity")
                other = seen_calls.get(("reservations", call)) if kind == "unresolved" else None
                if other and any(row.get(key) != other.get(key) for key in ("project_id", "provider")):
                    raise ValueError("conflicting state call identity")
                seen_calls[kind, call] = row
                if kind == "reservations":
                    if row["id"] in seen_ids:
                        raise ValueError("duplicate reservation identity")
                    seen_ids[row["id"]] = row
        for row in acknowledgements.values():
            validate_numbers(row, allow_null=False)
    except (ValueError, TypeError) as exc:
        raise CostControlStateError(f"invalid cost state: {exc}") from exc
    return {
        "version": _STATE_VERSION,
        "day": _local_day(timestamp),
        "reservations": [row for row in reservations if isinstance(row, dict)],
        "unresolved": [row for row in unresolved if isinstance(row, dict)],
        "project_roots": [str(path) for path in payload.get("project_roots", [])
                          if isinstance(path, str)],
        "acknowledgements": acknowledgements,
        "updated_at": float(payload.get("updated_at") or timestamp),
    }


def _write_state(root: Path, state: dict[str, Any], timestamp: float) -> None:
    root.mkdir(parents=True, exist_ok=True)
    state["version"] = _STATE_VERSION
    state["day"] = _local_day(timestamp)
    state["updated_at"] = timestamp
    durable_json(root / COST_CONTROL_STATE_FILE, state)


@contextmanager
def _locked(
    root: Path,
    *,
    timeout_seconds: float | None = None,
) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    path = root / COST_CONTROL_LOCK_FILE
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    if timeout_seconds is None:
        thread_lock.acquire()
    elif not thread_lock.acquire(timeout=max(0.0, timeout_seconds)):
        raise CostControlLockBusyError(f"cost control lock busy: {path}")
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if timeout_seconds is None:
                portalocker.lock(fd, portalocker.LOCK_EX)
            else:
                deadline = time.monotonic() + max(0.0, timeout_seconds)
                while True:
                    try:
                        portalocker.lock(
                            fd,
                            portalocker.LOCK_EX | portalocker.LOCK_NB,
                        )
                        break
                    except portalocker.exceptions.LockException as exc:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise CostControlLockBusyError(
                                f"cost control lock busy: {path}"
                            ) from exc
                        time.sleep(min(0.01, remaining))
            yield
        finally:
            try:
                portalocker.unlock(fd)
            except (OSError, portalocker.exceptions.LockException):
                pass
            os.close(fd)
    finally:
        thread_lock.release()


def _pid_alive(pid: int) -> bool:
    return is_pid_running(pid)


def _prune_reservations(
    rows: list[dict[str, Any]], *, records: list[UsageRecord],
) -> list[dict[str, Any]]:
    """Partial receipts cannot erase an observed lower bound before finalization."""
    by_id = {record.call_id: record for record in records}
    kept = []
    for row in rows:
        record = by_id.get(str(row.get("call_id") or ""))
        if record is not None and (
            record.status == "denied" or record.pricing_status == "not_billed"
            or (record.cost_usd is not None and record.pricing_status not in {"partial", "unpriced"})
        ):
            continue
        # Process death and zero observations are NOT not-billed receipts.
        kept.append({**row, "amount_usd": 0.0})
    return kept


def _project_records(project_root: Path, day_start: float) -> list[UsageRecord]:
    # day_start is deliberately not applied here: discharge evidence is all-history.
    # Only the final spend/token projection filters the accounting day.
    # Reconciliation is explicit: preflight/status must not rewrite evidence.
    return UsageLedger(project_root, migrate_legacy=False).records()


def _known_cost(records: list[UsageRecord]) -> float:
    unique = {(record.project_id, record.call_id): record for record in records}
    return summarize_usage(unique.values()).known_cost_usd


def _unresolved_costs(
    records: list[UsageRecord], state_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project late reconciliations without taking any usage lock under ours."""
    by_id = {record.call_id: record for record in records}
    state_by_id = {str(row.get("call_id") or ""): row for row in state_rows}
    unresolved = {
        str(row.get("call_id") or ""): row for row in state_rows
        if str(row.get("call_id") or "") not in by_id
    }
    for record in records:
        if record.status == "denied" or record.pricing_status == "not_billed":
            continue
        if record.cost_usd is None or record.pricing_status in {"partial", "unpriced"}:
            unresolved[record.call_id] = {
                **state_by_id.get(record.call_id, {}),
                "call_id": record.call_id, "project_id": record.project_id,
                "mission_id": record.mission_id, "provider": record.provider,
                "model": record.model, "pricing_status": record.pricing_status,
                "run_label": record.run_label,
                "reason": usage_pricing_reason(record),
                "created_at": record.completed_at,
            }
    return list(unresolved.values())


def _unresolved_observed_costs(
    records: list[UsageRecord], state: dict[str, Any],
) -> dict[str, float]:
    known = {r.call_id: _known_cost([r]) for r in records}
    return {
        str(row.get("call_id") or ""): max(
            0.0, float(row.get("observed_cost_usd") or 0) - known.get(str(row.get("call_id") or ""), 0.0),
        )
        for row in _unresolved_costs(records, list(state["unresolved"]))
    }


def _pending_liabilities(
    records: list[UsageRecord], state: dict[str, Any],
) -> dict[str, float]:
    """Explicit operator risk holds, never fabricated provider settlements.

    Priced reconciliation replaces a hold automatically. A partially priced
    call contributes its known cost plus only the remaining approved amount.
    """
    known = {r.call_id: _known_cost([r]) for r in records}
    acknowledgements = state.get("acknowledgements", {})
    pending = {}
    for row in _unresolved_costs(records, list(state["unresolved"])):
        call_id = str(row.get("call_id") or "")
        acknowledgement = acknowledgements.get(call_id)
        if acknowledgement and acknowledgement["project_id"] == row.get("project_id"):
            pending[call_id] = max(
                0.0, max(acknowledgement["liability_usd"], float(row.get("observed_cost_usd") or 0))
                - known.get(call_id, 0.0),
            )
    return pending


def _cost_projection(
    records: list[UsageRecord], state: dict[str, Any],
) -> tuple[list[dict[str, Any]], float, float, dict[str, float]]:
    """Partition outstanding spend without adding overlapping evidence twice.

    A completed unknown call transfers its observation to unresolved state (v2),
    not to a fictitious live provider call. Before that transfer, partial usage
    may already be durable. Both stages count only the part absent from usage.
    """
    liabilities = _pending_liabilities(records, state)
    observed_unknown = _unresolved_observed_costs(records, state)
    unacknowledged = {key: amount for key, amount in observed_unknown.items() if key not in liabilities}
    live = _prune_reservations(list(state["reservations"]), records=records)
    known = {record.call_id: _known_cost([record]) for record in records}
    live_by_id: dict[str, float] = {}
    for row in live:
        call_id = str(row.get("call_id") or "")
        remainder = max(0.0, float(row.get("observed_cost_usd") or 0) - known.get(call_id, 0.0))
        live_by_id[call_id] = max(live_by_id.get(call_id, 0.0), remainder)
    live_extra = sum(max(0.0, amount - max(liabilities.get(key, 0.0), unacknowledged.get(key, 0.0)))
                     for key, amount in live_by_id.items())
    return live, live_extra, sum(unacknowledged.values()), liabilities


def cached_token_weight() -> float:
    """How much of a cached input token counts against the daily token cap.

    Providers bill cache reads at a fraction of fresh input; a long tool
    conversation re-sends its whole prefix every turn, so at weight 1.0 one
    planner call can look like millions of tokens while 90% of them were
    cache hits. 1.0 keeps the historical meaning of the cap.
    """
    from .knobs import resolve_knob

    raw = resolve_knob("ARGUS_SKILL_DAILY_TOKEN_CAP_CACHED_WEIGHT", "1").value
    try:
        weight = float(str(raw).strip() or "1")
    except ValueError:
        weight = 1.0
    return min(1.0, max(0.0, weight))


def _observed_tokens(records: list[UsageRecord], state: dict[str, Any]) -> tuple[int, int]:
    # Input already includes cache reads/writes. Reasoning is a separate count
    # in the normalized ledger; do not add cache a second time. Cached input
    # may count at a configured fraction of a fresh token.
    weight = cached_token_weight()

    def count(summary: UsageSummary) -> int:
        cached = int(summary.cached_input_tokens or 0)
        cached = min(cached, summary.input_tokens)
        discounted = summary.input_tokens - int(round(cached * (1.0 - weight)))
        return discounted + summary.output_tokens + summary.reasoning_output_tokens

    records = list({r.call_id: r for r in records}.values())
    settled = [r for r in records if r.status != "denied"]
    known = {r.call_id: count(summarize_usage([r])) for r in settled}
    extra: dict[str, int] = {}
    rows = [*_prune_reservations(state["reservations"], records=records),
            *_unresolved_costs(records, state["unresolved"])]
    for row in rows:
        call_id = str(row.get("call_id") or "")
        remaining = max(0, int(row.get("observed_tokens") or 0) - known.get(call_id, 0))
        extra[call_id] = max(extra.get(call_id, 0), remaining)
    # The shared ledger fold deduplicates Copilot SQLite rows that arrive in
    # overlapping final receipts from resumed sessions.
    unsettled = sum(extra.values())
    return count(summarize_usage(_daily_records(settled, state))) + unsettled, unsettled


def _daily_records(records: list[UsageRecord], state: dict[str, Any]) -> list[UsageRecord]:
    return [record for record in records if _local_day(record.completed_at) == state["day"]]


def _budget_reason(
    records: list[UsageRecord], state: dict[str, Any], cap: float,
    *, check_unresolved: bool = True, token_cap: int = 0,
) -> str:
    if token_cap > 0:
        tokens, _ = _observed_tokens(records, state)
        if tokens >= token_cap:
            return f"global daily token budget exhausted ({tokens}/{token_cap} tokens)"
    _live, live_cost, observed_unknown, liabilities = _cost_projection(records, state)
    spent = _known_cost(_daily_records(records, state)) + sum(liabilities.values()) + observed_unknown + live_cost
    if cap > 0 and spent >= cap:
        return f"global daily budget exhausted (${cap - spent:.6f} available)"
    if check_unresolved and _unpriced_policy() == "block":
        unresolved = [row for row in _unresolved_costs(records, list(state["unresolved"]))
                      if row.get("call_id") not in liabilities]
        if unresolved:
            first = unresolved[0]
            detail = (
                f"call={first.get('call_id') or '(unknown)'}, "
                f"provider={first.get('provider') or '(unknown)'}, "
                f"model={first.get('model') or '(missing)'}; "
                f"{str(first.get('reason') or 'usage is incomplete')[:240]}"
            )
            return (
                f"unresolved provider cost: {len(unresolved)} call(s) "
                f"awaiting usage reconciliation ({detail})"
            )
    return ""


def global_daily_usage_summary(
    *, global_root: Path | str | None = None, now: float | None = None,
) -> UsageSummary:
    """Expose the same complete daily ledgers to supervisor budget recovery."""
    timestamp = time.time() if now is None else float(now)
    records = _global_records(_global_root(global_root), _local_day_start(timestamp), state_timestamp=timestamp)
    unique = {(record.project_id, record.call_id): record for record in records
              if record.completed_at >= _local_day_start(timestamp)}
    return summarize_usage(unique.values())


def accounting_integrity_preflight(
    *, project_root: Path | None = None, global_root: Path | str | None = None,
    execution_project_root: Path | None | object = _USE_ACCOUNTING_PROJECT,
) -> None:
    """Mandatory integrity; explicit execution owner is a trusted backend binding."""
    dispatch_project = project_root if execution_project_root is _USE_ACCOUNTING_PROJECT else execution_project_root
    assert_project_dispatch(dispatch_project)
    if project_root is not None:
        strict_jsonl(project_root / "usage.jsonl", require_call_id=True)
    root = _global_root(global_root)
    timestamp = time.time()
    records = _global_records(root, _local_day_start(timestamp), state_timestamp=timestamp)
    if project_root is not None:
        records.extend(_project_records(project_root, _local_day_start(timestamp)))
    try:
        validate_usage_rows([record.to_jsonable() for record in records])
    except (ValueError, TypeError) as exc:
        raise CostControlStateError(str(exc)) from exc
    with _locked(root, timeout_seconds=_CALL_STATE_LOCK_TIMEOUT_SECONDS):
        state = _read_state(root, timestamp)
        assert_finalization_integrity(root, state["reservations"], state=state)


def cost_admission_reason(
    *, global_root: Path | str | None = None, cap: float | None = None,
    now: float | None = None,
) -> str:
    """Shared preflight for automatic pause recovery; no provider call is made."""
    timestamp = time.time() if now is None else float(now)
    root = _global_root(global_root)
    records = _global_records(root, _local_day_start(timestamp), state_timestamp=timestamp)
    caps = resolve_budget_caps(global_root=root)
    limit = caps.global_daily_cap_usd if cap is None else cap
    state = _read_state(root, timestamp)
    assert_finalization_integrity(root, state["reservations"], state=state)
    return _budget_reason(records, state, limit, token_cap=caps.global_daily_token_cap)


def acknowledge_unpriced_call(
    *, global_root: Path | str, project_id: str, call_id: str,
    liability_usd: float, reason: str,
) -> dict[str, Any]:
    """Approve one unknown call with a budgeted liability, preserving its ledger.

    This is an explicit operator decision, not a claim that the provider charged
    this amount. Repeating the same decision is idempotent; conflicting decisions
    are rejected. New unknown calls still fail closed under the block policy.
    """
    if (isinstance(liability_usd, bool) or not isinstance(liability_usd, (int, float))
            or not math.isfinite(liability_usd) or liability_usd <= 0):
        raise ValueError("liability_usd must be finite and positive")
    if not project_id or not call_id or not reason.strip() or len(reason) > 1000:
        raise ValueError("project_id, call_id and a reason (at most 1000 characters) are required")
    root = _global_root(global_root)
    timestamp = time.time()
    records = _global_records(root, _local_day_start(timestamp), state_timestamp=timestamp)
    with _locked(root, timeout_seconds=2):
        state = _read_state(root, timestamp)
        previous = state["acknowledgements"].get(call_id)
        decision = {"project_id": project_id, "liability_usd": float(liability_usd),
                    "reason": reason.strip()}
        if previous:
            if any(previous.get(key) != value for key, value in decision.items()):
                raise ValueError("this call already has a different acknowledgement")
            return {"call_id": call_id, **previous}
        unresolved = _unresolved_costs(records, list(state["unresolved"]))
        target = next((row for row in unresolved if row.get("call_id") == call_id
                       and row.get("project_id") == project_id), None)
        if target is None:
            raise LookupError("no unresolved call with this ID belongs to this project today")
        known = max((_known_cost([r]) for r in records if r.call_id == call_id), default=0.0)
        live_observed = max((float(row.get("observed_cost_usd") or 0)
                             for row in state["reservations"] if row.get("call_id") == call_id), default=0.0)
        known = max(known, float(target.get("observed_cost_usd") or 0), live_observed)
        if liability_usd < known:
            raise ValueError("approved liability cannot be less than the already known cost")
        decision["acknowledged_at"] = timestamp
        state["acknowledgements"][call_id] = decision
        target["blocking"] = False
        state["unresolved"] = unresolved
        _write_state(root, state, timestamp)
        _append_audit(root, EventType.BUDGET_UNPRICED_ACKNOWLEDGED,
                      call_id=call_id, **decision)
        return {"call_id": call_id, **decision}


def _global_records(root: Path, day_start: float, *, state_timestamp: float) -> list[UsageRecord]:
    projects = session_states_root(root)
    try:
        project_roots = [path for path in projects.iterdir() if path.is_dir()]
    except FileNotFoundError:
        project_roots = []
    except OSError as exc:
        raise CostControlStateError("cannot enumerate project accounting") from exc
    # A caller can place its ledger outside global/projects. Retain those
    # unsettled references so a later reconciliation releases the global gate.
    # This reader must always run before acquiring the global state lock.
    try:
        # A valid replay timestamp can have a local midnight before epoch;
        # Windows rejects converting that negative midnight with localtime.
        # The state's day is the caller's actual timestamp, not the ledger cutoff.
        state = _read_state(root, state_timestamp)
        known = {path.resolve() for path in project_roots}
        references = [*state["unresolved"], *state["reservations"],
                      *({"project_root": path} for path in state["project_roots"])]
        for row in references:
            project_text = str(row.get("project_root") or "")
            if project_text:
                path = Path(project_text).expanduser().resolve()
                if path not in known:
                    project_roots.append(path)
                    known.add(path)
    except CostControlStateError:
        # Admission/snapshot performs its own authoritative state validation.
        pass
    records: list[UsageRecord] = []
    for project_root in project_roots:
        try:
            records.extend(_project_records(project_root, day_start))
        except AccountingIntegrityError:
            raise
        except Exception as exc:  # noqa: BLE001 - missing spend is not zero
            raise CostControlStateError("cannot read project accounting") from exc
    try:
        validate_usage_rows([record.to_jsonable() for record in records])
    except (ValueError, TypeError) as exc:
        raise CostControlStateError(str(exc)) from exc
    seen = {}
    for record in records:
        if record.call_id in seen and seen[record.call_id] != record:
            raise CostControlStateError("conflicting global call identity")
        seen[record.call_id] = record
    state = _read_state(root, state_timestamp)
    for row in [*state["reservations"], *state["unresolved"]]:
        record = seen.get(row["call_id"])
        if record is not None and record.project_id != row.get("project_id"):
            raise CostControlStateError("state/ledger project identity mismatch")
    for call_id, row in state["acknowledgements"].items():
        record = seen.get(call_id)
        if record is not None and record.project_id != row["project_id"]:
            raise CostControlStateError("acknowledgement/ledger project identity mismatch")
    return list(seen.values())


def _resolved_unpriced(
    unresolved: list[dict[str, Any]],
    *,
    day_start: float,
) -> list[dict[str, Any]]:
    by_project: dict[str, list[dict[str, Any]]] = {}
    for row in unresolved:
        project_root = str(row.get("project_root") or "")
        by_project.setdefault(project_root, []).append(row)
    kept: list[dict[str, Any]] = []
    for project_text, rows in by_project.items():
        if not project_text:
            kept.extend(rows)
            continue
        try:
            records = _project_records(Path(project_text), day_start)
        except Exception:  # noqa: BLE001
            kept.extend(rows)
            continue
        settled = {
            record.call_id
            for record in records
            if record.cost_usd is not None
            and record.pricing_status not in {"partial", "unpriced"}
        }
        kept.extend(row for row in rows if str(row.get("call_id") or "") not in settled)
    return kept


def _append_audit(root: Path, event_type: EventType, **payload: Any) -> None:
    try:
        row = new_event(event_type, **payload)
        with (root / COST_CONTROL_AUDIT_FILE).open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
    except OSError:
        pass


def _unpriced_policy() -> str:
    value = resolve_knob(
        "ARGUS_SKILL_UNPRICED_COST_POLICY",
        "block",
    ).value.strip().lower()
    return "allow" if value == "allow" else "block"


def cost_control_enabled() -> bool:
    explicit = str(os.environ.get("ARGUS_SKILL_COST_CONTROL", "") or "").strip()
    if explicit:
        return explicit.lower() in {"1", "true", "yes", "on"}
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    value = resolve_knob("ARGUS_SKILL_COST_CONTROL", "on").value.strip().lower()
    return value in {"1", "true", "yes", "on"}


@dataclass
class CallBudgetReservation:
    root: Path
    reservation_id: str
    call_id: str
    project_root: Path | None
    amount_usd: float
    mission_id: str | None = None
    provider: str = ""
    model: str = ""
    run_label: str = ""
    state_tracked: bool = True
    _closed: bool = False
    intent: FinalizationIntent | None = None
    enforce_policy: bool = True
    _finalize_lock: Any = field(default_factory=threading.RLock, repr=False)

    def prepare_finalization(self, record: UsageRecord | None = None, *, error: str = "") -> None:
        with self._finalize_lock:
            self._prepare_finalization(record, error=error)

    def _prepare_finalization(self, record: UsageRecord | None = None, *, error: str = "") -> None:
        if self.intent is not None:
            bound = self.intent.data["reservation"]
            project = str(self.project_root.resolve()) if self.project_root is not None else ""
            if (bound["call_id"] != self.call_id or bound["id"] != self.reservation_id
                    or self.root.resolve() != self.intent.path.parent.parent.resolve()
                    or bound["project_root"] != project):
                raise CostControlStateError("reservation project/root identity mismatch")
        if record is not None:
            try:
                validate_usage_row(record.to_jsonable())
            except (ValueError, TypeError) as exc:
                raise CostControlStateError(f"invalid finalization receipt: {exc}") from exc
            if (record.call_id != self.call_id or self.project_root is None
                    or record.project_id != self.project_root.resolve().name):
                raise CostControlStateError("receipt call/project identity mismatch")
            if self.intent is not None:
                bound = self.intent.data["reservation"]
                if (bound["call_id"] != self.call_id
                        or self.root.resolve() != self.intent.path.parent.parent.resolve()
                        or bound["project_root"] != str(self.project_root.resolve())):
                    raise CostControlStateError("reservation project/root identity mismatch")
            if record.status == "denied" or record.pricing_status == "not_billed":
                from .finalization_intents import _not_started_receipt
                if not _not_started_receipt({**record.to_jsonable(), "status": "denied"}):
                    raise CostControlStateError("no-charge receipt contradicts usage evidence")
                self._assert_no_charge()
        if self.intent is not None:
            self.intent.prepare(record=record.to_jsonable() if record else None, error=error)

    def _assert_no_charge(self, *, proving=False):
        if self.intent is None:
            raise CostControlStateError("no-charge closure requires a finalizer")
        self.intent.assert_no_charge(proving=proving)
        state = _read_state(self.root, time.time())
        original = next((r for r in state["reservations"] if r["id"] == self.reservation_id), None)
        bound = self.intent.data["reservation"]
        if (original is None or any(original.get(k) != bound.get(k)
                                   for k in ("id", "call_id", "project_id", "project_root"))
                or original.get("observed_cost_usd", 0) != 0
                or original.get("observed_tokens", 0) != 0):
            raise CostControlStateError("no-charge closure conflicts with original obligation")

    def prove_no_charge(self, *, proof):
        """Consume one call-bound producer capability, never a caller label."""
        from .finalization_intents import _no_charge_evidence
        from .no_charge_provenance import consume_producer_proof
        with self._finalize_lock:
            kind, receipt = consume_producer_proof(self, proof)
            self._assert_no_charge(proving=True)
            evidence = {"kind": kind, "call_id": self.call_id, "producer_checked": True}
            if receipt is not None:
                evidence["receipt"] = receipt
            updated = {**self.intent.data, "no_charge_evidence": evidence}
            if not _no_charge_evidence(updated):
                raise CostControlStateError("invalid no-charge producer evidence")
            durable_json(self.intent.path, updated)
            self.intent.data = updated

    def invalidate_no_charge_proof(self):
        """Every later process attempt invalidates an earlier refusal proof."""
        with self._finalize_lock:
            if self._closed or self.intent is None or self.intent.fd < 0:
                raise CostControlStateError("provider attempt requires an active finalizer")
            if "no_charge_evidence" in self.intent.data:
                # Retain invalidation in memory even if persistence fails.
                self.intent.data.pop("no_charge_evidence")
                durable_json(self.intent.path, self.intent.data)

    def mark_execution_started(self) -> None:
        """Durably mark possible execution before handing control to transport."""
        with self._finalize_lock:
            if self._closed or self.intent is None:
                raise CostControlStateError("execution requires an active finalizer")
            self.intent.mark_execution_started()

    def finalization_failed(self, error: str) -> None:
        with self._finalize_lock:
            if self.intent is not None:
                self.intent.failure(error)

    def release(self, *, reason: str = "not_started") -> bool:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("not-started release requires a reason")
        with self._finalize_lock:
            if self._closed:
                return False
            changed = _close_reservation(self, release_reason=reason)
            self._closed = True
            return changed

    def settle(self, record: UsageRecord) -> bool:
        with self._finalize_lock:
            if self._closed:
                return False
            changed = _close_reservation(self, record=record)
            self._closed = True
            return changed

    def settle_unknown(self, *, reason: str) -> bool:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("unknown settlement requires a reason")
        with self._finalize_lock:
            if self._closed:
                return False
            changed = _close_reservation(self, unknown_reason=reason)
            self._closed = True
            return changed

    def observe_cost(self, cost_usd: float = 0.0, *, now: float | None = None,
                     tokens: int = 0) -> str:
        with self._finalize_lock:
            return self._observe_cost(cost_usd, now=now, tokens=tokens)

    def _observe_cost(self, cost_usd: float = 0.0, *, now: float | None = None,
                      tokens: int = 0) -> str:
        """Publish observed in-flight spend and check the shared daily cap.

        This is real provider telemetry, not a speculative fixed call hold.
        Callers pass only usage incurred on the current local day.
        """
        if self._closed:
            return ""
        if isinstance(cost_usd, bool) or not isinstance(cost_usd, (int, float)) or not math.isfinite(cost_usd) or cost_usd < 0:
            raise ValueError("observed cost must be finite and non-negative")
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
            raise ValueError("observed tokens must be a non-negative integer")
        timestamp = time.time() if now is None else now
        records = _global_records(self.root, _local_day_start(timestamp), state_timestamp=timestamp)
        if self.project_root is not None:
            if self.project_root.resolve().parent != session_states_root(self.root).resolve():
                records.extend(_project_records(self.project_root, _local_day_start(timestamp)))
        caps = resolve_budget_caps(global_root=self.root)
        # Always read ledgers before the cost-state lock (usage -> cost order).
        with _locked(self.root, timeout_seconds=_CALL_STATE_LOCK_TIMEOUT_SECONDS):
            state = _read_state(self.root, timestamp)
            if self.project_root is not None:
                state["project_roots"] = sorted(set(state["project_roots"]) |
                                                {str(self.project_root.resolve())})
            row = next((item for item in state["reservations"]
                        if item.get("id") == self.reservation_id), None)
            if row is None:
                raise CostControlStateError("observed call lost its durable reservation")
            row["observed_cost_usd"] = max(float(row.get("observed_cost_usd") or 0), cost_usd)
            row["observed_tokens"] = max(int(row.get("observed_tokens") or 0), tokens)
            # An unrelated failed call must not kill a provider turn already
            # admitted: doing so loses more final usage and cascades the outage.
            # Monetary limits (including approved risk holds) still interrupt.
            reason = _budget_reason(records, state, caps.global_daily_cap_usd, check_unresolved=False,
                                    token_cap=caps.global_daily_token_cap)
            _write_state(self.root, state, timestamp)
            self.state_tracked = True
        return reason if self.enforce_policy else ""


def _accounting_revision(root: Path, project: Path | None, timestamp: float) -> tuple:
    """Read-only file revisions; never acquire a usage lock under the cost lock.

    Capture before ledger reads and compare under the admission lock. A writer
    racing the read invalidates admission instead of combining old usage and new
    state. Concurrent writes after comparison linearize after this admission.
    """
    state = _read_state(root, timestamp)
    projects = session_states_root(root)
    paths = {root / COST_CONTROL_STATE_FILE, projects}
    if projects.exists():
        paths.update(p / "usage.jsonl" for p in projects.iterdir() if p.is_dir())
    references = [*state["project_roots"], *(r.get("project_root") for r in
                  [*state["reservations"], *state["unresolved"]])]
    if project is not None:
        references.append(str(project))
    paths.update(Path(p).resolve() / "usage.jsonl" for p in references if p)
    revision = []
    for path in sorted(paths):
        try:
            st = path.stat()
            value = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        except FileNotFoundError:
            value = None
        revision.append((str(path), value))
    return tuple(revision)


def reserve_call_budget(
    *,
    call_id: str,
    project_root: Path | str | None,
    mission_id: str | None,
    provider: str,
    model: str,
    run_label: str,
    global_root: Path | str | None = None,
    global_daily_cap_usd: float | None = None,
    now: float | None = None,
    pid: int | None = None,
    lock_timeout_seconds: float = _CALL_STATE_LOCK_TIMEOUT_SECONDS,
    enforce_policy: bool = True,
    execution_project_root: Path | None | object = _USE_ACCOUNTING_PROJECT,
) -> tuple[CallBudgetReservation | None, str]:
    """Admit a call against settled spend, observed running costs and unknowns."""
    timestamp = time.time() if now is None else float(now)
    root = _global_root(global_root)
    project = Path(project_root).expanduser().resolve() if project_root is not None else None
    dispatch_project = project if execution_project_root is _USE_ACCOUNTING_PROJECT else execution_project_root
    assert_project_dispatch(dispatch_project)
    if project is not None:
        strict_jsonl(project / "usage.jsonl", require_call_id=True)
    caps = resolve_budget_caps(project_state_dir=project, global_root=root)
    global_cap = max(
        0.0,
        float(
            caps.global_daily_cap_usd
            if global_daily_cap_usd is None
            else global_daily_cap_usd
        ),
    )
    day_start = _local_day_start(timestamp)
    owner_pid = os.getpid() if pid is None else int(pid)
    project_key = str(project.resolve()) if project is not None else ""
    mission_key = str(mission_id or "")
    # Reading distributed usage ledgers is the expensive part. Never do it
    # while holding the host-global state lock: concurrent daemons otherwise
    # form a lock convoy and even a greeting can wait tens of seconds.
    try:
        revision = _accounting_revision(root, project, timestamp)
    except CostControlStateError as exc:
        return None, f"cost control unavailable: {exc}"
    project_records = _project_records(project, day_start) if project else []
    global_records = _global_records(root, day_start, state_timestamp=timestamp)
    if project is not None:
        projects_root = session_states_root(root).resolve()
        try:
            inside_global = project.resolve().parent == projects_root
        except OSError:
            inside_global = False
        if not inside_global:
            known = {record.call_id: record for record in global_records}
            for record in project_records:
                if record.call_id in known and known[record.call_id] != record:
                    raise CostControlStateError("external/global ledger call identity conflict")
                if record.call_id not in known:
                    global_records.append(record)

    try:
        validate_usage_rows([record.to_jsonable() for record in global_records])
    except (ValueError, TypeError) as exc:
        raise CostControlStateError(str(exc)) from exc
    global_spend = _known_cost([r for r in global_records if r.completed_at >= day_start])
    available = global_cap - global_spend
    if enforce_policy and global_cap > 0 and available <= 0:
        reason = f"global daily budget exhausted (${available:.6f} available)"
        _append_audit(
            root,
            EventType.BUDGET_RESERVATION_DENIED,
            call_id=call_id,
            project_id=project.name if project is not None else "",
            mission_id=mission_key or None,
            provider=provider,
            model=model,
            run_label=run_label,
            reason=reason,
            global_spend_usd=global_spend,
        )
        return None, reason

    amount = 0.0
    reservation_id = uuid.uuid4().hex
    row = {
        "id": reservation_id,
        "call_id": call_id,
        "pid": owner_pid,
        "project_root": project_key,
        "project_id": project.name if project is not None else "",
        "mission_id": mission_key or None,
        "provider": str(provider or ""),
        "model": str(model or ""),
        "run_label": str(run_label or ""),
        "amount_usd": amount,
        "created_at": timestamp,
    }
    state_tracked = True
    intent = None
    try:
        with _locked(root, timeout_seconds=lock_timeout_seconds):
            if enforce_policy and revision != _accounting_revision(root, project, timestamp):
                return None, "cost control unavailable: accounting snapshot changed; retry admission"
            state = _read_state(root, timestamp)
            assert_project_dispatch(dispatch_project)
            if project is not None:
                strict_jsonl(project / "usage.jsonl", require_call_id=True)
            assert_finalization_integrity(root, state["reservations"], state=state)
            if project_key:
                state["project_roots"] = sorted(set(state["project_roots"]) | {project_key})
            # Only the finalizer closes ownership; ledger evidence can reduce
            # its projected charge but cannot silently remove the owner row.
            reservations = list(state["reservations"])
            state["reservations"] = reservations
            state["unresolved"] = _unresolved_costs(global_records, list(state["unresolved"]))
            reason = _budget_reason(global_records, state, global_cap, token_cap=caps.global_daily_token_cap)
            if enforce_policy and reason:
                _write_state(root, state, timestamp)
                _append_audit(root, EventType.BUDGET_RESERVATION_DENIED,
                              call_id=call_id, provider=provider, reason=reason)
                return None, reason
            if any(item.get("call_id") == call_id for item in [*reservations, *state["unresolved"]]) or any(r.call_id == call_id for r in global_records):
                raise CostControlStateError("call identity already registered")
            reservations.append(row)
            state["reservations"] = reservations
            _write_state(root, state, timestamp)
            # The durable reservation precedes the durable intent/active lease.
            # Failure at either step refuses dispatch and leaves debt visible.
            intent = FinalizationIntent(root, row)
    except CostControlLockBusyError:
        # No durable reservation means no dispatch, even during contention.
        return None, "cost control unavailable: reservation lock busy"
    except CostControlStateError as exc:
        reason = f"cost control unavailable: {exc}"
        _append_audit(
            root,
            EventType.BUDGET_RESERVATION_DENIED,
            call_id=call_id,
            project_id=project.name if project is not None else "",
            mission_id=mission_key or None,
            provider=provider,
            model=model,
            run_label=run_label,
            reason=reason,
        )
        return None, reason

    _append_audit(
        root,
        EventType.BUDGET_RESERVATION_CREATED,
        reservation_id=reservation_id,
        call_id=call_id,
        project_id=project.name if project is not None else "",
        mission_id=mission_key or None,
        provider=provider,
        model=model,
        run_label=run_label,
        amount_usd=amount,
        state_tracked=state_tracked,
    )
    return (
        CallBudgetReservation(
            root=root,
            reservation_id=reservation_id,
            call_id=call_id,
            project_root=project,
            amount_usd=amount,
            mission_id=mission_key or None,
            provider=str(provider or ""),
            model=str(model or ""),
            run_label=str(run_label or ""),
            state_tracked=state_tracked,
            intent=intent,
            enforce_policy=enforce_policy,
        ),
        "",
    )


def _close_reservation(
    reservation: CallBudgetReservation,
    *,
    record: UsageRecord | None = None,
    release_reason: str = "",
    unknown_reason: str = "",
) -> bool:
    if release_reason:
        if reservation.intent is None:
            raise CostControlStateError("release requires checked not-started lifecycle")
        reservation.intent.assert_releasable()
        current = _read_state(reservation.root, time.time())
        original = next((r for r in current["reservations"] if r["id"] == reservation.reservation_id), {})
        if (not original or original["call_id"] != reservation.call_id
                or original["project_root"] != reservation.intent.data["reservation"]["project_root"]):
            raise CostControlStateError("release lacks original reservation identity")
        if original.get("observed_cost_usd", 0) or original.get("observed_tokens", 0):
            raise CostControlStateError("not-started release conflicts with observed usage")
    reservation.prepare_finalization(record, error=unknown_reason)
    if record is not None:
        # A public settle() must not close debt based only on an in-memory bill.
        # Usage -> cost lock order; append is idempotent for concurrent receipts.
        if reservation.project_root is None:
            raise CostControlStateError("settlement requires a durable project ledger")
        ledger = UsageLedger(reservation.project_root, migrate_legacy=False)
        try:
            ledger.append(record)
        except AccountingIntegrityError as exc:
            raise CostControlStateError(f"settlement conflicts with canonical receipt: {exc}") from exc
        from .usage import _read_usage_json_rows
        with ledger._locked():
            canonical = next((r for r in _read_usage_json_rows(ledger.path)
                              if r.get("call_id") == record.call_id), None)
            if canonical != record.to_jsonable():
                raise CostControlStateError("settlement conflicts with canonical receipt; reconcile explicitly")
    timestamp = time.time()
    pricing_status = record.pricing_status if record is not None else "unknown"
    cost_usd = record.cost_usd if record is not None else None
    error = record.error if record is not None else unknown_reason
    unresolved_row: dict[str, Any] | None = None
    if record is not None and (
        record.status != "denied"
        and (
            record.cost_usd is None
            or record.pricing_status in {"partial", "unpriced"}
        )
    ):
        unresolved_row = {
            "call_id": record.call_id,
            "project_root": (
                str(reservation.project_root.resolve())
                if reservation.project_root is not None
                else ""
            ),
            "project_id": record.project_id,
            "mission_id": record.mission_id,
            "provider": record.provider,
            "model": record.model,
            "pricing_status": record.pricing_status,
            "reason": usage_pricing_reason(record),
            "blocking": _unpriced_policy() == "block",
            "created_at": timestamp,
        }
    elif unknown_reason:
        unresolved_row = {
            "call_id": reservation.call_id,
            "project_root": (
                str(reservation.project_root.resolve())
                if reservation.project_root is not None
                else ""
            ),
            "project_id": (
                reservation.project_root.name
                if reservation.project_root is not None
                else ""
            ),
            "mission_id": reservation.mission_id,
            "provider": reservation.provider,
            "model": reservation.model,
            "run_label": reservation.run_label,
            "pricing_status": "unknown",
            "reason": unknown_reason,
            "blocking": _unpriced_policy() == "block",
            "created_at": timestamp,
        }

    state_updated = False
    if reservation.state_tracked:
        try:
            with _locked(
                reservation.root,
                timeout_seconds=_CALL_STATE_LOCK_TIMEOUT_SECONDS,
            ):
                state = _read_state(reservation.root, timestamp)
                rows = list(state["reservations"])
                original = next((row for row in rows if row.get("id") == reservation.reservation_id), None)
                if reservation.intent is not None:
                    reservation.intent.data["obligation"] = original or reservation.intent.data["reservation"]
                    if release_reason:
                        if original and (original.get("observed_cost_usd", 0) or original.get("observed_tokens", 0)):
                            raise CostControlStateError("not-started release conflicts with observed usage")
                        reservation.intent.data["release_evidence"] = {"kind": "caller_not_started",
                            "reason": release_reason, "observed_cost_usd": 0, "observed_tokens": 0,
                            "lifecycle_checked": True, "execution_started": False, "failure_recorded": False}
                    durable_json(reservation.intent.path, reservation.intent.data)
                if unresolved_row is not None:
                    # A final receipt can be missing even after priced messages
                    # arrived. Keep that real lower bound after closing the call.
                    observed = max((float(row.get("observed_cost_usd") or 0)
                                    for row in rows if row.get("id") == reservation.reservation_id), default=0.0)
                    if observed:
                        unresolved_row["observed_cost_usd"] = observed
                    unresolved_row["observed_tokens"] = max((int(row.get("observed_tokens") or 0)
                        for row in rows if row.get("id") == reservation.reservation_id), default=0)
                state["reservations"] = [
                    row
                    for row in rows
                    if row.get("id") != reservation.reservation_id
                ]
                unresolved = [
                    row
                    for row in state["unresolved"]
                    if str(row.get("call_id") or "") != reservation.call_id
                ]
                if unresolved_row is not None:
                    unresolved.append(unresolved_row)
                state["unresolved"] = unresolved
                _write_state(reservation.root, state, timestamp)
                state_updated = True
        except CostControlLockBusyError:
            raise

    if reservation.intent is not None:
        reservation.intent.complete(disposition=("released_not_started" if release_reason else
                                                "unresolved" if unknown_reason else "settled"))
    if release_reason:
        _append_audit(
            reservation.root,
            EventType.BUDGET_RESERVATION_RELEASED,
            reservation_id=reservation.reservation_id,
            call_id=reservation.call_id,
            amount_usd=reservation.amount_usd,
            reason=release_reason,
            state_tracked=state_updated,
        )
    else:
        actual = float(cost_usd) if cost_usd is not None else None
        _append_audit(
            reservation.root,
            EventType.BUDGET_RESERVATION_SETTLED,
            reservation_id=reservation.reservation_id,
            call_id=reservation.call_id,
            amount_usd=reservation.amount_usd,
            cost_usd=actual,
            pricing_status=pricing_status,
            error=error,
            state_tracked=state_updated,
        )
    return True


def cost_control_snapshot(
    *,
    global_root: Path | str | None = None,
    now: float | None = None,
    lock_timeout_seconds: float = 0.25,
) -> dict[str, Any]:
    timestamp = time.time() if now is None else float(now)
    root = _global_root(global_root)
    day_start = _local_day_start(timestamp)
    records = _global_records(root, day_start, state_timestamp=timestamp)
    snapshot_stale = False
    try:
        with _locked(root, timeout_seconds=lock_timeout_seconds):
            state = _read_state(root, timestamp)
            reservations = _prune_reservations(list(state["reservations"]), records=records)
            unresolved = _unresolved_costs(records, list(state["unresolved"]))
            state["reservations"] = reservations
            state["unresolved"] = unresolved
            # Projection only: status never persists pruning/reconciliation.
    except CostControlLockBusyError:
        # State writes use atomic replace, so a lock-free read is consistent.
        # UI/metrics readers must not become partial merely because a provider
        # call is settling; prune only in the returned projection and leave the
        # writer-owned file untouched.
        state = _read_state(root, timestamp)
        reservations = _prune_reservations(list(state["reservations"]), records=records)
        unresolved = _unresolved_costs(records, list(state["unresolved"]))
        snapshot_stale = True
    reservations, live_cost, observed_unknown, liabilities = _cost_projection(
        records, {**state, "unresolved": unresolved, "reservations": reservations},
    )
    blocking = [row for row in unresolved if row.get("call_id") not in liabilities]
    tokens, unsettled_tokens = _observed_tokens(records, state)
    payload = {
        "day": state["day"],
        "daily_tokens": tokens,
        "daily_token_cap": resolve_budget_caps(global_root=root).global_daily_token_cap,
        "unsettled_tokens": unsettled_tokens,
        "active_reservations": len(reservations),
        "unresolved_calls": len(unresolved),
        "blocking_unresolved_calls": len(blocking) if _unpriced_policy() == "block" else 0,
        "acknowledged_unresolved_calls": len(liabilities),
        "pending_liability_usd": sum(liabilities.values()),
        "unacknowledged_observed_cost_usd": observed_unknown,
        "in_flight_cost_usd": live_cost,
        "unresolved": [
            {
                **{
                    key: row.get(key)
                    for key in (
                        "call_id",
                        "project_id",
                        "mission_id",
                        "provider",
                        "model",
                        "pricing_status",
                        "run_label",
                        "observed_cost_usd",
                        "reason",
                        "created_at",
                    )
                },
                "blocking": _unpriced_policy() == "block" and row.get("call_id") not in liabilities,
                "acknowledgement": state.get("acknowledgements", {}).get(row.get("call_id")),
            }
            for row in unresolved
        ],
        "policy": _unpriced_policy(),
    }
    if snapshot_stale:
        payload["snapshot_stale"] = True
    return payload


__all__ = [
    "COST_CONTROL_AUDIT_FILE",
    "COST_CONTROL_LOCK_FILE",
    "COST_CONTROL_STATE_FILE",
    "CallBudgetReservation",
    "CostControlLockBusyError",
    "CostControlStateError",
    "acknowledge_unpriced_call",
    "cost_admission_reason",
    "global_daily_usage_summary",
    "cost_control_enabled",
    "cost_control_snapshot",
    "reserve_call_budget",
]
