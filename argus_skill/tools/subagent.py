"""Unified sub-agent system for delegating long-running tasks.

The main engineer spawns lightweight sub-agents for slow operations
(GPU training, image generation, validation, compilation). Sub-agents
run as independent codex processes and report results through a shared
task registry on disk.

Usage from the engineer:

    # Submit a task (returns immediately)
    python -m argus_skill.tools.subagent submit \
      --task-id fig-gen \
      --description "Generate 3 Figure 1 candidates" \
      --command "python code/generate_variants.py --generate"

    # Check one task
    python -m argus_skill.tools.subagent status --task-id fig-gen

    # Check all tasks
    python -m argus_skill.tools.subagent list

    # Wait for a task to finish (blocking)
    python -m argus_skill.tools.subagent wait --task-id fig-gen

    # Submit multiple tasks at once
    python -m argus_skill.tools.subagent submit \
      --task-id review-academic \
      --description "Run academic language review" \
      --command "python -m argus_skill.skills.academic_language_review --project-root . --review-mode model --write"
    python -m argus_skill.tools.subagent submit \
      --task-id review-infra \
      --description "Run infrastructure review" \
      --command "python -m argus_skill.skills.paper_infrastructure_review --project-root . --review-mode model --write"

    # ... do other work ...

    # Check all at once
    python -m argus_skill.tools.subagent list
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REGISTRY_DIR = Path(".argus_subagents")
WATCHER_MODEL = "gpt-5.4-mini"


# ---------------------------------------------------------------------------
# Registry: persistent task state on disk
# ---------------------------------------------------------------------------

def _registry_path(task_id: str) -> Path:
    return REGISTRY_DIR / f"{task_id}.json"


def _write_task(task_id: str, data: dict[str, Any]) -> None:
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    path = _registry_path(task_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_task(task_id: str) -> dict[str, Any] | None:
    path = _registry_path(task_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _list_tasks() -> list[dict[str, Any]]:
    if not REGISTRY_DIR.exists():
        return []
    tasks = []
    for f in sorted(REGISTRY_DIR.glob("*.json")):
        if f.name.endswith(".tmp"):
            continue
        try:
            tasks.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return tasks


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


# ---------------------------------------------------------------------------
# Sub-agent execution
# ---------------------------------------------------------------------------

def _find_codex() -> str:
    codex = shutil.which("codex")
    if codex:
        return codex
    for candidate in ["/usr/local/bin/codex", "/usr/bin/codex"]:
        if os.path.isfile(candidate):
            return candidate
    return "codex"


def _run_subagent(
    task_id: str,
    command: str,
    description: str,
    timeout: int,
    model: str,
    cwd: str,
) -> None:
    """Run in the forked child process. Launches codex sub-agent."""

    report_path = _registry_path(task_id)
    codex = _find_codex()

    prompt = (
        f"You are a background sub-agent. Your ONLY job:\n"
        f"1. Run this command: {command}\n"
        f"2. Wait for it to complete (timeout: {timeout}s)\n"
        f"3. Write the result to: {report_path}\n\n"
        f"Task: {description}\n\n"
        f"Run the command now. After it finishes, update {report_path} with:\n"
        f'{{"state": "done" or "error", "task_id": "{task_id}", '
        f'"description": "{description}", "command": "<the command>", '
        f'"exit_code": <code>, "elapsed_seconds": <time>, '
        f'"stdout_tail": "<last 2000 chars>", "stderr_tail": "<last 2000 chars>", '
        f'"completed_at": <timestamp>, "summary": "<one sentence>"}}\n\n'
        f"Do NOT edit any other files. Just run, report, exit."
    )

    codex_cmd = [
        codex, "exec",
        "--json",
        "-m", model,
        "--skip-git-repo-check",
        "--ephemeral",
        "--dangerously-bypass-approvals-and-sandbox",
        prompt,
    ]

    start_time = time.time()
    try:
        result = subprocess.run(
            codex_cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 120,
            cwd=cwd,
        )
        # Check if sub-agent wrote the report
        existing = _read_task(task_id)
        if existing and existing.get("state") == "running":
            # Sub-agent didn't write final report — write fallback
            _write_task(task_id, {
                "state": "done" if result.returncode == 0 else "error",
                "task_id": task_id,
                "description": description,
                "command": command,
                "exit_code": result.returncode,
                "elapsed_seconds": round(time.time() - start_time, 1),
                "stdout_tail": (result.stdout or "")[-2000:],
                "stderr_tail": (result.stderr or "")[-2000:],
                "completed_at": time.time(),
                "summary": "Sub-agent exited without writing final report",
                "pid": os.getpid(),
            })

    except subprocess.TimeoutExpired:
        _write_task(task_id, {
            "state": "timeout",
            "task_id": task_id,
            "description": description,
            "command": command,
            "timeout_seconds": timeout,
            "elapsed_seconds": round(time.time() - start_time, 1),
            "completed_at": time.time(),
            "pid": os.getpid(),
        })

    except Exception as exc:
        _write_task(task_id, {
            "state": "error",
            "task_id": task_id,
            "description": description,
            "command": command,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.time() - start_time, 1),
            "completed_at": time.time(),
            "pid": os.getpid(),
        })


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_submit(args: argparse.Namespace) -> int:
    """Submit a task to a sub-agent. Returns immediately."""
    task_id = args.task_id
    existing = _read_task(task_id)
    if existing and existing.get("state") == "running":
        pid = existing.get("pid", 0)
        if _is_pid_alive(pid):
            print(json.dumps({
                "error": f"task '{task_id}' is already running (pid {pid})",
            }))
            return 1

    cwd = args.cwd or os.getcwd()

    # Write initial state
    _write_task(task_id, {
        "state": "starting",
        "task_id": task_id,
        "description": args.description,
        "command": args.command,
        "submitted_at": time.time(),
    })

    # Fork: parent returns immediately
    pid = os.fork()
    if pid > 0:
        # Update with child pid
        _write_task(task_id, {
            "state": "running",
            "task_id": task_id,
            "description": args.description,
            "command": args.command,
            "pid": pid,
            "submitted_at": time.time(),
        })
        print(json.dumps({
            "state": "submitted",
            "task_id": task_id,
            "pid": pid,
            "description": args.description,
            "check_with": f"python -m argus_skill.tools.subagent status --task-id {task_id}",
        }))
        return 0

    # Child: detach and run
    os.setsid()
    try:
        os.close(0)
    except OSError:
        pass

    _run_subagent(
        task_id=task_id,
        command=args.command,
        description=args.description,
        timeout=args.timeout,
        model=args.model,
        cwd=cwd,
    )
    os._exit(0)


def cmd_status(args: argparse.Namespace) -> int:
    """Check status of a single task."""
    task = _read_task(args.task_id)
    if task is None:
        print(json.dumps({"error": f"task '{args.task_id}' not found"}))
        return 1

    # Update state if process died without writing final report
    if task.get("state") == "running":
        pid = task.get("pid", 0)
        if pid and not _is_pid_alive(pid):
            task["state"] = "crashed"
            task["error"] = f"sub-agent process {pid} no longer running"
            task["completed_at"] = time.time()
            _write_task(args.task_id, task)

    print(json.dumps(task, indent=2))
    return 0 if task.get("state") == "done" else 1


def cmd_list(args: argparse.Namespace) -> int:
    """List all sub-agent tasks with their current state."""
    tasks = _list_tasks()
    if not tasks:
        print("No sub-agent tasks.")
        return 0

    # Update crashed tasks
    for task in tasks:
        if task.get("state") == "running":
            pid = task.get("pid", 0)
            if pid and not _is_pid_alive(pid):
                task["state"] = "crashed"
                task["error"] = f"process {pid} no longer running"
                _write_task(task["task_id"], task)

    # Summary table
    running = [t for t in tasks if t.get("state") == "running"]
    done = [t for t in tasks if t.get("state") == "done"]
    errors = [t for t in tasks if t.get("state") in ("error", "crashed", "timeout")]

    print(f"Sub-agents: {len(running)} running, {len(done)} done, {len(errors)} failed")
    print()
    for t in tasks:
        state = t.get("state", "?")
        tid = t.get("task_id", "?")
        desc = t.get("description", "")[:60]
        elapsed = t.get("elapsed_seconds", "")
        icon = {"done": "✅", "running": "⏳", "error": "❌",
                "crashed": "💀", "timeout": "⏰"}.get(state, "?")
        elapsed_str = f" ({elapsed:.0f}s)" if isinstance(elapsed, (int, float)) else ""
        print(f"  {icon} {tid}: {state}{elapsed_str} — {desc}")

    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    """Block until a task completes."""
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        task = _read_task(args.task_id)
        if task is None:
            print(json.dumps({"error": f"task '{args.task_id}' not found"}))
            return 1
        if task.get("state") not in ("running", "starting"):
            print(json.dumps(task, indent=2))
            return 0 if task.get("state") == "done" else 1
        time.sleep(5)
    print(json.dumps({"error": "wait timeout", "task_id": args.task_id}))
    return 1


def cmd_clean(args: argparse.Namespace) -> int:
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="subagent",
        description="Unified sub-agent system for long-running background tasks.",
    )
    sub = parser.add_subparsers(dest="subcommand")

    p_submit = sub.add_parser("submit", help="Submit a task to a sub-agent")
    p_submit.add_argument("--task-id", required=True, help="Unique task identifier")
    p_submit.add_argument("--description", default="background task")
    p_submit.add_argument("--command", required=True, help="Shell command to run")
    p_submit.add_argument("--timeout", type=int, default=1800)
    p_submit.add_argument("--model", default=WATCHER_MODEL)
    p_submit.add_argument("--cwd", default=None)

    p_status = sub.add_parser("status", help="Check one task's status")
    p_status.add_argument("--task-id", required=True)

    p_list = sub.add_parser("list", help="List all tasks")

    p_wait = sub.add_parser("wait", help="Wait for a task to complete")
    p_wait.add_argument("--task-id", required=True)
    p_wait.add_argument("--timeout", type=int, default=3600)

    p_clean = sub.add_parser("clean", help="Remove completed task records")

    args = parser.parse_args()
    handlers = {
        "submit": cmd_submit,
        "status": cmd_status,
        "list": cmd_list,
        "wait": cmd_wait,
        "clean": cmd_clean,
    }
    handler = handlers.get(args.subcommand)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
