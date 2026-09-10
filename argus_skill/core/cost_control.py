"""Host-global settled and observed in-flight cost admission.

``usage.jsonl`` remains the authoritative settled ledger. This module protects
the global admission check across concurrent daemons and tracks unresolved
prices without blocking calls. Calls publish observed provider spend while
running; they do not receive or consume a speculative fixed per-call USD hold.
"""

from __future__ import annotations

import calendar
import json
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
from typing import Any, Iterator

import portalocker

from .daemon_lock import is_pid_running
from .event_catalog import EventType, new_event
from .knobs import resolve_budget_caps, resolve_knob
from .paths import session_states_root
from .usage import UsageLedger, UsageRecord, UsageSummary, summarize_usage, usage_pricing_reason

COST_CONTROL_STATE_FILE = "cost-control.json"
COST_CONTROL_LOCK_FILE = "cost-control.lock"
COST_CONTROL_AUDIT_FILE = "cost-control.jsonl"

_STATE_VERSION = 1
_CALL_STATE_LOCK_TIMEOUT_SECONDS = 0.25
_THREAD_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = (
    weakref.WeakValueDictionary()
)
_THREAD_LOCKS_GUARD = threading.Lock()
class CostControlStateError(RuntimeError):
    pass


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
    if version != _STATE_VERSION:
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
    rows: list[dict[str, Any]],
    *,
    records: list[UsageRecord],
) -> tuple[list[dict[str, Any]], float]:
    """Retain observed floors until settlement; count only the unledgered part."""
    by_id = {record.call_id: record for record in records}
    kept: list[dict[str, Any]] = []
    observed_cost = 0.0
    for row in rows:
        record = by_id.get(str(row.get("call_id") or ""))
        if record is not None and (
            record.status == "denied"
            or record.pricing_status == "not_billed"
            or (record.cost_usd is not None
                and record.pricing_status not in {"partial", "unpriced"})
        ):
            continue
        observed = max(0.0, float(row.get("observed_cost_usd") or 0.0))
        if not _pid_alive(int(row.get("pid") or 0)) and observed <= 0:
            continue
        kept.append({**row, "amount_usd": 0.0})
        ledger_cost = _known_cost([record]) if record is not None else 0.0
        observed_cost += max(0.0, observed - ledger_cost)
    return kept, observed_cost


def _project_records(project_root: Path, day_start: float) -> list[UsageRecord]:
    # All callers read before taking the global cost lock. Reconcile pending
    # Copilot telemetry here so a late SQLite write updates known spend
    # without requiring a UI reader. Never migrate unrelated historical events
    # or take the usage lock while holding the cost lock (usage -> cost order).
    ledger = UsageLedger(project_root, migrate_legacy=False)
    records = ledger.records(since=day_start)
    if any(record.provider == "copilot" and (
        record.cost_usd is None
        or record.pricing_status in {"partial", "unpriced"}
        or record.cost_basis == "premium_request"
    ) for record in records):
        ledger.ensure_copilot_usage_reconciled()
        records = ledger.records(since=day_start)
    return records


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
                "reason": usage_pricing_reason(record),
                "created_at": record.completed_at,
            }
    return [{**row, "blocking": False} for row in unresolved.values()]


def _budget_reason(
    records: list[UsageRecord], state: dict[str, Any], cap: float,
) -> str:
    _, observed_cost = _prune_reservations(list(state["reservations"]), records=records)
    spent = _known_cost(records) + observed_cost
    if cap > 0 and spent >= cap:
        return f"global daily budget exhausted (${cap - spent:.6f} available)"
    return ""


def _global_records(root: Path, day_start: float) -> list[UsageRecord]:
    projects = session_states_root(root)
    try:
        project_roots = [path for path in projects.iterdir() if path.is_dir()]
    except OSError:
        project_roots = []
    # A caller can place its ledger outside global/projects. Retain those
    # unsettled references so later reconciliations update global known spend.
    # This reader must always run before acquiring the global state lock.
    try:
        state = _read_state(root, day_start + 12 * 60 * 60)
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
        except Exception:  # noqa: BLE001 - one project cannot hide all spend
            continue
    return records


def global_daily_usage_summary(
    *,
    global_root: Path | str | None = None,
    now: float | None = None,
) -> UsageSummary:
    """Settled daily usage from the same ledgers that enforce call admission.

    Registered ledgers may live outside ``projects/``. Include those references
    and deduplicate calls just as admission does, so a paused supervisor does
    not repeatedly resume against an incomplete spend total.
    """
    timestamp = time.time() if now is None else float(now)
    records = _global_records(_global_root(global_root), _local_day_start(timestamp))
    unique = {(record.project_id, record.call_id): record for record in records}
    return summarize_usage(unique.values())


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

    def observe_cost(self, cost_usd: float = 0.0, *, now: float | None = None) -> str:
        """Publish observed in-flight spend and check the shared daily cap.

        This is real provider telemetry, not a speculative fixed call hold.
        Callers pass only usage incurred on the current local day.
        """
        if self._closed:
            return ""
        if not math.isfinite(cost_usd) or cost_usd < 0:
            raise ValueError("observed cost must be finite and non-negative")
        timestamp = time.time() if now is None else now
        records = _global_records(self.root, _local_day_start(timestamp))
        if self.project_root is not None:
            if self.project_root.resolve().parent != session_states_root(self.root).resolve():
                records.extend(_project_records(self.project_root, _local_day_start(timestamp)))
        cap = resolve_budget_caps(global_root=self.root).global_daily_cap_usd
        # Always read ledgers before the cost-state lock (usage -> cost order).
        with _locked(self.root, timeout_seconds=_CALL_STATE_LOCK_TIMEOUT_SECONDS):
            state = _read_state(self.root, timestamp)
            if self.project_root is not None:
                state["project_roots"] = sorted(set(state["project_roots"]) |
                                                {str(self.project_root.resolve())})
            state["reservations"], _ = _prune_reservations(
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
            reason = _budget_reason(records, state, cap)
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
    """Admit a call against known settled spend and observed running costs."""
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
    project_records = _project_records(project, day_start) if project else []
    global_records = _global_records(root, day_start)
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
            reservations, _ = _prune_reservations(
                list(state["reservations"]),
                records=global_records,
            )
            state["reservations"] = reservations
            state["unresolved"] = _unresolved_costs(global_records, list(state["unresolved"]))
            reason = _budget_reason(global_records, state, global_cap)
            if reason:
                _write_state(root, state, timestamp)
                _append_audit(root, EventType.BUDGET_RESERVATION_DENIED,
                              call_id=call_id, provider=provider, reason=reason)
                return None, reason
            reservations.append(row)
            state["reservations"] = reservations
            _write_state(root, state, timestamp)
    except CostControlLockBusyError:
        # Atomic state reads still include observed in-flight costs.
        # Contention must not silently bypass the daily budget gate.
        reason = _budget_reason(global_records, _read_state(root, timestamp), global_cap)
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
            "pricing_status": record.pricing_status,
            "reason": usage_pricing_reason(record),
            "blocking": False,
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
            "blocking": False,
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
                state["reservations"] = [
                    row
                    for row in rows
                    if row.get("id") != reservation.reservation_id
                    or (unresolved_row is not None
                        and float(row.get("observed_cost_usd") or 0.0) > 0)
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
            # Usage is already durable in the project ledger. Do not delay the
            # user-visible result behind unrelated cost-state housekeeping.
            state_updated = False

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
    records = _global_records(root, day_start)
    snapshot_stale = False
    try:
        with _locked(root, timeout_seconds=lock_timeout_seconds):
            state = _read_state(root, timestamp)
            reservations, observed_cost = _prune_reservations(
                list(state["reservations"]),
                records=records,
            )
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
        reservations, observed_cost = _prune_reservations(
            list(state["reservations"]),
            records=records,
        )
        unresolved = _unresolved_costs(records, list(state["unresolved"]))
        snapshot_stale = True
    payload = {
        "day": state["day"],
        "active_reservations": len(reservations),
        "unresolved_calls": len(unresolved),
        "blocking_unresolved_calls": 0,
        "in_flight_cost_usd": observed_cost,
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
                        "reason",
                        "created_at",
                    )
                },
                "blocking": False,
            }
            for row in unresolved
        ],
        "policy": "allow",
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
    "cost_control_enabled",
    "cost_control_snapshot",
    "global_daily_usage_summary",
    "reserve_call_budget",
]
