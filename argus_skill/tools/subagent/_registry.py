"""Registry/persistence layer for subagent task state on disk.

Owns: task-record JSON read/write, exit-sidecar, process-group launch,
child environment, lane routing, run-dir resolution, experiment-history
ledger, structured RunWriter signal readers, and usage accounting helpers.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

try:
    import fcntl  # POSIX advisory locks for safe concurrent appends
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

from ._text import _tail_file

# ---------------------------------------------------------------------------
# Module-level constants (used across multiple subagent modules)
# ---------------------------------------------------------------------------

REGISTRY_DIR = Path(".argus_subagents")
SUPERVISOR_MODEL = "gpt-5.5"
SUPERVISOR_INTERVAL_CAP = 900

# Reuse one persistent codex supervisor thread for at most this many checks,
# then rotate to a fresh thread seeded with a short summary so a multi-hour
# run never overflows the context window.
SUPERVISOR_THREAD_MAX_CHECKS = 12

# A parked supervisor refreshes ``last_heartbeat`` every poll. A discussion
# whose heartbeat is older than this is treated as abandoned (worker hung/dead)
# so it never wedges the relaunch gate forever. Sized to clear the worst-case
# gap between heartbeats: one poll plus a resume-then-fresh codex retry
# (~2×120s).
DISCUSSION_STALE_AFTER_S = 600

# Append-only, project-local ledger of every supervised experiment so a future
# engineer mission can learn why past runs succeeded or failed.
EXPERIMENT_HISTORY_REL = "research/EXPERIMENT_HISTORY.jsonl"

# Internal accounting helpers used by the supervised worker.
_ZERO_USAGE_TUPLE = (0, 0, 0, 0)

_QUIET_LOGS_ENV = "ARGUS_SUBAGENT_QUIET_LOGS"


# ---------------------------------------------------------------------------
# Registry paths
# ---------------------------------------------------------------------------

def _registry_path(task_id: str) -> Path:
    return REGISTRY_DIR / f"{task_id}.json"


def _exit_status_path(task_id: str, run_id: str | None = None) -> Path:
    name = f"exit_code.{run_id}" if run_id else "exit_code"
    return REGISTRY_DIR / f"{task_id}_logs" / name


# ---------------------------------------------------------------------------
# Child environment
# ---------------------------------------------------------------------------

def _child_env() -> dict[str, str]:
    """Environment for spawned task processes with quieter framework logs.

    The captured ``stdout``/``stderr`` feed both the LLM supervisor and the
    engineer. By default the box exports ``NCCL_DEBUG=INFO``, which floods the
    logs with hundreds of ``NCCL INFO`` lines per run and drowns the real
    signal; vLLM/tqdm progress bars do the same on stderr. Quiet those by
    default so the useful signal survives in the tail windows. Set
    ``ARGUS_SUBAGENT_QUIET_LOGS=0`` to keep the inherited verbosity untouched.
    """
    env = os.environ.copy()
    if os.environ.get(_QUIET_LOGS_ENV, "1").strip().lower() in {"0", "false", "no"}:
        return env
    # Force NCCL down from the inherited INFO default; respect explicit choices
    # for the others.
    env["NCCL_DEBUG"] = "WARN"
    env.setdefault("VLLM_LOGGING_LEVEL", "WARNING")
    env.setdefault("TQDM_DISABLE", "1")
    return env


# ---------------------------------------------------------------------------
# Process launch
# ---------------------------------------------------------------------------

def _launch_durable_command(
    *,
    task_id: str,
    run_id: str,
    command: str,
    cwd: str,
    stdout: Any,
    stderr: Any,
) -> "subprocess.Popen[Any]":
    """Launch a command whose exit status survives loss of its Python owner."""
    exit_path = _exit_status_path(task_id, run_id).resolve()
    temporary = exit_path.with_name(exit_path.name + ".tmp")
    wrapper = (
        'set +e\n'
        'bash -lc "$1"\n'
        'rc=$?\n'
        'printf "%s\\n" "$rc" > "$2"\n'
        'mv -f "$2" "$3"\n'
        'exit "$rc"\n'
    )
    return subprocess.Popen(
        ["bash", "-c", wrapper, "argus-durable-job", command, str(temporary), str(exit_path)],
        stdout=stdout,
        stderr=stderr,
        cwd=cwd,
        start_new_session=os.name != "nt",
        env=_child_env(),
    )


# ---------------------------------------------------------------------------
# Task record I/O
# ---------------------------------------------------------------------------

def _write_task(task_id: str, data: dict[str, Any]) -> None:
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    path = _registry_path(task_id)
    try:
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (json.JSONDecodeError, OSError):
        existing = None
    if isinstance(existing, dict):
        preserved_fields = {
            key: existing[key]
            for key in ("cpu_ids", "cpu_count", "cwd")
            if key not in data and key in existing
        }
        if preserved_fields:
            data = dict(data)
            data.update(preserved_fields)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _write_task_if_run_id(
    task_id: str,
    data: dict[str, Any],
    *,
    expected_run_id: str,
) -> bool:
    """Write only while *task_id* still names the expected run."""
    from ._cpu_admission import cpu_admission_lock  # noqa: PLC0415

    with cpu_admission_lock(Path.cwd()):
        current = _read_task(task_id)
        if current is None or str(current.get("run_id") or "") != expected_run_id:
            return False
        _write_task(task_id, data)
        return True


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


# ---------------------------------------------------------------------------
# Process liveness + exit sidecar
# ---------------------------------------------------------------------------

def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_exit_code(task_id: str, run_id: str | None = None) -> int | None:
    try:
        return int(
            _exit_status_path(task_id, run_id).read_text(encoding="utf-8").strip()
        )
    except (OSError, ValueError):
        return None


def reconcile_terminal_task(task_id: str, task: dict[str, Any]) -> dict[str, Any]:
    """Recover a terminal direct/supervised job after its worker owner died."""
    if task.get("state") not in {"starting", "preflight", "running"}:
        return task
    pid = int(task.get("pid") or 0)
    if pid and _is_pid_alive(pid):
        worker_pid = int(task.get("worker_pid") or 0)
        if worker_pid and not _is_pid_alive(worker_pid):
            task["owner_lost"] = True
            task["terminal_owner"] = "exit_sidecar_reconciler"
        return task
    run_id = str(task.get("run_id") or "") or None
    exit_code = _read_exit_code(task_id, run_id)
    if exit_code is None:
        task["state"] = "crashed"
        task["error"] = f"sub-agent process {pid} no longer running and no exit sidecar exists"
    else:
        task["state"] = "done" if exit_code == 0 else "error"
        task["exit_code"] = exit_code
        task["terminal_owner"] = "exit_sidecar_reconciler"
        task["owner_lost"] = True
        stdout_path = Path(str(task.get("stdout_log") or ""))
        stderr_path = Path(str(task.get("stderr_log") or ""))
        task["stdout_tail"] = _tail_file(stdout_path, 3000) if stdout_path else ""
        task["stderr_tail"] = _tail_file(stderr_path, 3000) if stderr_path else ""
    task["completed_at"] = time.time()
    _write_task(task_id, task)
    return task


# ---------------------------------------------------------------------------
# Lane routing
# ---------------------------------------------------------------------------

def _lane_of(task_id: str | None) -> str | None:
    """Team lane encoded as a ``<lane>::<id>`` task-id prefix, else None.

    Teammates in an agent team submit subagent tasks under a per-team lane so a
    parked discussion in one lane never blocks submits in another. Legacy task
    ids (no ``::``) carry no lane and keep the global behaviour.
    """
    if task_id and "::" in task_id:
        return task_id.split("::", 1)[0]
    return None


def _open_discussion_blockers(lane: str | None = None) -> list[dict[str, Any]]:
    """Tasks with a LIVE parked supervisor still waiting on the engineer.

    Liveness uses worker_pid-alive AND a fresh heartbeat (not pid alone), so a
    hung or dead supervisor, or PID reuse, never wedges new launches forever.

    When ``lane`` is given, only tasks in that lane are considered, so an agent
    team's parked teammate blocks only its own lane. ``lane=None`` scans every
    task (legacy global behaviour, preserved for non-team submits).
    """
    blockers: list[dict[str, Any]] = []
    now = time.time()
    for t in _list_tasks():
        if t.get("state") != "discussing":
            continue
        if lane is not None and _lane_of(t.get("task_id")) != lane:
            continue
        # The liveness pid for a parked discussion is the WORKER (the forked
        # process running the discussion loop), never the killed experiment pid —
        # falling back to that could false-block on PID reuse.
        wpid = t.get("worker_pid") or 0
        hb = t.get("last_heartbeat")
        # Require a numeric, fresh heartbeat. A record stuck in "discussing" with
        # no heartbeat (a worker that died before its first poll) must NOT wedge
        # the gate forever, so a missing heartbeat is treated as stale.
        fresh = isinstance(hb, (int, float)) and (now - hb < DISCUSSION_STALE_AFTER_S)
        alive = bool(wpid and _is_pid_alive(wpid))
        if alive and fresh:
            blockers.append(t)
    return blockers


# ---------------------------------------------------------------------------
# Run-dir resolution
# ---------------------------------------------------------------------------

def _run_dir_from_command(command: str) -> str | None:
    """Best-effort extract ``--run-dir <path>`` from a task command.

    Experiment/eval commands already carry ``--run-dir`` (the RunWriter output
    dir with progress.jsonl/status.json/summary.tsv). The engineer routinely
    forgets to ALSO pass it to ``subagent submit``, which left status/report
    blind to the structured signals -- the "black box" symptom. Parsing it back
    out of the command makes every such task observable without extra wiring.
    """
    if not command or "--run-dir" not in command:
        return None
    try:
        import shlex
        tokens = shlex.split(command)
    except ValueError:
        return None
    for i, tok in enumerate(tokens):
        if tok == "--run-dir" and i + 1 < len(tokens):
            return tokens[i + 1]
        if tok.startswith("--run-dir="):
            return tok.split("=", 1)[1]
    return None


def _effective_run_dir(task: dict[str, Any]) -> str | None:
    """Run dir for a task record, recovering it from the command if unstored.

    Tasks submitted before run_dir auto-capture (or whose terminal record
    dropped the field) still carry ``--run-dir`` in their command, so reads
    stay observable without a re-submit.

    A preflight-rejected task never launched: it intentionally has no run_dir,
    and recovering one from its ``--run-dir`` flag would surface stale metrics
    from a prior run of the same directory, so the command fallback is skipped.
    """
    if task.get("preflight") and not task.get("run_dir"):
        return None
    return task.get("run_dir") or _run_dir_from_command(task.get("command", ""))


# ---------------------------------------------------------------------------
# Structured run-signal readers (RunWriter contract)
# ---------------------------------------------------------------------------

def _read_status_json(base: Path) -> dict[str, Any]:
    """Read the RunWriter status.json (state/method/task_count/elapsed)."""
    path = base / "status.json"
    # WHY M0.7 full-sweep: status paths can be stale or permission-blocked;
    # status rendering must degrade to empty instead of crashing.
    try:
        status_exists = path.exists()
    except OSError:
        status_exists = False
    if not status_exists:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_summary_tsv(base: Path) -> list[dict[str, Any]]:
    """Parse aggregate rows from summary.tsv (the headline reward/score)."""
    path = base / "summary.tsv"
    # WHY M0.7 full-sweep: summary paths share the same stale-run-dir failure
    # mode as status/progress files, so treat inaccessible paths as absent.
    try:
        summary_exists = path.exists()
    except OSError:
        summary_exists = False
    if not summary_exists:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    if len(lines) < 2:
        return []
    header = lines[0].split("\t")
    rows: list[dict[str, Any]] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        cells = line.split("\t")
        row = dict(zip(header, cells))
        if row.get("row_kind") == "aggregate":
            rows.append(row)
    return rows


def _progress_summary(run_dir: str | None) -> dict[str, Any]:
    """Summarize a run directory so one `status` call answers 'alive & advancing'."""
    summary: dict[str, Any] = {}
    if not run_dir:
        return summary
    base = Path(run_dir)
    progress = base / "progress.jsonl"
    # WHY M0.7 full-sweep: status rendering must be best-effort for stale or
    # inaccessible run paths; Path.exists() itself can raise PermissionError.
    try:
        progress_exists = progress.exists()
    except OSError:
        progress_exists = False
    if progress_exists:
        try:
            lines = progress.read_text(encoding="utf-8").splitlines()
            summary["progress_rows"] = len(lines)
            if lines:
                try:
                    summary["last_progress"] = json.loads(lines[-1])
                except (ValueError, json.JSONDecodeError):
                    summary["last_progress"] = lines[-1][:200]
            try:
                summary["progress_age_seconds"] = round(time.time() - progress.stat().st_mtime, 1)
            except OSError:
                pass
        except OSError:
            pass
    results = base / "results.jsonl"
    # WHY M0.7 full-sweep: mirror the progress path guard so a missing or
    # permission-blocked run dir never crashes `subagent status`.
    try:
        results_exists = results.exists()
    except OSError:
        results_exists = False
    if results_exists:
        try:
            summary["result_rows"] = sum(1 for _ in results.open(encoding="utf-8"))
        except OSError:
            pass
    # Headline state + score: the numbers that turn a "black box" run into an
    # observable one. status.json gives run state/method; summary.tsv carries
    # the aggregate reward and completed/errored counts.
    status = _read_status_json(base)
    for key in ("state", "method"):
        if status.get(key) is not None:
            summary[key] = status[key]
    aggregates = _read_summary_tsv(base)
    if aggregates:
        metrics = []
        for row in aggregates:
            entry: dict[str, Any] = {}
            for src, dst, cast_name in (
                ("condition", "condition", "str"),
                ("dataset_id", "dataset", "str"),
                ("reward", "reward", "float"),
                ("n_total_trials", "total", "int"),
                ("n_completed_trials", "completed", "int"),
                ("n_errored_trials", "errored", "int"),
            ):
                val = row.get(src)
                if val in (None, ""):
                    continue
                try:
                    if cast_name == "float":
                        entry[dst] = float(str(val))
                    elif cast_name == "int":
                        entry[dst] = int(str(val))
                    else:
                        entry[dst] = str(val)
                except (TypeError, ValueError):
                    entry[dst] = val
            if entry:
                metrics.append(entry)
        if metrics:
            summary["metrics"] = metrics
    return summary


# ---------------------------------------------------------------------------
# Metric formatting
# ---------------------------------------------------------------------------

def _format_metric_line(summary: dict[str, Any]) -> str:
    """Compact one-line headline (state + reward + completed/total) or ''."""
    if not summary:
        return ""
    parts: list[str] = []
    if summary.get("state"):
        parts.append(str(summary["state"]))
    for m in summary.get("metrics", []):
        seg = []
        if "reward" in m:
            try:
                seg.append(f"reward={float(m['reward']):.4g}")
            except (TypeError, ValueError):
                seg.append(f"reward={m['reward']}")
        if "completed" in m and "total" in m:
            seg.append(f"{m['completed']}/{m['total']}")
        if m.get("errored"):
            seg.append(f"{m['errored']} err")
        if seg:
            label = m.get("dataset") or m.get("condition") or ""
            parts.append((f"{label} " if label else "") + " ".join(seg))
    if not summary.get("metrics") and summary.get("progress_rows"):
        parts.append(f"{summary['progress_rows']} progress rows")
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# JSONL event parsing + usage accounting
# ---------------------------------------------------------------------------

def _parse_codex_jsonl_events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw in (stdout or "").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _add_usage_totals(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    return (
        left[0] + right[0],
        left[1] + right[1],
        left[2] + right[2],
        left[3] + right[3],
    )


def _apply_supervisor_usage_fields(
    task: dict[str, Any],
    *,
    model: str,
    totals: tuple[int, int, int, int],
) -> dict[str, Any]:
    task["supervisor_usage_model"] = model
    task["supervisor_input_tokens"] = int(totals[0])
    task["supervisor_cached_input_tokens"] = int(totals[1])
    task["supervisor_output_tokens"] = int(totals[2])
    task["supervisor_reasoning_output_tokens"] = int(totals[3])
    return task


# ---------------------------------------------------------------------------
# Experiment history ledger
# ---------------------------------------------------------------------------

def _append_experiment_history(cwd: str, record: dict[str, Any]) -> None:
    """Idempotently append one experiment row to the project ledger.

    Dedup on ``run_id`` so retries / terminal-event reprocessing never double
    count. This is the durable, project-local memory a future engineer scans to
    learn why past runs succeeded or failed.
    """
    try:
        path = Path(cwd) / EXPERIMENT_HISTORY_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        rid = record.get("run_id")
        if rid and path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    if json.loads(line).get("run_id") == rid:
                        return  # already recorded
                except (json.JSONDecodeError, AttributeError):
                    continue
        with path.open("a", encoding="utf-8") as f:
            if fcntl is not None:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                except OSError:
                    pass
            f.write(json.dumps(record) + "\n")
            f.flush()
            if fcntl is not None:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
    except OSError:
        pass


def _persist_experiment_record(
    task_id: str,
    event: str,
    td: dict[str, Any],
    cwd: str,
    verdict_text: str = "",
) -> None:
    """Co-locate durable supervisor artifacts with the experiment + append the
    project ledger, so a future engineer can review why this run succeeded or
    failed long after the supervisor process exits. Pure plumbing: the supervisor
    codex authors the verdict prose; Python only writes files.
    """
    run_dir = td.get("run_dir")
    metrics = _progress_summary(_effective_run_dir(td)) or {}
    headline = ""
    for m in metrics.get("metrics", []) or []:
        if "reward" in m:
            label = m.get("dataset") or m.get("condition") or "aggregate"
            headline = f"{label} reward={m['reward']}"
            break
    record = {
        "run_id": td.get("run_id") or task_id,
        "task_id": task_id,
        "event": event,
        "state": td.get("state"),
        "command": td.get("command", ""),
        "run_dir": run_dir,
        "supervisor_concern": td.get("concern") or td.get("last_supervisor_concern", ""),
        "stop_reason": td.get("stop_reason", ""),
        "discussion_resolution": td.get("discussion_resolution", ""),
        "headline_metric": headline,
        "run_state": metrics.get("state", ""),
        "ts": time.time(),
    }
    _append_experiment_history(cwd, record)
    if not run_dir:
        return
    try:
        rp = Path(run_dir)
        rp.mkdir(parents=True, exist_ok=True)
        sup_log = td.get("supervisor_log")
        if sup_log and Path(sup_log).exists():
            (rp / "SUPERVISOR_LOG.jsonl").write_text(
                Path(sup_log).read_text(encoding="utf-8"), encoding="utf-8")
        # Lazy import to avoid circular dependency (_discussion_log imports
        # REGISTRY_DIR from this module).
        from ._discussion_log import _mirror_discussion_md  # noqa: PLC0415
        _mirror_discussion_md(task_id, run_dir)
        vt = (verdict_text or "").strip()
        if not vt:
            vt = (f"Event: {event}\n"
                  f"Concern: {record['supervisor_concern'] or 'none'}\n"
                  f"Stop reason: {record['stop_reason'] or 'n/a'}\n"
                  f"Resolution: {record['discussion_resolution'] or 'n/a'}\n"
                  f"Headline: {headline or 'n/a'}")
        (rp / "SUPERVISOR_VERDICT.md").write_text(
            f"# Supervisor verdict — {task_id} [{event}]\n\n{vt}\n", encoding="utf-8")
    except OSError:
        pass
