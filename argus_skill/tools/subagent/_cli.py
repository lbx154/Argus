"""CLI command handlers + entrypoint for the subagent tool."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import _cpu_admission
from ._direct_run import _run_direct
from ._discuss_run import DISCUSSION_POLL_INTERVAL
from ._discussion_log import (
    _append_discussion,
    _engineer_turn_count,
    _mirror_discussion_md,
)
from ._llm import resolve_supervisor_model
from ._registry import (
    REGISTRY_DIR,
    _append_experiment_history,
    _child_env,
    _effective_run_dir,
    _format_metric_line,
    _is_pid_alive,
    _lane_of,
    _list_tasks,
    _open_discussion_blockers,
    _process_identity,
    _progress_summary,
    _read_task,
    _recorded_process_alive,
    _run_dir_from_command,
    _task_log_dir,
    _unlink_task_records,
    _write_task,
    reconcile_terminal_task,
)
from ._supervised_run import _run_supervised
from ..resource_ledger.cli import parse_duration
from ..resource_ledger.ledger import normalize_demand

_ACTIVE_STATES = frozenset({
    "starting", "preflight", "waiting_resource", "running", "discussing",
})
def _detach_child_stdio() -> None:
    """Release caller-owned pipes before the background worker does any work."""
    while True:
        null_fd = os.open(os.devnull, os.O_RDWR)
        if null_fd > 2:
            break
    try:
        for fd in (0, 1, 2):
            os.dup2(null_fd, fd)
    finally:
        os.close(null_fd)


def _busy_owner_pid(task: dict) -> int:
    """Return a live owner PID while a task record is not safe to reuse."""
    live_pids: list[int] = []
    for key in ("worker_pid", "pid", "submitter_pid"):
        try:
            pid = int(task.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if pid > 0 and _recorded_process_alive(task, key):
            live_pids.append(pid)
    if not live_pids:
        return 0
    if str(task.get("state") or "") in _ACTIVE_STATES:
        return live_pids[0]
    # A terminal worker may still be persisting its report; identity, not age,
    # determines when the task id is safe to reuse.
    return live_pids[0]


def _worker_cpu_ids_arg(cpu_ids: tuple[int, ...]) -> str:
    return ",".join(str(cpu_id) for cpu_id in cpu_ids)


def _parse_worker_cpu_ids(value: str | None) -> tuple[int, ...]:
    if not value:
        return ()
    return tuple(int(part.strip()) for part in value.split(",") if part.strip())


def _declared_resource_demand(args: argparse.Namespace) -> dict | None:
    values = (
        getattr(args, "accelerator", None),
        getattr(args, "gpu_count", None),
        getattr(args, "gpu_mem_mib", None),
        getattr(args, "expected_duration", None),
        getattr(args, "checkpointable", None),
        getattr(args, "intent", None),
    )
    if all(value is None for value in values):
        return None
    accelerator = getattr(args, "accelerator", None) or "any"
    count = getattr(args, "gpu_count", None)
    if count is None:
        count = 0 if accelerator == "none" else 1
    return normalize_demand({
        "accelerator": accelerator,
        "device_count": count,
        "mem_mib_estimate": getattr(args, "gpu_mem_mib", None) or 0,
        "expected_duration_seconds": getattr(args, "expected_duration", None) or 0,
        "checkpointable": bool(getattr(args, "checkpointable", None)),
        "intent": getattr(args, "intent", None) or "",
    })


def _windows_worker_command(
    *,
    task_id: str,
    description: str,
    command: str,
    mode: str,
    timeout: int | None,
    monitor_interval: int,
    model: str | None,
    cwd: str,
    run_dir: str | None,
    preflight: bool,
    cpu_ids: tuple[int, ...],
) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "argus_skill.tools.subagent",
        "_worker",
        "--task-id",
        task_id,
        "--description",
        description,
        "--command",
        command,
        "--mode",
        mode,
        "--monitor-interval",
        str(int(monitor_interval)),
        "--cwd",
        cwd,
    ]
    if timeout is not None:
        argv.extend(["--timeout", str(int(timeout))])
    if model:
        argv.extend(["--model", model])
    if run_dir:
        argv.extend(["--run-dir", run_dir])
    if not preflight:
        argv.append("--no-preflight")
    if cpu_ids:
        argv.extend(["--cpu-ids", _worker_cpu_ids_arg(cpu_ids)])
    return argv


def _spawn_windows_worker(
    *,
    task_id: str,
    description: str,
    command: str,
    mode: str,
    timeout: int | None,
    monitor_interval: int,
    model: str | None,
    cwd: str,
    run_dir: str | None,
    preflight: bool,
    cpu_ids: tuple[int, ...],
    registry_cwd: str | None = None,
) -> subprocess.Popen[bytes]:
    worker_cwd = str(Path(registry_cwd or os.getcwd()).resolve())
    log_dir = Path(worker_cwd) / _task_log_dir(task_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    env = _child_env()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    with (log_dir / "worker.log").open("ab") as worker_log:
        return subprocess.Popen(
            _windows_worker_command(
                task_id=task_id,
                description=description,
                command=command,
                mode=mode,
                timeout=timeout,
                monitor_interval=monitor_interval,
                model=model,
                cwd=cwd,
                run_dir=run_dir,
                preflight=preflight,
                cpu_ids=cpu_ids,
            ),
            cwd=worker_cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=worker_log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=creationflags,
        )


def _write_worker_start_error(
    *,
    task_id: str,
    run_id: str,
    description: str,
    command: str,
    mode: str,
    run_dir: str | None,
    error: str,
) -> None:
    task = _read_task(task_id) or {}
    task.update({
        "state": "error",
        "task_id": task_id,
        "run_id": run_id,
        "description": description,
        "command": command,
        "mode": mode,
        "run_dir": run_dir,
        "error": error,
        "completed_at": time.time(),
        "worker_pid": os.getpid(),
    })
    _write_task(task_id, task)


def cmd_worker(args: argparse.Namespace) -> int:
    """Run one submitted task in a Windows worker subprocess."""
    task_id = args.task_id
    run_id = str((_read_task(task_id) or {}).get("run_id") or f"{task_id}-{time.time_ns()}")
    mode = args.mode
    run_dir = args.run_dir
    worker_task = _read_task(task_id) or {
        "state": "starting",
        "task_id": task_id,
        "run_id": run_id,
        "description": args.description,
        "command": args.command,
        "mode": mode,
        "run_dir": run_dir,
        "cwd": str(Path(args.cwd or os.getcwd()).resolve()),
    }
    worker_task["worker_pid"] = os.getpid()
    worker_task.setdefault("pid", os.getpid())
    worker_task["worker_process_identity"] = _process_identity(os.getpid())
    worker_task.setdefault("timeout_seconds", args.timeout)
    worker_task.setdefault("timeout_defaulted", False)
    _write_task(task_id, worker_task)
    try:
        _cpu_admission.apply_current_process_affinity(
            _parse_worker_cpu_ids(args.cpu_ids)
        )
    except (OSError, RuntimeError, ValueError) as exc:
        _write_worker_start_error(
            task_id=task_id,
            run_id=run_id,
            description=args.description,
            command=args.command,
            mode=mode,
            run_dir=run_dir,
            error=f"CPU affinity setup failed: {exc}",
        )
        return 1

    if mode == "supervised":
        _run_supervised(
            task_id=task_id,
            command=args.command,
            description=args.description,
            timeout=args.timeout,
            monitor_interval=args.monitor_interval or 120,
            model=args.model or resolve_supervisor_model(),
            cwd=args.cwd,
            run_dir=run_dir,
            preflight=not args.no_preflight,
        )
    else:
        _run_direct(
            task_id=task_id,
            command=args.command,
            description=args.description,
            timeout=args.timeout,
            cwd=args.cwd,
            run_dir=run_dir,
        )
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    """Submit a task. Returns immediately."""
    task_id = args.task_id
    existing = _read_task(task_id)
    busy_pid = _busy_owner_pid(existing) if existing else 0
    if existing and busy_pid:
        print(json.dumps({
            "error": (
                f"task '{task_id}' is already {existing.get('state')} "
                f"(pid {busy_pid})"
            ),
        }))
        return 1

    cwd = str(Path(args.cwd or os.getcwd()).expanduser().resolve())
    registry_cwd = str(Path.cwd().resolve())
    mode = args.mode
    run_id = f"{task_id}-{time.time_ns()}"
    try:
        resource_demand = _declared_resource_demand(args)
    except ValueError as exc:
        print(json.dumps({"error": f"invalid resource demand: {exc}"}))
        return 1

    # Resolve the run directory: prefer an explicit --run-dir, else recover it
    # from the command itself (commands already carry --run-dir). Store it as an
    # absolute path so status/report can read progress.jsonl/status.json/
    # summary.tsv regardless of the caller's cwd -- this is what makes the run
    # observable instead of a black box.
    run_dir = args.run_dir or _run_dir_from_command(args.command)
    if run_dir:
        rp = Path(run_dir).expanduser()
        run_dir = str((rp if rp.is_absolute() else Path(cwd) / rp).resolve())
    timeout_seconds = int(args.timeout) if args.timeout is not None else None
    timeout_defaulted = False

    # Forced-discussion gate: while a supervisor is parked on an OPEN discussion
    # (it stopped a run and is waiting on the engineer), block launching new runs
    # so a concern can never be bypassed by silently starting something else. The
    # `reply` command is never blocked. A stale/dead supervisor does not wedge
    # this (liveness = live pid + fresh heartbeat). Break-glass: --override-discussion.
    override = args.override_discussion
    blockers = _open_discussion_blockers(_lane_of(task_id))
    if blockers and not override:
        b = blockers[0]
        rd = b.get("run_dir")
        print(json.dumps({
            "error": "blocked: a supervisor stopped a run and is waiting for your reply",
            "blocking_task": b.get("task_id"),
            "supervisor_concern": b.get("concern") or b.get("last_supervisor_concern", ""),
            "discussion_file": (str(Path(rd) / "DISCUSSION.md") if rd else b.get("discussion_path")),
            "reply_with": shlex.join([
                sys.executable,
                "-m",
                "argus_skill.tools.subagent",
                "reply",
                "--task-id",
                str(b.get("task_id") or ""),
                "--message",
                "<your rationale>",
            ]),
            "hint": (
                "Read the discussion and reply first. Only if you have a deliberate "
                "reason to proceed anyway, re-run submit with "
                "--override-discussion \"<reason>\"."
            ),
        }))
        return 1
    if override and blockers:
        for b in blockers:
            _append_experiment_history(cwd, {
                "run_id": f"override-{b.get('task_id')}-{int(time.time())}",
                "task_id": b.get("task_id"), "event": "DISCUSSION-OVERRIDE",
                "override_reason": override, "ts": time.time(),
            })

    # STOP preflight: a leftover run_dir/STOP would make RunWriter abort this run
    # the instant it starts. Refuse rather than silently waste a launch; --clear-stop
    # removes it to reuse the directory deliberately.
    if run_dir:
        stop_path = Path(run_dir) / "STOP"
        if stop_path.exists():
            if args.clear_stop:
                try:
                    stop_path.unlink()
                except OSError:
                    pass
            else:
                print(json.dumps({
                    "error": "stale STOP file in run_dir would abort this run immediately",
                    "stop_file": str(stop_path),
                    "hint": "Use a fresh --run-dir, or pass --clear-stop to remove it and reuse this directory.",
                }))
                return 1

    # Admit and reserve CPUs before creating the first task/log/run artifact.
    # The starting record is the lease placeholder during the short fork window.
    try:
        with _cpu_admission.cpu_admission_lock(Path.cwd()):
            existing = _read_task(task_id)
            existing_pid = _busy_owner_pid(existing) if existing else 0
            if existing and existing_pid:
                print(json.dumps({
                    "error": (
                        f"task '{task_id}' is already {existing.get('state')} "
                        f"(pid {existing_pid})"
                    ),
                }))
                return 1
            selected_cpu_ids = _cpu_admission.select_cpu_ids(
                cpu_count=args.cpu_count,
                cpu_ids=args.cpu_ids,
                tasks=_list_tasks(),
                is_pid_alive=_is_pid_alive,
            )
            initial_task = {
                "state": "starting",
                "task_id": task_id,
                "run_id": run_id,
                "description": args.description,
                "command": args.command,
                "mode": mode,
                "run_dir": run_dir,
                "cwd": str(Path(cwd).resolve()),
                "submitted_at": time.time(),
                "submitter_pid": os.getpid(),
                "submitter_process_identity": _process_identity(os.getpid()),
                "timeout_seconds": timeout_seconds,
                "timeout_defaulted": timeout_defaulted,
            }
            if resource_demand is not None:
                initial_task["resource_demand"] = resource_demand
            if selected_cpu_ids:
                initial_task["cpu_ids"] = list(selected_cpu_ids)
                initial_task["cpu_count"] = len(selected_cpu_ids)
            _write_task(task_id, initial_task)
    except ValueError as exc:
        print(json.dumps({
            "error": f"invalid task id: {exc}",
            "task_id": task_id,
        }))
        return 1
    except _cpu_admission.CpuAdmissionError as exc:
        print(json.dumps({
            "error": f"CPU admission rejected: {exc}",
            "task_id": task_id,
        }))
        return 1

    if os.name == "nt":
        try:
            worker = _spawn_windows_worker(
                task_id=task_id,
                description=args.description,
                command=args.command,
                mode=mode,
                timeout=timeout_seconds,
                monitor_interval=args.monitor_interval or 120,
                model=args.model,
                cwd=cwd,
                run_dir=run_dir,
                preflight=not args.no_preflight,
                cpu_ids=selected_cpu_ids,
                registry_cwd=registry_cwd,
            )
        except OSError as exc:
            with _cpu_admission.cpu_admission_lock(Path.cwd()):
                _unlink_task_records(task_id)
            print(json.dumps({
                "error": f"failed to spawn Windows subagent worker: {exc}",
                "task_id": task_id,
            }))
            return 2
        rec = _read_task(task_id) or initial_task
        rec.setdefault("worker_pid", worker.pid)
        rec.setdefault("pid", worker.pid)
        rec["worker_process_identity"] = _process_identity(worker.pid)
        _write_task(task_id, rec)
        result = {
            "state": "submitted",
            "task_id": task_id,
            "run_id": run_id,
            "pid": worker.pid,
            "mode": mode,
            "run_dir": run_dir,
            "description": args.description,
            "cpu_ids": list(selected_cpu_ids),
            "timeout_seconds": timeout_seconds,
            "timeout_defaulted": timeout_defaulted,
            "check_with": shlex.join([
                sys.executable,
                "-m",
                "argus_skill.tools.subagent",
                "status",
                "--task-id",
                task_id,
            ]),
        }
        if resource_demand is not None:
            result["resource_demand"] = resource_demand
        print(json.dumps(result))
        return 0

    # Fork: parent returns immediately
    try:
        pid = os.fork()
    except OSError as exc:
        with _cpu_admission.cpu_admission_lock(Path.cwd()):
            _unlink_task_records(task_id)
        print(json.dumps({
            "error": f"failed to fork background subagent: {exc}",
            "task_id": task_id,
        }))
        return 2
    if pid > 0:
        # The forked child owns the rich, evolving task record (it writes
        # "running" with the real training pid + heartbeats). The parent must NOT
        # clobber that with a stale snapshot — it only merges in the worker pid.
        rec = _read_task(task_id) or {
            "state": "running", "task_id": task_id,
            "run_id": run_id,
            "description": args.description, "command": args.command,
            "mode": mode, "run_dir": run_dir, "submitted_at": time.time(),
            "timeout_seconds": timeout_seconds,
            "timeout_defaulted": timeout_defaulted,
        }
        rec["worker_pid"] = pid
        rec.setdefault("pid", pid)
        rec["worker_process_identity"] = _process_identity(pid)
        _write_task(task_id, rec)
        result = {
            "state": "submitted",
            "task_id": task_id,
            "run_id": run_id,
            "pid": pid,
            "mode": mode,
            "run_dir": run_dir,
            "description": args.description,
            "cpu_ids": list(selected_cpu_ids),
            "timeout_seconds": timeout_seconds,
            "timeout_defaulted": timeout_defaulted,
            "check_with": shlex.join([
                sys.executable,
                "-m",
                "argus_skill.tools.subagent",
                "status",
                "--task-id",
                task_id,
            ]),
        }
        if resource_demand is not None:
            result["resource_demand"] = resource_demand
        print(json.dumps(result))
        return 0

    # Child: detach and run
    os.setsid()
    try:
        _detach_child_stdio()
    except OSError as exc:
        task = _read_task(task_id) or initial_task
        task.update({
            "state": "error",
            "error": f"stdio detach failed: {exc}",
            "completed_at": time.time(),
            "worker_pid": os.getpid(),
            "worker_process_identity": _process_identity(os.getpid()),
        })
        _write_task(task_id, task)
        os._exit(1)
    try:
        _cpu_admission.apply_current_process_affinity(selected_cpu_ids)
    except (OSError, RuntimeError) as exc:
        task = _read_task(task_id) or initial_task
        task.update({
            "state": "error",
            "error": f"CPU affinity setup failed: {exc}",
            "completed_at": time.time(),
            "worker_pid": os.getpid(),
            "worker_process_identity": _process_identity(os.getpid()),
        })
        _write_task(task_id, task)
        os._exit(1)

    if mode == "supervised":
        _run_supervised(
            task_id=task_id,
            command=args.command,
            description=args.description,
            timeout=timeout_seconds,
            monitor_interval=args.monitor_interval or 120,
            model=args.model or resolve_supervisor_model(),
            cwd=cwd,
            run_dir=run_dir,
            preflight=not args.no_preflight,
        )
    else:
        _run_direct(
            task_id=task_id,
            command=args.command,
            description=args.description,
            timeout=timeout_seconds,
            cwd=cwd,
            run_dir=run_dir,
        )
    os._exit(0)

# States that mean "this task did NOT fail". A healthy *running* job is not a
# failure, so polling its status must exit 0 — otherwise the engineer's shell
# flags every poll as a failed command and wastes rounds working around a
# non-error. Only genuine failures get a non-zero exit.
_OK_STATES = frozenset({
    "done", "running", "starting", "preflight", "waiting_resource", "early_stopped",
})

_FAILED_STATES = frozenset({"error", "crashed", "timeout"})


def _undelivered_reports() -> list[dict[str, object]]:
    """Reports that were written to disk because the inbox refused them.

    ``_queue_to_inbox`` drops ``<task_id>_ALERT.md`` into the registry when a
    handoff report cannot be queued. That report is the only signal that a run
    finished, early-stopped, timed out or crashed, so an alert file nobody reads
    is an engineer waiting forever. Status is the consumer: every poll, for any
    task, lists whatever is sitting there.
    """
    suffix = "_ALERT.md"
    try:
        alerts = sorted(REGISTRY_DIR.glob(f"*{suffix}"))
    except OSError:
        return []
    out: list[dict[str, object]] = []
    for path in alerts:
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append({
            "task_id": path.name[: -len(suffix)],
            "path": str(path),
            "written_at": stat.st_mtime,
            "bytes": stat.st_size,
        })
    return out


def cmd_status(args: argparse.Namespace) -> int:
    """Check status of a single task.

    Exit code is 0 for any non-failure state (including a healthy ``running``
    job) and non-zero only for genuine failures, so routine polling never reads
    as a failed command.
    """
    task = _read_task(args.task_id)
    if task is None:
        print(json.dumps({"error": f"task '{args.task_id}' not found"}))
        return 2

    # The command wrapper writes its own exit sidecar. This lets status recover
    # the real terminal state even if the forked Python worker/Engineer vanished.
    task = reconcile_terminal_task(args.task_id, task)
    pid = task.get("pid", 0)

    # Enrich with a live-process flag and run-directory progress so a single
    # poll tells the engineer whether the job is alive and advancing, without
    # it having to hand-inspect progress.jsonl/status.json itself.
    task["live"] = bool(pid and _recorded_process_alive(task, "pid"))
    progress = _progress_summary(_effective_run_dir(task))
    if progress:
        task["progress"] = progress

    # An open discussion is the single most action-required state: the supervisor
    # STOPPED a run and is waiting on the engineer. Surface it loudly with the
    # exact reply command and the co-located discussion file, and note that new
    # launches are blocked until it resolves.
    if task.get("state") == "discussing":
        rd = task.get("run_dir")
        task["ACTION_REQUIRED"] = (
            "Supervisor STOPPED this run and is WAITING for your reply. Read the "
            "discussion and reply BEFORE relaunching anything — new `submit`s are "
            "blocked until this concern resolves."
        )
        task["discussion_file"] = (
            str(Path(rd) / "DISCUSSION.md") if rd else task.get("discussion_path"))
        task["reply_with"] = shlex.join([
            sys.executable,
            "-m",
            "argus_skill.tools.subagent",
            "reply",
            "--task-id",
            args.task_id,
            "--message",
            "<your rationale>",
        ])

    # A report that never reached the inbox leaves an _ALERT.md behind. Surface
    # every one of them on any status poll — the engineer whose report was lost
    # is by definition not being told anything else.
    alerts = _undelivered_reports()
    if alerts:
        task["undelivered_reports"] = alerts
        task["UNDELIVERED_REPORTS"] = (
            f"{len(alerts)} subagent report(s) never reached your inbox — the "
            "run(s) below finished but nothing told you. Read each `path`, act "
            "on it, then delete the file so it stops being reported."
        )

    print(json.dumps(task, indent=2))
    state = task.get("state")
    if state in _FAILED_STATES:
        return 1
    return 0

def cmd_list(_args: argparse.Namespace) -> int:
    """List all sub-agent tasks with their current state."""
    tasks = _list_tasks()
    if not tasks:
        print("No sub-agent tasks.")
        return 0

    # Update crashed tasks
    for task in tasks:
        if task.get("state") in {"running", "starting", "preflight", "waiting_resource"}:
            reconcile_terminal_task(str(task.get("task_id") or ""), task)

    # Summary table
    running = [t for t in tasks if t.get("state") == "running"]
    waiting = [t for t in tasks if t.get("state") == "waiting_resource"]
    done = [t for t in tasks if t.get("state") == "done"]
    errors = [t for t in tasks if t.get("state") in ("error", "crashed", "timeout")]
    discussing = [t for t in tasks if t.get("state") == "discussing"]

    print(f"Sub-agents: {len(running)} running, {len(waiting)} waiting for resources, {len(done)} done, "
          f"{len(errors)} failed, {len(discussing)} awaiting your reply")
    print()
    for t in tasks:
        state = t.get("state", "?")
        tid = t.get("task_id", "?")
        desc = t.get("description", "")[:60]
        elapsed = t.get("elapsed_seconds", "")
        icon = {"done": "✅", "running": "⏳", "error": "❌",
                "crashed": "💀", "timeout": "⏰", "early_stopped": "🛑",
                "waiting_resource": "⌛", "discussing": "💬"}.get(state, "?")
        elapsed_str = f" ({elapsed:.0f}s)" if isinstance(elapsed, (int, float)) else ""
        print(f"  {icon} {tid}: {state}{elapsed_str} — {desc}")
        if state == "discussing":
            rd = t.get("run_dir")
            df = str(Path(rd) / "DISCUSSION.md") if rd else t.get("discussion_path", "")
            print(f"      ⚠ supervisor is WAITING for your reply — see {df}")
            print(f"        reply: python -m argus_skill.tools.subagent reply "
                  f"--task-id {tid} --message \"...\"")
        metric_line = _format_metric_line(_progress_summary(_effective_run_dir(t)))
        if metric_line:
            print(f"      ↳ {metric_line}")

    return 0

def cmd_wait(args: argparse.Namespace) -> int:
    """Block until a task completes."""
    deadline = time.time() + args.timeout if args.timeout is not None else None
    while deadline is None or time.time() < deadline:
        task = _read_task(args.task_id)
        if task is None:
            print(json.dumps({"error": f"task '{args.task_id}' not found"}))
            return 1
        task = reconcile_terminal_task(args.task_id, task)
        if task.get("state") not in ("running", "starting", "preflight", "waiting_resource"):
            print(json.dumps(task, indent=2))
            return 1 if task.get("state") in _FAILED_STATES else 0
        time.sleep(5)
    print(json.dumps({"error": "wait timeout", "task_id": args.task_id}))
    return 1

def cmd_clean(_args: argparse.Namespace) -> int:
    """Remove completed/failed task records."""
    tasks = _list_tasks()
    removed = 0
    for task in tasks:
        state = task.get("state", "")
        if state in ("done", "error", "crashed", "timeout"):
            _unlink_task_records(task["task_id"])
            removed += 1
    print(f"Cleaned {removed} completed task(s)")
    return 0

def cmd_reply(args: argparse.Namespace) -> int:
    """Append the engineer's turn to a task's supervisor discussion thread.

    Closes the loop: the engineer explains WHY it will act (and not the
    supervisor's suggested alternative). On a stopped run a parked supervisor is
    waiting on the shared transcript and will answer; for a finished task the
    turn stays on the audit trail.
    """
    task = _read_task(args.task_id)
    if task is None:
        print(json.dumps({"error": f"task '{args.task_id}' not found"}))
        return 2

    message = args.message
    if args.message_file:
        try:
            message = sys.stdin.read() if args.message_file == "-" else \
                Path(args.message_file).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as e:
            print(json.dumps({"error": f"cannot read --message-file: {e}"}))
            return 2
    if not message or not message.strip():
        print(json.dumps({"error": "reply message is empty"}))
        return 2

    path = _append_discussion(args.task_id, "engineer", message)
    _mirror_discussion_md(args.task_id, task.get("run_dir"))
    # The parked supervisor is the worker process; it watches the transcript.
    worker_pid = task.get("worker_pid") or task.get("pid") or 0
    last_hb = task.get("last_heartbeat")
    hb_age = (time.time() - last_hb) if isinstance(last_hb, (int, float)) else None
    # A live supervisor = worker process alive, supervised, in a live state, and
    # a fresh heartbeat (guards against PID reuse on a stale record).
    supervisor_alive = bool(
        worker_pid and _recorded_process_alive(task, "worker_pid")
        and task.get("mode") == "supervised"
        and task.get("state") in ("running", "discussing")
    )
    # The discussion is still open (this reply will get an answer) only while the
    # supervisor is parked discussing. Once it sets a terminal resolution, late
    # replies are recorded for the audit trail but nobody will respond.
    resolution = task.get("discussion_resolution")
    will_be_answered = bool(supervisor_alive and task.get("state") == "discussing")
    payload = {
        "state": "reply_recorded",
        "task_id": args.task_id,
        "discussion_path": str(path),
        "reply_count": _engineer_turn_count(args.task_id),
        "live_supervisor": supervisor_alive,
        "supervisor_state": task.get("state"),
        "will_be_answered": will_be_answered,
        "supervisor_heartbeat_age_s": (round(hb_age, 1) if hb_age is not None else None),
    }
    if not will_be_answered:
        payload["note"] = (
            "Discussion is closed (resolution="
            f"{resolution or 'n/a'}); this reply is on the audit trail but the "
            "supervisor will not respond. Act on your judgement and relaunch "
            "if needed."
        )
    print(json.dumps(payload))
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="subagent",
        description="Unified sub-agent system for long-running background tasks.",
    )
    sub = parser.add_subparsers(dest="subcommand")

    p_submit = sub.add_parser("submit", help="Submit a task")
    p_submit.add_argument("--task-id", required=True, help="Unique task identifier")
    p_submit.add_argument("--description", default="background task")
    p_submit.add_argument("--command", required=True, help="Shell command to run")
    p_submit.add_argument("--mode", choices=["direct", "supervised"], default="direct",
                          help="direct: just run (no LLM). supervised: run + periodic LLM monitoring")
    p_submit.add_argument("--timeout", type=int, default=None, help="Optional maximum seconds")
    p_submit.add_argument("--monitor-interval", type=int, default=120,
                          help="Base seconds between supervisor checks; backs off "
                               "while healthy, tightens when degrading (supervised mode)")
    p_submit.add_argument(
        "--model",
        default=None,
        help="Supervisor model (defaults to configured supervisor/shared model)",
    )
    p_submit.add_argument("--run-dir", default=None,
                          help="Run directory whose progress.jsonl/status.json the "
                               "supervisor reads and where it writes STOP on early-stop")
    p_submit.add_argument("--cwd", default=None)
    p_submit.add_argument("--override-discussion", default=None, metavar="REASON",
                          help="Break-glass: launch even though a supervisor is "
                               "parked on an open discussion. Records REASON to the "
                               "experiment ledger.")
    p_submit.add_argument("--clear-stop", action="store_true",
                          help="Remove a leftover run_dir/STOP before launching "
                               "(otherwise submit refuses a poisoned run dir).")
    p_submit.add_argument("--no-preflight", action="store_true",
                          help="Skip the supervised-mode pre-launch RL config "
                               "preflight (escape hatch for a known-good config).")
    p_submit.add_argument(
        "--accelerator",
        choices=["cuda", "rocm", "any", "none"],
        default=None,
        help="Declare accelerator demand; omit all demand flags for legacy behavior.",
    )
    p_submit.add_argument("--gpu-count", type=int, default=None)
    p_submit.add_argument("--gpu-mem-mib", type=int, default=None)
    p_submit.add_argument("--expected-duration", type=parse_duration, default=None)
    p_submit.add_argument("--checkpointable", action="store_true", default=None)
    p_submit.add_argument("--intent", default=None)
    cpu_group = p_submit.add_mutually_exclusive_group()
    cpu_group.add_argument(
        "--cpu-count",
        type=int,
        default=0,
        help=(
            "Lease this many distinct CPUs before creating task/run artifacts; "
            "the launched process inherits the selected affinity."
        ),
    )
    cpu_group.add_argument(
        "--cpu-ids",
        default=None,
        help=(
            "Lease exact comma-separated CPU ids before launch; conflicts with "
            "other participating live subagents are rejected."
        ),
    )

    p_worker = sub.add_parser("_worker", help=argparse.SUPPRESS)
    p_worker.add_argument("--task-id", required=True)
    p_worker.add_argument("--description", default="background task")
    p_worker.add_argument("--command", required=True)
    p_worker.add_argument("--mode", choices=["direct", "supervised"], default="direct")
    p_worker.add_argument("--timeout", type=int, default=None)
    p_worker.add_argument("--monitor-interval", type=int, default=120)
    p_worker.add_argument("--model", default=None)
    p_worker.add_argument("--run-dir", default=None)
    p_worker.add_argument("--cwd", required=True)
    p_worker.add_argument("--no-preflight", action="store_true")
    p_worker.add_argument("--cpu-ids", default=None)

    p_status = sub.add_parser("status", help="Show task status")
    p_status.add_argument("--task-id", required=True)

    sub.add_parser("list", help="List all tasks")

    p_wait = sub.add_parser("wait", help="Wait for a task to complete")
    p_wait.add_argument("--task-id", required=True)
    p_wait.add_argument("--timeout", type=int, default=None)

    sub.add_parser("clean", help="Remove completed task records")

    p_reply = sub.add_parser(
        "reply",
        help="Post your turn to a task's supervisor discussion thread",
    )
    p_reply.add_argument("--task-id", required=True)
    p_reply.add_argument("--message", default="",
                         help="Your reply: why you will act this way (and not the "
                              "supervisor's suggested alternative)")
    p_reply.add_argument("--message-file", default=None,
                         help="Read the reply from a file ('-' for stdin); "
                              "use for rationales with quotes/newlines")

    args = parser.parse_args()
    handlers = {
        "submit": cmd_submit,
        "_worker": cmd_worker,
        "status": cmd_status,
        "list": cmd_list,
        "wait": cmd_wait,
        "clean": cmd_clean,
        "reply": cmd_reply,
    }
    handler = handlers.get(args.subcommand)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)
