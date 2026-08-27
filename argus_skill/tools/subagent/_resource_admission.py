"""Resource-ledger mechanics at the subagent command-launch boundary."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..resource_ledger.ledger import ResourceLedger, owner_identity

# Renewed leases bound crash cleanup; they never bound a live job's duration.
_DIRECT_TTL_SECONDS = 120.0
_SUPERVISED_TTL_SECONDS = 1800.0


@dataclass
class ResourceLease:
    ledger: ResourceLedger
    grant_id: str
    demand: dict[str, Any]
    owner: dict[str, Any]
    record: dict[str, Any]

    @property
    def env(self) -> dict[str, str]:
        return {
            str(key): str(value)
            for key, value in self.record.get("grant", {}).get("env", {}).items()
        }

    @property
    def ttl_seconds(self) -> float:
        return float(self.record.get("ttl_seconds") or _DIRECT_TTL_SECONDS)

    def renew(self) -> bool:
        renewed = self.ledger.renew(self.grant_id, ttl_seconds=self.ttl_seconds)
        if renewed is None:
            return False
        self.record = renewed
        return True

    def release(self) -> None:
        self.ledger.release(self.grant_id)


def task_has_demand(task: Mapping[str, Any]) -> bool:
    return isinstance(task.get("resource_demand"), Mapping)


class UnsatisfiableResourceDemand(RuntimeError):
    """The demanded hardware does not exist on this host; waiting cannot help."""


def acquire_for_task(
    task_id: str,
    *,
    mode: str,
    project_root: Path | str | None = None,
) -> ResourceLease | None:
    """Wait for and validate this worker's declared grant, or no-op if undeclared."""
    from ._registry import _process_identity, _read_task, _write_task  # noqa: PLC0415

    task = _read_task(task_id) or {}
    if not task_has_demand(task):
        return None
    demand = dict(task["resource_demand"])
    root = Path(project_root or Path.cwd()).resolve()
    owner = owner_identity(project_root=root, task_id=task_id, pid=os.getpid())
    ttl = _SUPERVISED_TTL_SECONDS if mode == "supervised" else _DIRECT_TTL_SECONDS
    ledger = ResourceLedger()
    request_id: str | None = None
    while True:
        result = ledger.acquire(
            demand,
            owner=owner,
            ttl_seconds=ttl,
            request_id=request_id,
        )
        request_id = str(result["id"])
        if result["state"] == "unsatisfiable":
            raise UnsatisfiableResourceDemand(
                str(result.get("unsatisfiable_reason") or "demanded hardware absent")
            )
        if result["state"] == "granted":
            admitted = ledger.admit(request_id, demand=demand, owner=owner)
            if admitted is None:
                continue
            task = _read_task(task_id) or task
            task.update({
                "state": "starting",
                "resource_grant_id": request_id,
                "resource_grant": admitted.get("grant", {}),
                "resource_enforcement": admitted.get("enforcement"),
                "resource_warning": admitted.get("warning", ""),
                "resource_ledger_root": str(ledger.root),
                "resource_owner": owner,
                "worker_pid": os.getpid(),
                "worker_process_identity": _process_identity(os.getpid()),
            })
            task.setdefault("pid", os.getpid())
            task.pop("resource_wait", None)
            task.pop("resource_queue_id", None)
            _write_task(task_id, task)
            return ResourceLease(ledger, request_id, demand, owner, admitted)
        poll_after = float(result.get("poll_after_seconds") or 5.0)
        holders = [
            {
                "grant_id": item.get("grant_id"),
                "task_id": item.get("owner", {}).get("task_id"),
                "intent": item.get("intent", ""),
                "demand": item.get("demand", {}),
                "expires_at": item.get("expires_at"),
            }
            for item in result.get("holders", [])
        ]
        task = _read_task(task_id) or task
        task.update({
            "state": "waiting_resource",
            "resource_queue_id": request_id,
            "resource_ledger_root": str(ledger.root),
            "resource_owner": owner,
            "resource_wait": {
                "position": result.get("position"),
                "holders": holders,
                "probe": result.get("probe", {}),
                "poll_after_seconds": poll_after,
            },
            "current_monitor_interval": poll_after,
            "next_check_at": time.time() + poll_after,
            "heartbeat_at": time.time(),
            "worker_pid": os.getpid(),
            "worker_process_identity": _process_identity(os.getpid()),
        })
        task.setdefault("pid", os.getpid())
        _write_task(task_id, task)
        time.sleep(poll_after)


def command_env(lease: ResourceLease | None) -> dict[str, str] | None:
    if lease is None:
        return None
    from ._registry import _child_env  # noqa: PLC0415

    env = _child_env()
    env.update(lease.env)
    return env


def record_renewal_failure(task_id: str, lease: ResourceLease) -> None:
    from ._registry import _read_task, _write_task  # noqa: PLC0415

    task = _read_task(task_id) or {}
    if str(task.get("resource_grant_id") or "") == lease.grant_id:
        task["resource_warning"] = "resource grant renewal failed; ledger facts may be stale"
        _write_task(task_id, task)


def yield_facts_for_task(task_id: str) -> list[dict[str, Any]]:
    from ._registry import _read_task  # noqa: PLC0415

    task = _read_task(task_id) or {}
    root = str(task.get("resource_ledger_root") or "").strip()
    grant_id = str(task.get("resource_grant_id") or "").strip()
    if not root or not grant_id or not Path(root).is_dir():
        return []
    try:
        status = ResourceLedger(root=root).status(refresh_probe=False)
    except (OSError, ValueError):
        return []
    grant = next(
        (item for item in status["grants"] if item.get("id") == grant_id),
        None,
    )
    return [
        dict(request)
        for request in (grant or {}).get("yield_requests", [])
        if isinstance(request, Mapping) and not request.get("response")
    ]


__all__ = [
    "ResourceLease",
    "acquire_for_task",
    "command_env",
    "record_renewal_failure",
    "task_has_demand",
    "yield_facts_for_task",
]
