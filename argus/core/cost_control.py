"""Host-global settled and observed in-flight cost admission.

``usage.jsonl`` remains the authoritative settled ledger. This module protects
the global admission check and unresolved-price policy across concurrent
daemons. Calls publish observed provider spend while running; they do not
receive or consume a speculative fixed per-call USD hold. A call whose cost is
not settled counts as the day's costliest priced call until it is; nothing is
ever refused for want of a price, only for the daily cap.
"""

from __future__ import annotations

import calendar
import json
import logging
import math
import os
import tempfile
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

import portalocker

from .daemon_lock import is_pid_running
from .event_catalog import EventType, new_event
from .knobs import resolve_budget_caps, resolve_knob
from .paths import session_states_root
from .pricing import copilot_usd_per_premium_request, quote_copilot_usage
from .usage import (
    DamagedJournalLine,
    UsageJournalIntegrityError,
    UsageJournalRepair,
    UsageLedger,
    UsageRecord,
    UsageSummary,
    summarize_usage,
    usage_pricing_reason,
)

log = logging.getLogger(__name__)

# A record the repair wrote for a call whose own record was truncated. Its
# cost is the journal's costliest comparable call, never the provider's word.
JOURNAL_REPAIR_TIER = "journal_repair_estimate"

COST_CONTROL_STATE_FILE = "cost-control.json"
COST_CONTROL_LOCK_FILE = "cost-control.lock"
COST_CONTROL_AUDIT_FILE = "cost-control.jsonl"
ACCOUNTING_INTEGRITY_REASON = "accounting_integrity"

# Version 2 files may still carry the retired ``acknowledgements`` map; it is
# read past and dropped on the next write.
_STATE_VERSION = 2
_CALL_STATE_LOCK_TIMEOUT_SECONDS = 0.25
_THREAD_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = (
    weakref.WeakValueDictionary()
)
_THREAD_LOCKS_GUARD = threading.Lock()
class CostControlStateError(RuntimeError):
    pass


class CostControlLockBusyError(CostControlStateError):
    """Raised when a bounded read cannot acquire the host-global lock."""


class AccountingIntegrityError(CostControlStateError):
    """A ledger this admission depends on holds malformed records."""

    def __init__(self, cause: UsageJournalIntegrityError) -> None:
        self.cause = cause
        self.reason_code = cause.reason_code
        super().__init__(f"{ACCOUNTING_INTEGRITY_REASON}: {cause}")


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
        "updated_at": timestamp,
    }


def _read_state(root: Path, timestamp: float) -> dict[str, Any]:
    path = root / COST_CONTROL_STATE_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _default_state(timestamp)
    except OSError as exc:
        raise CostControlStateError(f"cannot read {path}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CostControlStateError(f"invalid {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CostControlStateError(f"invalid {path}: expected an object")
    try:
        version = int(payload.get("version") or 0)
    except (TypeError, ValueError) as exc:
        raise CostControlStateError(
            f"invalid {path}: version must be an integer"
        ) from exc
    if version not in {1, _STATE_VERSION}:
        raise CostControlStateError(
            f"unsupported cost-control state version {payload.get('version')!r}"
        )
    if str(payload.get("day") or "") != _local_day(timestamp):
        return _default_state(timestamp)
    reservations = payload.get("reservations")
    unresolved = payload.get("unresolved")
    if not isinstance(reservations, list) or not isinstance(unresolved, list):
        raise CostControlStateError(
            f"invalid {path}: reservations and unresolved must be arrays"
        )
    return {
        "version": _STATE_VERSION,
        "day": payload["day"],
        "reservations": [row for row in reservations if isinstance(row, dict)],
        "unresolved": [row for row in unresolved if isinstance(row, dict)],
        "project_roots": [str(path) for path in payload.get("project_roots", [])
                          if isinstance(path, str)],
        "updated_at": float(payload.get("updated_at") or timestamp),
    }


def _write_state(root: Path, state: dict[str, Any], timestamp: float) -> None:
    root.mkdir(parents=True, exist_ok=True)
    state["version"] = _STATE_VERSION
    state["day"] = _local_day(timestamp)
    state["updated_at"] = timestamp
    target = root / COST_CONTROL_STATE_FILE
    fd, tmp_name = tempfile.mkstemp(prefix=".cost-control-", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


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
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0), 0o600)
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
        if (_pid_alive(int(row.get("pid") or 0))
                or float(row.get("observed_cost_usd") or 0) > 0
                or int(row.get("observed_tokens") or 0) > 0):
            kept.append({**row, "amount_usd": 0.0})
    return kept


def _project_records(
    project_root: Path, day_start: float, *, global_root: Path | None = None,
) -> list[UsageRecord]:
    # All callers read before taking the global cost lock. Reconcile pending
    # Copilot telemetry here so a late SQLite write releases the budget gate
    # without requiring a UI reader. Never migrate unrelated historical events
    # or take the usage lock while holding the cost lock (usage -> cost order).
    ledger = UsageLedger(project_root, migrate_legacy=False)
    try:
        return _reconciled_records(ledger, day_start)
    except UsageJournalIntegrityError as exc:
        if not _repair_project_journal(ledger, exc, global_root=global_root):
            raise AccountingIntegrityError(exc) from exc
    try:
        return _reconciled_records(ledger, day_start)
    except UsageJournalIntegrityError as exc:
        raise AccountingIntegrityError(exc) from exc


def _reconciled_records(ledger: UsageLedger, day_start: float) -> list[UsageRecord]:
    records = ledger.records(since=day_start)
    if any(record.provider == "copilot" and (
        record.cost_usd is None
        or record.pricing_status in {"partial", "unpriced"}
        or record.cost_basis == "premium_request"
    ) for record in records):
        ledger.ensure_copilot_usage_reconciled()
        records = ledger.records(since=day_start)
    return records


def _journal_repair_estimate(
    records: list[UsageRecord], *, provider: str, model: str,
) -> float | None:
    """The costliest comparable priced call in this journal, or None without one.

    A truncated record hides a call that ran; zero observations are not
    evidence of zero charge. The estimate is deliberately the largest cost
    the same provider (and model, when both are known) has produced in this
    journal, so it can only err on the side of the campaign. Another
    provider's prices say nothing about this call: without a same-provider
    comparable the call stays unpriced and is held for the operator.
    """
    if not provider or provider == "unknown":
        return None
    priced = [
        record for record in records
        if record.cost_usd is not None and record.pricing_status == "priced"
        and record.pricing_tier != JOURNAL_REPAIR_TIER and record.provider == provider
    ]
    by_model = [record for record in priced if model and record.model == model]
    pool = by_model or priced
    if pool:
        return max(float(record.cost_usd or 0.0) for record in pool)
    if provider == "copilot":
        return quote_copilot_usage(1.0).cost_usd
    return None


def _repair_liabilities(
    ledger: UsageLedger, lines: dict[str, int],
) -> Callable[[list[dict[str, Any]], tuple[DamagedJournalLine, ...], Path], list[dict[str, Any]]]:
    """Rows that carry each cut-off call as debt, written with the repair itself."""

    def liabilities(
        rows: list[dict[str, Any]], damaged: tuple[DamagedJournalLine, ...], copy: Path,
    ) -> list[dict[str, Any]]:
        records: list[UsageRecord] = []
        for row in rows:
            try:
                records.append(UsageRecord.from_jsonable(row))
            except (TypeError, ValueError):
                continue
        known = {record.call_id for record in records}
        now = time.time()
        written: list[dict[str, Any]] = []
        for line in damaged:
            if line.lost_bytes <= 0:
                continue
            fields = line.prefix_fields
            call_id = str(fields.get("call_id") or "")
            if call_id in known:
                continue
            identified = bool(call_id)
            if not identified:
                # The cut fell before the call said who it was. Something ran;
                # hold it under a name tied to the evidence copy and line.
                call_id = f"journal-repair:{copy.name}:{line.line_number}"
            provider = str(fields.get("provider") or "unknown")
            model = str(fields.get("model") or "unknown")
            estimate = (
                _journal_repair_estimate(records, provider=provider, model=model)
                if identified else None
            )
            started_at = float(fields.get("started_at") or now)
            completed_at = float(fields.get("completed_at") or started_at)
            if estimate is not None:
                cost_note = "cost estimated from the journal's costliest comparable call"
            elif identified:
                cost_note = "no comparable priced call in the journal, cost unknown"
            else:
                cost_note = "call identity lost with the record, cost unknown"
            record = UsageRecord(
                call_id=call_id,
                project_id=str(fields.get("project_id") or ledger.project_root.name),
                mission_id=fields.get("mission_id"),
                provider=provider,
                model=model,
                run_label=str(fields.get("run_label") or ""),
                started_at=started_at,
                completed_at=completed_at,
                status="completed",
                input_tokens=None,
                cached_input_tokens=None,
                output_tokens=None,
                reasoning_output_tokens=None,
                premium_requests=None,
                pricing_status="priced" if estimate is not None else "unpriced",
                pricing_tier=JOURNAL_REPAIR_TIER if estimate is not None else "unknown",
                cost_usd=estimate,
                cost_basis="estimate" if estimate is not None else "none",
                thread_id=fields.get("thread_id"),
                error=(
                    f"usage record truncated at {ledger.path.name} line {line.line_number} "
                    f"({line.detail}); {cost_note}"
                ),
            )
            known.add(call_id)
            lines[call_id] = line.line_number
            written.append(record.to_jsonable())
        return written

    return liabilities


def _repair_project_journal(
    ledger: UsageLedger, error: UsageJournalIntegrityError, *, global_root: Path | None,
) -> bool:
    """Recover from a damaged usage journal without losing evidence or spend.

    The damaged bytes are kept beside the journal; every complete record is
    kept in it. A call whose record was truncated is written back as a
    liability in the same atomic replace: priced at the journal's costliest
    same-provider call when there is one, so the campaign continues under a
    conservative number, or left unpriced so the usual unresolved-cost
    policy holds it for the operator. Returns False only when the repair
    could not write; admission then stays refused with the original reason.
    When another reader repaired first, there is nothing left to repair and
    the caller simply reads again.
    """
    lines: dict[str, int] = {}
    try:
        repair = ledger.repair_journal(liabilities=_repair_liabilities(ledger, lines))
    except OSError as exc:
        log.error("usage journal %s could not be repaired: %s", ledger.path, exc)
        return False
    if repair is None:
        return True
    written = [
        {"call_id": row.get("call_id"), "cost_usd": row.get("cost_usd"),
         "line": lines.get(str(row.get("call_id")))}
        for row in repair.liabilities
    ]
    if global_root is not None:
        _append_repair_audit(global_root, ledger, repair, written)
    log.warning(
        "usage journal %s repaired: %d damaged line(s) set aside in %s, %d record(s) kept, "
        "%d truncated call(s) written back as liabilities",
        repair.path, len(repair.damaged), repair.damaged_copy, repair.kept_records, len(written),
    )
    return True


def _append_repair_audit(
    root: Path, ledger: UsageLedger, repair: UsageJournalRepair, written: list[dict[str, Any]],
) -> None:
    _append_audit(
        root,
        EventType.ACCOUNTING_JOURNAL_REPAIRED,
        project_id=ledger.project_root.name,
        path=str(repair.path),
        damaged_copy=str(repair.damaged_copy),
        kept_records=repair.kept_records,
        damaged_lines=[
            {
                "line": line.line_number, "detail": line.detail,
                "call_id": line.prefix_fields.get("call_id"),
                "recovered_call_id": line.recovered_call_id,
                "lost_bytes": line.lost_bytes,
            }
            for line in repair.damaged
        ],
        liabilities=written,
    )


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


def _unpriced_estimate_usd(records: list[UsageRecord]) -> float:
    """What one unsettled call counts as today: the day's costliest priced call.

    A call whose price is not known yet ran like the others, so the dearest
    settled call of the same day is the figure that can only err against the
    campaign. Journal-repair estimates are themselves estimates and stay out.
    """
    unique = {record.call_id: record for record in records}.values()
    return max(
        (
            float(record.cost_usd or 0.0) for record in unique
            if record.status != "denied" and record.pricing_status == "priced"
            and record.cost_usd is not None and record.pricing_tier != JOURNAL_REPAIR_TIER
        ),
        default=0.0,
    )


def _counted_unknown_costs(
    records: list[UsageRecord], state: dict[str, Any],
) -> dict[str, float]:
    """What each unresolved call counts toward the cap beyond its known cost.

    That is the day's costliest priced call, or the call's observed in-flight
    spend when that is higher; a Copilot call on a day without any priced call
    counts as one premium request.
    """
    estimate = _unpriced_estimate_usd(records)
    known = {r.call_id: _known_cost([r]) for r in records}
    counted: dict[str, float] = {}
    for row in _unresolved_costs(records, list(state["unresolved"])):
        call_id = str(row.get("call_id") or "")
        floor = estimate
        if not floor and str(row.get("provider") or "").strip().lower() == "copilot":
            floor = copilot_usd_per_premium_request()
        observed = float(row.get("observed_cost_usd") or 0)
        counted[call_id] = max(0.0, max(observed, floor) - known.get(call_id, 0.0))
    return counted


def _cost_projection(
    records: list[UsageRecord], state: dict[str, Any],
) -> tuple[list[dict[str, Any]], float, float]:
    """Partition outstanding spend without adding overlapping evidence twice.

    A completed unknown call transfers its observation to unresolved state (v2),
    not to a fictitious live provider call. Before that transfer, partial usage
    may already be durable. Both stages count only the part absent from usage.
    Returns the live reservations, the in-flight spend not yet covered by an
    unresolved row, and what unresolved calls count toward the cap.
    """
    counted = _counted_unknown_costs(records, state)
    live = _prune_reservations(list(state["reservations"]), records=records)
    known = {record.call_id: _known_cost([record]) for record in records}
    live_by_id: dict[str, float] = {}
    for row in live:
        call_id = str(row.get("call_id") or "")
        remainder = max(0.0, float(row.get("observed_cost_usd") or 0) - known.get(call_id, 0.0))
        live_by_id[call_id] = max(live_by_id.get(call_id, 0.0), remainder)
    live_extra = sum(max(0.0, amount - counted.get(key, 0.0)) for key, amount in live_by_id.items())
    return live, live_extra, sum(counted.values())


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
    return count(summarize_usage(settled)) + unsettled, unsettled


def _budget_reason(
    records: list[UsageRecord], state: dict[str, Any], cap: float,
    *, token_cap: int = 0,
) -> str:
    if token_cap > 0:
        tokens, _ = _observed_tokens(records, state)
        if tokens >= token_cap:
            return f"global daily token budget exhausted ({tokens}/{token_cap} tokens)"
    _live, live_cost, counted_unknown = _cost_projection(records, state)
    spent = _known_cost(records) + counted_unknown + live_cost
    if cap > 0 and spent >= cap:
        return f"global daily budget exhausted (${cap - spent:.6f} available)"
    return ""


def global_daily_usage_summary(
    *, global_root: Path | str | None = None, now: float | None = None,
) -> UsageSummary:
    """Expose the same complete daily ledgers to supervisor budget recovery."""
    timestamp = time.time() if now is None else float(now)
    records = _global_records(_global_root(global_root), _local_day_start(timestamp), state_timestamp=timestamp)
    unique = {(record.project_id, record.call_id): record for record in records}
    return summarize_usage(unique.values())


def cost_admission_reason(
    *, global_root: Path | str | None = None, cap: float | None = None,
    now: float | None = None,
) -> str:
    """Shared preflight for automatic pause recovery; no provider call is made."""
    timestamp = time.time() if now is None else float(now)
    root = _global_root(global_root)
    try:
        records = _global_records(root, _local_day_start(timestamp), state_timestamp=timestamp)
    except AccountingIntegrityError as exc:
        return str(exc)
    caps = resolve_budget_caps(global_root=root)
    limit = caps.global_daily_cap_usd if cap is None else cap
    state = _read_state(root, timestamp)
    return _budget_reason(records, state, limit, token_cap=caps.global_daily_token_cap)


def _global_records(root: Path, day_start: float, *, state_timestamp: float) -> list[UsageRecord]:
    projects = session_states_root(root)
    try:
        project_roots = [path for path in projects.iterdir() if path.is_dir()]
    except OSError:
        project_roots = []
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
            records.extend(_project_records(project_root, day_start, global_root=root))
        except AccountingIntegrityError:
            # Malformed accounting is not "one project's" problem: admission
            # must refuse rather than sum a ledger with hidden records.
            raise
        except Exception:  # noqa: BLE001 - one project cannot hide all spend
            continue
    return records


def _append_audit(root: Path, event_type: EventType, **payload: Any) -> None:
    try:
        row = new_event(event_type, **payload)
        with (root / COST_CONTROL_AUDIT_FILE).open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
    except OSError:
        pass


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

    def release(self, *, reason: str = "not_started") -> bool:
        if self._closed:
            return False
        changed = _close_reservation(self, release_reason=reason)
        self._closed = True
        return changed

    def settle(self, record: UsageRecord) -> bool:
        if self._closed:
            return False
        changed = _close_reservation(self, record=record)
        self._closed = True
        return changed

    def settle_unknown(self, *, reason: str) -> bool:
        if self._closed:
            return False
        changed = _close_reservation(self, unknown_reason=reason)
        self._closed = True
        return changed

    def observe_cost(self, cost_usd: float = 0.0, *, now: float | None = None,
                     tokens: int = 0) -> str:
        """Publish observed in-flight spend and check the shared daily cap.

        This is real provider telemetry, not a speculative fixed call hold.
        Callers pass only usage incurred on the current local day.
        """
        if self._closed:
            return ""
        if not math.isfinite(cost_usd) or cost_usd < 0:
            raise ValueError("observed cost must be finite and non-negative")
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
            raise ValueError("observed tokens must be a non-negative integer")
        timestamp = time.time() if now is None else now
        records = _global_records(self.root, _local_day_start(timestamp), state_timestamp=timestamp)
        if self.project_root is not None:
            if self.project_root.resolve().parent != session_states_root(self.root).resolve():
                records.extend(_project_records(
                    self.project_root, _local_day_start(timestamp), global_root=self.root,
                ))
        caps = resolve_budget_caps(global_root=self.root)
        # Always read ledgers before the cost-state lock (usage -> cost order).
        with _locked(self.root, timeout_seconds=_CALL_STATE_LOCK_TIMEOUT_SECONDS):
            state = _read_state(self.root, timestamp)
            if self.project_root is not None:
                state["project_roots"] = sorted(set(state["project_roots"]) |
                                                {str(self.project_root.resolve())})
            state["reservations"] = _prune_reservations(
                list(state["reservations"]),
                records=records,
            )
            row = next((item for item in state["reservations"]
                        if item.get("id") == self.reservation_id), None)
            if row is None:
                row = {"id": self.reservation_id, "call_id": self.call_id,
                       "pid": os.getpid(), "amount_usd": 0.0,
                       "created_at": timestamp}
                state["reservations"].append(row)
            row["observed_cost_usd"] = max(float(row.get("observed_cost_usd") or 0), cost_usd)
            row["observed_tokens"] = max(int(row.get("observed_tokens") or 0), tokens)
            reason = _budget_reason(records, state, caps.global_daily_cap_usd,
                                    token_cap=caps.global_daily_token_cap)
            _write_state(self.root, state, timestamp)
            self.state_tracked = True
        return reason


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
) -> tuple[CallBudgetReservation | None, str]:
    """Admit a call against settled spend, observed running costs and unknowns."""
    timestamp = time.time() if now is None else float(now)
    root = _global_root(global_root)
    project = Path(project_root).expanduser() if project_root is not None else None
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
        project_records = _project_records(project, day_start, global_root=root) if project else []
        global_records = _global_records(root, day_start, state_timestamp=timestamp)
    except AccountingIntegrityError as exc:
        reason = str(exc)
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
            reason_code=exc.reason_code,
        )
        return None, reason
    if project is not None:
        projects_root = session_states_root(root).resolve()
        try:
            inside_global = project.resolve().parent == projects_root
        except OSError:
            inside_global = False
        if not inside_global:
            known_ids = {record.call_id for record in global_records}
            global_records.extend(record for record in project_records
                                  if record.call_id not in known_ids)

    global_spend = _known_cost(global_records)
    available = global_cap - global_spend
    if global_cap > 0 and available <= 0:
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
    try:
        with _locked(root, timeout_seconds=lock_timeout_seconds):
            state = _read_state(root, timestamp)
            if project_key:
                state["project_roots"] = sorted(set(state["project_roots"]) | {project_key})
            reservations = _prune_reservations(
                list(state["reservations"]),
                records=global_records,
            )
            state["reservations"] = reservations
            state["unresolved"] = _unresolved_costs(global_records, list(state["unresolved"]))
            reason = _budget_reason(global_records, state, global_cap, token_cap=caps.global_daily_token_cap)
            if reason:
                _write_state(root, state, timestamp)
                _append_audit(root, EventType.BUDGET_RESERVATION_DENIED,
                              call_id=call_id, provider=provider, reason=reason)
                return None, reason
            reservations.append(row)
            state["reservations"] = reservations
            _write_state(root, state, timestamp)
    except CostControlLockBusyError:
        # Atomic state reads still include observed in-flight costs and unknown
        # settlements. Contention must not silently bypass either budget gate.
        state = _read_state(root, timestamp)
        reason = _budget_reason(global_records, state, global_cap,
                                token_cap=caps.global_daily_token_cap)
        if reason:
            return None, reason
        state_tracked = False
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
            "run_label": record.run_label,
            "pricing_status": record.pricing_status,
            "reason": usage_pricing_reason(record),
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
            "created_at": timestamp,
        }

    state_updated = False
    failure: Exception | None = None
    if reservation.state_tracked:
        try:
            with _locked(
                reservation.root,
                timeout_seconds=_CALL_STATE_LOCK_TIMEOUT_SECONDS,
            ):
                state = _read_state(reservation.root, timestamp)
                rows = list(state["reservations"])
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
        except CostControlLockBusyError as exc:
            # Usage is already durable in the project ledger. Do not delay the
            # user-visible result behind unrelated cost-state housekeeping.
            state_updated = False
            failure = exc
        except (OSError, CostControlStateError) as exc:
            failure = exc
    if record is None and unresolved_row is not None and not state_updated:
        log.warning(
            "call %s ended without a usage record and its unsettled cost could not be "
            "recorded (%s); it will not count toward today's cap",
            reservation.call_id,
            f"{type(failure).__name__}: {failure}" if failure is not None else "reservation was not state-tracked",
        )
    if failure is not None and not isinstance(failure, CostControlLockBusyError):
        raise failure

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


def _premium_usage(records: list[UsageRecord]) -> dict[str, Any]:
    """Today's premium requests and what they cost, total and per run label.

    A subscription backend bills requests, not tokens, so a day of work can
    read "0 tokens" while spending real quota; this is the figure the operator
    actually pays in. Same call-id dedupe as the token tally.
    """
    by_label: dict[str, dict[str, Any]] = {}
    unique = {record.call_id: record for record in records}.values()
    for record in unique:
        if record.status == "denied":
            continue
        requests = float(record.premium_requests or 0.0)
        if requests <= 0:
            continue
        usd = record.premium_request_cost_usd
        if usd is None:
            usd = requests * copilot_usd_per_premium_request()
        label = str(record.run_label or "") or "unlabelled"
        row = by_label.setdefault(label, {"run_label": label, "premium_requests": 0.0, "usd": 0.0, "calls": 0})
        row["premium_requests"] += requests
        row["usd"] += float(usd)
        row["calls"] += 1
    rows = sorted(by_label.values(), key=lambda row: (-row["usd"], row["run_label"]))
    return {
        "daily_premium_requests": sum(row["premium_requests"] for row in rows),
        "daily_premium_usd": sum(row["usd"] for row in rows),
        "premium_by_run_label": rows,
    }


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
            _write_state(root, state, timestamp)
    except CostControlLockBusyError:
        # State writes use atomic replace, so a lock-free read is consistent.
        # UI/metrics readers must not become partial merely because a provider
        # call is settling; prune only in the returned projection and leave the
        # writer-owned file untouched.
        state = _read_state(root, timestamp)
        reservations = _prune_reservations(list(state["reservations"]), records=records)
        unresolved = _unresolved_costs(records, list(state["unresolved"]))
        snapshot_stale = True
    projected = {**state, "unresolved": unresolved, "reservations": reservations}
    reservations, live_cost, counted_unknown = _cost_projection(records, projected)
    observed_unknown = sum(_unresolved_observed_costs(records, projected).values())
    tokens, unsettled_tokens = _observed_tokens(records, state)
    payload = {
        "day": state["day"],
        "daily_tokens": tokens,
        "daily_token_cap": resolve_budget_caps(global_root=root).global_daily_token_cap,
        **_premium_usage(records),
        "unsettled_tokens": unsettled_tokens,
        "active_reservations": len(reservations),
        # Calls whose cost the provider has not settled: how many, the spend
        # already observed for them, what they count toward the cap, and the
        # per-call figure behind it (0 when nothing is priced today).
        "unresolved_calls": len(unresolved),
        "observed_unpriced_usd": observed_unknown,
        "counted_unpriced_usd": counted_unknown,
        "unpriced_estimate_usd": _unpriced_estimate_usd(records),
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
            }
            for row in unresolved
        ],
    }
    if snapshot_stale:
        payload["snapshot_stale"] = True
    return payload


__all__ = [
    "ACCOUNTING_INTEGRITY_REASON",
    "COST_CONTROL_AUDIT_FILE",
    "COST_CONTROL_LOCK_FILE",
    "COST_CONTROL_STATE_FILE",
    "AccountingIntegrityError",
    "CallBudgetReservation",
    "CostControlLockBusyError",
    "CostControlStateError",
    "cost_admission_reason",
    "global_daily_usage_summary",
    "cost_control_enabled",
    "cost_control_snapshot",
    "reserve_call_budget",
]
