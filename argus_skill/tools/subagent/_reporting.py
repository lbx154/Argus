"""Reporting layer: build and deliver handoff reports to the engineer inbox.

Owns: LLM-authored supervisor summary, deterministic template fallback,
inbox queuing, and the EARLY-STOPPED reply-back instruction block.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ._discussion_log import (
    _discussion_path,
)
from ._llm import _run_supervisor_with_usage, resolve_supervisor_model
from ._registry import (
    REGISTRY_DIR,
    _add_usage_totals,
    _apply_supervisor_usage_fields,
    _effective_run_dir,
    _progress_summary,
    _read_task,
    _registry_path,
    _task_log_dir,
    _write_task_if_run_id,
)
from ._text import (
    _tail_file,
)

log = logging.getLogger(__name__)


def _timeout_notice(task_data: dict[str, Any]) -> str:
    message = str(task_data.get("timeout_message") or "").strip()
    if message:
        return message
    seconds = task_data.get("timeout_seconds")
    if seconds in (None, ""):
        return ""
    return f"Hard timeout reached after {seconds} seconds (configured --timeout limit)."

# ---------------------------------------------------------------------------
# LLM-authored supervisor summary
# ---------------------------------------------------------------------------

def _supervisor_summarize_report(task_id: str, event: str, task_data: dict[str, Any]) -> str:
    """Have the supervisor author its own handoff report to the engineer.

    The supervisor watched the run and (for an early-stop or concern) decided
    why it intervened, so it — not a separate, signal-blind summarizer — writes
    the report AND the next step. Its own diagnosis (concern / stop_reason /
    verdict trail) is fed in alongside the run signals so the next step follows
    from the reason instead of defaulting to a mechanical "remove the STOP file
    and rerun".
    """
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
    if event == "TIMEOUT":
        prompt += f"Mechanical timeout: {_timeout_notice(task_data)}\n"
    if verdict_tail:
        prompt += f"\n=== your recent verdicts (supervisor.jsonl tail) ===\n{verdict_tail}\n"
    if progress_tail:
        prompt += f"\n=== progress.jsonl (tail) ===\n{progress_tail}\n"
    if status_tail:
        prompt += f"\n=== status.json ===\n{status_tail}\n"
    prompt += f"\n=== stdout (last 2000 chars) ===\n{stdout_tail}\n"
    if stderr_tail and event != "COMPLETED":
        prompt += f"\n=== stderr (last 1000 chars) ===\n{stderr_tail}\n"

    log_dir = _task_log_dir(task_id)
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
        "4. The concrete next step that FOLLOWS FROM YOUR DIAGNOSIS. Read the\n"
        "   hyperparameter flags in the Command above and name the SPECIFIC flag(s)\n"
        "   and value(s) to change (e.g. 'raise --num-generations 2 -> 6',\n"
        "   '--max-completion-length 256 -> 512', 'lower --learning-rate 1e-5 ->\n"
        "   3e-6'), or the specific code/reward/prompt fix if the cause is not a\n"
        "   flag. If you stopped for a quality issue (truncation/clipping, reward\n"
        "   collapse, degenerate outputs), the next step must address that root cause\n"
        "   with a named change — do not default to rerunning unchanged and\n"
        "   do not stop at 'mark it failed'. If the run is healthy/complete, say how\n"
        "   to use it.\n"
        "5. Final health verdict (YOU are the authority on run health): end with a\n"
        "   line `Final health verdict: usable | unusable | inconclusive` plus a\n"
        "   short reason from the metric TREND. A mechanical health-gate or\n"
        "   `*_REJECTED.md` / `status.json state=failed` that fired on a single\n"
        "   metric-threshold breach (e.g. one tail step's clipped_ratio, a brief\n"
        "   reward dip) is ADVISORY ONLY — it does NOT override your judgement. If\n"
        "   the trend is actually healthy and the run produced usable signal,\n"
        "   call it `usable` and tell the engineer NOT to discard it or relaunch an\n"
        "   equivalent smoke just because a mechanical gate rejected it.\n"
        "Keep it under 320 words. Be direct and actionable."
    )

    persisted_before = _read_task(task_id)
    expected_run_id = str(task_data.get("run_id") or "")
    if (
        expected_run_id
        and persisted_before is not None
        and str(persisted_before.get("run_id") or "") != expected_run_id
    ):
        return ""
    model = str(task_data.get("supervisor_usage_model") or resolve_supervisor_model())
    cwd = str(
        task_data.get("cwd")
        or (persisted_before or {}).get("cwd")
        or Path.cwd()
    )
    messages, _thread_id, usage = _run_supervisor_with_usage(
        prompt,
        model,
        cwd,
        run_label=f"subagent:{task_id}:report",
        mission_id=expected_run_id or None,
    )
    totals = _add_usage_totals(
        (
            int(task_data.get("supervisor_input_tokens") or 0),
            int(task_data.get("supervisor_cached_input_tokens") or 0),
            int(task_data.get("supervisor_output_tokens") or 0),
            int(task_data.get("supervisor_reasoning_output_tokens") or 0),
        ),
        usage,
    )
    _apply_supervisor_usage_fields(task_data, model=model, totals=totals)
    persisted = _read_task(task_id)
    if persisted is not None:
        _apply_supervisor_usage_fields(persisted, model=model, totals=totals)
        _write_task_if_run_id(
            task_id,
            persisted,
            expected_run_id=expected_run_id,
        )
    return messages[-1] if messages else ""


# ---------------------------------------------------------------------------
# Reply-back instruction block
# ---------------------------------------------------------------------------

def _reply_back_block(task_id: str, event: str) -> str:
    """Deterministic 'reply to the supervisor' instruction for a stopped run.

    Appended OUTSIDE both the supervisor-authored and template report paths so
    the engineer is always told to reply WHY it will act — and not the
    supervisor's suggested alternative. On an early-stop the supervisor is parked
    on the discussion thread waiting, so the engineer must reply to discuss.
    """
    if event != "EARLY-STOPPED":
        return ""
    discussion = _discussion_path(task_id)
    cli = (
        '${ARGUS_SKILL_PYTHON:-python3} -m argus_skill.tools.subagent reply '
        f'--task-id {task_id} --message "<your root-cause diagnosis + the SPECIFIC '
        'parameter/code change you will make (e.g. num_generations 2->6, '
        'max_completion_length 256->512, fix reward extraction), OR a reasoned '
        'pushback on why the supervisor is wrong>"'
    )
    where = (
        "The run is STOPPED and the supervisor is WAITING on the discussion "
        f"thread (`{discussion}`) for your reply — it will read your rationale "
        "and either agree on the fix or push back, all in that one file. "
        "Nothing resumes until you reply, so do not move on silently."
    )
    return (
        "\n\n**Reply to the supervisor (required)**: do NOT just agree and mark the "
        "run failure. Actually diagnose the root cause and decide a concrete fix — "
        "name the specific hyperparameter(s) or code/reward/prompt change you will "
        "make next, or push back with reasoning if you think the run was fine. Send "
        "that back so the discussion is two-way and converges on a real fix; do not "
        f"silently act against the advice. {where}\n```bash\n{cli}\n```"
    )


# ---------------------------------------------------------------------------
# Report builder (LLM-authored + deterministic fallback)
# ---------------------------------------------------------------------------

def _build_report(task_id: str, event: str, task_data: dict[str, Any]) -> str:
    """Build a report for engineer. The supervisor authors the summary when a
    codex backend is available; falls back to a deterministic template."""
    # A supervisor concern is surfaced verbatim on every path so it survives the
    # supervisor's own prose too (e.g. when an early-stop carries a diagnosis).
    concern = task_data.get("concern", "") or task_data.get("last_supervisor_concern", "")
    concern_block = f"**Supervisor concern**: {concern}\n\n" if concern else ""
    timeout_notice = _timeout_notice(task_data) if event == "TIMEOUT" else ""
    timeout_block = f"**Hard timeout**: {timeout_notice}\n\n" if timeout_notice else ""
    reply_block = _reply_back_block(task_id, event)
    # The supervisor — which watched the run and made the call — writes the
    # summary and the next step, grounded in its own diagnosis.
    llm_report = ""
    report_error = ""
    if task_data.get("mode") == "supervised":
        try:
            llm_report = _supervisor_summarize_report(task_id, event, task_data)
        except Exception as exc:
            report_error = (
                f"{type(exc).__name__}: model-authored report unavailable; "
                "using deterministic evidence summary"
            )
    if llm_report and len(llm_report) > 50:
        return (
            f"## Subagent Report: {task_id} [{event}]\n\n"
            f"{timeout_block}{concern_block}{llm_report}{reply_block}"
        )

    # Fallback: template-based report
    lines = [f"## Subagent Report: {task_id}", f"**Event**: {event}", ""]
    if report_error:
        lines.extend([f"**Supervisor report error**: {report_error}", ""])

    if concern:
        lines.append(f"**Supervisor concern**: {concern}")
        lines.append("")
    if timeout_notice:
        lines.extend([f"**Hard timeout**: {timeout_notice}", ""])

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
    log_dir = _task_log_dir(task_id)
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
        for line in last_lines:
            lines.append(f"  {line}")
    if stderr_tail and event != "COMPLETED":
        last_err = stderr_tail.strip().splitlines()[-3:]
        lines.append("**Last errors**:")
        for line in last_err:
            lines.append(f"  {line}")

    # Action guidance
    lines.append("")
    if event == "COMPLETED":
        lines.append("**Next action**: collect results from the paths above, update PIPELINE_STATE, and continue pipeline.")
    elif event == "EARLY-STOPPED":
        lines.append("**Next action**: the run is STOPPED and the supervisor is waiting on the discussion thread. Inspect the supervisor log / concern above, decide the fix (revise the idea or hyperparameters, or relaunch), and reply your rationale so the two-way discussion can resolve.")
    else:
        lines.append("**Next action**: inspect stderr for root cause, fix, and re-submit if needed.")

    return "\n".join(lines) + reply_block


# ---------------------------------------------------------------------------
# Inbox delivery
# ---------------------------------------------------------------------------

class InboxDeliveryError(RuntimeError):
    """A subagent report could not be queued to the engineer inbox.

    The handoff report is the ONLY channel by which the engineer learns that a
    run completed, early-stopped, timed out or crashed, so a delivery that
    failed must never read as one that succeeded. The report text is still
    written to ``REGISTRY_DIR/<task_id>_ALERT.md`` for forensics before this is
    raised.

    A caller is expected to (a) stop treating the run as reported, and (b)
    record the failure on the durable task record — ``report_delivery:
    "failed"`` plus the error string — so ``subagent status`` can tell the
    engineer a report exists that never reached them. It must NOT let this kill
    the worker: the run itself is already over by the time a report is sent.
    """


def _life_dir_for_cwd(cwd: str | Path) -> Path:
    """Map a run's working directory to that project's engineer inbox root."""
    from ...core.paths import session_state_root  # noqa: PLC0415
    from ...core.project import project_fingerprint  # noqa: PLC0415

    return session_state_root(project_fingerprint(cwd).fingerprint)


def _task_life_dir(task_id: str, task_data: dict[str, Any]) -> Path | None:
    """Resolve the engineer inbox root from the task record's own cwd.

    Returns ``None`` only when no cwd can be found on either the in-memory task
    data or the persisted record, which leaves ``_queue_to_inbox`` to infer one
    from this process — see the warning it logs when that happens.
    """
    cwd = str(task_data.get("cwd") or "").strip()
    if not cwd:
        persisted = _read_task(task_id)
        cwd = str((persisted or {}).get("cwd") or "").strip()
    if not cwd:
        log.warning(
            "subagent %s: task record carries no cwd, so the engineer inbox will "
            "be inferred from this worker's own cwd — which is not necessarily "
            "the project the run belongs to",
            task_id,
        )
        return None
    try:
        return _life_dir_for_cwd(cwd)
    except Exception:  # noqa: BLE001 — identity resolution shells out to git; a
        # broken repo must not cost us the report, but it must not be silent
        # either, so the guess that follows is logged with its traceback.
        log.exception(
            "subagent %s: could not resolve the engineer inbox for cwd %s; "
            "falling back to inference from this worker's cwd",
            task_id,
            cwd,
        )
        return None


def _queue_to_inbox(
    report: str,
    task_id: str = "subagent",
    life_dir: str | Path | None = None,
) -> None:
    """Queue a report to the engineer inbox rooted at *life_dir*.

    *life_dir* is supplied by the caller, which holds the task record and
    therefore the run's real cwd. Identity is deliberately NOT inferred from
    this process: the durable worker is ``setsid``-detached and can hold a cwd
    of its own, while ``project_fingerprint()`` hashes the git remote (or the
    path) of whatever cwd it is handed. Delivering to the wrong project's inbox
    raises nothing at all, so that misdelivery is invisible on every side —
    strictly worse than a loud failure. Inference survives only for callers that
    have no cwd to give, and says so in the log when it is used.

    Raises:
        InboxDeliveryError: the report did not land. The text is written to
            ``<task_id>_ALERT.md`` first so the evidence survives.
    """
    try:
        from ...apps._inbox import queue_inbox_message  # noqa: PLC0415

        if life_dir is None:
            from ...core.paths import session_state_root  # noqa: PLC0415
            from ...core.project import project_fingerprint  # noqa: PLC0415

            ident = project_fingerprint()
            life_dir = session_state_root(ident.fingerprint)
            log.warning(
                "subagent %s: no life_dir supplied; inferring the engineer inbox "
                "from this process's cwd (%s, source=%s, fingerprint=%s). If this "
                "worker is detached from the run's project, the report lands in "
                "another project's inbox.",
                task_id,
                ident.cwd,
                ident.source,
                ident.fingerprint,
            )
        queue_inbox_message(Path(life_dir), report, source="subagent")
    except Exception as exc:  # noqa: BLE001 — the import, the identity lookup and
        # the write itself can each fail, and they all mean one thing to the
        # caller: the engineer was not told. They are converted to one signal.
        alert_path = REGISTRY_DIR / f"{task_id}_ALERT.md"
        try:
            alert_path.parent.mkdir(parents=True, exist_ok=True)
            alert_path.write_text(report + "\n", encoding="utf-8")
        except OSError:
            log.exception(
                "subagent %s: could not write the fallback alert file %s either",
                task_id,
                alert_path,
            )
        log.exception(
            "subagent %s: report could not be queued to the engineer inbox "
            "(life_dir=%s); forensic copy at %s",
            task_id,
            life_dir,
            alert_path,
        )
        raise InboxDeliveryError(
            f"subagent report for '{task_id}' never reached the engineer inbox "
            f"(life_dir={life_dir}): {type(exc).__name__}: {exc}. "
            f"Forensic copy: {alert_path}"
        ) from exc


def _record_report_delivery_failure(
    task_id: str,
    event: str,
    task_data: dict[str, Any],
    error: Exception,
) -> None:
    """Persist "the engineer was never told" onto the durable task record.

    Without this the only trace of a lost report is an ``_ALERT.md`` nobody is
    looking for; with it, ``subagent status`` can say so on the next poll.
    """
    fields: dict[str, Any] = {
        "report_delivery": "failed",
        "report_delivery_error": f"{type(error).__name__}: {error}",
        "report_delivery_event": event,
    }
    task_data.update(fields)
    persisted = _read_task(task_id)
    if persisted is None:
        log.warning(
            "subagent %s: no task record on disk to mark the failed report "
            "delivery on; the _ALERT.md copy is the only trace",
            task_id,
        )
        return
    persisted.update(fields)
    expected_run_id = str(task_data.get("run_id") or persisted.get("run_id") or "")
    if not _write_task_if_run_id(task_id, persisted, expected_run_id=expected_run_id):
        log.warning(
            "subagent %s: failed report delivery was not persisted — the task "
            "record now belongs to a different run (expected run_id=%s)",
            task_id,
            expected_run_id,
        )


def _alert_engineer(task_id: str, event: str, task_data: dict[str, Any]) -> str:
    """Send a structured report to engineer via the project inbox.

    Returns the report text so callers can also persist it as the durable,
    co-located supervisor verdict for the experiment.

    A delivery failure is recorded, not raised: the run is already over by the
    time a report is sent, and killing the worker here would lose the teardown
    that follows. The record carries ``report_delivery="failed"`` so `subagent
    status` surfaces it, and the report text survives as ``_ALERT.md``.
    """
    report = _build_report(task_id, event, task_data)
    try:
        _queue_to_inbox(
            report,
            task_id,
            life_dir=_task_life_dir(task_id, task_data),
        )
    except InboxDeliveryError as exc:
        _record_report_delivery_failure(task_id, event, task_data, exc)
    else:
        task_data["report_delivery"] = "delivered"
    return report
