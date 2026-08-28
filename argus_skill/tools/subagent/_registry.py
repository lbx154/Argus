"""Registry/persistence layer for subagent task state on disk.

Owns: task-record JSON read/write, exit-sidecar, process-group launch,
child environment, lane routing, run-dir resolution, experiment-history
ledger, structured RunWriter signal readers, and usage accounting helpers.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from ...core.daemon_lock import is_pid_running
from ...core.evidence_ledger import EvidenceLedger
from ...core.portable_filename import (
    legacy_hashed_filename_components,
    portable_filename_component,
)
from ...core.process_identity import (
    capture_process_identity,
    process_identity_is_running,
)
from ._text import _tail_file

# ---------------------------------------------------------------------------
# Module-level constants (used across multiple subagent modules)
# ---------------------------------------------------------------------------

REGISTRY_DIR = Path(".argus_subagents")
SUPERVISOR_MODEL = "gpt-5.5"

# Reuse one persistent supervisor thread for at most this many checks,
# then rotate to a fresh thread seeded with a short summary so a multi-hour
# run never overflows the context window.
SUPERVISOR_THREAD_MAX_CHECKS = 12

# Append-only, project-local ledger of every supervised experiment so a future
# engineer mission can learn why past runs succeeded or failed.
EXPERIMENT_HISTORY_REL = "research/EXPERIMENT_HISTORY.jsonl"

# Internal accounting helpers used by the supervised worker.
_ZERO_USAGE_TUPLE = (0, 0, 0, 0)

_QUIET_LOGS_ENV = "ARGUS_SUBAGENT_QUIET_LOGS"


# ---------------------------------------------------------------------------
# Registry paths
# ---------------------------------------------------------------------------

def _task_file_component(task_id: str) -> str:
    return portable_filename_component(str(task_id), windows=os.name == "nt")


def _registry_root(registry_root: Path | str | None = None) -> Path:
    return REGISTRY_DIR if registry_root is None else Path(registry_root)


def _registry_path(
    task_id: str,
    *,
    registry_root: Path | str | None = None,
) -> Path:
    return _registry_root(registry_root) / f"{_task_file_component(task_id)}.json"


def _legacy_registry_paths(
    task_id: str,
    *,
    registry_root: Path | str | None = None,
) -> tuple[Path, ...]:
    root = _registry_root(registry_root)
    return tuple(
        root / f"{component}.json"
        for component in legacy_hashed_filename_components(task_id)
    )


def _task_record_paths(
    task_id: str,
    *,
    registry_root: Path | str | None = None,
) -> tuple[Path, ...]:
    return (
        _registry_path(task_id, registry_root=registry_root),
        *_legacy_registry_paths(task_id, registry_root=registry_root),
    )


def _unlink_task_records(task_id: str) -> None:
    for path in _task_record_paths(task_id):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(task, dict) and str(task.get("task_id") or "") == task_id:
            path.unlink(missing_ok=True)


def _exit_status_path(
    task_id: str,
    run_id: str | None = None,
    *,
    registry_root: Path | str | None = None,
) -> Path:
    name = f"exit_code.{run_id}" if run_id else "exit_code"
    return _registry_root(registry_root) / f"{_task_file_component(task_id)}_logs" / name


def _task_log_dir(task_id: str) -> Path:
    return REGISTRY_DIR / f"{_task_file_component(task_id)}_logs"


def _legacy_exit_status_paths(
    task_id: str,
    run_id: str | None = None,
    *,
    registry_root: Path | str | None = None,
) -> tuple[Path, ...]:
    name = f"exit_code.{run_id}" if run_id else "exit_code"
    root = _registry_root(registry_root)
    return tuple(
        root / f"{component}_logs" / name
        for component in legacy_hashed_filename_components(task_id)
    )


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
    # stdout is a file here, not a tty, so CPython block-buffers it and a long
    # experiment's progress stays invisible until 8KB accumulates or it exits.
    # One campaign held four GPUs for five hours behind a 0-byte stdout.log:
    # indistinguishable from a hang, and a crash would have taken the run with
    # no record of how far it got. This is observability, not verbosity, so it
    # applies even when the caller keeps the inherited log levels.
    env.setdefault("PYTHONUNBUFFERED", "1")
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
    env: dict[str, str] | None = None,
) -> "subprocess.Popen[Any]":
    """Launch a command whose exit status survives loss of its Python owner."""
    exit_path = _exit_status_path(task_id, run_id).resolve()
    exit_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = exit_path.with_name(exit_path.name + ".tmp")
    if os.name == "nt":
        wrapper = (
            "$__command = [Environment]::GetEnvironmentVariable('ARGUS_DURABLE_COMMAND', 'Process')\n"
            "$__tmp = [Environment]::GetEnvironmentVariable('ARGUS_DURABLE_TMP', 'Process')\n"
            "$__exit = [Environment]::GetEnvironmentVariable('ARGUS_DURABLE_EXIT', 'Process')\n"
            "$__rc = 1\n"
            "try {\n"
            "  & powershell.exe -NoProfile -NonInteractive "
            "-Command $__command\n"
            "  if ($null -ne $global:LASTEXITCODE) {\n"
            "    $__rc = [int]$global:LASTEXITCODE\n"
            "  } elseif ($?) {\n"
            "    $__rc = 0\n"
            "  } else {\n"
            "    $__rc = 1\n"
            "  }\n"
            "} catch {\n"
            "  Write-Error $_\n"
            "  $__rc = 1\n"
            "}\n"
            "[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($__exit)) | Out-Null\n"
            "[IO.File]::WriteAllText($__tmp, ([string]$__rc + [Environment]::NewLine), [Text.Encoding]::ASCII)\n"
            "Move-Item -LiteralPath $__tmp -Destination $__exit -Force\n"
            "exit $__rc\n"
        )
        child_env = _child_env() if env is None else dict(env)
        child_env.setdefault("PYTHONUTF8", "1")
        child_env.setdefault("PYTHONIOENCODING", "utf-8")
        child_env["ARGUS_DURABLE_COMMAND"] = command
        child_env["ARGUS_DURABLE_TMP"] = str(temporary)
        child_env["ARGUS_DURABLE_EXIT"] = str(exit_path)
        return subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                wrapper,
            ],
            stdout=stdout,
            stderr=stderr,
            cwd=cwd,
            env=child_env,
            creationflags=(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            ),
        )
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
        env=_child_env() if env is None else env,
    )


# ---------------------------------------------------------------------------
# Task record I/O
# ---------------------------------------------------------------------------

def _write_task(
    task_id: str,
    data: dict[str, Any],
    *,
    registry_root: Path | str | None = None,
) -> None:
    root = _registry_root(registry_root)
    root.mkdir(parents=True, exist_ok=True)
    path = _registry_path(task_id, registry_root=root)
    legacy_paths = _legacy_registry_paths(task_id, registry_root=root)
    existing = _read_task(task_id, registry_root=root)
    if existing is None and len(str(task_id).encode("utf-8")) > 120:
        raise ValueError("task_id exceeds 120 UTF-8 bytes")
    if isinstance(existing, dict):
        preserved_keys = [
            "cpu_ids",
            "cpu_count",
            "cwd",
            "timeout_seconds",
            "timeout_defaulted",
            "process_identity",
            "worker_process_identity",
        ]
        if str(existing.get("run_id") or "") == str(data.get("run_id") or ""):
            preserved_keys.extend([
                "resource_demand",
                "resource_grant_id",
                "resource_grant",
                "resource_enforcement",
                "resource_warning",
                "resource_ledger_root",
                "resource_owner",
            ])
        preserved_fields = {
            key: existing[key]
            for key in preserved_keys
            if key not in data and key in existing
        }
        if preserved_fields:
            data = dict(data)
            data.update(preserved_fields)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    for legacy in legacy_paths:
        try:
            legacy_task = json.loads(legacy.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            legacy_task = None
        if (
            legacy != path
            and isinstance(legacy_task, dict)
            and str(legacy_task.get("task_id") or "") == task_id
        ):
            legacy.unlink(missing_ok=True)


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


def _read_task(
    task_id: str,
    *,
    registry_root: Path | str | None = None,
) -> dict[str, Any] | None:
    records: list[tuple[int, bool, dict[str, Any]]] = []
    canonical = _registry_path(task_id, registry_root=registry_root)
    for path in _task_record_paths(task_id, registry_root=registry_root):
        if not path.exists():
            continue
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(task, dict) and str(task.get("task_id") or "") == task_id:
            try:
                modified = path.stat().st_mtime_ns
            except OSError:
                modified = 0
            records.append((modified, path == canonical, task))
    if not records:
        return None
    return max(records, key=lambda item: item[:2])[2]


def _list_tasks() -> list[dict[str, Any]]:
    if not REGISTRY_DIR.exists():
        return []
    tasks: dict[str, tuple[int, dict[str, Any]]] = {}
    for f in sorted(REGISTRY_DIR.glob("*.json")):
        if f.name.endswith(".tmp"):
            continue
        try:
            task = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(task, dict):
            task_id = str(task.get("task_id") or f.name)
            try:
                modified = f.stat().st_mtime_ns
            except OSError:
                modified = 0
            if task_id not in tasks or modified > tasks[task_id][0]:
                tasks[task_id] = (modified, task)
    return [task for _modified, task in tasks.values()]


# ---------------------------------------------------------------------------
# Process liveness + exit sidecar
# ---------------------------------------------------------------------------

def _is_pid_alive(pid: int) -> bool:
    return is_pid_running(pid)


def _identity_from_record(
    task: Mapping[str, Any],
    pid_field: str,
) -> Mapping[str, Any] | None:
    identity_fields = {
        "pid": ("process_identity", "command_process_identity"),
        "worker_pid": ("worker_process_identity",),
        "submitter_pid": ("submitter_process_identity",),
    }
    for field in identity_fields.get(pid_field, ()):
        identity = task.get(field)
        if isinstance(identity, Mapping):
            return identity
    if pid_field == "pid" and task.get("pid") == task.get("worker_pid"):
        worker_identity = task.get("worker_process_identity")
        if isinstance(worker_identity, Mapping):
            return worker_identity
    flat_fields = {
        "pid": ("pid_start_time_ticks", "start_time_ticks"),
        "worker_pid": ("worker_start_time_ticks",),
        "submitter_pid": ("submitter_start_time_ticks",),
    }
    for field in flat_fields.get(pid_field, ()):
        start_ticks = task.get(field)
        if start_ticks not in (None, ""):
            return {"pid": task.get(pid_field), "start_time_ticks": start_ticks}
    return None


def _recorded_process_alive(task: Mapping[str, Any], pid_field: str) -> bool:
    try:
        pid = int(task.get(pid_field) or 0)
    except (TypeError, ValueError):
        return False
    return process_identity_is_running(
        pid,
        _identity_from_record(task, pid_field),
        pid_is_running=_is_pid_alive,
    )


def _process_identity(pid: int) -> dict[str, Any]:
    return capture_process_identity(pid)


def _read_exit_code(
    task_id: str,
    run_id: str | None = None,
    *,
    registry_root: Path | str | None = None,
) -> int | None:
    for path in (
        _exit_status_path(task_id, run_id, registry_root=registry_root),
        *_legacy_exit_status_paths(
            task_id,
            run_id,
            registry_root=registry_root,
        ),
    ):
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
    return None


def reconcile_terminal_task(
    task_id: str,
    task: dict[str, Any],
    *,
    registry_root: Path | str | None = None,
) -> dict[str, Any]:
    """Recover a terminal direct/supervised job after its worker owner died."""
    if task.get("state") not in {"starting", "preflight", "waiting_resource", "running"}:
        return task
    pid = int(task.get("pid") or 0)
    run_id = str(task.get("run_id") or "") or None
    exit_code = _read_exit_code(task_id, run_id, registry_root=registry_root)
    if exit_code is None and pid and _recorded_process_alive(task, "pid"):
        worker_pid = int(task.get("worker_pid") or 0)
        if worker_pid and not _recorded_process_alive(task, "worker_pid"):
            task["owner_lost"] = True
            task["terminal_owner"] = "exit_sidecar_reconciler"
        return task
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
        task["stdout_tail"] = _tail_file(stdout_path, 3000)
        task["stderr_tail"] = _tail_file(stderr_path, 3000)
    task["completed_at"] = time.time()
    _write_task(task_id, task, registry_root=registry_root)
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

    Liveness uses the recorded worker process identity, so a dead supervisor or
    PID reuse never wedges new launches and long model turns remain legitimate.

    When ``lane`` is given, only tasks in that lane are considered, so an agent
    team's parked teammate blocks only its own lane. ``lane=None`` scans every
    task (legacy global behaviour, preserved for non-team submits).
    """
    blockers: list[dict[str, Any]] = []
    for t in _list_tasks():
        if t.get("state") != "discussing":
            continue
        if lane is not None and _lane_of(t.get("task_id")) != lane:
            continue
        # The liveness pid for a parked discussion is the WORKER (the forked
        # process running the discussion loop), never the killed experiment pid —
        # falling back to that could false-block on PID reuse.
        wpid = t.get("worker_pid") or 0
        alive = bool(wpid and _recorded_process_alive(t, "worker_pid"))
        if alive:
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
    except (OSError, ValueError):
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
                except ValueError:
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
    path = Path(cwd) / EXPERIMENT_HISTORY_REL
    run_id = str(record.get("run_id") or "").strip()
    if not run_id:
        encoded = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        run_id = f"event-{hashlib.sha256(encoded).hexdigest()[:20]}"
    EvidenceLedger(path).append_record(
        record_id=run_id,
        record_type="experiment",
        payload=record,
        preserve_existing=True,
    )


def append_experiment_correction(
    cwd: str,
    *,
    run_id: str,
    correction_id: str,
    relation: str,
    reason: str,
    evidence_refs: list[str] | tuple[str, ...] = (),
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add a traceable correction without rewriting the original run row."""
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        raise ValueError("run_id must be non-empty")
    return EvidenceLedger(Path(cwd) / EXPERIMENT_HISTORY_REL).append_correction(
        correction_id=correction_id,
        target_record_id=normalized_run_id,
        relation=relation,
        reason=reason,
        evidence_refs=evidence_refs,
        payload={
            "run_id": normalized_run_id,
            "event": "CORRECTION",
            "details": dict(details or {}),
        },
    )


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
    metrics = _progress_summary(_effective_run_dir(td))
    headline = ""
    for m in metrics.get("metrics", []):
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
    try:
        _append_experiment_history(cwd, record)
    except (OSError, TimeoutError, ValueError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        td["evidence_persistence_error"] = error
        _write_task(task_id, td)
        print(
            f"argus subagent: terminal result preserved, but experiment "
            f"history persistence failed for {task_id}: {error}",
            file=sys.stderr,
            flush=True,
        )
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
            f"# Supervisor verdict - {task_id} [{event}]\n\n{vt}\n", encoding="utf-8")
    except OSError:
        pass
