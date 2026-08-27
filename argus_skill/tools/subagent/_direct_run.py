"""Direct (unmonitored) execution: fork + Popen, no LLM.

Owns process termination, RL detection, run-contract preflight, launch-flag
parsing, and the direct `_run_direct` dispatcher.
"""
from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from ...daemon.state import (
    _terminate_windows_process_tree as terminate_windows_process_tree,
)
from ._experiment_preflight import (
    experiment_launch_preflight,
    release_experiment_launch_claim,
)
from ._registry import (
    _ZERO_USAGE_TUPLE,
    _apply_supervisor_usage_fields,
    _exit_status_path,
    _launch_durable_command,
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
)
from ._text import _tail_file

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RL detection and collapse-guidance helpers
# ---------------------------------------------------------------------------

# Built-in skill that arms the supervisor with concrete RL-collapse signatures.
_RL_COLLAPSE_SKILL_REL = "engineer/rl-training-collapse-diagnosis.md"
_RL_COLLAPSE_GUIDANCE_CACHE: str | None = None


def _strip_skill_frontmatter(text: str) -> str:
    """Drop a leading ``---`` YAML-ish frontmatter block from a skill markdown."""
    if text.startswith("---"):
        parts = text.split("\n---", 1)
        if len(parts) == 2:
            return parts[1].lstrip("\n")
    return text


def _rl_collapse_guidance() -> str:
    """Body of the RL-collapse-diagnosis skill, cached and fail-soft.

    Returns an empty string if the skill cannot be loaded for any reason so the
    supervisor never crashes just because a guidance file moved.
    """
    global _RL_COLLAPSE_GUIDANCE_CACHE
    if _RL_COLLAPSE_GUIDANCE_CACHE is not None:
        return _RL_COLLAPSE_GUIDANCE_CACHE
    text = ""
    try:
        path = (
            Path(__file__).resolve().parents[2]
            / "builtin_skills"
            / _RL_COLLAPSE_SKILL_REL
        )
        text = _strip_skill_frontmatter(path.read_text(encoding="utf-8")).strip()
    except Exception:
        text = ""
    _RL_COLLAPSE_GUIDANCE_CACHE = text
    return text


# Cheap gate: only spend a preflight LLM call when the launch command actually
# looks like RL / post-training. Non-RL supervised launches (evals, data prep,
# generic scripts) skip preflight so we never pay for or risk a false block on
# work the preflight has no opinion about.
_RL_TRAINING_HINTS = (
    "--num-generations", "--num_generations", "--rollouts", "--reward",
    "--kl", "--ref-model", "--ref_model", "--max-completion-length",
    "grpo", "rlvr", "rloo", "reinforce", "ppo", "train_rl",
    "train_rl_lora_adapter", "grpotrainer", "ppotrainer",
)


def _looks_like_rl_training(command: str) -> bool:
    """True when the command looks like an RL/post-training launch worth a
    pre-launch config preflight. Deliberately permissive — the preflight itself
    is conservative and only hard-blocks mechanically-degenerate configs."""
    if not command:
        return False
    c = command.lower()
    return any(tok in c for tok in _RL_TRAINING_HINTS)


# Aliases the same logical knob may appear under in a launch command.
_KNOB_ALIASES: dict[str, tuple[str, ...]] = {
    "lr": ("lr", "learning_rate"),
    "group_size": ("group_size", "num_generations", "rollouts", "rollout_n"),
    "total_steps": ("total_steps", "total_training_steps", "max_steps", "steps"),
    "batch_size": ("batch_size", "train_batch_size"),
    "model_id": ("model", "model_id", "model_path"),
    "curriculum_hash": ("curriculum_hash",),
    "run_contract": ("run_contract", "contract"),
    "feasibility_packet": ("feasibility_packet", "packet"),
}


def _flag(flags: dict[str, str], logical: str) -> str | None:
    for alias in _KNOB_ALIASES.get(logical, (logical,)):
        if alias in flags:
            return flags[alias]
    return None


def _is_full_scale_rl(command: str) -> bool:
    """True for an explicit ``--scale full`` RL training launch — the only case
    the deterministic RUN_CONTRACT interlock applies to. Pilots / smoke runs
    (scale != full) launch freely; only a full-scale run must cite a frozen,
    feasibility-probed contract."""
    if not _looks_like_rl_training(command):
        return False
    return _parse_launch_flags(command).get("scale", "").strip().lower() == "full"


# ---------------------------------------------------------------------------
# Launch-flag parser
# ---------------------------------------------------------------------------

def _parse_launch_flags(command: str) -> dict[str, str]:
    """Best-effort ``--flag value`` / ``--flag=value`` table from a shell command.

    Used only to show the preflight a normalized, structured view of the config
    (and to keep the raw, untrusted command clearly fenced). Never raises.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return {}
    flags: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--"):
            key = tok[2:]
            if "=" in key:
                k, _, v = key.partition("=")
                flags[k.replace("-", "_")] = v
            else:
                nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
                if nxt and not nxt.startswith("--"):
                    flags[key.replace("-", "_")] = nxt
                    i += 1
                else:
                    flags[key.replace("-", "_")] = "true"
        i += 1
    return flags


# ---------------------------------------------------------------------------
# Deterministic run-contract preflight
# ---------------------------------------------------------------------------

#: Mirrors ``skills.run_contract.DEFAULT_RUN_CONTRACT_PATH``. Duplicated because
#: the only caller needs it when that very import is what failed, so it cannot
#: read the constant from there. ``test_default_contract_path_stays_in_sync``
#: fails if the two ever drift.
_DEFAULT_CONTRACT_REL = "research/RUN_CONTRACT.json"


def _run_contract_preflight(command: str, cwd: str) -> tuple[bool, str, str]:
    """Deterministic provenance interlock for a ``scale=full`` RL launch.

    Refuses a full-scale launch that is not a faithful, feasibility-probed
    execution of the frozen ``research/RUN_CONTRACT.json`` (drift in LR / group
    size / steps / curriculum, or a missing/invalid feasibility packet). This is
    provenance/consistency enforcement, NOT a scientific verdict — adequacy stays
    with the L2 reviewer. An unreadable or malformed contract is itself a
    provenance failure and rejects the launch. Unexpected framework errors remain
    fail-soft, returning status ``"skipped"`` so they cannot wedge a launch but
    also cannot be mistaken for a completed interlock.
    """
    # Rebound to the resolved path once the command's ``--run-contract`` flag has
    # been read; until then the default is the only honest thing to name.
    contract_path: Path | None = None
    try:
        from ...skills import run_contract as rc  # noqa: PLC0415

        flags = _parse_launch_flags(command)

        def _to_float(v: str | None) -> float | None:
            try:
                return float(v) if v is not None else None
            except ValueError:
                return None

        def _to_int(v: str | None) -> int | None:
            try:
                return int(float(v)) if v is not None else None
            except ValueError:
                return None

        knobs = rc.LaunchKnobs(
            lr=_to_float(_flag(flags, "lr")),
            group_size=_to_int(_flag(flags, "group_size")),
            total_steps=_to_int(_flag(flags, "total_steps")),
            batch_size=_to_int(_flag(flags, "batch_size")),
            model_id=_flag(flags, "model_id"),
            curriculum_hash=_flag(flags, "curriculum_hash"),
        )
        base = Path(cwd)
        contract_rel = _flag(flags, "run_contract") or rc.DEFAULT_RUN_CONTRACT_PATH
        contract_path = Path(contract_rel)
        if not contract_path.is_absolute():
            contract_path = base / contract_path
        packet_rel = _flag(flags, "feasibility_packet")
        packet_path: Path | None = None
        if packet_rel:
            packet_path = Path(packet_rel)
            if not packet_path.is_absolute():
                packet_path = base / packet_path
        reject, concern = rc.check_full_run_launch(
            contract_path=contract_path,
            packet_path=packet_path,
            knobs=knobs,
        )
        return reject, concern, ""
    except (OSError, ValueError) as exc:
        # The contract itself is unreadable or does not say what a contract has
        # to say — ``run_contract`` raises ValueError for exactly that (a
        # non-object payload, an empty materialized curriculum). Not being able
        # to read the provenance record IS a provenance failure, so reject and
        # name the file the engineer has to fix.
        named = contract_path or f"{cwd}/{_DEFAULT_CONTRACT_REL}"
        return (
            True,
            f"provenance contract {named} is unreadable or malformed: "
            f"{type(exc).__name__}: {exc}",
            "",
        )
    except Exception:  # noqa: BLE001 — framework bugs must not wedge a launch
        # TypeError / KeyError / anything else escaping here is a bug in this
        # harness, not a statement about the contract. Blocking a legitimate
        # launch on our own defect is the wrong trade, so stay fail-soft — but
        # say so, and let the caller record ``skipped`` on the run so nobody
        # later reads this launch as provenance-checked.
        log.exception("Run-contract provenance interlock could not run")
        return (False, "", "skipped")


# ---------------------------------------------------------------------------
# Process termination
# ---------------------------------------------------------------------------

def _terminate_proc(proc: "subprocess.Popen[Any]", grace: float = 10.0) -> None:
    """Stop a run's whole process group, escalating SIGTERM -> SIGKILL.

    Run commands launch with ``start_new_session=True``, so the GPU training
    children share ``proc.pid`` as their process-group leader. Killing the GROUP
    (not just the shell) is what actually frees VRAM on an early-stop/timeout;
    terminating only the shell can orphan the trainer and leak the GPU.
    """
    if proc.poll() is not None:
        return
    if os.name == "nt":
        pid = proc.pid
        if pid > 0:
            tree_stopped = terminate_windows_process_tree(
                pid,
                identity_check=lambda: proc.pid == pid and proc.poll() is None,
            )
            if tree_stopped:
                try:
                    proc.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
                return
        proc.terminate()
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


# ---------------------------------------------------------------------------
# Direct execution entry point
# ---------------------------------------------------------------------------

def _run_direct(
    task_id: str,
    command: str,
    description: str,
    timeout: int | None,
    cwd: str,
    run_dir: str | None = None,
) -> None:
    """Run command directly via Popen. No LLM involved."""
    log_dir = _task_log_dir(task_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "stdout.log"
    stderr_path = log_dir / "stderr.log"

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
    resource_lease: ResourceLease | None = None
    try:
        rejected, concern = experiment_launch_preflight(
            task_id=task_id,
            command=command,
            cwd=cwd,
            run_dir=run_dir,
            claim_owner=claim_owner,
        )
        if rejected:
            td = {
                "state": "error",
                "task_id": task_id,
                "run_id": run_id,
                "description": description,
                "command": command,
                "error": concern,
                "preflight": True,
                "elapsed_seconds": round(time.time() - start_time, 1),
                "completed_at": time.time(),
                "mode": "direct",
                "worker_pid": os.getpid(),
                "worker_process_identity": worker_identity,
                "run_dir": run_dir,
                **timeout_fields,
            }
            _apply_supervisor_usage_fields(td, model="", totals=_ZERO_USAGE_TUPLE)
            _write_task(task_id, td)
            _alert_engineer(task_id, "PREFLIGHT-REJECTED", td)
            return
        resource_lease = acquire_for_task(
            task_id,
            mode="direct",
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
            running_task = _apply_supervisor_usage_fields({
                "state": "running", "task_id": task_id,
                "run_id": run_id,
                "description": description, "command": command,
                "pid": proc.pid, "worker_pid": os.getpid(),
                "process_identity": command_identity,
                "worker_process_identity": worker_identity,
                "started_at": time.time(), "mode": "direct",
                "run_dir": run_dir,
                "exit_status_path": str(
                    _exit_status_path(task_id, run_id).resolve()
                ),
                "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
                **timeout_fields,
            }, model="", totals=_ZERO_USAGE_TUPLE)
            _write_task(task_id, running_task)
            try:
                if timeout is None and resource_lease is None:
                    proc.wait()
                elif resource_lease is None:
                    proc.wait(timeout=timeout)
                else:
                    deadline = (
                        time.monotonic() + timeout
                        if timeout is not None
                        else None
                    )
                    renew_every = max(1.0, resource_lease.ttl_seconds / 3.0)
                    while True:
                        remaining = (
                            deadline - time.monotonic()
                            if deadline is not None
                            else renew_every
                        )
                        if deadline is not None and remaining <= 0:
                            raise subprocess.TimeoutExpired(proc.args, timeout)
                        try:
                            proc.wait(timeout=min(renew_every, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            if not resource_lease.renew():
                                record_renewal_failure(task_id, resource_lease)
            except subprocess.TimeoutExpired:
                # Kill the whole process group, not just the shell: the command
                # runs with start_new_session=True, so a GPU trainer it spawned
                # would otherwise survive the timeout and leak the GPU.
                _terminate_proc(proc)
                td = {"state": "timeout", "task_id": task_id,
                    "run_id": run_id,
                    "description": description, "command": command,
                    "pid": proc.pid, "worker_pid": os.getpid(),
                    "process_identity": command_identity,
                    "worker_process_identity": worker_identity,
                    **timeout_fields,
                    "timeout_message": (
                        f"Hard timeout reached after {timeout} seconds; "
                        "this was the configured --timeout limit."
                    ),
                    "elapsed_seconds": round(time.time() - start_time, 1),
                    "completed_at": time.time(), "mode": "direct",
                    "run_dir": run_dir,
                    "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
                }
                _apply_supervisor_usage_fields(td, model="", totals=_ZERO_USAGE_TUPLE)
                _write_task(task_id, td)
                _alert_engineer(task_id, "TIMEOUT", td)
                return

        elapsed = round(time.time() - start_time, 1)
        stdout_tail = _tail_file(stdout_path, 3000)
        stderr_tail = _tail_file(stderr_path, 3000)
        td = {
            "state": "done" if proc.returncode == 0 else "error",
            "task_id": task_id, "run_id": run_id, "description": description,
            "command": command, "exit_code": proc.returncode,
            "elapsed_seconds": elapsed, "completed_at": time.time(),
            "pid": proc.pid, "worker_pid": os.getpid(), "mode": "direct",
            "process_identity": command_identity,
            "worker_process_identity": worker_identity,
            "run_dir": run_dir,
            "stdout_tail": stdout_tail, "stderr_tail": stderr_tail,
            "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
            **timeout_fields,
        }
        _apply_supervisor_usage_fields(td, model="", totals=_ZERO_USAGE_TUPLE)
        _write_task(task_id, td)
        _alert_engineer(task_id, "COMPLETED" if proc.returncode == 0 else "FAILED", td)

    except Exception as exc:
        td = {
            "state": "error", "task_id": task_id,
            "run_id": run_id,
            "description": description, "command": command,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.time() - start_time, 1),
            "completed_at": time.time(), "mode": "direct",
            "worker_pid": os.getpid(),
            "worker_process_identity": worker_identity,
            "run_dir": run_dir,
            **timeout_fields,
        }
        _apply_supervisor_usage_fields(td, model="", totals=_ZERO_USAGE_TUPLE)
        _write_task(task_id, td)
        _alert_engineer(task_id, "CRASHED", td)
    finally:
        if resource_lease is not None:
            resource_lease.release()
        release_experiment_launch_claim(
            task_id=task_id,
            cwd=cwd,
            run_dir=run_dir,
            claim_owner=claim_owner,
        )
