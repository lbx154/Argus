"""Supervised monitoring loop: fork + Popen + periodic LLM health checks.

Owns: per-check supervisor verdict, concern double-confirmation, early-stop
dispatch, and the `_run_supervised` entry point. Pre-launch config preflight
and health-adaptive interval backoff live in `_supervised_preflight.py` (split
out to keep both modules under the size target).

`_run_supervised` is decomposed into two private phase-helpers to keep each
function well under 350 lines while preserving exact semantics:

  _supervised_do_one_check   — one supervisor check + concern confirmation
  _supervised_handle_early_stop — write STOP, kill proc, discuss, persist
  _run_supervised            — outer shell: setup, preflight, launch, loop
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._direct_run import (
    _is_full_scale_rl,
    _looks_like_rl_training,
    _rl_collapse_guidance,
    _run_contract_preflight,
    _terminate_proc,
)
from ._discuss_run import _run_discussion
from ._discussion_log import _discussion_path, _reset_discussion
from ._experiment_preflight import (
    experiment_launch_preflight,
    release_experiment_launch_claim,
)
from ._llm import _run_supervisor_with_usage
from ._normalize import _clean_concern, _norm_decision
from ._normalize import _norm_health as _normalize_health
from ._registry import (
    _ZERO_USAGE_TUPLE,
    SUPERVISOR_INTERVAL_CAP,
    SUPERVISOR_THREAD_MAX_CHECKS,
    _add_usage_totals,
    _apply_supervisor_usage_fields,
    _exit_status_path,
    _launch_durable_command,
    _persist_experiment_record,
    _process_identity,
    _read_task,
    _task_log_dir,
    _write_task,
)
from ._reporting import _alert_engineer
from ._resource_admission import (
    ResourceLease,
    acquire_for_task,
    command_env,
    record_renewal_failure,
    yield_facts_for_task,
)
from ._supervised_preflight import _next_monitor_interval, _supervisor_preflight_with_usage
from ._text import _strip_code_fence, _tail_file

log = logging.getLogger(__name__)

_SUPERVISOR_FAILURE_THRESHOLD = 3


@dataclass(frozen=True)
class SupervisorCheck:
    """Result of one supervisor health check, including backend failures."""

    decision: str
    health: str
    concern: str
    thread_id: str | None
    usage: tuple[int, int, int, int]
    error: str | None


def _norm_health(value: object) -> str:
    """Normalize the five health values, otherwise returning ``"unknown"``.

    ``supervisor_unavailable`` means the model never answered. ``unknown`` is
    reserved for a model response whose health could not be determined.
    """
    token = str(value).strip().lower().replace("-", "_")
    if token == "supervisor_unavailable":
        return token
    return _normalize_health(value)

# ---------------------------------------------------------------------------
# Supervisor check (one LLM call + verdict parsing)
# ---------------------------------------------------------------------------

def _supervisor_check_with_usage(
    task_id: str,
    command: str,
    description: str,
    stdout_path: Path,
    stderr_path: Path,
    elapsed: float,
    check_number: int,
    model: str,
    cwd: str,
    run_dir: str | None = None,
    thread_id: str | None = None,
) -> SupervisorCheck:
    """Call codex to check training/eval progress.

    Returns a :class:`SupervisorCheck` where decision is
    ``continue`` / ``early_stop`` / ``save_checkpoint``, health is
    ``healthy`` / ``degrading`` / ``stuck`` / ``diverging`` / ``unknown`` /
    ``supervisor_unavailable``, and concern is a free-text note (possibly empty)
    the supervisor wants the engineer to re-discuss even when the run is
    progressing normally. ``supervisor_unavailable`` specifically means the
    backend never answered; ``unknown`` means it answered without usable health.

    ``thread_id`` resumes a persistent backend session so the supervisor keeps the
    whole run's observation history in context across checks; the (possibly new)
    thread id is returned for the next check.
    """
    stdout_tail = _tail_file(stdout_path, 2000)
    stderr_tail = _tail_file(stderr_path, 1000)

    # Structured run signals live in the run directory (experiment_io.RunWriter
    # contract). Resolve run_dir relative to the task cwd; fall back to cwd.
    if run_dir:
        signal_base = Path(run_dir)
        if not signal_base.is_absolute():
            signal_base = Path(cwd) / signal_base
    else:
        signal_base = Path(cwd)
    progress_tail = ""
    progress_path = signal_base / "progress.jsonl"
    if progress_path.exists():
        progress_tail = _tail_file(progress_path, 1500)
    status_tail = ""
    status_path = signal_base / "status.json"
    if status_path.exists():
        status_tail = _tail_file(status_path, 800)

    prompt = (
        f"You are a training/eval supervisor agent. Check #{check_number} on task '{task_id}'.\n"
        f"Task: {description}\n"
        f"Command: {command}\n"
        f"Running for: {elapsed:.0f}s\n\n"
        f"=== stdout (last 2000 chars) ===\n{stdout_tail}\n\n"
        f"=== stderr (last 1000 chars) ===\n{stderr_tail}\n\n"
    )
    if progress_tail:
        prompt += f"=== progress.jsonl (last 1500 chars) ===\n{progress_tail}\n\n"
    if status_tail:
        prompt += f"=== status.json ===\n{status_tail}\n\n"
    yield_requests = yield_facts_for_task(task_id)
    if yield_requests:
        prompt += (
            "=== resource yield requests (facts, never an automatic command) ===\n"
            + json.dumps(yield_requests, ensure_ascii=False, indent=2)
            + "\nSomeone asks for the card. Judge whether this run should checkpoint "
            "and release it or continue; a request alone never authorizes a kill. "
            "If continuing, give the Engineer a concrete reason to record with "
            "resource-ledger yield-response.\n\n"
        )

    prompt += (
        "Judge health by whatever signals appear — this may be supervised\n"
        "fine-tuning, RL (PPO/GRPO/RLVR), or a benchmark eval run:\n"
        "- SFT/pretrain: training loss should trend DOWN; watch for NaN/inf.\n"
        "- RL: the REWARD / return / score should trend UP. Watch KL divergence\n"
        "  not exploding, generation/response length not collapsing toward 0 or\n"
        "  blowing up, and outputs not degenerating (format collapse, repetition).\n"
        "  Do NOT treat a noisy/rising policy loss as failure — RL loss is not SFT loss.\n"
        "- Any run: watch for CUDA OOM, tracebacks, stalls (no new steps for a long\n"
        "  stretch), or throughput collapse.\n\n"
    )

    # Arm the supervisor with concrete RL-collapse criteria. This is reference
    # knowledge, not a hard rule engine: the decision below is still yours.
    rl_guidance = _rl_collapse_guidance()
    if rl_guidance:
        prompt += (
            "=== reference: when an RL run has COLLAPSED (read before deciding) ===\n"
            "Use this only when the run is RL post-training (PPO/GRPO/RLVR/DPO-style).\n"
            "It tells you which signals mean a dead learning signal vs. normal noise,\n"
            "and how to tell a transient early dip from a sustained tail-window\n"
            "collapse. It does not override your judgement; weigh it against what the\n"
            "actual logs above show.\n\n"
            f"{rl_guidance}\n\n"
            "=== end reference ===\n\n"
        )

    prompt += (
        "IMPORTANT — raising a 'concern' now STOPS the run immediately and opens a\n"
        "discussion with the engineer. So a concern is no longer a soft 'FYI' — it\n"
        "is a decision to HALT and re-plan. Only raise a concern when the run is\n"
        "genuinely not worth continuing as-is: a real anomaly or a flaw that makes\n"
        "the results invalid or the spend wasteful. Examples that DO warrant a\n"
        "stop: crash/traceback/OOM/NaN, reward or response-length collapse, KL\n"
        "blow-up, near-zero / near-chance results across the visible window,\n"
        "completions pinned at the cap (truncation/clipping invalidating outputs),\n"
        "a clearly wrong/too-small hyperparameter that wastes the run, degenerate\n"
        "or reward-hacked outputs. If the run is acceptable and progressing — even\n"
        "if not perfect, and even if you have a minor cosmetic note — leave\n"
        "'concern' EMPTY and continue; do NOT stop a healthy run over nitpicks.\n"
        "Use your own judgement on what is stop-worthy.\n\n"
        "When you DO raise a concern, be a hyperparameter engineer, not just an\n"
        "alarm. The launch Command above contains the run's actual hyperparameters\n"
        "(flags like --learning-rate, --num-generations, --max-completion-length,\n"
        "--kl-coef/--beta, --temperature, --max-steps, etc.). Read them, decide\n"
        "which specific flag(s) most likely caused the failure you see, and name\n"
        "them in the concern with a concrete suggested change, e.g. 'num_generations=2\n"
        "is too few for GRPO group contrast — try 4-8' or 'completions pinned at\n"
        "max_completion_length=256 — raise to 512'. A concern that only names the\n"
        "symptom ('reward collapsed') without pointing at a parameter or code cause\n"
        "the engineer can act on is half-done.\n\n"
        "Respond with EXACTLY one JSON object:\n"
        '{"decision": "continue" or "early_stop" or "save_checkpoint",\n'
        ' "reason": "one sentence explaining the decision",\n'
        ' "concern": "" or "1-2 sentences naming the stop-worthy anomaly AND the\n'
        '   specific launch-command flag/value (or code cause) to change before\n'
        '   relaunching",\n'
        ' "metrics": {"step": ..., "loss": ..., "reward": ..., "kl": ..., "resp_len": ...},\n'
        ' "health": "healthy" or "degrading" or "stuck" or "diverging"}\n\n'
        "Decision rules:\n"
        "- continue: signals look acceptable (loss down for SFT; reward up and KL stable for RL); concern EMPTY.\n"
        "- early_stop / non-empty concern: a stop-worthy anomaly above. Either one halts the run.\n"
        "- save_checkpoint: a notable improvement milestone reached.\n"
        "Only output the JSON, nothing else."
    )

    try:
        messages, thread_id, usage = _run_supervisor_with_usage(
            prompt,
            model,
            cwd,
            thread_id,
            timeout=120,
            run_label=f"subagent:{task_id}:health",
            mission_id=str((_read_task(task_id) or {}).get("run_id") or "") or None,
        )
        # codex emits JSONL; pull the assistant messages and accept the most
        # recent one that parses into a verdict (tolerates trailing chatter
        # after the JSON object the prompt asks for).
        for message in reversed(messages):
            try:
                data = json.loads(_strip_code_fence(message))
            except (json.JSONDecodeError, AttributeError):
                continue
            if isinstance(data, dict) and "decision" in data:
                return SupervisorCheck(
                    decision=_norm_decision(data.get("decision", "continue")),
                    health=_norm_health(data.get("health", "unknown")),
                    concern=_clean_concern(data.get("concern", "")),
                    thread_id=thread_id,
                    usage=usage,
                    error=None,
                )
        return SupervisorCheck(
            decision="continue",
            health="unknown",
            concern="",
            thread_id=thread_id,
            usage=usage,
            error=None,
        )
    except Exception as exc:  # noqa: BLE001 — supervision failure must not kill GPU work
        log.exception(
            "Supervisor health check unavailable for task %s check %s",
            task_id,
            check_number,
        )
        return SupervisorCheck(
            decision="continue",
            health="supervisor_unavailable",
            concern="",
            thread_id=thread_id,
            usage=_ZERO_USAGE_TUPLE,
            error=f"{type(exc).__name__}: {exc}",
        )


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
    run_dir: str | None = None,
    thread_id: str | None = None,
) -> tuple[str, str, str, str | None]:
    check = _supervisor_check_with_usage(
        task_id,
        command,
        description,
        stdout_path,
        stderr_path,
        elapsed,
        check_number,
        model,
        cwd,
        run_dir,
        thread_id,
    )
    return check.decision, check.health, check.concern, check.thread_id


# ---------------------------------------------------------------------------
# Private phase-helpers for _run_supervised
# ---------------------------------------------------------------------------

def _supervised_do_one_check(
    *,
    task_id: str,
    command: str,
    description: str,
    out: Any,
    err: Any,
    check_number: int,
    model: str,
    cwd: str,
    resolved_run_dir: str | None,
    start_time: float,
    stdout_path: Path,
    stderr_path: Path,
    supervisor_log: Path,
    supervisor_thread_id: str | None,
    supervisor_usage_totals: tuple[int, int, int, int],
) -> tuple[
    int,  # check_number (may have incremented for confirm re-check)
    str,  # decision
    str,  # health
    str,  # concern
    str | None,  # supervisor_thread_id
    tuple[int, int, int, int],  # supervisor_usage_totals
    bool,  # stop_now
]:
    """Perform one supervisor LLM check, optional concern confirmation, and
    task-record update.  Returns updated mutable state and whether to stop.

    The concern double-confirmation ensures a single misread does not kill a
    healthy run: when concern is non-empty but decision != early_stop, we ask
    the same LLM again immediately; only a confirmed concern triggers a stop.
    This is mechanical (re-ask), not encoded judgment — semantics unchanged.
    """
    out.flush()
    err.flush()
    elapsed = time.time() - start_time

    check = _supervisor_check_with_usage(
        task_id, command, description,
        stdout_path, stderr_path, elapsed, check_number,
        model, cwd, resolved_run_dir, supervisor_thread_id,
    )
    decision = check.decision
    health = check.health
    concern = check.concern
    supervisor_thread_id = check.thread_id
    supervisor_usage_totals = _add_usage_totals(
        supervisor_usage_totals,
        check.usage,
    )
    # Rotate the persistent supervisor thread every N checks so a multi-hour
    # run never overflows the backend context window; the next check seeds a
    # fresh thread from the current run signals.
    if check_number % SUPERVISOR_THREAD_MAX_CHECKS == 0:
        supervisor_thread_id = None

    # Log supervisor decision.
    entry = {
        "check": check_number, "elapsed_s": round(elapsed, 1),
        "decision": decision, "health": health,
        "concern": concern,
        "interval_s": 0, "timestamp": time.time(),
    }
    if health == "supervisor_unavailable":
        entry["supervisor_error"] = (
            check.error
            or "SupervisorUnavailable: no backend error detail was returned"
        )
    with supervisor_log.open("a") as sl:
        sl.write(json.dumps(entry) + "\n")

    # Update task with latest supervisor info.
    task = _read_task(task_id) or {}
    task["last_supervisor_check"] = check_number
    task["last_supervisor_decision"] = decision
    task["last_supervisor_health"] = health
    task["last_supervisor_concern"] = concern
    task["elapsed_seconds"] = round(elapsed, 1)
    _apply_supervisor_usage_fields(task, model=model, totals=supervisor_usage_totals)
    _write_task(task_id, task)

    # A non-empty concern is now a STOP decision: the supervisor only raises
    # one for a genuine, stop-worthy anomaly. Confirm with one immediate
    # re-check so a single misread does not kill a healthy run.
    stop_now = decision == "early_stop"
    if concern and not stop_now:
        check_number += 1
        confirmation = _supervisor_check_with_usage(
            task_id, command, description,
            stdout_path, stderr_path,
            time.time() - start_time, check_number,
            model, cwd, resolved_run_dir, supervisor_thread_id,
        )
        c_decision = confirmation.decision
        c_health = confirmation.health
        c_concern = confirmation.concern
        supervisor_thread_id = confirmation.thread_id
        supervisor_usage_totals = _add_usage_totals(
            supervisor_usage_totals,
            confirmation.usage,
        )
        confirmation_entry = {
            "check": check_number, "confirm_of": concern,
            "decision": c_decision, "health": c_health,
            "concern": c_concern, "timestamp": time.time(),
        }
        if c_health == "supervisor_unavailable":
            confirmation_entry["supervisor_error"] = (
                confirmation.error
                or "SupervisorUnavailable: no backend error detail was returned"
            )
        with supervisor_log.open("a") as sl:
            sl.write(json.dumps(confirmation_entry) + "\n")
        if c_concern or c_decision == "early_stop":
            stop_now = True
            concern = c_concern or concern
            health = c_health or health
            decision = "early_stop"
            task["last_supervisor_concern"] = concern
            task["last_supervisor_health"] = health
            _apply_supervisor_usage_fields(task, model=model, totals=supervisor_usage_totals)
            _write_task(task_id, task)
        else:
            # False alarm: the second read cleared it. Keep running, and clear
            # the stale concern from the task record so status/reporting does
            # not show a phantom anomaly.
            concern = ""
            health = c_health or health
            task["last_supervisor_concern"] = ""
            task["last_supervisor_health"] = health
            task["last_supervisor_decision"] = c_decision or decision
            _apply_supervisor_usage_fields(task, model=model, totals=supervisor_usage_totals)
            _write_task(task_id, task)

    return (
        check_number,
        decision,
        health,
        concern,
        supervisor_thread_id,
        supervisor_usage_totals,
        stop_now,
    )


def _supervised_handle_early_stop(
    *,
    task_id: str,
    run_id: str,
    command: str,
    description: str,
    proc: "subprocess.Popen[Any]",
    check_number: int,
    decision: str,
    health: str,
    concern: str,
    model: str,
    cwd: str,
    resolved_run_dir: str | None,
    start_time: float,
    supervisor_thread_id: str | None,
    supervisor_usage_totals: tuple[int, int, int, int],
    stdout_path: Path,
    stderr_path: Path,
    supervisor_log: Path,
) -> None:
    """Handle an early-stop: write STOP signal, kill proc, discuss, persist.

    Called when the supervisor decides to halt the run. Writes the STOP file
    into the run directory (experiment_io.RunWriter watches <run_dir>/STOP),
    kills the process group, sends the handoff report to the engineer inbox,
    parks in the discussion loop, and persists the experiment record.
    """
    # Write STOP into the run dir. Scope the flag to the run dir so a
    # per-run early-stop never drops a project-global STOP at cwd that could
    # poison unrelated runs or linger as stale root-owned cruft. Only fall
    # back to cwd when the run dir is unknown.
    stop_note = f"Early-stopped by supervisor at check #{check_number}\n"
    stop_file = Path(resolved_run_dir or cwd) / "STOP"
    try:
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.write_text(stop_note)
    except OSError:
        pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        _terminate_proc(proc)
    prior_task = _read_task(task_id) or {}
    guard_status_fields = {
        key: prior_task[key]
        for key in ("preflight", "provenance_interlock", "supervision")
        if isinstance(prior_task.get(key), str)
    }
    td = {
        "state": "discussing", "task_id": task_id, "run_id": run_id,
        "description": description, "command": command,
        "pid": proc.pid, "worker_pid": os.getpid(),
        "exit_code": proc.returncode,
        "elapsed_seconds": round(time.time() - start_time, 1),
        "completed_at": time.time(), "mode": "supervised",
        "last_heartbeat": time.time(),
        "supervisor_checks": check_number,
        "stop_reason": "supervisor early-stop",
        "concern": concern,
        "last_supervisor_health": health,
        "last_supervisor_decision": decision,
        "run_dir": resolved_run_dir,
        "supervisor_thread_id": supervisor_thread_id,
        "discussion_path": str(_discussion_path(task_id)),
        "stdout_tail": _tail_file(stdout_path, 3000),
        "stderr_tail": _tail_file(stderr_path, 3000),
        "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
        "supervisor_log": str(supervisor_log),
        **guard_status_fields,
    }
    _apply_supervisor_usage_fields(td, model=model, totals=supervisor_usage_totals)
    _write_task(task_id, td)
    # The handoff report tells the engineer the run is stopped and to reply on
    # the discussion thread; then we park and discuss.
    report = _alert_engineer(task_id, "EARLY-STOPPED", td)
    _run_discussion(
        task_id,
        td,
        model,
        cwd,
        resolved_run_dir,
        supervisor_thread_id,
        supervisor_usage_totals,
    )
    final_td = _read_task(task_id) or td
    _persist_experiment_record(
        task_id, "EARLY-STOPPED", final_td, cwd, report)


# ---------------------------------------------------------------------------
# Supervised execution entry point
# ---------------------------------------------------------------------------

def _run_supervised(
    task_id: str,
    command: str,
    description: str,
    timeout: int,
    monitor_interval: int,
    model: str,
    cwd: str,
    run_dir: str | None = None,
    preflight: bool = True,
) -> None:
    """Run command with periodic LLM supervisor checks."""
    log_dir = _task_log_dir(task_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "stdout.log"
    stderr_path = log_dir / "stderr.log"
    supervisor_log = log_dir / "supervisor.jsonl"
    # Stale transcript from a prior run of the same task-id must not leak into
    # this run's discussion.
    _reset_discussion(task_id)

    start_time = time.time()
    submitted_task = _read_task(task_id) or {}
    run_id = str(
        submitted_task.get("run_id")
        or f"{task_id}-{time.time_ns()}"
    )
    timeout_defaulted = bool(submitted_task.get("timeout_defaulted", False))
    timeout_fields = {
        "timeout_seconds": timeout,
        "timeout_defaulted": timeout_defaulted,
    }
    worker_identity = _process_identity(os.getpid())
    claim_owner = f"{run_id}:{os.getpid()}:{time.time_ns()}"
    supervisor_thread_id: str | None = None
    supervisor_usage_totals = _ZERO_USAGE_TUPLE
    guard_status_fields: dict[str, str] = {}
    resource_lease: ResourceLease | None = None
    # Resolve run_dir once relative to the task cwd so the supervisor reads the
    # right progress/status and writes STOP where RunWriter watches.
    resolved_run_dir: str | None = None
    if run_dir:
        base = Path(cwd).expanduser().resolve()
        rp = Path(run_dir).expanduser()
        resolved_run_dir = str(
            (rp if rp.is_absolute() else base / rp).resolve()
        )
    try:
        deterministic_reject, deterministic_concern = experiment_launch_preflight(
            task_id=task_id,
            command=command,
            cwd=cwd,
            run_dir=resolved_run_dir,
            claim_owner=claim_owner,
        )
        if deterministic_reject:
            td = {
                "state": "error",
                "task_id": task_id,
                "run_id": run_id,
                "description": description,
                "command": command,
                "error": deterministic_concern,
                "preflight": True,
                "worker_pid": os.getpid(),
                "worker_process_identity": worker_identity,
                "started_at": start_time,
                "completed_at": time.time(),
                "elapsed_seconds": 0.0,
                "mode": "supervised",
                "run_dir": resolved_run_dir,
                "supervisor_log": str(supervisor_log),
                **timeout_fields,
            }
            _apply_supervisor_usage_fields(
                td,
                model=model,
                totals=supervisor_usage_totals,
            )
            _write_task(task_id, td)
            report = _alert_engineer(task_id, "PREFLIGHT-REJECTED", td)
            _persist_experiment_record(
                task_id,
                "PREFLIGHT-REJECTED",
                td,
                cwd,
                report,
            )
            return
        # Pre-launch config preflight: hard-block a mechanically-unlearnable RL
        # config BEFORE spending any GPU, and hand the engineer the exact fix via
        # the same stop+discussion machinery a metric-based early-stop uses. Gated
        # to RL-ish commands and fail-soft, so it never blocks a normal launch.
        if preflight and _looks_like_rl_training(command):
            # Mark a distinct state so a duplicate submit during the (~30-60s)
            # LLM call sees this task as busy, not idle.
            preflight_task = _apply_supervisor_usage_fields({
                "state": "preflight", "task_id": task_id, "run_id": run_id,
                "description": description, "command": command,
                "worker_pid": os.getpid(), "pid": os.getpid(),
                "worker_process_identity": worker_identity,
                "started_at": start_time, "mode": "supervised",
                "run_dir": resolved_run_dir,
                "supervisor_log": str(supervisor_log),
                **timeout_fields,
            }, model=model, totals=supervisor_usage_totals)
            _write_task(task_id, preflight_task)
            # (A) Deterministic provenance interlock FIRST (cheap, no LLM): a
            # scale=full RL launch must faithfully execute the frozen, feasibility-
            # probed RUN_CONTRACT. (B) Then the LLM config preflight for
            # mechanically-degenerate configs. Either reject routes through the
            # same stop+discussion machinery below.
            reject, pf_concern = (False, "")
            if _is_full_scale_rl(command):
                reject, pf_concern, interlock_status = _run_contract_preflight(
                    command,
                    cwd,
                )
                if interlock_status:
                    guard_status_fields["provenance_interlock"] = interlock_status
                    preflight_task.update(guard_status_fields)
                    _write_task(task_id, preflight_task)
                    with supervisor_log.open("a") as sl:
                        sl.write(json.dumps({
                            "check": 0,
                            "provenance_interlock": interlock_status,
                            "timestamp": time.time(),
                        }) + "\n")
            if not reject:
                (
                    reject,
                    pf_concern,
                    raw_usage,
                    preflight_status,
                ) = _supervisor_preflight_with_usage(
                    task_id, command, description, model, cwd,
                )
                supervisor_usage_totals = _add_usage_totals(
                    supervisor_usage_totals,
                    raw_usage,
                )
                if preflight_status:
                    guard_status_fields["preflight"] = preflight_status
                    preflight_task.update(guard_status_fields)
                    _apply_supervisor_usage_fields(
                        preflight_task,
                        model=model,
                        totals=supervisor_usage_totals,
                    )
                    _write_task(task_id, preflight_task)
                    with supervisor_log.open("a") as sl:
                        sl.write(json.dumps({
                            "check": 0,
                            "preflight": preflight_status,
                            "timestamp": time.time(),
                        }) + "\n")
            if reject:
                with supervisor_log.open("a") as sl:
                    sl.write(json.dumps({
                        "check": 0, "preflight": True,
                        "decision": "early_stop", "health": "config_reject",
                        "concern": pf_concern, "timestamp": time.time(),
                    }) + "\n")
                # No run_dir on the record: nothing launched, so do not create a
                # phantom experiment directory. The discussion lives in the
                # registry and is reachable via discussion_path.
                td = {
                    "state": "discussing", "task_id": task_id, "run_id": run_id,
                    "description": description, "command": command,
                    "mode": "supervised", "preflight": True,
                    "worker_pid": os.getpid(),
                    "worker_process_identity": worker_identity,
                    "supervisor_checks": 0,
                    "stop_reason": "supervisor config preflight reject",
                    "concern": pf_concern,
                    "last_supervisor_health": "config_reject",
                    "last_supervisor_decision": "early_stop",
                    "started_at": start_time, "completed_at": time.time(),
                    "elapsed_seconds": 0.0,
                    # Heartbeat now so the forced-discussion gate sees a LIVE
                    # parked supervisor immediately — before _run_discussion's
                    # first loop heartbeat — closing the window where a duplicate
                    # submit could slip past the gate and launch GPU work.
                    "last_heartbeat": time.time(),
                    "discussion_path": str(_discussion_path(task_id)),
                    "supervisor_log": str(supervisor_log),
                    **guard_status_fields,
                    **timeout_fields,
                }
                _apply_supervisor_usage_fields(td, model=model, totals=supervisor_usage_totals)
                _write_task(task_id, td)
                report = _alert_engineer(task_id, "EARLY-STOPPED", td)
                _run_discussion(
                    task_id,
                    td,
                    model,
                    cwd,
                    None,
                    None,
                    supervisor_usage_totals,
                )
                final_td = _read_task(task_id) or td
                _persist_experiment_record(
                    task_id, "EARLY-STOPPED", final_td, cwd, report)
                return

        resource_lease = acquire_for_task(
            task_id,
            mode="supervised",
            project_root=Path.cwd(),
        )
        with stdout_path.open("w") as out, stderr_path.open("w") as err:
            proc = _launch_durable_command(
                task_id=task_id,
                run_id=run_id,
                command=command,
                stdout=out,
                stderr=err,
                cwd=cwd,
                env=command_env(resource_lease),
            )
            command_identity = _process_identity(proc.pid)
            current_interval = min(
                max(monitor_interval, 1),
                SUPERVISOR_INTERVAL_CAP,
            )
            next_check_at = min(
                time.time() + current_interval,
                start_time + timeout,
            )
            running_task = _apply_supervisor_usage_fields({
                "state": "running", "task_id": task_id, "run_id": run_id,
                "description": description, "command": command,
                "pid": proc.pid, "worker_pid": os.getpid(),
                "process_identity": command_identity,
                "worker_process_identity": worker_identity,
                "started_at": time.time(), "mode": "supervised",
                "monitor_interval": monitor_interval,
                "current_monitor_interval": current_interval,
                "next_check_at": next_check_at,
                "run_dir": resolved_run_dir,
                "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
                "supervisor_log": str(supervisor_log),
                "exit_status_path": str(
                    _exit_status_path(task_id, run_id).resolve()
                ),
                **guard_status_fields,
                **timeout_fields,
            }, model=model, totals=supervisor_usage_totals)
            _write_task(task_id, running_task)

            check_number = 0
            # Latest supervisor verdict, kept in scope for the terminal records
            # below (the loop may never run if the process exits immediately).
            decision, health, concern = "continue", "unknown", ""
            consecutive_supervisor_failures = 0
            supervision_alerted = False
            # Health-adaptive backoff: start at the configured interval (capped),
            # then double while healthy (save supervisor tokens), snap back to the
            # base interval the moment health degrades.
            while True:
                # Never wait past the hard timeout, even with a long interval.
                remaining = timeout - (time.time() - start_time)
                wait_for = min(current_interval, max(1, int(remaining)))
                try:
                    proc.wait(timeout=wait_for)
                    break  # Process exited
                except subprocess.TimeoutExpired:
                    pass  # Still running, do supervisor check
                if resource_lease is not None and not resource_lease.renew():
                    record_renewal_failure(task_id, resource_lease)

                elapsed = time.time() - start_time
                if elapsed > timeout:
                    _terminate_proc(proc)
                    td = {
                        "state": "timeout", "task_id": task_id, "run_id": run_id,
                        "description": description, "command": command,
                        "pid": proc.pid, "worker_pid": os.getpid(),
                        "process_identity": command_identity,
                        "worker_process_identity": worker_identity,
                        **timeout_fields,
                        "timeout_message": (
                            f"Hard timeout reached after {timeout} seconds"
                            + (
                                "; this was the default 2h limit because no "
                                "--timeout was supplied. Resubmit with "
                                "--timeout <seconds> for a longer run."
                                if timeout_defaulted
                                else "; this was the configured --timeout limit."
                            )
                        ),
                        "elapsed_seconds": round(elapsed, 1),
                        "completed_at": time.time(), "mode": "supervised",
                        "run_dir": resolved_run_dir,
                        "supervisor_log": str(supervisor_log),
                        **guard_status_fields,
                    }
                    _apply_supervisor_usage_fields(td, model=model, totals=supervisor_usage_totals)
                    _write_task(task_id, td)
                    report = _alert_engineer(task_id, "TIMEOUT", td)
                    _persist_experiment_record(task_id, "TIMEOUT", td, cwd, report)
                    return

                # Supervisor LLM check (+ optional concern confirmation).
                check_number += 1
                (
                    check_number,
                    decision,
                    health,
                    concern,
                    supervisor_thread_id,
                    supervisor_usage_totals,
                    stop_now,
                ) = _supervised_do_one_check(
                    task_id=task_id,
                    command=command,
                    description=description,
                    out=out,
                    err=err,
                    check_number=check_number,
                    model=model,
                    cwd=cwd,
                    resolved_run_dir=resolved_run_dir,
                    start_time=start_time,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    supervisor_log=supervisor_log,
                    supervisor_thread_id=supervisor_thread_id,
                    supervisor_usage_totals=supervisor_usage_totals,
                )

                if health == "supervisor_unavailable":
                    consecutive_supervisor_failures += 1
                    if (
                        consecutive_supervisor_failures
                        >= _SUPERVISOR_FAILURE_THRESHOLD
                        and not supervision_alerted
                    ):
                        supervision_alerted = True
                        guard_status_fields["supervision"] = "unavailable"
                        task = _read_task(task_id) or {}
                        task["supervision"] = "unavailable"
                        _write_task(task_id, task)
                        _alert_engineer(
                            task_id,
                            "SUPERVISION-UNAVAILABLE",
                            task,
                        )
                else:
                    consecutive_supervisor_failures = 0

                if stop_now:
                    _supervised_handle_early_stop(
                        task_id=task_id,
                        run_id=run_id,
                        command=command,
                        description=description,
                        proc=proc,
                        check_number=check_number,
                        decision=decision,
                        health=health,
                        concern=concern,
                        model=model,
                        cwd=cwd,
                        resolved_run_dir=resolved_run_dir,
                        start_time=start_time,
                        supervisor_thread_id=supervisor_thread_id,
                        supervisor_usage_totals=supervisor_usage_totals,
                        stdout_path=stdout_path,
                        stderr_path=stderr_path,
                        supervisor_log=supervisor_log,
                    )
                    return

                # Healthy: back off while healthy, tighten when degrading.
                current_interval = _next_monitor_interval(
                    health, current_interval, monitor_interval,
                )
                task = _read_task(task_id) or {}
                if (
                    str(task.get("run_id") or "") == run_id
                    and task.get("state") == "running"
                ):
                    task["current_monitor_interval"] = current_interval
                    task["next_check_at"] = min(
                        time.time() + current_interval,
                        start_time + timeout,
                    )
                    _write_task(task_id, task)

            # Process exited naturally.
            elapsed = round(time.time() - start_time, 1)
            stdout_tail = _tail_file(stdout_path, 3000)
            stderr_tail = _tail_file(stderr_path, 3000)
            td = {
                "state": "done" if proc.returncode == 0 else "error",
                "task_id": task_id, "run_id": run_id, "description": description,
                "command": command, "exit_code": proc.returncode,
                "elapsed_seconds": elapsed, "completed_at": time.time(),
                "pid": proc.pid, "worker_pid": os.getpid(), "mode": "supervised",
                "process_identity": command_identity,
                "worker_process_identity": worker_identity,
                "supervisor_checks": check_number,
                "concern": concern,
                "last_supervisor_health": health,
                "last_supervisor_decision": decision,
                "run_dir": resolved_run_dir,
                "stdout_tail": stdout_tail, "stderr_tail": stderr_tail,
                "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
                "supervisor_log": str(supervisor_log),
                **guard_status_fields,
                **timeout_fields,
            }
            _apply_supervisor_usage_fields(td, model=model, totals=supervisor_usage_totals)
            _write_task(task_id, td)
            event = "COMPLETED" if proc.returncode == 0 else "FAILED"
            report = _alert_engineer(task_id, event, td)
            _persist_experiment_record(task_id, event, td, cwd, report)

    except Exception as exc:
        td = {
            "state": "error", "task_id": task_id, "run_id": run_id,
            "description": description, "command": command,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.time() - start_time, 1),
            "completed_at": time.time(), "mode": "supervised",
            "worker_pid": os.getpid(),
            "worker_process_identity": worker_identity,
            "run_dir": resolved_run_dir,
            **guard_status_fields,
            **timeout_fields,
        }
        _apply_supervisor_usage_fields(td, model=model, totals=supervisor_usage_totals)
        _write_task(task_id, td)
        report = _alert_engineer(task_id, "CRASHED", td)
        _persist_experiment_record(task_id, "CRASHED", td, cwd, report)
    finally:
        if resource_lease is not None:
            resource_lease.release()
        release_experiment_launch_claim(
            task_id=task_id,
            cwd=cwd,
            run_dir=resolved_run_dir,
            claim_owner=claim_owner,
        )
