"""Resource-ledger mechanics at the subagent command-launch boundary."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..resource_ledger.ledger import ResourceLedger, owner_identity

log = logging.getLogger(__name__)

# Renewed leases bound crash cleanup; they never bound a live job's duration.
_DIRECT_TTL_SECONDS = 120.0
_SUPERVISED_TTL_SECONDS = 1800.0
# A queue wait longer than this, or four times the job's own estimate, is told
# to the operator once. Only the job's --timeout ends the wait.
_LONG_QUEUE_WAIT_SECONDS = 3600.0


@dataclass
class ResourceLease:
    task_id: str
    incident_root: str
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
        if self.incident_root:
            try:
                from ...core.runtime_incidents import RuntimeIncidentStore

                RuntimeIncidentStore(self.incident_root).recover_if_present(
                    detector="resource_lease",
                    invariant="live_worker_renews_resource_grant",
                    subject_kind="subagent",
                    subject_id=self.task_id,
                    action="renew_resource_grant",
                    expected_postcondition="resource grant renewal succeeds",
                    evidence={
                        "grant_id": self.grant_id,
                        "renewed": True,
                        "expires_at": renewed.get("expires_at"),
                    },
                )
            except Exception:  # noqa: BLE001 - lease renewal remains authoritative
                log.exception(
                    "subagent %s: failed to close resource renewal incident",
                    self.task_id,
                )
        return True

    def release(self) -> None:
        self.ledger.release(self.grant_id)


def task_has_demand(task: Mapping[str, Any]) -> bool:
    return isinstance(task.get("resource_demand"), Mapping)


class UnsatisfiableResourceDemand(RuntimeError):
    """The demanded hardware does not exist on this host; waiting cannot help."""


class ResourceQueueTimeout(RuntimeError):
    """The job's --timeout ran out while it waited for hardware."""


def _tell_long_queue_wait(
    task_id: str, task: Mapping[str, Any], holders: list[dict[str, Any]], waited: float,
) -> None:
    """Tell the operator once that a job has waited long, naming overrun holders."""
    life_dir = str(task.get("owner_session_root") or "").strip()
    if not life_dir:
        return
    from ...core.operator_messages import publish_operator_message, uses_cjk

    now = time.time()
    overrun = []
    for holder in holders:
        expected = float((holder.get("demand") or {}).get("expected_duration_seconds") or 0.0)
        held = now - float(holder.get("granted_at") or now)
        if expected > 0 and held > 2 * expected:
            overrun.append((
                str(holder.get("task_id") or holder.get("grant_id") or "?"),
                held / 3600, expected / 3600,
            ))
    hours = waited / 3600
    if uses_cjk(str(task.get("description") or "")):
        text = f"后台任务 {task_id} 已排队等待 GPU {hours:.1f} 小时，仍在队列中。" + (
            "超过预计时长两倍的占用者：" + "；".join(
                f"{name}（已占用 {held:.1f} 小时，预计 {expected:.1f} 小时）"
                for name, held, expected in overrun
            ) + "。"
            if overrun else "当前占用者都还在预计时长内。"
        )
    else:
        text = f"Background job {task_id} has waited {hours:.1f} h for a GPU and stays queued. " + (
            "Holders past twice their estimate: " + "; ".join(
                f"{name} (held {held:.1f} h, estimate {expected:.1f} h)"
                for name, held, expected in overrun
            ) + "."
            if overrun else "The current holders are within their estimates."
        )
    try:
        publish_operator_message(
            life_dir, text=text, message_id=f"gpu-queue-wait-{task_id}",
            event_fields={"user_action_required": False},
        )
    except Exception:  # noqa: BLE001 - the notice must never end the wait
        log.warning("subagent %s: could not report its long queue wait", task_id, exc_info=True)


def acquire_for_task(
    task_id: str,
    *,
    mode: str,
    project_root: Path | str | None = None,
    timeout: float | None = None,
) -> ResourceLease | None:
    """Wait for and validate this worker's declared grant, or no-op if undeclared.

    The job's ``timeout`` also bounds the wait; a wait past the long-wait mark
    is told to the operator once.
    """
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
    started = time.monotonic()
    long_wait = max(
        _LONG_QUEUE_WAIT_SECONDS, 4.0 * float(demand.get("expected_duration_seconds") or 0.0),
    )
    told = False
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
            return ResourceLease(
                task_id=task_id,
                incident_root=str(task.get("owner_session_root") or ""),
                ledger=ledger,
                grant_id=request_id,
                demand=demand,
                owner=owner,
                record=admitted,
            )
        poll_after = float(result.get("poll_after_seconds") or 5.0)
        holders = [
            {
                "grant_id": item.get("grant_id"),
                "task_id": item.get("owner", {}).get("task_id"),
                "intent": item.get("intent", ""),
                "demand": item.get("demand", {}),
                "expires_at": item.get("expires_at"),
                "granted_at": item.get("granted_at"),
            }
            for item in result.get("holders", [])
        ]
        waited = time.monotonic() - started
        if timeout is not None and waited >= timeout:
            ledger.release(request_id)
            raise ResourceQueueTimeout(
                f"Waited {waited:.0f} seconds for hardware without a grant; "
                "this was the configured --timeout limit."
            )
        if not told and waited >= long_wait:
            told = True
            _tell_long_queue_wait(task_id, task, holders, waited)
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
        time.sleep(poll_after if timeout is None else min(poll_after, max(0.0, timeout - waited)))


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
        life_dir = str(task.get("owner_session_root") or lease.incident_root or "").strip()
        if life_dir:
            try:
                from ...core.runtime_incidents import RuntimeIncidentStore

                RuntimeIncidentStore(life_dir).record_unresolved(
                    detector="resource_lease",
                    invariant="live_worker_renews_resource_grant",
                    subject_kind="subagent",
                    subject_id=task_id,
                    severity="error",
                    observed={
                        "grant_id": lease.grant_id,
                        "worker_pid": task.get("worker_pid"),
                        "state": task.get("state"),
                        "resource_warning": task["resource_warning"],
                    },
                    reason="resource grant renewal failed while the worker remained active",
                    escalation_after=3,
                )
            except Exception:  # noqa: BLE001 - registry warning remains authoritative
                log.exception(
                    "subagent %s: failed to record resource renewal incident",
                    task_id,
                )


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
    "ResourceQueueTimeout",
    "acquire_for_task",
    "command_env",
    "record_renewal_failure",
    "task_has_demand",
    "yield_facts_for_task",
]
