"""Shared, concurrently-claimable task list for an agent team.

All mutating ops take an exclusive flock on ``.tasks.lock`` and persist
each task as ``tasks/<task_id>.json`` via atomic write. Claiming is a
compare-and-set (state must be ``pending`` and every dep ``done``) so two
teammates can never own the same task.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _store


def _tasks_dir(root: Path) -> Path:
    return Path(root) / "tasks"


def _lock(root: Path) -> Path:
    return Path(root) / ".tasks.lock"


def _task_filename(task_id: str) -> str:
    if not isinstance(task_id, str):
        raise TypeError("task_id must be a string")
    invalid = not task_id or task_id in {".", ".."} or any(c in task_id for c in "/\\\0")
    if invalid:
        raise ValueError(f"invalid task_id for task board path: {task_id!r}")
    return f"{task_id}.json"


def _path(root: Path, task_id: str) -> Path:
    return _tasks_dir(root) / _task_filename(task_id)


def _load_all(root: Path) -> list[dict[str, Any]]:
    d = _tasks_dir(root)
    if not d.exists():
        return []
    # Atomic writes use hidden ``.tmp-*.json`` siblings. If a process dies
    # between temp creation and replace, the leftover file must not become a
    # second claimable copy of the same logical task.
    out = [
        _store.read_json(p, default=None)
        for p in sorted(d.glob("*.json"))
        if not p.name.startswith(".")
    ]
    return [t for t in out if isinstance(t, dict)]


# Liveness/ownership fields that belong to a teammate ACTIVELY working a task.
# A re-form of an already-running campaign (operator re-runs ``team form`` while
# the Curator has teammates in flight) must NOT reset these to the pending
# defaults: doing so silently de-owns the task, drops ``count_in_flight`` to 0,
# and lets the pool double-spawn a second teammate into the SAME workdir on the
# next reap. The static spec fields are always refreshed from the new spec.
_LIVE_OWNERSHIP_FIELDS = ("state", "owner", "claim_ts", "heartbeat_ts", "attempts")


def form(root: Path, tasks: list[dict[str, Any]]) -> None:
    """Create (or refresh) the task records for a team from partial specs.

    Idempotent over an ACTIVE campaign: when a task record already exists and a
    teammate is mid-flight on it (``state`` is ``claimed``/``running``), its
    ownership/liveness fields are PRESERVED and only the static spec fields
    (title/objective/target/priority/...) are refreshed. Re-running ``form`` on a
    live fleet therefore never de-owns a task a Curator teammate is working —
    which would otherwise defeat the pool's double-spawn guard. Takes the board
    lock so the read-merge can't race a concurrent claim/heartbeat/reassign.
    """
    with _store.locked(_lock(root)):
        for spec in tasks:
            tid = spec["task_id"]
            task = {
                "task_id": tid,
                "title": spec.get("title", ""),
                "objective": spec.get("objective", ""),
                # The target this task contributes to. Several tasks (breadth + depth
                # re-forms) can share one target, so the leaderboard aggregates by
                # target, not task_id. Defaults to task_id.
                "target": spec.get("target") or tid,
                # Optimization direction for this target's metric, for the leaderboard:
                # True = lower is better (latency / error-count / loss), False = higher
                # (a speedup / score). None (default) → the leaderboard's global default.
                "lower_is_better": spec.get("lower_is_better"),
                "owns_paths": list(spec.get("owns_paths", [])),
                # Per-task working directory. When set, the Curator spawns this task's
                # teammate in its OWN dir, so N tasks can be independent project
                # workdirs (e.g. one per kernel) instead of all sharing the campaign
                # cwd. Empty → fall back to the campaign cwd (legacy behaviour).
                "cwd": str(spec.get("cwd", "") or ""),
                "deps": list(spec.get("deps", [])),
                "state": "pending",
                "owner": "",
                "result_shard": spec.get("result_shard", ""),
                "reason": "",
                "claim_ts": 0.0,
                "heartbeat_ts": 0.0,
                "attempts": 0,
                "priority": int(spec.get("priority", 100)),
            }
            prior = _store.read_json(_path(root, tid), default=None)
            if isinstance(prior, dict) and prior.get("state") in ("claimed", "running"):
                # A teammate is mid-flight on this task — keep its ownership and
                # only refresh the static spec fields rebuilt above.
                for field in _LIVE_OWNERSHIP_FIELDS:
                    if field in prior:
                        task[field] = prior[field]
            _store.atomic_write_json(_path(root, tid), task)


def _done_ids(tasks: list[dict[str, Any]]) -> set[str]:
    return {t["task_id"] for t in tasks if t["state"] == "done"}


def claim(root: Path, member_id: str, *, now: float) -> dict[str, Any] | None:
    """Atomically claim the first pending task whose deps are all done."""
    with _store.locked(_lock(root)):
        tasks = _load_all(root)
        done = _done_ids(tasks)
        for task in sorted(tasks, key=lambda t: t["task_id"]):
            if task["state"] != "pending":
                continue
            if not all(dep in done for dep in task["deps"]):
                continue
            task["state"] = "claimed"
            task["owner"] = member_id
            task["claim_ts"] = now
            task["heartbeat_ts"] = now
            _store.atomic_write_json(_path(root, task["task_id"]), task)
            return task
    return None


def claim_specific(root: Path, task_id: str, member_id: str, *, now: float) -> dict[str, Any] | None:
    """Atomically claim a SPECIFIC task for ``member_id`` (only if pending + deps done).

    Spawn uses this so parallel teammates each claim their OWN assigned task and
    never cross member IDs the way the next-pending ``claim()`` can under a race.
    """
    with _store.locked(_lock(root)):
        task = _store.read_json(_path(root, task_id), default=None)
        if not isinstance(task, dict) or task["state"] != "pending":
            return None
        if not all(dep in _done_ids(_load_all(root)) for dep in task["deps"]):
            return None
        task["state"] = "claimed"
        task["owner"] = member_id
        task["claim_ts"] = now
        task["heartbeat_ts"] = now
        _store.atomic_write_json(_path(root, task_id), task)
        return task


def claim_top(root: Path, member_id: str, *, now: float) -> dict[str, Any] | None:
    """Atomically claim the highest-priority pending task whose deps are all done.

    Like ``claim()`` but orders eligible tasks by ``priority`` (lower number =
    higher priority), tie-broken by ``task_id``. This is what the coordinator
    uses to pull the top of the lead's backlog.
    """
    with _store.locked(_lock(root)):
        tasks = _load_all(root)
        done = _done_ids(tasks)
        eligible = [
            t for t in tasks
            if t["state"] == "pending" and all(dep in done for dep in t["deps"])
        ]
        if not eligible:
            return None
        eligible.sort(key=lambda t: (int(t.get("priority", 100)), t["task_id"]))
        task = eligible[0]
        task["state"] = "claimed"
        task["owner"] = member_id
        task["claim_ts"] = now
        task["heartbeat_ts"] = now
        _store.atomic_write_json(_path(root, task["task_id"]), task)
        return task


def count_in_flight(root: Path) -> int:
    """Number of tasks currently claimed or running (occupying a pool slot)."""
    return sum(1 for t in _load_all(root) if t["state"] in ("claimed", "running"))


def heartbeat(root: Path, task_id: str, *, now: float) -> None:
    """Refresh liveness; first heartbeat promotes ``claimed`` -> ``running``."""
    with _store.locked(_lock(root)):
        task = _store.read_json(_path(root, task_id), default=None)
        if not isinstance(task, dict):
            return
        task["heartbeat_ts"] = now
        if task["state"] == "claimed":
            task["state"] = "running"
        _store.atomic_write_json(_path(root, task_id), task)


def _mutate(root: Path, task_id: str, **changes: Any) -> None:
    with _store.locked(_lock(root)):
        task = _store.read_json(_path(root, task_id), default=None)
        if not isinstance(task, dict):
            return
        task.update(changes)
        _store.atomic_write_json(_path(root, task_id), task)


def complete(root: Path, task_id: str, *, shard: str = "") -> None:
    _mutate(root, task_id, state="done", result_shard=shard)


def fail(root: Path, task_id: str, *, reason: str = "") -> None:
    _mutate(root, task_id, state="failed", reason=reason)


def reassign_stale(
    root: Path,
    *,
    ttl: float,
    now: float,
    live_owners: set[str] | None = None,
) -> list[str]:
    """Return stale claimed/running tasks to pending.

    ``live_owners`` lets the rolling coordinator distinguish a stale heartbeat
    from a still-running teammate process whose heartbeat thread is delayed. A
    task with a live owner must not be reset to pending, or the same logical task
    can be claimed again while the old teammate is still running.
    """
    live_owners = live_owners or set()
    reassigned: list[str] = []
    with _store.locked(_lock(root)):
        for task in _load_all(root):
            if task["state"] in ("claimed", "running") and now - task["heartbeat_ts"] > ttl:
                if task.get("owner") in live_owners:
                    continue
                task["state"] = "pending"
                task["owner"] = ""
                task["attempts"] = int(task.get("attempts", 0)) + 1
                _store.atomic_write_json(_path(root, task["task_id"]), task)
                reassigned.append(task["task_id"])
    return reassigned


def all_done(root: Path) -> bool:
    tasks = _load_all(root)
    return bool(tasks) and all(t["state"] == "done" for t in tasks)


def snapshot(root: Path) -> list[dict[str, Any]]:
    return _load_all(root)
