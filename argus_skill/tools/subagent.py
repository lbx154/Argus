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
import shlex
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
   reads the log tails every N seconds and can intervene (early-stop,
   save checkpoint, flag anomaly). It is RL-aware (judges reward/KL/response
   length, not just SFT loss) and backs off while healthy to save tokens.
   Best for long GPU training where you want an agent watching the run.

Usage from the engineer:

    # Direct mode — just run the command (no LLM cost)
    python -m argus_skill.tools.subagent submit \
      --task-id eval-geneval \
      --description "Evaluate zImage on GenEval" \
      --command ".venv/bin/python code/eval.py --benchmark geneval"

    # Supervised mode — RL-aware monitoring, base interval 120s, backs off
    # while healthy and snaps back when health degrades. Point --run-dir at
    # the experiment_io run directory so the supervisor reads progress/status
    # and writes STOP there on early-stop.
    python -m argus_skill.tools.subagent submit \
      --task-id train-grpo \
      --mode supervised \
      --monitor-interval 120 \
      --run-dir experiments/train-grpo \
      --description "Train policy with GRPO (veRL)" \
      --command ".venv/bin/python code/train.py --config grpo_config.yaml"

    # Check status
    python -m argus_skill.tools.subagent status --task-id train-grpo

    # List all
    python -m argus_skill.tools.subagent list
"""


REGISTRY_DIR = Path(".argus_subagents")
SUPERVISOR_MODEL = "gpt-5.5"
SUPERVISOR_INTERVAL_CAP = 900


def _next_monitor_interval(
    health: str,
    current: int,
    base: int,
    cap: int = SUPERVISOR_INTERVAL_CAP,
) -> int:
    """Health-adaptive polling backoff for the supervisor.

    Healthy training is boring, so back off exponentially to save supervisor
    tokens. Any non-healthy signal pulls the interval back to ``base`` so the
    supervisor looks closely while things are interesting.
    """
    base = max(int(base), 1)
    cap = max(int(cap), base)
    current = max(int(current), base)
    if health in {"degrading", "stuck", "diverging"}:
        return base
    if health == "healthy":
        return min(current * 2, cap)
    # unknown / parse failure: hold steady within bounds.
    return min(current, cap)


_VALID_DECISIONS = {"continue", "early_stop", "save_checkpoint"}
_VALID_HEALTH = {"healthy", "degrading", "stuck", "diverging"}
_HEALTH_ALIASES = {
    "degraded": "degrading",
    "diverged": "diverging",
    "diverge": "diverging",
    "stalling": "stuck",
    "stalled": "stuck",
    "stall": "stuck",
    "ok": "healthy",
    "good": "healthy",
}


def _norm_decision(value: object) -> str:
    """Normalize a supervisor decision, defaulting to the safe ``continue``."""
    token = str(value).strip().lower().replace("-", "_")
    return token if token in _VALID_DECISIONS else "continue"


def _norm_health(value: object) -> str:
    """Normalize a health label, mapping common variants; else ``unknown``."""
    token = str(value).strip().lower().replace("-", "_")
    token = _HEALTH_ALIASES.get(token, token)
    return token if token in _VALID_HEALTH else "unknown"


_EMPTY_CONCERNS = {
    "", "none", "n/a", "na", "null", "nil", "-", "no concern",
    "no concerns", "nothing", "no issues", "no issue",
}
_EMPTY_CONCERN_PREFIXES = (
    "no concern", "no issue", "nothing notewor", "nothing to report",
    "nothing of note", "all good", "all healthy", "looks healthy",
    "no anomal", "no problem",
)


def _clean_concern(value: object) -> str:
    """Normalize a supervisor concern note; empty when nothing noteworthy.

    The supervisor may flag any anomaly worth the engineer re-discussing even
    when the run is healthy. Treat the common "nothing to report" phrasings as
    empty so they do not spam the engineer's inbox.
    """
    text = " ".join(str(value or "").split())
    low = text.lower().strip(".")
    if low in _EMPTY_CONCERNS or low.startswith(_EMPTY_CONCERN_PREFIXES):
        return ""
    return text[:600]


def _concern_signature(concern: str) -> str:
    """Stable dedup key for a concern: lowercase letters only.

    Strips digits/punctuation so the same issue rephrased with changing step
    numbers, elapsed times, or metric values dedups to one engineer alert.
    """
    return " ".join(
        "".join(c for c in concern.lower() if c.isalpha() or c.isspace()).split()
    )


def _supervisor_summarize_report(task_id: str, event: str, task_data: dict[str, Any]) -> str:
    """Have the supervisor author its own handoff report to the engineer.

    The supervisor watched the run and (for an early-stop or concern) decided
    why it intervened, so it — not a separate, signal-blind summarizer — writes
    the report AND the next step. Its own diagnosis (concern / stop_reason /
    verdict trail) is fed in alongside the run signals so the next step follows
    from the reason instead of defaulting to a mechanical "remove the STOP file
    and rerun".
    """
    codex = _find_codex()
    stdout_tail = task_data.get("stdout_tail", "")[-2000:]
    stderr_tail = task_data.get("stderr_tail", "")[-1000:]
    elapsed = task_data.get("elapsed_seconds", 0)
    command = task_data.get("command", "")
    description = task_data.get("description", "")
    exit_code = task_data.get("exit_code", "N/A")
    checks = task_data.get("supervisor_checks", 0)
    concern = task_data.get("concern", "") or task_data.get("last_supervisor_concern", "")
    stop_reason = task_data.get("stop_reason", "")
    decision = task_data.get("last_supervisor_decision", "")
    health = task_data.get("last_supervisor_health", "")

    # Structured run signals — the same clean channel the periodic checks read.
    run_dir = _effective_run_dir(task_data)
    progress_tail = status_tail = ""
    if run_dir:
        base = Path(run_dir)
        if (base / "progress.jsonl").exists():
            progress_tail = _tail_file(base / "progress.jsonl", 1200)
        if (base / "status.json").exists():
            status_tail = _tail_file(base / "status.json", 800)
    sup_log = task_data.get("supervisor_log", "")
    verdict_tail = ""
    if sup_log and Path(sup_log).exists():
        verdict_tail = _tail_file(Path(sup_log), 800)

    prompt = (
        "You are the supervisor agent that has monitored this GPU run from the\n"
        "start. You make the call here and you write the handoff report that goes\n"
        "to the engineer — speak in the first person as the supervisor.\n\n"
        f"Task: {task_id}\n"
        f"Description: {description}\n"
        f"Event: {event}\n"
        f"Command: {command}\n"
        f"Duration: {elapsed:.0f}s | Exit code: {exit_code}\n"
    )
    if checks:
        prompt += f"Supervisor checks: {checks}\n"
    if decision:
        prompt += f"Your last decision: {decision} | health: {health}\n"
    if stop_reason:
        prompt += f"Mechanical stop reason: {stop_reason}\n"
    if concern:
        prompt += (
            "YOUR DIAGNOSIS (authoritative — this is WHY; ground the report and "
            f"next step in it): {concern}\n"
        )
    if verdict_tail:
        prompt += f"\n=== your recent verdicts (supervisor.jsonl tail) ===\n{verdict_tail}\n"
    if progress_tail:
        prompt += f"\n=== progress.jsonl (tail) ===\n{progress_tail}\n"
    if status_tail:
        prompt += f"\n=== status.json ===\n{status_tail}\n"
    prompt += f"\n=== stdout (last 2000 chars) ===\n{stdout_tail}\n"
    if stderr_tail and event != "COMPLETED":
        prompt += f"\n=== stderr (last 1000 chars) ===\n{stderr_tail}\n"

    log_dir = REGISTRY_DIR / f"{task_id}_logs"
    prompt += (
        f"\nArtifact paths:\n"
        f"- stdout: {task_data.get('stdout_log', str(log_dir / 'stdout.log'))}\n"
        f"- stderr: {task_data.get('stderr_log', str(log_dir / 'stderr.log'))}\n"
        f"- task record: {_registry_path(task_id)}\n"
    )
    if sup_log:
        prompt += f"- supervisor log: {sup_log}\n"
    if run_dir:
        prompt += f"- run dir: {run_dir}\n"

    prompt += (
        "\nWrite your handoff report to the engineer in markdown:\n"
        "1. One sentence: what happened and — if you stopped or flagged it — WHY,\n"
        "   grounded in your diagnosis above. Do NOT reduce an early-stop to 'a STOP\n"
        "   file appeared'; say what was actually wrong.\n"
        "2. Key metrics from the signals (reward, loss, steps, clipped_ratio,\n"
        "   response/completion length, KL, etc.).\n"
        "3. Artifact paths the engineer should inspect.\n"
        "4. The concrete next step that FOLLOWS FROM YOUR DIAGNOSIS. If you stopped\n"
        "   for a quality issue (truncation/clipping, reward collapse, degenerate\n"
        "   outputs), the next step must address that root cause — do not default to\n"
        "   rerunning unchanged. If the run is healthy/complete, say how to use it.\n"
        "Keep it under 300 words. Be direct and actionable."
    )

    try:
        result = subprocess.run(
            [codex, "exec", "--json", "-m", SUPERVISOR_MODEL,
             "--skip-git-repo-check", "--ephemeral",
             "--dangerously-bypass-approvals-and-sandbox", prompt],
            capture_output=True, text=True, timeout=90,
        )
        return _codex_last_agent_message(result.stdout)
    except Exception:
        pass
    return ""


def _build_report(task_id: str, event: str, task_data: dict[str, Any]) -> str:
    """Build a report for engineer. The supervisor authors the summary when a
    codex backend is available; falls back to a deterministic template."""
    # A supervisor concern is surfaced verbatim on every path so it survives the
    # supervisor's own prose too (e.g. when an early-stop carries a diagnosis).
    concern = task_data.get("concern", "") or task_data.get("last_supervisor_concern", "")
    concern_block = f"**Supervisor concern**: {concern}\n\n" if concern else ""
    # A CONCERN is a mid-run flag whose whole point is the supervisor's own
    # words; skip the authored summary so the concern stays front and center
    # instead of being paraphrased away.
    if event != "CONCERN":
        # The supervisor — which watched the run and made the call — writes the
        # summary and the next step, grounded in its own diagnosis.
        llm_report = _supervisor_summarize_report(task_id, event, task_data)
        if llm_report and len(llm_report) > 50:
            return f"## Subagent Report: {task_id} [{event}]\n\n{concern_block}{llm_report}"

    # Fallback: template-based report
    lines = [f"## Subagent Report: {task_id}", f"**Event**: {event}", ""]

    if concern:
        lines.append(f"**Supervisor concern**: {concern}")
        lines.append("")

    desc = task_data.get("description", "")
    cmd = task_data.get("command", "")
    elapsed = task_data.get("elapsed_seconds", 0)
    mode = task_data.get("mode", "direct")
    exit_code = task_data.get("exit_code", "N/A")
    checks = task_data.get("supervisor_checks", 0)

    lines.append(f"- **Description**: {desc}")
    lines.append(f"- **Command**: `{cmd}`")
    lines.append(f"- **Mode**: {mode} | **Duration**: {elapsed:.0f}s | **Exit code**: {exit_code}")
    if checks:
        lines.append(f"- **Supervisor checks**: {checks}")

    # Headline results pulled from the structured run dir (reward / completed /
    # errored) so the engineer sees the actual numbers instead of having to
    # decode a noisy stdout tail.
    run_summary = _progress_summary(_effective_run_dir(task_data))
    if run_summary:
        lines.append("")
        lines.append("**Results**:")
        if run_summary.get("state"):
            lines.append(f"- run state: {run_summary['state']}")
        for m in run_summary.get("metrics", []):
            label = m.get("dataset") or m.get("condition") or "aggregate"
            bits = []
            if "reward" in m:
                bits.append(f"reward={m['reward']}")
            if "completed" in m and "total" in m:
                bits.append(f"completed={m['completed']}/{m['total']}")
            if "errored" in m:
                bits.append(f"errored={m['errored']}")
            lines.append(f"- {label}: {', '.join(bits)}")
        if not run_summary.get("metrics"):
            rows = run_summary.get("progress_rows")
            if rows is not None:
                lines.append(f"- progress rows: {rows}")
            res = run_summary.get("result_rows")
            if res is not None:
                lines.append(f"- result rows: {res}")

    # Paths for engineer to inspect
    lines.append("")
    lines.append("**Artifact paths**:")
    log_dir = REGISTRY_DIR / f"{task_id}_logs"
    stdout_log = task_data.get("stdout_log", str(log_dir / "stdout.log"))
    stderr_log = task_data.get("stderr_log", str(log_dir / "stderr.log"))
    lines.append(f"- stdout: `{stdout_log}`")
    lines.append(f"- stderr: `{stderr_log}`")
    lines.append(f"- task record: `{_registry_path(task_id)}`")
    sup_log = task_data.get("supervisor_log", "")
    if sup_log:
        lines.append(f"- supervisor log: `{sup_log}`")

    # Self-summary from stdout tail
    stdout_tail = task_data.get("stdout_tail", "")
    stderr_tail = task_data.get("stderr_tail", "")
    if stdout_tail:
        last_lines = stdout_tail.strip().splitlines()[-5:]
        lines.append("")
        lines.append("**Last output**:")
        for l in last_lines:
            lines.append(f"  {l}")
    if stderr_tail and event != "COMPLETED":
        last_err = stderr_tail.strip().splitlines()[-3:]
        lines.append("**Last errors**:")
        for l in last_err:
            lines.append(f"  {l}")

    # Action guidance
    lines.append("")
    if event == "COMPLETED":
        lines.append("**Next action**: collect results from the paths above, update PIPELINE_STATE, and continue pipeline.")
    elif event == "EARLY-STOPPED":
        lines.append("**Next action**: inspect supervisor log for stop reason, check if idea needs revision or if hyperparameters need adjustment.")
    elif event == "CONCERN":
        lines.append("**Next action**: the run is STILL RUNNING — this is a flag, not a stop. Re-discuss the supervisor concern above: decide whether to adjust hyperparameters/data/approach (you may need to stop and re-submit with new settings) or to dismiss it with a brief rationale.")
    else:
        lines.append("**Next action**: inspect stderr for root cause, fix, and re-submit if needed.")

    return "\n".join(lines)


def _alert_engineer(task_id: str, event: str, task_data: dict[str, Any]) -> None:
    """Send a structured report to engineer via the project inbox."""
    report = _build_report(task_id, event, task_data)
    try:
        from ..core.project import project_fingerprint
        from ..core.paths import global_root
        from ..apps._inbox import queue_inbox_message
        ident = project_fingerprint()
        life_dir = global_root() / "projects" / ident.fingerprint
        queue_inbox_message(life_dir, report, source="subagent")
    except Exception:
        alert_path = REGISTRY_DIR / f"{task_id}_ALERT.md"
        alert_path.parent.mkdir(parents=True, exist_ok=True)
        alert_path.write_text(report + "\n")


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
            proc = subprocess.Popen(
                command, shell=True, stdout=out, stderr=err,
                cwd=cwd, start_new_session=True, env=_child_env(),
            )
            _write_task(task_id, {
                "state": "running", "task_id": task_id,
                "description": description, "command": command,
                "pid": proc.pid, "worker_pid": os.getpid(),
                "started_at": time.time(), "mode": "direct",
                "run_dir": run_dir,
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
                td = {"state": "timeout", "task_id": task_id,
                    "description": description, "command": command,
                    "pid": proc.pid, "timeout_seconds": timeout,
                    "elapsed_seconds": round(time.time() - start_time, 1),
                    "completed_at": time.time(), "mode": "direct",
                    "run_dir": run_dir,
                    "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
                }
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
        _write_task(task_id, td)
        _alert_engineer(task_id, "CRASHED", td)


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


def _codex_agent_messages(stdout: str) -> list[str]:
    """Extract all assistant messages from ``codex exec --json`` output.

    Codex emits JSONL (one event per line); each assistant reply arrives as
    ``{"type": "item.completed", "item": {"type": "agent_message",
    "text": ...}}``. This mirrors the canonical parser in
    ``argus_skill.codex_autoloop.codex_runner`` so the subagent supervisor and
    reporter read the real schema instead of a stale ``messages`` shape.
    """
    out: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text", "")
                if isinstance(text, str) and text:
                    out.append(text)
    return out


def _codex_last_agent_message(stdout: str) -> str:
    """Return the final assistant message (empty string if none)."""
    messages = _codex_agent_messages(stdout)
    return messages[-1] if messages else ""


def _strip_code_fence(text: str) -> str:
    """Drop a leading/trailing markdown code fence if the model wrapped JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


_QUIET_LOGS_ENV = "ARGUS_SUBAGENT_QUIET_LOGS"


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
    """
    return task.get("run_dir") or _run_dir_from_command(task.get("command", ""))


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
) -> tuple[str, str, str]:
    """Call codex to check training/eval progress.

    Returns ``(decision, health, concern)`` where decision is
    ``continue`` / ``early_stop`` / ``save_checkpoint``, health is
    ``healthy`` / ``degrading`` / ``stuck`` / ``diverging`` / ``unknown``, and
    concern is a free-text note (possibly empty) the supervisor wants the
    engineer to re-discuss even when the run is progressing normally.
    """
    codex = _find_codex()

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
        "Beyond pass/fail, you are the engineer's eyes on this run. If ANYTHING\n"
        "looks off, suboptimal, or worth a second opinion — even when the run is\n"
        "NOT failing and you decide to continue — raise it in 'concern' so the\n"
        "engineer can re-discuss the experiment. Examples worth flagging: reward\n"
        "flat/low or near chance, completion length saturating the cap\n"
        "(clipping/truncation, e.g. a high clipped_ratio), suspicious or too-small\n"
        "hyperparameters (max length, steps, lr, batch, num generations),\n"
        "data/format problems, reward-hacking, low sample diversity, or anything\n"
        "that could weaken the eventual paper. Use your own judgement — you are\n"
        "not limited to the stop conditions below. Leave 'concern' as an empty\n"
        "string only when nothing is noteworthy. A concern does NOT stop the run;\n"
        "it just flags the issue for the engineer.\n\n"
        "Respond with EXACTLY one JSON object:\n"
        '{"decision": "continue" or "early_stop" or "save_checkpoint",\n'
        ' "reason": "one sentence explaining the decision",\n'
        ' "concern": "" or "1-2 sentences flagging anything the engineer should re-discuss",\n'
        ' "metrics": {"step": ..., "loss": ..., "reward": ..., "kl": ..., "resp_len": ...},\n'
        ' "health": "healthy" or "degrading" or "stuck" or "diverging"}\n\n'
        "Decision rules:\n"
        "- continue: signals look healthy (loss down for SFT; reward up and KL stable for RL).\n"
        "- early_stop: NaN/inf, CUDA OOM, KL blow-up, reward collapse, response-length\n"
        "  collapse, or no progress for a long stretch of total steps.\n"
        "- save_checkpoint: a notable improvement milestone reached.\n"
        "Note: 'continue' with a non-empty 'concern' is the right answer for a run\n"
        "that is progressing but has a quality issue worth the engineer's attention.\n"
        "Only output the JSON, nothing else."
    )

    try:
        result = subprocess.run(
            [codex, "exec", "--json", "-m", model,
             "--skip-git-repo-check", "--ephemeral",
             "--dangerously-bypass-approvals-and-sandbox", prompt],
            capture_output=True, text=True, timeout=120, cwd=cwd,
        )
        # codex emits JSONL; pull the assistant messages and accept the most
        # recent one that parses into a verdict (tolerates trailing chatter
        # after the JSON object the prompt asks for).
        for message in reversed(_codex_agent_messages(result.stdout)):
            try:
                data = json.loads(_strip_code_fence(message))
            except (json.JSONDecodeError, AttributeError):
                continue
            if isinstance(data, dict) and "decision" in data:
                return (
                    _norm_decision(data.get("decision", "continue")),
                    _norm_health(data.get("health", "unknown")),
                    _clean_concern(data.get("concern", "")),
                )
        return ("continue", "unknown", "")
    except Exception:
        return ("continue", "unknown", "")  # On any error, don't intervene


def _run_supervised(
    task_id: str,
    command: str,
    description: str,
    timeout: int,
    monitor_interval: int,
    model: str,
    cwd: str,
    run_dir: str | None = None,
) -> None:
    """Run command with periodic LLM supervisor checks."""
    log_dir = REGISTRY_DIR / f"{task_id}_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "stdout.log"
    stderr_path = log_dir / "stderr.log"
    supervisor_log = log_dir / "supervisor.jsonl"

    start_time = time.time()
    # Resolve run_dir once relative to the task cwd so the supervisor reads the
    # right progress/status and writes STOP where RunWriter watches.
    resolved_run_dir: str | None = None
    if run_dir:
        rp = Path(run_dir)
        resolved_run_dir = str(rp if rp.is_absolute() else Path(cwd) / rp)
    try:
        with stdout_path.open("w") as out, stderr_path.open("w") as err:
            proc = subprocess.Popen(
                command, shell=True, stdout=out, stderr=err,
                cwd=cwd, start_new_session=True, env=_child_env(),
            )
            _write_task(task_id, {
                "state": "running", "task_id": task_id,
                "description": description, "command": command,
                "pid": proc.pid, "worker_pid": os.getpid(),
                "started_at": time.time(), "mode": "supervised",
                "monitor_interval": monitor_interval,
                "run_dir": resolved_run_dir,
                "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
                "supervisor_log": str(supervisor_log),
            })

            check_number = 0
            # Latest supervisor verdict, kept in scope for the terminal records
            # below (the loop may never run if the process exits immediately).
            decision, health, concern = "continue", "unknown", ""
            # Track the last concern we surfaced so a persistent issue is raised
            # to the engineer once, not on every interval. Reset on a clean check
            # so a recurrence after recovery alerts again.
            last_concern_sig = ""
            # Health-adaptive backoff: start at the configured interval (capped),
            # then double while healthy (save supervisor tokens), snap back to the
            # base interval the moment health degrades.
            current_interval = min(max(monitor_interval, 1), SUPERVISOR_INTERVAL_CAP)
            while True:
                # Never wait past the hard timeout, even with a long interval.
                remaining = timeout - (time.time() - start_time)
                wait_for = min(current_interval, max(1, int(remaining)))
                try:
                    proc.wait(timeout=wait_for)
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
                    td = {
                        "state": "timeout", "task_id": task_id,
                        "description": description, "command": command,
                        "pid": proc.pid, "timeout_seconds": timeout,
                        "elapsed_seconds": round(elapsed, 1),
                        "completed_at": time.time(), "mode": "supervised",
                    }
                    _write_task(task_id, td)
                    _alert_engineer(task_id, "TIMEOUT", td)
                    return

                # Supervisor LLM check
                check_number += 1
                out.flush()
                err.flush()
                decision, health, concern = _supervisor_check(
                    task_id, command, description,
                    stdout_path, stderr_path, elapsed, check_number,
                    model, cwd, resolved_run_dir,
                )

                # Log supervisor decision
                entry = {
                    "check": check_number, "elapsed_s": round(elapsed, 1),
                    "decision": decision, "health": health,
                    "concern": concern,
                    "interval_s": current_interval, "timestamp": time.time(),
                }
                with supervisor_log.open("a") as sl:
                    sl.write(json.dumps(entry) + "\n")

                # Update task with latest supervisor info
                task = _read_task(task_id) or {}
                task["last_supervisor_check"] = check_number
                task["last_supervisor_decision"] = decision
                task["last_supervisor_health"] = health
                task["last_supervisor_concern"] = concern
                task["elapsed_seconds"] = round(elapsed, 1)
                _write_task(task_id, task)

                # Surface a mid-run concern to the engineer WITHOUT stopping the
                # run. early_stop already alerts below, so skip the duplicate.
                # Dedup on a digit-stripped signature so a persistent issue is
                # raised once even if rephrased with new step/metric numbers.
                if concern and decision != "early_stop":
                    concern_sig = _concern_signature(concern)
                    if concern_sig != last_concern_sig:
                        last_concern_sig = concern_sig
                        concern_td = dict(task)
                        concern_td.update({
                            "description": description, "command": command,
                            "mode": "supervised", "concern": concern,
                            "supervisor_checks": check_number,
                            "elapsed_seconds": round(elapsed, 1),
                            "run_dir": resolved_run_dir,
                            "stdout_log": str(stdout_path),
                            "stderr_log": str(stderr_path),
                            "supervisor_log": str(supervisor_log),
                            "stdout_tail": _tail_file(stdout_path, 2000),
                        })
                        _alert_engineer(task_id, "CONCERN", concern_td)
                elif not concern:
                    # Clean check: allow an identical concern to alert again if it
                    # recurs after the run appears to recover.
                    last_concern_sig = ""

                # Back off while healthy, tighten the moment health degrades. An
                # open concern is the supervisor asking for attention, so hold at
                # the base interval rather than widening (without stopping).
                if concern:
                    current_interval = min(
                        max(monitor_interval, 1), SUPERVISOR_INTERVAL_CAP,
                    )
                else:
                    current_interval = _next_monitor_interval(
                        health, current_interval, monitor_interval,
                    )

                if decision == "early_stop":
                    # Send STOP signal. Write into the run dir (experiment_io
                    # RunWriter watches <run_dir>/STOP) and cwd for back-compat.
                    stop_note = f"Early-stopped by supervisor at check #{check_number}\n"
                    stop_targets = {Path(cwd) / "STOP"}
                    if resolved_run_dir:
                        stop_targets.add(Path(resolved_run_dir) / "STOP")
                    for stop_file in stop_targets:
                        try:
                            stop_file.parent.mkdir(parents=True, exist_ok=True)
                            stop_file.write_text(stop_note)
                        except OSError:
                            pass
                    try:
                        proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        proc.terminate()
                        try:
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                    td = {
                        "state": "early_stopped", "task_id": task_id,
                        "description": description, "command": command,
                        "pid": proc.pid, "exit_code": proc.returncode,
                        "elapsed_seconds": round(time.time() - start_time, 1),
                        "completed_at": time.time(), "mode": "supervised",
                        "supervisor_checks": check_number,
                        "stop_reason": "supervisor early-stop",
                        "concern": concern,
                        "last_supervisor_health": health,
                        "last_supervisor_decision": decision,
                        "run_dir": resolved_run_dir,
                        "stdout_tail": _tail_file(stdout_path, 3000),
                        "stderr_tail": _tail_file(stderr_path, 3000),
                        "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
                        "supervisor_log": str(supervisor_log),
                    }
                    _write_task(task_id, td)
                    _alert_engineer(task_id, "EARLY-STOPPED", td)
                    return

        # Process exited naturally
        elapsed = round(time.time() - start_time, 1)
        stdout_tail = _tail_file(stdout_path, 3000)
        stderr_tail = _tail_file(stderr_path, 3000)
        td = {
            "state": "done" if proc.returncode == 0 else "error",
            "task_id": task_id, "description": description,
            "command": command, "exit_code": proc.returncode,
            "elapsed_seconds": elapsed, "completed_at": time.time(),
            "pid": proc.pid, "mode": "supervised",
            "supervisor_checks": check_number,
            "concern": concern,
            "last_supervisor_health": health,
            "last_supervisor_decision": decision,
            "run_dir": resolved_run_dir,
            "stdout_tail": stdout_tail, "stderr_tail": stderr_tail,
            "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
            "supervisor_log": str(supervisor_log),
        }
        _write_task(task_id, td)
        _alert_engineer(task_id, "COMPLETED" if proc.returncode == 0 else "FAILED", td)

    except Exception as exc:
        td = {
            "state": "error", "task_id": task_id,
            "description": description, "command": command,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.time() - start_time, 1),
            "completed_at": time.time(), "mode": "supervised",
        }
        _write_task(task_id, td)
        _alert_engineer(task_id, "CRASHED", td)


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

    # Resolve the run directory: prefer an explicit --run-dir, else recover it
    # from the command itself (commands already carry --run-dir). Store it as an
    # absolute path so status/report can read progress.jsonl/status.json/
    # summary.tsv regardless of the caller's cwd -- this is what makes the run
    # observable instead of a black box.
    run_dir = getattr(args, "run_dir", None) or _run_dir_from_command(args.command)
    if run_dir:
        rp = Path(run_dir)
        run_dir = str(rp if rp.is_absolute() else Path(cwd) / rp)

    # Write initial state
    _write_task(task_id, {
        "state": "starting",
        "task_id": task_id,
        "description": args.description,
        "command": args.command,
        "mode": mode,
        "run_dir": run_dir,
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
            "run_dir": run_dir,
            "pid": pid,
            "submitted_at": time.time(),
        })
        print(json.dumps({
            "state": "submitted",
            "task_id": task_id,
            "pid": pid,
            "mode": mode,
            "run_dir": run_dir,
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
            run_dir=run_dir,
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
_OK_STATES = frozenset({"done", "running", "starting", "early_stopped"})
_FAILED_STATES = frozenset({"error", "crashed", "timeout"})


def _read_status_json(base: Path) -> dict[str, Any]:
    """Read the RunWriter status.json (state/method/task_count/elapsed)."""
    path = base / "status.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_summary_tsv(base: Path) -> list[dict[str, Any]]:
    """Parse aggregate rows from summary.tsv (the headline reward/score)."""
    path = base / "summary.tsv"
    if not path.exists():
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
    if progress.exists():
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
    if results.exists():
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
            for src, dst, cast in (
                ("condition", "condition", str),
                ("dataset_id", "dataset", str),
                ("reward", "reward", float),
                ("n_total_trials", "total", int),
                ("n_completed_trials", "completed", int),
                ("n_errored_trials", "errored", int),
            ):
                val = row.get(src)
                if val in (None, ""):
                    continue
                try:
                    entry[dst] = cast(val)
                except (TypeError, ValueError):
                    entry[dst] = val
            if entry:
                metrics.append(entry)
        if metrics:
            summary["metrics"] = metrics
    return summary


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

    # Update state if process died without writing final report.
    pid = task.get("pid", 0)
    if task.get("state") in ("running", "starting"):
        if pid and not _is_pid_alive(pid):
            task["state"] = "crashed"
            task["error"] = f"sub-agent process {pid} no longer running"
            task["completed_at"] = time.time()
            _write_task(args.task_id, task)

    # Enrich with a live-process flag and run-directory progress so a single
    # poll tells the engineer whether the job is alive and advancing, without
    # it having to hand-inspect progress.jsonl/status.json itself.
    task["live"] = bool(pid and _is_pid_alive(pid))
    progress = _progress_summary(_effective_run_dir(task))
    if progress:
        task["progress"] = progress

    print(json.dumps(task, indent=2))
    state = task.get("state")
    if state in _FAILED_STATES:
        return 1
    return 0


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
        if task.get("state") not in ("running", "starting"):
            print(json.dumps(task, indent=2))
            return 1 if task.get("state") in _FAILED_STATES else 0
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
                          help="Base seconds between supervisor checks; backs off "
                               "while healthy, tightens when degrading (supervised mode)")
    p_submit.add_argument("--model", default=SUPERVISOR_MODEL, help="Supervisor model (supervised mode)")
    p_submit.add_argument("--run-dir", default=None,
                          help="Run directory whose progress.jsonl/status.json the "
                               "supervisor reads and where it writes STOP on early-stop")
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
