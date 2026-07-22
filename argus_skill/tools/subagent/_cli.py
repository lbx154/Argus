"""CLI command handlers + entrypoint for the subagent tool."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import _core
from . import _cpu_admission
from ._core import (
    DISCUSSION_POLL_INTERVAL,
    SUPERVISOR_MODEL,
    _append_discussion,
    _append_experiment_history,
    _effective_run_dir,
    _engineer_turn_count,
    _format_metric_line,
    _list_tasks,
    _mirror_discussion_md,
    _open_discussion_blockers,
    _progress_summary,
    _read_task,
    _registry_path,
    _run_dir_from_command,
    _run_direct,
    _run_supervised,
    _write_task,
    reconcile_terminal_task,
)


def cmd_submit(args: argparse.Namespace) -> int:
    """Submit a task. Returns immediately."""
    task_id = args.task_id
    existing = _read_task(task_id)
    if existing and existing.get("state") in {"starting", "preflight", "running"}:
        pid = existing.get("pid") or existing.get("worker_pid") or 0
        if _core._is_pid_alive(pid):
            print(json.dumps({
                "error": (
                    f"task '{task_id}' is already {existing.get('state')} "
                    f"(pid {pid})"
                ),
            }))
            return 1

    cwd = args.cwd or os.getcwd()
    mode = getattr(args, "mode", "direct") or "direct"

    # Resolve the run directory: prefer an explicit --run-dir, else recover it
    # from the command itself (commands already carry --run-dir). Store it as an
    # absolute path so status/report can read progress.jsonl/status.json/
    # summary.tsv regardless of the caller's cwd -- this is what makes the run
    # observable instead of a black box.
    run_dir = getattr(args, "run_dir", None) or _run_dir_from_command(args.command)
    if run_dir:
        rp = Path(run_dir)
        run_dir = str(rp if rp.is_absolute() else Path(cwd) / rp)

    # Forced-discussion gate: while a supervisor is parked on an OPEN discussion
    # (it stopped a run and is waiting on the engineer), block launching new runs
    # so a concern can never be bypassed by silently starting something else. The
    # `reply` command is never blocked. A stale/dead supervisor does not wedge
    # this (liveness = live pid + fresh heartbeat). Break-glass: --override-discussion.
    override = getattr(args, "override_discussion", None)
    blockers = _open_discussion_blockers(_core._lane_of(getattr(args, "task_id", None)))
    if blockers and not override:
        b = blockers[0]
        rd = b.get("run_dir")
        print(json.dumps({
            "error": "blocked: a supervisor stopped a run and is waiting for your reply",
            "blocking_task": b.get("task_id"),
            "supervisor_concern": b.get("concern") or b.get("last_supervisor_concern", ""),
            "discussion_file": (str(Path(rd) / "DISCUSSION.md") if rd else b.get("discussion_path")),
            "reply_with": (
                "python -m argus_skill.tools.subagent reply --task-id "
                f"{b.get('task_id')} --message \"<your rationale>\""
            ),
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
            if getattr(args, "clear_stop", False):
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

    if os.name == "nt":
        print(json.dumps({
            "error": "background subagent detach is unavailable in the Windows terminal preview",
            "hint": "run this workload in the foreground or use WSL2 for detached subagents",
        }))
        return 2

    # Admit and reserve CPUs before creating the first task/log/run artifact.
    # The starting record is the lease placeholder during the short fork window.
    try:
        with _cpu_admission.cpu_admission_lock(Path.cwd()):
            existing = _read_task(task_id)
            if existing and existing.get("state") in {"starting", "preflight", "running"}:
                existing_pid = (
                    existing.get("pid")
                    or existing.get("worker_pid")
                    or existing.get("submitter_pid")
                    or 0
                )
                if _core._is_pid_alive(int(existing_pid or 0)):
                    print(json.dumps({
                        "error": (
                            f"task '{task_id}' is already {existing.get('state')} "
                            f"(pid {existing_pid})"
                        ),
                    }))
                    return 1
            selected_cpu_ids = _cpu_admission.select_cpu_ids(
                cpu_count=getattr(args, "cpu_count", 0),
                cpu_ids=getattr(args, "cpu_ids", None),
                tasks=_list_tasks(),
                is_pid_alive=_core._is_pid_alive,
            )
            initial_task = {
                "state": "starting",
                "task_id": task_id,
                "description": args.description,
                "command": args.command,
                "mode": mode,
                "run_dir": run_dir,
                "submitted_at": time.time(),
                "submitter_pid": os.getpid(),
            }
            if selected_cpu_ids:
                initial_task["cpu_ids"] = list(selected_cpu_ids)
                initial_task["cpu_count"] = len(selected_cpu_ids)
            _write_task(task_id, initial_task)
    except _cpu_admission.CpuAdmissionError as exc:
        print(json.dumps({
            "error": f"CPU admission rejected: {exc}",
            "task_id": task_id,
        }))
        return 1

    # Fork: parent returns immediately
    try:
        pid = os.fork()
    except OSError as exc:
        with _cpu_admission.cpu_admission_lock(Path.cwd()):
            _registry_path(task_id).unlink(missing_ok=True)
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
            "description": args.description, "command": args.command,
            "mode": mode, "run_dir": run_dir, "submitted_at": time.time(),
        }
        rec["worker_pid"] = pid
        rec.setdefault("pid", pid)
        _write_task(task_id, rec)
        print(json.dumps({
            "state": "submitted",
            "task_id": task_id,
            "pid": pid,
            "mode": mode,
            "run_dir": run_dir,
            "description": args.description,
            "cpu_ids": list(selected_cpu_ids),
            "check_with": f"python -m argus_skill.tools.subagent status --task-id {task_id}",
        }))
        return 0

    # Child: detach and run
    os.setsid()
    try:
        os.close(0)
    except OSError:
        pass
    try:
        _cpu_admission.apply_current_process_affinity(selected_cpu_ids)
    except (OSError, RuntimeError) as exc:
        task = _read_task(task_id) or initial_task
        task.update({
            "state": "error",
            "error": f"CPU affinity setup failed: {exc}",
            "completed_at": time.time(),
            "worker_pid": os.getpid(),
        })
        _write_task(task_id, task)
        os._exit(1)

    if mode == "supervised":
        _run_supervised(
            task_id=task_id,
            command=args.command,
            description=args.description,
            timeout=args.timeout,
            monitor_interval=getattr(args, "monitor_interval", 120) or 120,
            model=getattr(args, "model", SUPERVISOR_MODEL) or SUPERVISOR_MODEL,
            cwd=cwd,
            run_dir=run_dir,
            preflight=not getattr(args, "no_preflight", False),
        )
    else:
        _run_direct(
            task_id=task_id,
            command=args.command,
            description=args.description,
            timeout=args.timeout,
            cwd=cwd,
            run_dir=run_dir,
        )
    os._exit(0)

# States that mean "this task did NOT fail". A healthy *running* job is not a
# failure, so polling its status must exit 0 — otherwise the engineer's shell
# flags every poll as a failed command and wastes rounds working around a
# non-error. Only genuine failures get a non-zero exit.
_OK_STATES = frozenset({"done", "running", "starting", "preflight", "early_stopped"})

_FAILED_STATES = frozenset({"error", "crashed", "timeout"})

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
    task["live"] = bool(pid and _core._is_pid_alive(pid))
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
        task["reply_with"] = (
            "python -m argus_skill.tools.subagent reply --task-id "
            f"{args.task_id} --message \"<your rationale>\"")

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
        if task.get("state") in {"running", "starting", "preflight"}:
            reconcile_terminal_task(str(task.get("task_id") or ""), task)

    # Summary table
    running = [t for t in tasks if t.get("state") == "running"]
    done = [t for t in tasks if t.get("state") == "done"]
    errors = [t for t in tasks if t.get("state") in ("error", "crashed", "timeout")]
    discussing = [t for t in tasks if t.get("state") == "discussing"]

    print(f"Sub-agents: {len(running)} running, {len(done)} done, "
          f"{len(errors)} failed, {len(discussing)} awaiting your reply")
    print()
    for t in tasks:
        state = t.get("state", "?")
        tid = t.get("task_id", "?")
        desc = t.get("description", "")[:60]
        elapsed = t.get("elapsed_seconds", "")
        icon = {"done": "✅", "running": "⏳", "error": "❌",
                "crashed": "💀", "timeout": "⏰", "early_stopped": "🛑",
                "discussing": "💬"}.get(state, "?")
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
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        task = _read_task(args.task_id)
        if task is None:
            print(json.dumps({"error": f"task '{args.task_id}' not found"}))
            return 1
        task = reconcile_terminal_task(args.task_id, task)
        if task.get("state") not in ("running", "starting", "preflight"):
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
            path = _registry_path(task["task_id"])
            path.unlink(missing_ok=True)
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
    if getattr(args, "message_file", None):
        try:
            message = sys.stdin.read() if args.message_file == "-" else \
                Path(args.message_file).read_text()
        except OSError as e:
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
        worker_pid and _core._is_pid_alive(worker_pid)
        and task.get("mode") == "supervised"
        and task.get("state") in ("running", "discussing")
        and (hb_age is None or hb_age < DISCUSSION_POLL_INTERVAL * 6)
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
    p_submit.add_argument("--timeout", type=int, default=7200, help="Max seconds (default: 2h)")
    p_submit.add_argument("--monitor-interval", type=int, default=120,
                          help="Base seconds between supervisor checks; backs off "
                               "while healthy, tightens when degrading (supervised mode)")
    p_submit.add_argument("--model", default=SUPERVISOR_MODEL, help="Supervisor model (supervised mode)")
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

    p_status = sub.add_parser("status", help="Show task status")
    p_status.add_argument("--task-id", required=True)

    sub.add_parser("list", help="List all tasks")

    p_wait = sub.add_parser("wait", help="Wait for a task to complete")
    p_wait.add_argument("--task-id", required=True)
    p_wait.add_argument("--timeout", type=int, default=3600)

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
