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


"""Unified sub-agent system for delegating long-running tasks.

Two execution modes:

1. **direct** (default): fork + Popen. No LLM involved. Best for GPU
   training, inference, evaluation — any command that just needs to run.

2. **supervised**: fork + Popen + periodic LLM monitoring. A codex agent
   checks training logs every N seconds and can intervene (early-stop,
   save checkpoint, flag anomaly). Best for long GPU training where you
   want an agent watching the loss curve.

Usage from the engineer:

    # Direct mode — just run the command (no LLM cost)
    python -m argus_skill.tools.subagent submit \
      --task-id eval-geneval \
      --description "Evaluate zImage on GenEval" \
      --command ".venv/bin/python code/eval.py --benchmark geneval"

    # Supervised mode — run with LLM monitoring every 120s
    python -m argus_skill.tools.subagent submit \
      --task-id train-grpo \
      --mode supervised \
      --monitor-interval 120 \
      --description "Train zImage LoRA with GRPO" \
      --command ".venv/bin/python code/train.py --config grpo_config.yaml"

    # Check status
    python -m argus_skill.tools.subagent status --task-id train-grpo

    # List all
    python -m argus_skill.tools.subagent list
"""


REGISTRY_DIR = Path(".argus_subagents")
SUPERVISOR_MODEL = "gpt-5.4-mini"


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
# Direct execution: fork + Popen, no LLM
# ---------------------------------------------------------------------------

def _run_direct(
    task_id: str,
    command: str,
    description: str,
    timeout: int,
    cwd: str,
) -> None:
    """Run command directly via Popen. No LLM involved."""
    log_dir = REGISTRY_DIR / f"{task_id}_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "stdout.log"
    stderr_path = log_dir / "stderr.log"

    start_time = time.time()
    try:
        with stdout_path.open("w") as out, stderr_path.open("w") as err:
            proc = subprocess.Popen(
                command, shell=True, stdout=out, stderr=err,
                cwd=cwd, start_new_session=True,
            )
            _write_task(task_id, {
                "state": "running", "task_id": task_id,
                "description": description, "command": command,
                "pid": proc.pid, "worker_pid": os.getpid(),
                "started_at": time.time(), "mode": "direct",
                "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
            })
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                _write_task(task_id, {
                    "state": "timeout", "task_id": task_id,
                    "description": description, "command": command,
                    "pid": proc.pid, "timeout_seconds": timeout,
                    "elapsed_seconds": round(time.time() - start_time, 1),
                    "completed_at": time.time(), "mode": "direct",
                    "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
                })
                return

        elapsed = round(time.time() - start_time, 1)
        stdout_tail = _tail_file(stdout_path, 3000)
        stderr_tail = _tail_file(stderr_path, 3000)
        _write_task(task_id, {
            "state": "done" if proc.returncode == 0 else "error",
            "task_id": task_id, "description": description,
            "command": command, "exit_code": proc.returncode,
            "elapsed_seconds": elapsed, "completed_at": time.time(),
            "pid": proc.pid, "mode": "direct",
            "stdout_tail": stdout_tail, "stderr_tail": stderr_tail,
            "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
        })

    except Exception as exc:
        _write_task(task_id, {
            "state": "error", "task_id": task_id,
            "description": description, "command": command,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.time() - start_time, 1),
            "completed_at": time.time(), "mode": "direct",
        })


# ---------------------------------------------------------------------------
# Supervised execution: fork + Popen + periodic LLM check
# ---------------------------------------------------------------------------

def _find_codex() -> str:
    codex = shutil.which("codex")
    if codex:
        return codex
    for candidate in ["/usr/local/bin/codex", "/usr/bin/codex"]:
        if os.path.isfile(candidate):
            return candidate
    return "codex"


def _tail_file(path: Path, max_chars: int = 3000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[-max_chars:] if len(text) > max_chars else text
    except (OSError, FileNotFoundError):
        return ""


def _supervisor_check(
    task_id: str,
    command: str,
    description: str,
    stdout_path: Path,
    stderr_path: Path,
    elapsed: float,
    check_number: int,
    model: str,
    cwd: str,
) -> str:
    """Call codex to check training progress. Returns: continue/early-stop/checkpoint."""
    codex = _find_codex()

    stdout_tail = _tail_file(stdout_path, 2000)
    stderr_tail = _tail_file(stderr_path, 1000)

    # Also read progress.jsonl if it exists
    progress_path = Path(cwd) / "progress.jsonl"
    progress_tail = ""
    if progress_path.exists():
        progress_tail = _tail_file(progress_path, 1500)

    prompt = (
        f"You are a training supervisor agent. Check #{check_number} on task '{task_id}'.\n"
        f"Task: {description}\n"
        f"Command: {command}\n"
        f"Running for: {elapsed:.0f}s\n\n"
        f"=== stdout (last 2000 chars) ===\n{stdout_tail}\n\n"
        f"=== stderr (last 1000 chars) ===\n{stderr_tail}\n\n"
    )
    if progress_tail:
        prompt += f"=== progress.jsonl (last 1500 chars) ===\n{progress_tail}\n\n"

    prompt += (
        "Analyze the training progress. Respond with EXACTLY one JSON object:\n"
        '{"decision": "continue" or "early_stop" or "save_checkpoint",\n'
        ' "reason": "one sentence explaining why",\n'
        ' "metrics": {"loss": ..., "step": ..., "epoch": ...},\n'
        ' "health": "healthy" or "degrading" or "stuck" or "diverging"}\n\n'
        "Decision rules:\n"
        "- continue: training looks healthy, loss trending down\n"
        "- early_stop: loss diverging, NaN detected, GPU OOM, or no progress for >30% of total steps\n"
        "- save_checkpoint: notable improvement milestone reached\n"
        "Only output the JSON, nothing else."
    )

    try:
        result = subprocess.run(
            [codex, "exec", "--json", "-m", model,
             "--skip-git-repo-check", "--ephemeral",
             "--dangerously-bypass-approvals-and-sandbox", prompt],
            capture_output=True, text=True, timeout=120, cwd=cwd,
        )
        # Parse codex output for the agent's response
        try:
            output = json.loads(result.stdout)
            messages = output.get("messages", [])
            for msg in reversed(messages):
                if msg.get("role") == "assistant":
                    text = msg.get("content", "")
                    if text.startswith("```"):
                        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                    data = json.loads(text)
                    return data.get("decision", "continue")
        except (json.JSONDecodeError, KeyError, IndexError):
            pass
        return "continue"
    except Exception:
        return "continue"  # On any error, don't intervene


def _run_supervised(
    task_id: str,
    command: str,
    description: str,
    timeout: int,
    monitor_interval: int,
    model: str,
    cwd: str,
) -> None:
    """Run command with periodic LLM supervisor checks."""
    log_dir = REGISTRY_DIR / f"{task_id}_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "stdout.log"
    stderr_path = log_dir / "stderr.log"
    supervisor_log = log_dir / "supervisor.jsonl"

    start_time = time.time()
    try:
        with stdout_path.open("w") as out, stderr_path.open("w") as err:
            proc = subprocess.Popen(
                command, shell=True, stdout=out, stderr=err,
                cwd=cwd, start_new_session=True,
            )
            _write_task(task_id, {
                "state": "running", "task_id": task_id,
                "description": description, "command": command,
                "pid": proc.pid, "worker_pid": os.getpid(),
                "started_at": time.time(), "mode": "supervised",
                "monitor_interval": monitor_interval,
                "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
                "supervisor_log": str(supervisor_log),
            })

            check_number = 0
            while True:
                # Wait for monitor_interval or process exit
                try:
                    proc.wait(timeout=monitor_interval)
                    break  # Process exited
                except subprocess.TimeoutExpired:
                    pass  # Still running, do supervisor check

                elapsed = time.time() - start_time
                if elapsed > timeout:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    _write_task(task_id, {
                        "state": "timeout", "task_id": task_id,
                        "description": description, "command": command,
                        "pid": proc.pid, "timeout_seconds": timeout,
                        "elapsed_seconds": round(elapsed, 1),
                        "completed_at": time.time(), "mode": "supervised",
                    })
                    return

                # Supervisor LLM check
                check_number += 1
                out.flush()
                err.flush()
                decision = _supervisor_check(
                    task_id, command, description,
                    stdout_path, stderr_path, elapsed, check_number,
                    model, cwd,
                )

                # Log supervisor decision
                entry = {
                    "check": check_number, "elapsed_s": round(elapsed, 1),
                    "decision": decision, "timestamp": time.time(),
                }
                with supervisor_log.open("a") as sl:
                    sl.write(json.dumps(entry) + "\n")

                # Update task with latest supervisor info
                task = _read_task(task_id) or {}
                task["last_supervisor_check"] = check_number
                task["last_supervisor_decision"] = decision
                task["elapsed_seconds"] = round(elapsed, 1)
                _write_task(task_id, task)

                if decision == "early_stop":
                    # Send STOP signal
                    stop_file = Path(cwd) / "STOP"
                    stop_file.write_text(
                        f"Early-stopped by supervisor at check #{check_number}\n",
                    )
                    # Wait briefly for graceful shutdown
                    try:
                        proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        proc.terminate()
                        try:
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                    _write_task(task_id, {
                        "state": "early_stopped", "task_id": task_id,
                        "description": description, "command": command,
                        "pid": proc.pid, "exit_code": proc.returncode,
                        "elapsed_seconds": round(time.time() - start_time, 1),
                        "completed_at": time.time(), "mode": "supervised",
                        "supervisor_checks": check_number,
                        "stop_reason": "supervisor early-stop",
                    })
                    return

        # Process exited naturally
        elapsed = round(time.time() - start_time, 1)
        stdout_tail = _tail_file(stdout_path, 3000)
        stderr_tail = _tail_file(stderr_path, 3000)
        _write_task(task_id, {
            "state": "done" if proc.returncode == 0 else "error",
            "task_id": task_id, "description": description,
            "command": command, "exit_code": proc.returncode,
            "elapsed_seconds": elapsed, "completed_at": time.time(),
            "pid": proc.pid, "mode": "supervised",
            "supervisor_checks": check_number,
            "stdout_tail": stdout_tail, "stderr_tail": stderr_tail,
            "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
        })

    except Exception as exc:
        _write_task(task_id, {
            "state": "error", "task_id": task_id,
            "description": description, "command": command,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.time() - start_time, 1),
            "completed_at": time.time(), "mode": "supervised",
        })


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_submit(args: argparse.Namespace) -> int:
    """Submit a task. Returns immediately."""
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
    mode = getattr(args, "mode", "direct") or "direct"

    # Write initial state
    _write_task(task_id, {
        "state": "starting",
        "task_id": task_id,
        "description": args.description,
        "command": args.command,
        "mode": mode,
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
            "mode": mode,
            "pid": pid,
            "submitted_at": time.time(),
        })
        print(json.dumps({
            "state": "submitted",
            "task_id": task_id,
            "pid": pid,
            "mode": mode,
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

    if mode == "supervised":
        _run_supervised(
            task_id=task_id,
            command=args.command,
            description=args.description,
            timeout=args.timeout,
            monitor_interval=getattr(args, "monitor_interval", 120) or 120,
            model=getattr(args, "model", SUPERVISOR_MODEL) or SUPERVISOR_MODEL,
            cwd=cwd,
        )
    else:
        _run_direct(
            task_id=task_id,
            command=args.command,
            description=args.description,
            timeout=args.timeout,
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
                "crashed": "💀", "timeout": "⏰", "early_stopped": "🛑"}.get(state, "?")
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

    p_submit = sub.add_parser("submit", help="Submit a task")
    p_submit.add_argument("--task-id", required=True, help="Unique task identifier")
    p_submit.add_argument("--description", default="background task")
    p_submit.add_argument("--command", required=True, help="Shell command to run")
    p_submit.add_argument("--mode", choices=["direct", "supervised"], default="direct",
                          help="direct: just run (no LLM). supervised: run + periodic LLM monitoring")
    p_submit.add_argument("--timeout", type=int, default=7200, help="Max seconds (default: 2h)")
    p_submit.add_argument("--monitor-interval", type=int, default=120,
                          help="Seconds between supervisor checks (supervised mode only)")
    p_submit.add_argument("--model", default=SUPERVISOR_MODEL, help="Supervisor model (supervised mode)")
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
