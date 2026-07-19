"""Atomic call-level cost reservation and settlement.

``usage.jsonl`` remains the authoritative settled ledger. This module protects
the interval between starting a provider call and persisting its final usage so
concurrent daemons cannot all spend the same remaining budget.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .event_catalog import EventType, new_event
from .knobs import resolve_budget_caps, resolve_knob
from .provider_fencing import ProviderSpendFence, provider_spend_fence
from .usage import UsageLedger, UsageRecord

try:  # pragma: no cover - production daemons are POSIX
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

COST_CONTROL_STATE_FILE = "cost-control.json"
COST_CONTROL_LOCK_FILE = "cost-control.lock"
COST_CONTROL_AUDIT_FILE = "cost-control.jsonl"

_STATE_VERSION = 1
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_CONTROL_PLANE_RUN_LABELS = frozenset({
    "manager-frontdoor-classify",
    "router-classify",
    "simple-1",
})
_CONTROL_PLANE_CALL_CAP_USD = 1.0


def _is_control_plane_call(run_label: str) -> bool:
    return str(run_label or "").strip().lower() in _CONTROL_PLANE_RUN_LABELS


def _breach_blocks_call(
    breach: dict[str, Any],
    *,
    provider: str,
    project_id: str,
    control_plane: bool,
) -> bool:
    if str(breach.get("provider") or "") != str(provider or ""):
        return False
    breach_project_id = str(breach.get("project_id") or "")
    if not breach_project_id:
        return True
    if breach_project_id != project_id:
        return False
    breach_is_control_plane = bool(breach.get("control_plane")) or (
        _is_control_plane_call(str(breach.get("run_label") or ""))
    )
    # A bounded Manager/front-door overrun must not stop an independent mission.
    # Mission-call overruns remain provider-wide because they can exhaust the
    # substantive workload's budget directly.
    return control_plane if breach_is_control_plane else True


class CostControlStateError(RuntimeError):
    pass


def _local_day(timestamp: float) -> str:
    local = time.localtime(timestamp)
    return f"{local.tm_year:04d}-{local.tm_mon:02d}-{local.tm_mday:02d}"


def _local_day_start(timestamp: float) -> float:
    local = time.localtime(timestamp)
    return time.mktime(
        (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
    )


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
        "breaches": [],
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
    breaches = payload.get("breaches", [])
    if (
        not isinstance(reservations, list)
        or not isinstance(unresolved, list)
        or not isinstance(breaches, list)
    ):
        raise CostControlStateError(
            f"invalid {path}: reservations, unresolved, and breaches must be arrays"
        )
    return {
        "version": _STATE_VERSION,
        "day": payload["day"],
        "reservations": [row for row in reservations if isinstance(row, dict)],
        "unresolved": [row for row in unresolved if isinstance(row, dict)],
        "breaches": [row for row in breaches if isinstance(row, dict)],
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
        raise CostControlStateError(f"cost control lock busy: {path}")
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl is not None:
                if timeout_seconds is None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                else:
                    deadline = time.monotonic() + max(0.0, timeout_seconds)
                    while True:
                        try:
                            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError as exc:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise CostControlStateError(
                                    f"cost control lock busy: {path}"
                                ) from exc
                            time.sleep(min(0.01, remaining))
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)
    finally:
        thread_lock.release()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _prune_reservations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if _pid_alive(int(row.get("pid") or 0))]


def _project_records(project_root: Path, day_start: float) -> list[UsageRecord]:
    # This reader runs while the global cost-control lock may already be held.
    # Usage reconciliation takes the project usage lock, while provider-call
    # finalization takes those locks in the opposite order (usage, then cost).
    # Triggering reconciliation here can therefore deadlock the whole WebAPI.
    # Budget accounting only needs the durable ledger rows already on disk.
    return UsageLedger(project_root, migrate_legacy=False).records(since=day_start)


def _known_cost(records: list[UsageRecord]) -> float:
    return sum(float(record.cost_usd) for record in records if record.cost_usd is not None)


def _global_records(root: Path, day_start: float) -> list[UsageRecord]:
    projects = root / "projects"
    try:
        project_roots = [path for path in projects.iterdir() if path.is_dir()]
    except OSError:
        project_roots = []
    records: list[UsageRecord] = []
    for project_root in project_roots:
        try:
            records.extend(_project_records(project_root, day_start))
        except Exception:  # noqa: BLE001 - one project cannot hide all spend
            continue
    return records


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


def _intentional_interrupt_reason(reason: object) -> bool:
    low = str(reason or "").strip().casefold()
    return low.startswith(
        (
            "external interrupt: operator abort requested",
            "external interrupt: daemon stop requested",
        )
    )


def _bounded_unresolved(row: dict[str, Any]) -> bool:
    return "blocking" in row and row.get("blocking") is False


def _unresolved_hold_usd(row: dict[str, Any]) -> float:
    if not _bounded_unresolved(row):
        return 0.0
    raw = row.get("held_usd")
    if raw is None:
        raw = resolve_knob("ARGUS_SKILL_PER_CALL_CAP_USD", "5.0").value
    try:
        return max(0.0, float(raw or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _fence_breach_policy() -> str:
    value = resolve_knob(
        "ARGUS_SKILL_FENCE_BREACH_POLICY",
        "block",
    ).value.strip().lower()
    return "allow" if value == "allow" else "block"


def _fence_breach_cooldown_seconds() -> float:
    raw = resolve_knob("ARGUS_SKILL_FENCE_BREACH_COOLDOWN_S", "900").value
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 900.0


def _active_breaches(rows: list[dict[str, Any]], *, now: float) -> list[dict[str, Any]]:
    cooldown = _fence_breach_cooldown_seconds()
    if cooldown <= 0:
        return []
    return [
        row
        for row in rows
        if now - float(row.get("created_at") or 0.0) < cooldown
    ]


def cost_control_enabled() -> bool:
    explicit = str(os.environ.get("ARGUS_SKILL_COST_CONTROL", "") or "").strip()
    if explicit:
        return explicit.lower() in {"1", "true", "yes", "on"}
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    value = resolve_knob("ARGUS_SKILL_COST_CONTROL", "on").value.strip().lower()
    return value in {"1", "true", "yes", "on"}


def per_call_budget_cap_usd() -> float:
    """Configured provider-call envelope; ``0`` keeps legacy all-remaining mode."""
    raw = resolve_knob("ARGUS_SKILL_PER_CALL_CAP_USD", "5.0").value
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 5.0


def _control_plane_call_cap_usd() -> float:
    raw = resolve_knob(
        "ARGUS_SKILL_CONTROL_PLANE_CALL_CAP_USD",
        str(_CONTROL_PLANE_CALL_CAP_USD),
    ).value
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return _CONTROL_PLANE_CALL_CAP_USD


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
    provider_fence: ProviderSpendFence = ProviderSpendFence(enforcement="none")
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


def reserve_call_budget(
    *,
    call_id: str,
    project_root: Path | str | None,
    mission_id: str | None,
    provider: str,
    model: str,
    run_label: str,
    global_root: Path | str | None = None,
    per_mission_cap_usd: float | None = None,
    project_daily_cap_usd: float | None = None,
    global_daily_cap_usd: float | None = None,
    per_call_cap_usd: float | None = None,
    now: float | None = None,
    pid: int | None = None,
) -> tuple[CallBudgetReservation | None, str]:
    """Atomically reserve the maximum currently available amount for one call."""
    timestamp = time.time() if now is None else float(now)
    root = _global_root(global_root)
    project = Path(project_root).expanduser() if project_root is not None else None
    caps = resolve_budget_caps(project_state_dir=project, global_root=root)
    mission_cap = max(
        0.0,
        float(caps.per_mission_cap_usd if per_mission_cap_usd is None else per_mission_cap_usd),
    )
    project_cap = max(
        0.0,
        float(caps.daily_cap_usd if project_daily_cap_usd is None else project_daily_cap_usd),
    )
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
    control_plane = _is_control_plane_call(run_label)

    try:
        with _locked(root):
            state = _read_state(root, timestamp)
            reservations = _prune_reservations(list(state["reservations"]))
            unresolved = _resolved_unpriced(
                list(state["unresolved"]),
                day_start=day_start,
            )
            breaches = _active_breaches(list(state["breaches"]), now=timestamp)
            state["reservations"] = reservations
            state["unresolved"] = unresolved
            state["breaches"] = breaches
            blocking_unresolved = [
                row for row in unresolved if not _bounded_unresolved(row)
            ]
            if (
                blocking_unresolved
                and _unpriced_policy() == "block"
                and not control_plane
            ):
                first = blocking_unresolved[0]
                reason = (
                    "unresolved provider cost blocks new calls "
                    f"(call_id={first.get('call_id')}, "
                    f"pricing_status={first.get('pricing_status')})"
                )
                _write_state(root, state, timestamp)
                _append_audit(
                    root,
                    EventType.BUDGET_UNPRICED_BLOCKED,
                    call_id=call_id,
                    project_id=project.name if project is not None else "",
                    mission_id=mission_key or None,
                    provider=provider,
                    model=model,
                    run_label=run_label,
                    reason=reason,
                )
                return None, reason

            breach = next(
                (
                    row
                    for row in breaches
                    if _breach_blocks_call(
                        row,
                        provider=provider,
                        project_id=project.name if project is not None else "",
                        control_plane=control_plane,
                    )
                ),
                None,
            )
            if breach is not None and _fence_breach_policy() == "block":
                reason = (
                    f"provider {provider} is cooling down after budget fence breach "
                    f"(call_id={breach.get('call_id')}, "
                    f"overrun=${float(breach.get('overrun_usd') or 0.0):.6f})"
                )
                _write_state(root, state, timestamp)
                _append_audit(
                    root,
                    EventType.BUDGET_FENCE_BREACH_BLOCKED,
                    call_id=call_id,
                    project_id=project.name if project is not None else "",
                    mission_id=mission_key or None,
                    provider=provider,
                    model=model,
                    run_label=run_label,
                    breach_call_id=str(breach.get("call_id") or ""),
                    overrun_usd=max(0.0, float(breach.get("overrun_usd") or 0.0)),
                    reason=reason,
                )
                return None, reason

            project_records = _project_records(project, day_start) if project else []
            global_records = _global_records(root, day_start)
            if project is not None:
                projects_root = (root / "projects").resolve()
                try:
                    inside_global = project.resolve().parent == projects_root
                except OSError:
                    inside_global = False
                if not inside_global:
                    global_records.extend(project_records)

            project_spend = _known_cost(project_records)
            global_spend = _known_cost(global_records)
            mission_spend = _known_cost([
                record
                for record in project_records
                if mission_key and record.mission_id == mission_key
            ])
            project_reserved = sum(
                max(0.0, float(row.get("amount_usd") or 0.0))
                for row in reservations
                if str(row.get("project_root") or "") == project_key
            ) + sum(
                _unresolved_hold_usd(row)
                for row in unresolved
                if str(row.get("project_root") or "") == project_key
            )
            global_reserved = sum(
                max(0.0, float(row.get("amount_usd") or 0.0))
                for row in reservations
            ) + sum(_unresolved_hold_usd(row) for row in unresolved)
            mission_reserved = sum(
                max(0.0, float(row.get("amount_usd") or 0.0))
                for row in reservations
                if mission_key
                and str(row.get("project_root") or "") == project_key
                and str(row.get("mission_id") or "") == mission_key
            ) + sum(
                _unresolved_hold_usd(row)
                for row in unresolved
                if mission_key
                and str(row.get("project_root") or "") == project_key
                and str(row.get("mission_id") or "") == mission_key
            )

            available: list[tuple[str, float]] = []
            if mission_key and mission_cap > 0:
                available.append(
                    ("mission", mission_cap - mission_spend - mission_reserved)
                )
            if project is not None and project_cap > 0:
                available.append(
                    ("project daily", project_cap - project_spend - project_reserved)
                )
            if global_cap > 0:
                available.append(
                    ("global daily", global_cap - global_spend - global_reserved)
                )
            exhausted = [(name, amount) for name, amount in available if amount <= 0]
            if exhausted:
                reason = "; ".join(
                    f"{name} budget exhausted (${amount:.6f} available)"
                    for name, amount in exhausted
                )
                state["reservations"] = reservations
                _write_state(root, state, timestamp)
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
                    project_spend_usd=project_spend,
                    global_spend_usd=global_spend,
                )
                return None, reason

            ceiling = mission_cap if mission_cap > 0 else float("inf")
            if available:
                ceiling = min(ceiling, *(amount for _name, amount in available))
            if per_call_cap_usd is not None:
                call_cap = max(0.0, float(per_call_cap_usd))
                if call_cap > 0:
                    ceiling = min(ceiling, call_cap)
            if control_plane:
                control_plane_cap = _control_plane_call_cap_usd()
                if control_plane_cap > 0:
                    ceiling = min(ceiling, control_plane_cap)
            amount = 0.0 if ceiling == float("inf") else max(0.0, ceiling)
            fence = provider_spend_fence(provider, amount)
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
                **fence.event_fields(),
                "created_at": timestamp,
            }
            reservations.append(row)
            state["reservations"] = reservations
            _write_state(root, state, timestamp)
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
        **fence.event_fields(),
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
            provider_fence=fence,
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
    with _locked(reservation.root):
        state = _read_state(reservation.root, timestamp)
        rows = list(state["reservations"])
        matched = next(
            (row for row in rows if row.get("id") == reservation.reservation_id),
            None,
        )
        if matched is None and not unknown_reason:
            return False
        state["reservations"] = [
            row for row in rows if row.get("id") != reservation.reservation_id
        ]
        unresolved = [
            row
            for row in state["unresolved"]
            if str(row.get("call_id") or "") != reservation.call_id
        ]
        breaches = [
            row
            for row in _active_breaches(list(state.get("breaches") or []), now=timestamp)
            if str(row.get("call_id") or "") != reservation.call_id
        ]
        pricing_status = "unknown"
        cost_usd: float | None = None
        error = ""
        if record is not None:
            pricing_status = record.pricing_status
            cost_usd = record.cost_usd
            error = record.error
            if (
                record.status != "denied"
                and (
                    record.cost_usd is None
                    or record.pricing_status in {"partial", "unpriced"}
                )
            ):
                bounded_interrupt = _intentional_interrupt_reason(record.error)
                bounded_copilot_partial = (
                    str(record.provider or "").strip().casefold() == "copilot"
                    and record.pricing_status == "partial"
                    and reservation.amount_usd > 0
                )
                bounded_unpriced = bounded_interrupt or bounded_copilot_partial
                unresolved.append({
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
                    "reason": record.error or "provider usage is not fully priced",
                    "blocking": not bounded_unpriced,
                    "held_usd": (
                        reservation.amount_usd if bounded_unpriced else 0.0
                    ),
                    "created_at": timestamp,
                })
            if record.cost_usd is not None:
                overrun = max(0.0, float(record.cost_usd) - reservation.amount_usd)
                if overrun > 1e-6:
                    breaches.append({
                        "call_id": record.call_id,
                        "project_id": record.project_id,
                        "mission_id": record.mission_id,
                        "provider": record.provider,
                        "model": record.model,
                        "run_label": record.run_label,
                        "control_plane": _is_control_plane_call(record.run_label),
                        "amount_usd": reservation.amount_usd,
                        "cost_usd": record.cost_usd,
                        "overrun_usd": overrun,
                        "created_at": timestamp,
                    })
        elif unknown_reason:
            error = unknown_reason
            bounded_interrupt = _intentional_interrupt_reason(unknown_reason)
            unresolved.append({
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
                "blocking": not bounded_interrupt,
                "held_usd": (
                    reservation.amount_usd if bounded_interrupt else 0.0
                ),
                "created_at": timestamp,
            })
        state["unresolved"] = unresolved
        state["breaches"] = breaches
        _write_state(reservation.root, state, timestamp)

    if release_reason:
        _append_audit(
            reservation.root,
            EventType.BUDGET_RESERVATION_RELEASED,
            reservation_id=reservation.reservation_id,
            call_id=reservation.call_id,
            amount_usd=reservation.amount_usd,
            **reservation.provider_fence.event_fields(),
            reason=release_reason,
        )
    else:
        actual = float(cost_usd) if cost_usd is not None else None
        _append_audit(
            reservation.root,
            EventType.BUDGET_RESERVATION_SETTLED,
            reservation_id=reservation.reservation_id,
            call_id=reservation.call_id,
            amount_usd=reservation.amount_usd,
            **reservation.provider_fence.event_fields(),
            cost_usd=actual,
            overrun_usd=(
                max(0.0, actual - reservation.amount_usd)
                if actual is not None
                else None
            ),
            pricing_status=pricing_status,
            error=error,
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
    with _locked(root, timeout_seconds=lock_timeout_seconds):
        state = _read_state(root, timestamp)
        reservations = _prune_reservations(list(state["reservations"]))
        unresolved = _resolved_unpriced(
            list(state["unresolved"]),
            day_start=_local_day_start(timestamp),
        )
        breaches = _active_breaches(list(state["breaches"]), now=timestamp)
        state["reservations"] = reservations
        state["unresolved"] = unresolved
        state["breaches"] = breaches
        _write_state(root, state, timestamp)
    cooldown = _fence_breach_cooldown_seconds()
    recovery_times = [
        float(row.get("created_at") or 0.0) + cooldown for row in breaches
    ]
    next_recovery = min(recovery_times) if recovery_times else None
    unresolved_held_usd = sum(_unresolved_hold_usd(row) for row in unresolved)
    blocking_unresolved = [
        row for row in unresolved if not _bounded_unresolved(row)
    ]
    return {
        "day": state["day"],
        "active_reservations": len(reservations),
        "reserved_usd": sum(
            max(0.0, float(row.get("amount_usd") or 0.0))
            for row in reservations
        ) + unresolved_held_usd,
        "unresolved_held_usd": unresolved_held_usd,
        "unresolved_calls": len(unresolved),
        "blocking_unresolved_calls": len(blocking_unresolved),
        "unresolved": [
            {
                key: row.get(key)
                for key in (
                    "call_id",
                    "project_id",
                    "mission_id",
                    "provider",
                    "model",
                    "pricing_status",
                    "reason",
                    "blocking",
                    "held_usd",
                    "created_at",
                )
            }
            for row in unresolved
        ],
        "fence_breach_calls": len(breaches),
        "fence_breach_cooldown_seconds": cooldown,
        "fence_breach_next_recovery_at": next_recovery,
        "fence_breach_remaining_seconds": (
            max(0.0, next_recovery - timestamp) if next_recovery is not None else 0.0
        ),
        "fence_breaches": [
            {
                key: row.get(key)
                for key in (
                    "call_id",
                    "project_id",
                    "mission_id",
                    "provider",
                    "model",
                    "run_label",
                    "control_plane",
                    "amount_usd",
                    "cost_usd",
                    "overrun_usd",
                    "created_at",
                )
            }
            for row in breaches
        ],
        "policy": _unpriced_policy(),
        "fence_breach_policy": _fence_breach_policy(),
    }


__all__ = [
    "COST_CONTROL_AUDIT_FILE",
    "COST_CONTROL_LOCK_FILE",
    "COST_CONTROL_STATE_FILE",
    "CallBudgetReservation",
    "CostControlStateError",
    "cost_control_enabled",
    "per_call_budget_cap_usd",
    "cost_control_snapshot",
    "reserve_call_budget",
]
