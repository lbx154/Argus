"""Atomic, host-visible resource grants with TTL orphan recovery."""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import socket
import stat
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ...core.file_lock import exclusive_file_lock
from ...core.paths import global_root
from ...core.process_identity import (
    capture_process_identity,
    process_identity_is_running,
)
from .probe import ResourceProbe

DEFAULT_ROOT = Path("/var/tmp/argus-resource-ledger")
# Heartbeat renewal keeps live grants valid; this TTL only reaps crashed owners.
DEFAULT_TTL_SECONDS = 120.0


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def demand_hash(demand: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(normalize_demand(demand))).hexdigest()


def normalize_demand(demand: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(demand.get("accelerator") or "any").strip().lower()
    if kind not in {"cuda", "rocm", "any", "none"}:
        raise ValueError(f"unsupported accelerator kind: {kind!r}")
    default_count = 0 if kind == "none" else 1
    count = int(demand.get("device_count", default_count))
    memory = int(demand.get("mem_mib_estimate", 0))
    duration = int(demand.get("expected_duration_seconds", 0))
    if count < 0 or memory < 0 or duration < 0:
        raise ValueError("device count, memory estimate, and duration must be non-negative")
    if kind == "none" and count != 0:
        raise ValueError("accelerator=none requires device_count=0")
    if kind != "none" and count == 0:
        raise ValueError("accelerator demand requires at least one device")
    return {
        "accelerator": kind,
        "device_count": count,
        "mem_mib_estimate": memory,
        "expected_duration_seconds": duration,
        "checkpointable": bool(demand.get("checkpointable", False)),
        "intent": " ".join(str(demand.get("intent") or "").split()),
    }


def owner_identity(
    *,
    project_root: Path | str,
    task_id: str,
    pid: int | None = None,
) -> dict[str, Any]:
    process = capture_process_identity(os.getpid() if pid is None else int(pid))
    return {
        "unix_user": getpass.getuser(),
        "unix_uid": os.getuid() if hasattr(os, "getuid") else None,
        "pid": process["pid"],
        "start_ticks": process.get("start_time_ticks"),
        "process_identity": process,
        "project_root": str(Path(project_root).expanduser().resolve()),
        "task_id": str(task_id),
    }


def _host_epoch() -> str:
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        boot_id = ""
    return boot_id or socket.gethostname()


def _prepare_directory(path: Path, mode: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, mode)
    except PermissionError:
        actual = stat.S_IMODE(path.stat().st_mode)
        if actual & mode != mode:
            raise


def _is_shared_sticky(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return bool(mode & stat.S_ISVTX and mode & stat.S_IWOTH and mode & stat.S_IXOTH)


class ResourceLedger:
    """One filesystem ledger whose decisions are serialized by one flock."""

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        probe: ResourceProbe | Callable[[], dict[str, Any]] | None = None,
        clock: Callable[[], float] = time.time,
        identity_alive: Callable[[Mapping[str, Any]], bool] | None = None,
    ) -> None:
        self._clock = clock
        self._probe = probe or ResourceProbe()
        self._identity_alive = identity_alive or self._default_identity_alive
        self.host_epoch = _host_epoch()
        self.root, self.scope, self.scope_detail = self._resolve_root(root)
        self.grants_dir = self.root / "grants"
        self.queue_dir = self.root / "queue"
        _prepare_directory(self.grants_dir, 0o777)
        _prepare_directory(self.queue_dir, 0o777)
        self.lock_path = self.root / "ledger.lock"
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o666)
        os.close(descriptor)
        try:
            os.chmod(self.lock_path, 0o666)
        except OSError:
            pass
        self.probe_path = self.root / "probe.json"

    @staticmethod
    def _resolve_root(root: Path | str | None) -> tuple[Path, str, str]:
        explicit = root or os.environ.get("ARGUS_RESOURCE_LEDGER_DIR")
        if explicit:
            path = Path(explicit).expanduser().resolve()
            if path.exists():
                if not path.is_dir():
                    raise NotADirectoryError(path)
            else:
                _prepare_directory(path, 0o1777)
            return path, "host", "configured ledger root"
        try:
            _prepare_directory(DEFAULT_ROOT, 0o1777)
            if not _is_shared_sticky(DEFAULT_ROOT):
                raise PermissionError(f"{DEFAULT_ROOT} is not shared sticky")
            return DEFAULT_ROOT, "host", "shared host ledger"
        except OSError as exc:
            fallback = (global_root() / "resource-ledger").resolve()
            _prepare_directory(fallback, 0o700)
            return fallback, "user", f"host ledger unavailable: {type(exc).__name__}: {exc}"

    @staticmethod
    def _default_identity_alive(owner: Mapping[str, Any]) -> bool:
        try:
            pid = int(owner.get("pid") or 0)
        except (TypeError, ValueError):
            return False
        identity = owner.get("process_identity")
        if not isinstance(identity, Mapping):
            identity = {"pid": pid, "start_time_ticks": owner.get("start_ticks")}
        return process_identity_is_running(pid, identity)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self.lock_path.open("a+b") as handle:
            with exclusive_file_lock(
                handle,
                lock_name=f"resource ledger {self.lock_path}",
            ):
                yield

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _record_path(directory: Path, record_id: object) -> Path:
        token = str(record_id)
        if (
            not token
            or len(token) > 128
            or any(not (character.isalnum() or character in "-_") for character in token)
        ):
            raise ValueError("invalid ledger record id")
        return directory / f"{token}.json"

    def _write(self, path: Path, value: Mapping[str, Any]) -> None:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o666)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            try:
                os.chmod(path, 0o666)
            except OSError:
                pass
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _records(directory: Path) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        try:
            paths = sorted(directory.glob("*.json"))
        except OSError:
            return records
        for path in paths:
            record = ResourceLedger._read(path)
            if record is not None:
                records.append(record)
        return records

    def _capture_probe(self) -> dict[str, Any]:
        snapshot = (
            self._probe.snapshot()
            if hasattr(self._probe, "snapshot")
            else self._probe()
        )
        snapshot = dict(snapshot)
        snapshot["host_epoch"] = self.host_epoch
        snapshot["ledger_scope"] = self.scope
        snapshot["scope_detail"] = self.scope_detail
        self._write(self.probe_path, snapshot)
        return snapshot

    def _expire_locked(self, now: float) -> list[str]:
        expired: list[str] = []
        for directory in (self.grants_dir, self.queue_dir):
            for path in list(directory.glob("*.json")):
                record = self._read(path)
                if record is None:
                    continue
                owner = record.get("owner")
                alive = isinstance(owner, Mapping) and self._identity_alive(owner)
                ttl_lapsed = float(record.get("expires_at") or 0) <= now
                wrong_host = str(record.get("host_epoch") or "") != self.host_epoch
                if ttl_lapsed or not alive or wrong_host:
                    path.unlink(missing_ok=True)
                    expired.append(str(record.get("id") or path.stem))
        return expired

    @staticmethod
    def _kind_unsatisfiable(snapshot: Mapping[str, Any], kind: str) -> bool:
        """True when the demanded hardware does not exist on this host.

        Hardware that is absent will not appear by waiting, so queueing such a
        demand would be a dishonest wait. Contention (present but claimed) and
        advisory mode (telemetry inaccessible/degraded) are not absence.
        """
        if kind == "none" or snapshot.get("enforcement") == "advisory":
            return False
        accelerators = snapshot.get("accelerators")
        if not isinstance(accelerators, list):
            return True
        relevant = [
            item for item in accelerators
            if isinstance(item, dict) and (kind == "any" or item.get("kind") == kind)
        ]
        if not relevant:
            return True
        return all(item.get("status") == "absent" for item in relevant)

    @staticmethod
    def _accelerator(snapshot: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
        accelerators = snapshot.get("accelerators")
        if not isinstance(accelerators, list):
            return []
        matches = [
            item for item in accelerators
            if isinstance(item, dict)
            and item.get("status") == "available"
            and (kind == "any" or item.get("kind") == kind)
        ]
        return sorted(matches, key=lambda item: str(item.get("kind") or ""))

    def _select_devices(
        self,
        snapshot: Mapping[str, Any],
        demand: Mapping[str, Any],
        grants: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, str]] | None:
        if demand["accelerator"] == "none":
            return [], {}
        claimed = {
            str(identity)
            for grant in grants
            for identity in grant.get("grant", {}).get("device_identities", [])
        }
        count = int(demand["device_count"])
        estimate = int(demand["mem_mib_estimate"])
        for accelerator in self._accelerator(snapshot, str(demand["accelerator"])):
            available: list[dict[str, Any]] = []
            for device in accelerator.get("devices", []):
                if not isinstance(device, dict):
                    continue
                if str(device.get("identity")) in claimed:
                    continue
                free_mib = int(device.get("total_memory_mib") or 0) - int(
                    device.get("used_memory_mib") or 0
                )
                if free_mib >= estimate:
                    available.append(device)
            if len(available) >= count:
                selected = available[:count]
                visibility_env = str(accelerator.get("visibility_env") or "")
                env = {
                    visibility_env: ",".join(str(item.get("visibility")) for item in selected)
                } if visibility_env else {}
                return selected, env
        return None

    def _grant_record(
        self,
        request: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        selected: tuple[list[dict[str, Any]], dict[str, str]] | None,
        now: float,
    ) -> dict[str, Any]:
        advisory = snapshot.get("enforcement") == "advisory"
        devices, env = selected or ([], {})
        record = dict(request)
        record["state"] = "granted"
        record["renewed_at"] = now
        record["expires_at"] = now + float(record["ttl_seconds"])
        record["enforcement"] = "advisory" if advisory else "strict"
        record["grant"] = {
            "device_identities": [str(item.get("identity")) for item in devices],
            "devices": devices,
            "env": env,
        }
        record.setdefault("yield_requests", [])
        if advisory:
            record["warning"] = (
                "accelerator telemetry is inaccessible or degraded; this grant is "
                "bookkeeping only and claims no free capacity"
            )
        return record

    def _promote_locked(self, snapshot: Mapping[str, Any], now: float) -> None:
        grants = self._records(self.grants_dir)
        queued = sorted(
            self._records(self.queue_dir),
            key=lambda item: (
                int(item.get("created_order") or 0),
                float(item.get("created_at") or 0),
                str(item.get("id") or ""),
            ),
        )
        for request in queued:
            selected = self._select_devices(snapshot, request["demand"], grants)
            if selected is None and snapshot.get("enforcement") != "advisory":
                break
            granted = self._grant_record(request, snapshot, selected, now)
            self._write(self._record_path(self.grants_dir, granted["id"]), granted)
            self._record_path(self.queue_dir, granted["id"]).unlink(missing_ok=True)
            grants.append(granted)

    def _facts(self, state: str, record: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(record)
        result["state"] = state
        result["probe"] = snapshot
        result["scope"] = self.scope
        result["ledger_root"] = str(self.root)
        holders = sorted(
            self._records(self.grants_dir),
            key=lambda item: float(item.get("created_at") or 0),
        )
        result["holders"] = [
            {
                "grant_id": item.get("id"),
                "owner": item.get("owner"),
                "intent": item.get("demand", {}).get("intent", ""),
                "demand": item.get("demand"),
                "expires_at": item.get("expires_at"),
            }
            for item in holders
        ]
        if state == "queued":
            queue = sorted(
                self._records(self.queue_dir),
                key=lambda item: (
                    int(item.get("created_order") or 0),
                    float(item.get("created_at") or 0),
                    str(item.get("id") or ""),
                ),
            )
            ids = [str(item.get("id")) for item in queue]
            result["position"] = ids.index(str(record.get("id"))) + 1
            result["poll_after_seconds"] = min(15.0, max(1.0, float(record["ttl_seconds"]) / 3.0))
        return result

    def acquire(
        self,
        demand: Mapping[str, Any],
        *,
        owner: Mapping[str, Any] | None = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_demand(demand)
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        actual_owner = dict(owner or owner_identity(project_root=Path.cwd(), task_id="cli"))
        now = self._clock()
        with self._locked():
            self._expire_locked(now)
            snapshot = self._capture_probe()
            self._promote_locked(snapshot, now)
            if request_id:
                granted = self._read(self._record_path(self.grants_dir, request_id))
                if granted is not None:
                    if (
                        granted.get("demand_hash") != demand_hash(normalized)
                        or granted.get("owner") != actual_owner
                    ):
                        raise ValueError("grant identity or demand does not match")
                    return self._facts("granted", granted, snapshot)
                queued = self._read(self._record_path(self.queue_dir, request_id))
                if queued is not None:
                    if (
                        queued.get("demand_hash") != demand_hash(normalized)
                        or queued.get("owner") != actual_owner
                    ):
                        raise ValueError("queued request identity or demand does not match")
                    if self._kind_unsatisfiable(snapshot, str(normalized["accelerator"])):
                        self._record_path(self.queue_dir, request_id).unlink(missing_ok=True)
                        queued["unsatisfiable_reason"] = (
                            f"host has no {normalized['accelerator']} hardware; "
                            "waiting cannot satisfy this demand"
                        )
                        return self._facts("unsatisfiable", queued, snapshot)
                    queued["renewed_at"] = now
                    queued["expires_at"] = now + ttl_seconds
                    queued["ttl_seconds"] = ttl_seconds
                    self._write(self._record_path(self.queue_dir, request_id), queued)
                    self._promote_locked(snapshot, now)
                    granted = self._read(self._record_path(self.grants_dir, request_id))
                    if granted is not None:
                        return self._facts("granted", granted, snapshot)
                    return self._facts("queued", queued, snapshot)
            identifier = request_id or uuid.uuid4().hex
            request = {
                "id": identifier,
                "state": "queued",
                "owner": actual_owner,
                "demand": normalized,
                "demand_hash": demand_hash(normalized),
                "host_epoch": self.host_epoch,
                "created_at": now,
                "created_order": time.time_ns(),
                "renewed_at": now,
                "expires_at": now + ttl_seconds,
                "ttl_seconds": ttl_seconds,
            }
            if self._kind_unsatisfiable(snapshot, str(normalized["accelerator"])):
                request["unsatisfiable_reason"] = (
                    f"host has no {normalized['accelerator']} hardware; "
                    "waiting cannot satisfy this demand"
                )
                return self._facts("unsatisfiable", request, snapshot)
            earlier_queue = bool(self._records(self.queue_dir))
            grants = self._records(self.grants_dir)
            selected = self._select_devices(snapshot, normalized, grants)
            if (
                snapshot.get("enforcement") == "advisory"
                or (selected is not None and not earlier_queue)
            ):
                granted = self._grant_record(request, snapshot, selected, now)
                self._write(self._record_path(self.grants_dir, identifier), granted)
                return self._facts("granted", granted, snapshot)
            self._write(self._record_path(self.queue_dir, identifier), request)
            return self._facts("queued", request, snapshot)

    def admit(
        self,
        grant_id: str,
        *,
        demand: Mapping[str, Any],
        owner: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Return and renew only an active grant matching this launch identity."""
        now = self._clock()
        with self._locked():
            self._expire_locked(now)
            record = self._read(self._record_path(self.grants_dir, grant_id))
            if record is None:
                return None
            matches = (
                record.get("demand_hash") == demand_hash(demand)
                and record.get("host_epoch") == self.host_epoch
                and record.get("owner") == dict(owner)
                and record.get("state") == "granted"
            )
            if not matches:
                return None
            record["renewed_at"] = now
            record["expires_at"] = now + float(record["ttl_seconds"])
            self._write(self._record_path(self.grants_dir, grant_id), record)
            return record

    def renew(self, grant_id: str, *, ttl_seconds: float | None = None) -> dict[str, Any] | None:
        now = self._clock()
        with self._locked():
            self._expire_locked(now)
            path = self._record_path(self.grants_dir, grant_id)
            record = self._read(path)
            if record is None:
                return None
            ttl = float(ttl_seconds or record.get("ttl_seconds") or DEFAULT_TTL_SECONDS)
            if ttl <= 0:
                raise ValueError("ttl_seconds must be positive")
            record["ttl_seconds"] = ttl
            record["renewed_at"] = now
            record["expires_at"] = now + ttl
            self._write(path, record)
            return record

    def release(self, record_id: str) -> bool:
        now = self._clock()
        with self._locked():
            removed = False
            for directory in (self.grants_dir, self.queue_dir):
                path = self._record_path(directory, record_id)
                if path.exists():
                    path.unlink(missing_ok=True)
                    removed = True
            self._expire_locked(now)
            snapshot = self._capture_probe()
            self._promote_locked(snapshot, now)
            return removed

    def expire(self) -> list[str]:
        now = self._clock()
        with self._locked():
            expired = self._expire_locked(now)
            snapshot = self._capture_probe()
            self._promote_locked(snapshot, now)
            return expired

    def yield_request(
        self,
        target_grant_id: str,
        reason_prose: str,
        *,
        requester: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        reason = " ".join(str(reason_prose).split())
        if not reason:
            raise ValueError("yield request reason must not be empty")
        with self._locked():
            path = self._record_path(self.grants_dir, target_grant_id)
            record = self._read(path)
            if record is None:
                raise KeyError(target_grant_id)
            request = {
                "id": uuid.uuid4().hex,
                "requested_at": self._clock(),
                "reason": reason,
                "requester": dict(requester or owner_identity(project_root=Path.cwd(), task_id="cli")),
                "response": None,
            }
            requests = list(record.get("yield_requests") or [])
            requests.append(request)
            record["yield_requests"] = requests
            self._write(path, record)
            return request

    def respond_yield(
        self,
        grant_id: str,
        request_id: str,
        response_prose: str,
        *,
        decision: str = "decline",
    ) -> dict[str, Any]:
        response = " ".join(str(response_prose).split())
        if decision not in {"decline", "yield"} or not response:
            raise ValueError("yield response needs decision=decline|yield and a reason")
        with self._locked():
            path = self._record_path(self.grants_dir, grant_id)
            record = self._read(path)
            if record is None:
                raise KeyError(grant_id)
            requests = list(record.get("yield_requests") or [])
            for request in requests:
                if str(request.get("id")) == request_id:
                    request["response"] = {
                        "decision": decision,
                        "reason": response,
                        "responded_at": self._clock(),
                    }
                    self._write(path, record)
                    return request
            raise KeyError(request_id)

    def status(self, *, refresh_probe: bool = True) -> dict[str, Any]:
        now = self._clock()
        with self._locked():
            self._expire_locked(now)
            snapshot = self._capture_probe() if refresh_probe else self._read(self.probe_path)
            return {
                "ledger_root": str(self.root),
                "scope": self.scope,
                "scope_detail": self.scope_detail,
                "host_epoch": self.host_epoch,
                "probe": snapshot or {},
                "grants": sorted(
                    self._records(self.grants_dir),
                    key=lambda item: float(item.get("created_at") or 0),
                ),
                "queue": sorted(
                    self._records(self.queue_dir),
                    key=lambda item: (
                        int(item.get("created_order") or 0),
                        float(item.get("created_at") or 0),
                    ),
                ),
            }


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "ResourceLedger",
    "demand_hash",
    "normalize_demand",
    "owner_identity",
]
