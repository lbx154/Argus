"""Direct (unmonitored) execution: fork + Popen, no LLM.

Owns: codex wrapper for supervisor LLM calls, process termination, RL
detection, run-contract preflight, launch-flag parsing, and the direct
`_run_direct` dispatcher.
"""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from ...core.codex_usage import sum_token_counts
from ._registry import (
    _ZERO_USAGE_TUPLE,
    REGISTRY_DIR,
    _apply_supervisor_usage_fields,
    _exit_status_path,
    _launch_durable_command,
    _parse_codex_jsonl_events,
    _write_task,
)
from ._reporting import _alert_engineer
from ._text import (
    _codex_agent_messages,
    _codex_thread_id,
    _find_codex,
    _tail_file,
)

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

def _run_contract_preflight(command: str, cwd: str) -> tuple[bool, str]:
    """Deterministic provenance interlock for a ``scale=full`` RL launch.

    Refuses a full-scale launch that is not a faithful, feasibility-probed
    execution of the frozen ``research/RUN_CONTRACT.json`` (drift in LR / group
    size / steps / curriculum, or a missing/invalid feasibility packet). This is
    provenance/consistency enforcement, NOT a scientific verdict — adequacy stays
    with the L2 reviewer. Fail-soft: any unexpected error yields ``(False, "")``
    so a framework bug can never wedge a launch.
    """
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
        return rc.check_full_run_launch(
            contract_path=contract_path,
            packet_path=packet_path,
            knobs=knobs,
        )
    except Exception:
        return (False, "")


# ---------------------------------------------------------------------------
# Codex LLM runner (used by supervisor check and preflight)
# ---------------------------------------------------------------------------

def _run_codex_with_usage(
    prompt: str,
    model: str,
    cwd: str,
    thread_id: str | None = None,
    timeout: int = 120,
) -> tuple[list[str], str | None, tuple[int, int, int, int]]:
    """Run one (optionally resumed) codex turn; return messages/thread/usage.

    Persistent supervisor: when ``thread_id`` is given the turn RESUMES that
    codex session, so the supervisor carries its full run-observation history
    and the discussion transcript across checks instead of re-deriving context
    from scratch each call. The prompt is streamed via stdin so it never appears
    in process lists and multiline survives intact. If a resume yields nothing
    (an expired/missing session), it retries once on a fresh thread so a lost
    session never blinds the supervisor. Never raises — returns ([], thread_id)
    on error.
    """
    codex = _find_codex()
    from ...core.sandbox import codex_sandbox_args, codex_sandbox_env  # noqa: PLC0415

    def _exec(tid: str | None) -> subprocess.CompletedProcess[str]:
        cmd = [codex, "exec"]
        if tid:
            cmd.append("resume")
        cmd += ["--json", "-m", model, "--skip-git-repo-check",
                *codex_sandbox_args(working_dir=cwd)]
        if tid:
            cmd.append(tid)
        cmd.append("-")  # stream prompt via stdin
        return subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=timeout, cwd=cwd, env=codex_sandbox_env(),
        )

    try:
        result = _exec(thread_id)
    except Exception:
        return ([], thread_id, _ZERO_USAGE_TUPLE)
    msgs = _codex_agent_messages(result.stdout)
    usage = sum_token_counts(_parse_codex_jsonl_events(result.stdout))
    new_tid = _codex_thread_id(result.stdout) or thread_id
    if thread_id and not msgs:
        # Resume produced nothing — the session is likely gone. Retry once fresh
        # so the supervisor keeps working (a new thread is seeded by the caller's
        # prompt, which already includes the run signals).
        try:
            result = _exec(None)
            msgs = _codex_agent_messages(result.stdout)
            usage = sum_token_counts(_parse_codex_jsonl_events(result.stdout))
            new_tid = _codex_thread_id(result.stdout)
        except Exception:
            return ([], None, _ZERO_USAGE_TUPLE)
    return (msgs, new_tid, usage)


def _run_codex(
    prompt: str,
    model: str,
    cwd: str,
    thread_id: str | None = None,
    timeout: int = 120,
) -> tuple[list[str], str | None]:
    """Backward-compatible wrapper returning only ``(agent_messages, thread_id)``."""
    msgs, new_tid, _usage = _run_codex_with_usage(
        prompt,
        model,
        cwd,
        thread_id,
        timeout,
    )
    return msgs, new_tid


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
        proc.terminate()
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            proc.kill()
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
    timeout: int,
    cwd: str,
    run_dir: str | None = None,
) -> None:
    """Run command directly via Popen. No LLM involved."""
    log_dir = REGISTRY_DIR / f"{task_id}_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "stdout.log"
    stderr_path = log_dir / "stderr.log"

    start_time = time.time()
    try:
        with stdout_path.open("w") as out, stderr_path.open("w") as err:
            proc = _launch_durable_command(
                task_id=task_id,
                command=command,
                stdout=out,
                stderr=err,
                cwd=cwd,
            )
            running_task = _apply_supervisor_usage_fields({
                "state": "running", "task_id": task_id,
                "description": description, "command": command,
                "pid": proc.pid, "worker_pid": os.getpid(),
                "started_at": time.time(), "mode": "direct",
                "run_dir": run_dir,
                "exit_status_path": str(_exit_status_path(task_id).resolve()),
                "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
            }, model="", totals=_ZERO_USAGE_TUPLE)
            _write_task(task_id, running_task)
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Kill the whole process group, not just the shell: the command
                # runs with start_new_session=True, so a GPU trainer it spawned
                # would otherwise survive the timeout and leak the GPU.
                _terminate_proc(proc)
                td = {"state": "timeout", "task_id": task_id,
                    "description": description, "command": command,
                    "pid": proc.pid, "timeout_seconds": timeout,
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
            "task_id": task_id, "description": description,
            "command": command, "exit_code": proc.returncode,
            "elapsed_seconds": elapsed, "completed_at": time.time(),
            "pid": proc.pid, "mode": "direct",
            "run_dir": run_dir,
            "stdout_tail": stdout_tail, "stderr_tail": stderr_tail,
            "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
        }
        _apply_supervisor_usage_fields(td, model="", totals=_ZERO_USAGE_TUPLE)
        _write_task(task_id, td)
        _alert_engineer(task_id, "COMPLETED" if proc.returncode == 0 else "FAILED", td)

    except Exception as exc:
        td = {
            "state": "error", "task_id": task_id,
            "description": description, "command": command,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.time() - start_time, 1),
            "completed_at": time.time(), "mode": "direct",
            "run_dir": run_dir,
        }
        _apply_supervisor_usage_fields(td, model="", totals=_ZERO_USAGE_TUPLE)
        _write_task(task_id, td)
        _alert_engineer(task_id, "CRASHED", td)
