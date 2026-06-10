"""Background-subagent awareness for the engineer round loop.

When a mission launches a long-running job through the subagent tool in
``--mode supervised`` (``python -m argus_skill.tools.subagent submit ... --mode
supervised``), that job gets its OWN independent supervisor process. The
supervisor polls the run's health every ``monitor_interval`` seconds, can
early-stop it on collapse, and posts a terminal report to the engineer's inbox
when it finishes or fails. The run is therefore already being watched *without*
the engineer.

Despite that, a long-horizon engineer session tends to spend every round
re-polling the same healthy run — burning rounds/tokens (and a full reviewer
call) babysitting work that is already supervised, instead of pulling
independent downstream work forward while the run self-progresses. The observed
pathology: an RL training pilot accumulated hundreds of rounds whose only action
was re-reading ``status.json`` and writing another ``MONITOR_*.md``.

This module reads the subagent registry (the same ``.argus_subagents/*.json``
records the subagent tool writes) and renders a small advisory block. The
engineer/runner splices it into the round prompt the same way the curated
checkpoint and failed-tool advisories are. Per the harness design philosophy it
does NOT decide what the engineer should do instead and it never forces the loop
off monitoring — it only gives the agent accurate context (this run is
self-watched; you do not have to babysit it; here is how to yield to its
supervisor's cadence if there is genuinely nothing else to do) and lets the
agent and reviewer decide.

A subagent that is NOT self-watched — ``direct`` mode (no supervisor), a
supervisor that opened a ``discussing`` handoff, a ``degrading``/``stuck``/
``diverging`` health label, a raised concern, a stale supervisor heartbeat, or a
dead worker pid — is surfaced as "needs your attention" instead, so the advisory
never lulls the agent away from a job that really does need a look.

Kept dependency-free (stdlib only) so it can be imported anywhere; the single
integration point is :mod:`argus_skill.engineer.runner`.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

# Registry directory name. Canonical definition is
# ``argus_skill.tools.subagent._core.REGISTRY_DIR`` (a ``Path(".argus_subagents")``);
# duplicated here as a bare name so this module stays import-light and does not
# pull in the subagent tool's heavier dependency chain.
_REGISTRY_DIRNAME = ".argus_subagents"

# Registry ``state`` values that mean the job is still alive and doing work.
# ``discussing`` is alive too, but it means the supervisor paused the run to ask
# the engineer something, so it is classified as needs-attention below rather
# than self-watched. Mirrors the launch states written in
# ``argus_skill.tools.subagent._core``.
_INFLIGHT_STATES = frozenset({"running", "starting", "preflight", "discussing"})
_ATTENTION_STATES = frozenset({"discussing"})

# Supervisor health labels (see ``argus_skill.tools.subagent._normalize``) that
# warrant a look from the engineer rather than "leave it to the supervisor".
_DEGRADED_HEALTH = frozenset({"degrading", "stuck", "diverging"})

# Supervisor decision that means the run is being stopped.
_STOP_DECISIONS = frozenset({"early_stop"})

# Cadence bounds for an agent-requested wait, mirroring the subagent tool's
# ``SUPERVISOR_INTERVAL_CAP = 900`` so a wait never sleeps longer than the
# supervisor's own slowest poll, and never busy-spins below a sane floor.
_CADENCE_FLOOR_S = 30.0
_CADENCE_CAP_S = 900.0

# A supervised job's registry record is rewritten by its supervisor on every
# health check, so the file mtime tracks the last supervisor heartbeat. The
# record's ``monitor_interval`` field holds the *launch* interval; the live
# interval grows up to the cap, so staleness is judged against the cap (times a
# multiplier) to avoid false-flagging a healthy run whose interval has grown.
_STALE_MULTIPLIER = 2.0

# Sentinel an agent emits when the only remaining work is to wait for a
# self-watched subagent to reach its next checkpoint / terminal report.
_WAIT_SENTINEL = "WAIT_FOR_SUBAGENT:"


@dataclass(frozen=True)
class InflightSubagent:
    """One in-flight subagent as seen from the registry."""

    task_id: str
    description: str
    mode: str
    state: str
    health: str
    decision: str
    concern: str
    monitor_interval: float
    elapsed_seconds: float
    pid_alive: bool
    stale: bool
    self_watched: bool

    @property
    def attention_reason(self) -> str:
        """Human-readable reason this job needs the engineer (empty if self-watched)."""
        if self.self_watched:
            return ""
        reasons: list[str] = []
        if self.mode != "supervised":
            reasons.append("no independent supervisor (direct mode)")
        if self.state in _ATTENTION_STATES:
            reasons.append("supervisor opened a discussion awaiting your reply")
        if not self.pid_alive:
            reasons.append("worker process is not alive")
        if self.stale:
            reasons.append("supervisor heartbeat is stale")
        if self.health in _DEGRADED_HEALTH:
            reasons.append(f"health={self.health}")
        if self.concern:
            reasons.append(f"concern raised: {self.concern}")
        if self.decision in _STOP_DECISIONS:
            reasons.append(f"supervisor decision={self.decision}")
        return "; ".join(reasons) or "not self-watched"


def _pid_alive(pid: object) -> bool:
    """Return True if ``pid`` names a live process.

    Mirrors ``argus_skill.tools.subagent._core._is_pid_alive`` (kept local to
    stay dependency-free). A missing/invalid pid is treated as "alive" because
    absence of a pid means the record predates pid tracking, not that the job
    died.
    """
    if pid is None:
        return True
    try:
        pid_int = int(str(pid).strip())
    except (TypeError, ValueError):
        return True
    if pid_int <= 0:
        return True
    try:
        os.kill(pid_int, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user (e.g. root-launched supervisor seen
        # from a non-root scan) — still alive.
        return True
    except OSError:
        return True


def _norm_health(value: object) -> str:
    token = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "degraded": "degrading",
        "diverged": "diverging",
        "diverge": "diverging",
        "stalling": "stuck",
        "stalled": "stuck",
        "stall": "stuck",
        "ok": "healthy",
        "good": "healthy",
    }
    return aliases.get(token, token)


def _clean_concern(value: object) -> str:
    text = " ".join(str(value or "").split())
    low = text.lower().strip(".")
    empties = {
        "", "none", "n/a", "na", "null", "nil", "-", "no concern",
        "no concerns", "nothing", "no issues", "no issue",
    }
    prefixes = (
        "no concern", "no issue", "nothing notewor", "nothing to report",
        "nothing of note", "all good", "all healthy", "looks healthy",
        "no anomal", "no problem",
    )
    if low in empties or low.startswith(prefixes):
        return ""
    return text


def _registry_dir(workdir: Path | str) -> Path:
    return Path(workdir) / _REGISTRY_DIRNAME


def _coerce_float(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _record_to_subagent(
    record: dict, *, registry_mtime: float | None, now: float
) -> InflightSubagent | None:
    """Build an :class:`InflightSubagent` from a registry record, or None if terminal."""
    state = str(record.get("state") or "").strip().lower()
    if state not in _INFLIGHT_STATES:
        return None

    mode = str(record.get("mode") or "direct").strip().lower()
    health = _norm_health(record.get("last_supervisor_health"))
    decision = str(record.get("last_supervisor_decision") or "").strip().lower()
    concern = _clean_concern(record.get("last_supervisor_concern") or record.get("concern"))
    monitor_interval = _coerce_float(record.get("monitor_interval"), 120.0)
    elapsed = _coerce_float(record.get("elapsed_seconds"), 0.0)

    pid_alive = _pid_alive(record.get("worker_pid") if record.get("worker_pid") is not None
                           else record.get("pid"))

    stale = False
    if registry_mtime is not None:
        threshold = max(monitor_interval, _CADENCE_CAP_S) * _STALE_MULTIPLIER
        stale = (now - registry_mtime) > threshold

    self_watched = (
        mode == "supervised"
        and state not in _ATTENTION_STATES
        and pid_alive
        and not stale
        and health not in _DEGRADED_HEALTH
        and not concern
        and decision not in _STOP_DECISIONS
    )

    return InflightSubagent(
        task_id=str(record.get("task_id") or "").strip() or "(unknown)",
        description=" ".join(str(record.get("description") or "").split())[:240],
        mode=mode,
        state=state,
        health=health or "unknown",
        decision=decision,
        concern=concern,
        monitor_interval=monitor_interval,
        elapsed_seconds=elapsed,
        pid_alive=pid_alive,
        stale=stale,
        self_watched=self_watched,
    )


def scan_inflight_subagents(
    workdir: Path | str, *, now: float | None = None
) -> list[InflightSubagent]:
    """Read ``<workdir>/.argus_subagents/*.json`` and return the in-flight jobs.

    Terminal jobs (done/error/crashed/timeout/early_stopped, or any non-in-flight
    state) are excluded. Never raises — a missing/unreadable registry yields ``[]``.
    """
    now = time.time() if now is None else now
    reg = _registry_dir(workdir)
    out: list[InflightSubagent] = []
    try:
        files = sorted(reg.glob("*.json"))
    except OSError:
        return out
    for f in files:
        if f.name.endswith(".tmp"):
            continue
        try:
            record = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        try:
            mtime: float | None = f.stat().st_mtime
        except OSError:
            mtime = None
        sub = _record_to_subagent(record, registry_mtime=mtime, now=now)
        if sub is not None:
            out.append(sub)
    return out


def _format_elapsed(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{int(seconds)}s"
    minutes = seconds / 60.0
    if minutes < 90:
        return f"{minutes:.0f}m"
    return f"{minutes / 60.0:.1f}h"


def render_background_subagents_advisory(
    workdir: Path | str, *, now: float | None = None
) -> str:
    """Render the engineer-prompt advisory block, or ``""`` if nothing in flight.

    Empty result (the common case for a mission with no background jobs) means
    the caller splices nothing, so there is zero behavioural change.
    """
    subs = scan_inflight_subagents(workdir, now=now)
    if not subs:
        return ""

    watched = [s for s in subs if s.self_watched]
    attention = [s for s in subs if not s.self_watched]

    lines: list[str] = ["## Background subagents in flight"]

    if watched:
        lines.append(
            "Each job below was launched as a SUPERVISED subagent: it has its OWN "
            "independent supervisor that polls its health on a cadence, can "
            "early-stop it on collapse, and will post a terminal report to your "
            "inbox when it finishes or fails. It is already being watched without "
            "you — re-reading its status every round is wasted work."
        )
        lines.append("Self-watched and healthy (do NOT spend a round polling these):")
        for s in watched:
            interval = int(max(1.0, s.monitor_interval))
            desc = f" — {s.description}" if s.description else ""
            lines.append(
                f"- `{s.task_id}`{desc}: state={s.state}, health={s.health}, "
                f"running {_format_elapsed(s.elapsed_seconds)}, supervisor checks every ~{interval}s."
            )

    if attention:
        lines.append("Needs your attention (NOT self-watched — handle these yourself):")
        for s in attention:
            desc = f" — {s.description}" if s.description else ""
            lines.append(f"- `{s.task_id}`{desc}: {s.attention_reason}.")

    if watched and not attention:
        lines.append(
            "Do not babysit a healthy self-watched run. Use this round to advance "
            "independent work that does NOT depend on its result — prepare "
            "downstream steps, repair tooling/evaluators, or scaffold artifacts. "
            "If the ONLY remaining work is to wait for one of these to reach its "
            "next checkpoint or finish, do not spin another full round: reply with "
            "exactly"
        )
        lines.append(f"    {_WAIT_SENTINEL} <task_id>")
        lines.append(
            "as your entire action, and the loop will pause on that subagent's "
            "supervisor cadence (no wasted round) and resume you at its next "
            "checkpoint or terminal report instead of re-polling every round."
        )
    elif attention:
        lines.append(
            "Address the attention items above yourself (the supervisor cannot, or "
            "is asking you to). Do not yield with "
            f"`{_WAIT_SENTINEL}` while a job needs attention."
        )

    return "\n".join(lines)


def parse_wait_sentinel(message: str | None) -> str | None:
    """Extract the task id from a ``WAIT_FOR_SUBAGENT: <id>`` agent message.

    The sentinel must be effectively the agent's whole action: this tolerates
    leading/trailing whitespace and a single surrounding code fence, but rejects
    a sentinel buried inside a larger message (so a mere mention in prose never
    hangs the loop). Returns the task id, or ``None`` if not a wait request.
    """
    if not message:
        return None
    text = message.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.strip("`").strip()
    # Collapse to the first non-empty line so a trailing newline / stray blank
    # line does not defeat the single-action check.
    non_empty = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(non_empty) != 1:
        return None
    line = non_empty[0]
    if not line.upper().startswith(_WAIT_SENTINEL):
        return None
    task_id = line[len(_WAIT_SENTINEL):].strip().strip("`").strip()
    task_id = task_id.split()[0] if task_id else ""
    return task_id or None


def find_waitable_subagent(
    workdir: Path | str, task_id: str, *, now: float | None = None
) -> InflightSubagent | None:
    """Return the self-watched in-flight subagent named ``task_id``, else None.

    Used by the runner to validate an agent ``WAIT_FOR_SUBAGENT`` request before
    yielding — a request that does not name a currently self-watched job is
    ignored (treated as a normal round) so a stale or mistaken sentinel cannot
    hang the loop.
    """
    target = (task_id or "").strip()
    if not target:
        return None
    for sub in scan_inflight_subagents(workdir, now=now):
        if sub.task_id == target and sub.self_watched:
            return sub
    return None


def cadence_seconds(sub: InflightSubagent) -> float:
    """The wait budget for one cadence yield: the supervisor interval, clamped."""
    return min(_CADENCE_CAP_S, max(_CADENCE_FLOOR_S, sub.monitor_interval))


def wait_for_subagent_cadence(
    workdir: Path | str,
    task_id: str,
    *,
    sleep=None,
    poll_interval: float = 15.0,
    now: float | None = None,
) -> tuple[str, float]:
    """Sleep on a self-watched subagent's supervisor cadence, waking early on terminal.

    Returns ``(reason, waited_seconds)`` where ``reason`` is:
      * ``"not_waitable"`` — ``task_id`` is not a self-watched in-flight job (no sleep);
      * ``"terminal"`` — the job left the in-flight set during the wait;
      * ``"cadence_elapsed"`` — the full cadence budget elapsed and the job is still live.

    ``sleep`` and ``now`` are injectable for tests; ``sleep`` defaults to
    :func:`time.sleep` resolved at call time. The wait re-scans the registry
    every ``poll_interval`` seconds so a job that finishes mid-wait wakes the
    engineer promptly instead of sleeping the whole budget.
    """
    _sleep = sleep if sleep is not None else time.sleep
    target = find_waitable_subagent(workdir, task_id, now=now)
    if target is None:
        return ("not_waitable", 0.0)
    budget = cadence_seconds(target)
    step = max(1.0, min(poll_interval, budget))
    waited = 0.0
    while waited < budget:
        chunk = min(step, budget - waited)
        _sleep(chunk)
        waited += chunk
        if find_waitable_subagent(workdir, task_id) is None:
            # Left the self-watched in-flight set: finished, failed, or now needs
            # attention — either way, wake the engineer to react.
            return ("terminal", waited)
    return ("cadence_elapsed", waited)
