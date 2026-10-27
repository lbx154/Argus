"""What the model chooser shows for each model: price, speed, availability.

Read-only except :func:`probe_model`, which an operator starts explicitly and
which spends one tiny call recorded in the project's usage ledger like any
other call. Every figure comes from the price table or from calls this home
actually recorded; a model with no data says so instead of a guess.
"""
from __future__ import annotations

import json
import os
import statistics
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from ..core.pricing import copilot_usd_per_premium_request, model_price_for, quote_token_usage
from . import mission_items, project_state

# How far back calls count, and how many of this project's tasks the per-task
# estimate re-prices.
_WINDOW_S = 30 * 24 * 3600
_TASK_SAMPLE = 20
# Tail sizes: enough for weeks of calls without reading a large ledger whole.
_HOME_TAIL_BYTES = 400_000
_PROJECT_TAIL_BYTES = 2_000_000
_REASON_CHARS = 160

_probing: set[str] = set()
_probing_lock = threading.Lock()


def _tail_rows(path: Path, max_bytes: int) -> Iterator[dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - max_bytes)
            handle.seek(start)
            lines = handle.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return
    if start:
        lines = lines[1:]  # the first line of a mid-file read is a torn record
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield row


def _row_time(row: dict[str, Any]) -> float:
    ts = row.get("completed_at") or row.get("recorded_at") or 0
    return float(ts) if isinstance(ts, (int, float)) else 0.0


def _interrupted(row: dict[str, Any]) -> bool:
    # A call Argus itself stopped (operator cancel, shutdown) says nothing
    # about whether the model works.
    from ..core.usage import _is_argus_interrupt

    return _is_argus_interrupt(row.get("error"))


def _short_reason(error: str) -> str:
    line = next((part.strip() for part in error.splitlines() if part.strip()), "")
    return line[:_REASON_CHARS]


def _call_stats(global_root: Path, now: float) -> dict[str, dict[str, Any]]:
    """Per model: durations of calls that answered, and the latest outcome."""
    stats: dict[str, dict[str, Any]] = {}
    try:
        files = sorted((global_root / "projects").glob("*/usage.jsonl"))
    except OSError:
        return stats
    for path in files[:200]:
        for row in _tail_rows(path, _HOME_TAIL_BYTES):
            model = str(row.get("model") or "").strip()
            ts = _row_time(row)
            status = str(row.get("status") or "").lower()
            if not model or ts < now - _WINDOW_S or status == "denied" or _interrupted(row):
                continue
            entry = stats.setdefault(model, {"durations": [], "last": None})
            error = str(row.get("error") or "")
            answered = status == "completed" and not error
            duration = row.get("duration_ms")
            if answered and isinstance(duration, (int, float)) and duration > 0:
                entry["durations"].append(float(duration))
            if entry["last"] is None or ts > entry["last"]["at"]:
                entry["last"] = {
                    "state": "ok" if answered else "failed",
                    "at": ts,
                    "reason": "" if answered else _short_reason(error or status or "error"),
                }
    return stats


def _project_tasks(project_dir: Path) -> list[list[dict[str, Any]]]:
    """This project's latest tasks that recorded token counts, each as its calls."""
    tasks: dict[str, list[dict[str, Any]]] = {}
    for row in _tail_rows(project_dir / "usage.jsonl", _PROJECT_TAIL_BYTES):
        mission = str(row.get("mission_id") or "").strip()
        if not mission or row.get("input_tokens") is None or row.get("output_tokens") is None:
            continue
        tasks.setdefault(mission, []).append(row)
    latest = sorted(tasks.values(), key=lambda calls: max(_row_time(call) for call in calls))
    return latest[-_TASK_SAMPLE:]


def _task_estimate(model: str, tasks: list[list[dict[str, Any]]]) -> float | None:
    """Mean cost of the sampled tasks had every call been made with ``model``.

    Each recorded call is re-priced on its own, so long-context tiers apply
    per call exactly as the provider bills them.
    """
    if not tasks or model_price_for(model) is None:
        return None
    totals = []
    for calls in tasks:
        total = 0.0
        for call in calls:
            quote = quote_token_usage(
                model,
                input_tokens=call.get("input_tokens"),
                cached_input_tokens=call.get("cached_input_tokens"),
                cache_write_tokens=call.get("cache_write_tokens"),
                output_tokens=call.get("output_tokens"),
                reasoning_output_tokens=call.get("reasoning_output_tokens"),
            )
            if quote.cost_usd is None:
                return None
            total += quote.cost_usd
        totals.append(total)
    return sum(totals) / len(totals)


def model_choices(
    project_dir: Path | str,
    *,
    global_root: Path | str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """The chooser's rows: one per model the active backend can be given."""
    root = project_state.resolve_global_root(global_root)
    current = time.time() if now is None else now
    backend = mission_items._active_backend()[0]
    per_request = backend == "copilot"
    stats = _call_stats(root, current)
    tasks = [] if per_request else _project_tasks(Path(project_dir))
    options = mission_items.model_options(root)
    if backend != "pi" and any(option.get("source") == "configured" for option in options):
        # The harness catalog is Pi's list. Once the operator has named what
        # this provider serves, its entries are only noise for another backend.
        options = [option for option in options if option.get("source") != "catalog"]
    rows = []
    for option in options:
        model = option["model"]
        price = None if per_request else model_price_for(model)
        entry = stats.get(model) or {}
        durations = entry.get("durations") or []
        rows.append({
            "model": model,
            "source": option.get("source", ""),
            "default": bool(option.get("default")),
            "invalid": bool(option.get("invalid")),
            "price": None if price is None else {
                "input": price.input_usd_per_mtok,
                "cached_input": price.cached_input_usd_per_mtok,
                "output": price.output_usd_per_mtok,
            },
            "task_estimate_usd": None if per_request else _task_estimate(model, tasks),
            "latency_ms": statistics.median(durations) if durations else None,
            "latency_samples": len(durations),
            "availability": entry.get("last") or {"state": "unknown", "at": 0, "reason": ""},
        })
    return {
        "backend": backend,
        "billing": "premium_request" if per_request else "token",
        "premium_request_usd": copilot_usd_per_premium_request() if per_request else None,
        "task_sample": len(tasks),
        "options": rows,
    }


def probe_model(
    model: str,
    project_dir: Path | str,
    *,
    global_root: Path | str | None = None,
) -> dict[str, Any]:
    """One tiny real call to ``model`` on the active backend, billed to this project.

    Raises ``LookupError`` while the same model is already being tested, so a
    double click never pays twice.
    """
    from ..adapters.agent_cli_backend import AgentCliBackend
    from ..core.models import RunnerOptions
    from ..core.role_config import is_reasoning_model
    from ..core.run_gateway import run_exec

    model = (model or "").strip()
    if not model:
        raise ValueError("model is required")
    with _probing_lock:
        if model in _probing:
            raise LookupError(f"{model} is already being tested")
        _probing.add(model)
    started = time.monotonic()
    try:
        backend, executable = mission_items._active_backend()
        if not executable:
            raise ValueError(f"the {backend} command is not installed on this machine")
        runner = AgentCliBackend(
            backend=backend,
            runner_bin=executable,
            default_watchdog_soft_idle_seconds=15,
            default_watchdog_stalled_idle_seconds=45,
            default_watchdog_hard_idle_seconds=90,
        )
        runner.set_usage_context(
            project_root=Path(project_dir),
            global_root=project_state.resolve_global_root(global_root),
        )
        with tempfile.TemporaryDirectory(prefix="argus-model-probe-") as workdir:
            result = run_exec(
                runner,
                prompt="Reply with the single word OK.",
                resume_thread_id=None,
                options=RunnerOptions(
                    model=model,
                    reasoning_effort="low" if is_reasoning_model(model) else None,
                    working_dir=workdir,
                    sandbox_mode="read-only",
                    force_safe_mode=True,
                    disable_tools=True,
                    skip_git_repo_check=True,
                ),
                run_label="model-probe",
            )
        answered = (
            result.exit_code == 0 and not result.fatal_error
            and any(str(message).strip() for message in result.agent_messages)
        )
        error = "" if answered else _short_reason(
            str(result.fatal_error or "")
            or (result.stderr_lines[-1] if result.stderr_lines else "")
            or f"exit {result.exit_code}"
        )
        return {
            "model": model,
            "ok": answered,
            "error": error,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    finally:
        with _probing_lock:
            _probing.discard(model)


__all__ = ["model_choices", "probe_model"]
