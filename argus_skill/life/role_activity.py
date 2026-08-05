"""Project role activity projected from the bounded event-log tail."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..core.event_catalog import canonical_event_type
from ..core.role_config import ROLES

# ── live activity (from events.jsonl) ─────────────────────────────────────


@dataclass(frozen=True)
class RoleActivity:
    role: str
    active: bool  # the role acting right now
    label: str  # short "what it is doing"
    status: str  # running / idle / done / blocked / …
    age_s: float | None  # seconds since the driving event


def _tail_jsonl(path: Path, *, limit: int = 500) -> list[dict[str, Any]]:
    # Reuse the reverse chunk reader used by persistent life memory. Reading
    # ``Path.read_text()`` here used to load every retained 100 MiB event-log
    # generation merely to show four role labels when switching Web projects.
    from .memory import _read_jsonl_tail_history

    return _read_jsonl_tail_history(path, limit)


def _event_role(event: dict[str, Any]) -> str | None:
    layer = event.get("agent_layer")
    if isinstance(layer, str) and layer in ROLES:
        return layer
    etype = canonical_event_type(event.get("canonical_type") or event.get("type"))
    if etype.startswith("agent.io."):
        label = str(event.get("run_label") or "").lower()
        if "compaction_batch" in label or "compaction-batch" in label:
            # Post-mission library housekeeping is not Engineer work. The TUI
            # presents it separately as Maintenance activity.
            return None
        if "reviewer" in label or label.startswith("review"):
            return "reviewer"
        if "planner" in label or label.startswith("plan"):
            return "planner"
        if (
            "manager" in label
            or label.startswith("router")
            or label.startswith("chat-")
            or label.startswith("simple-")
        ):
            return "manager"
        return "engineer"
    if etype.startswith("life.planner."):
        return "planner"
    if etype.startswith("venue.research.") or etype.startswith("idea.search."):
        return "engineer"
    if etype == "round.review.deferred":
        return "engineer"
    if etype.startswith("round.review") or etype.startswith("reviewer"):
        return "reviewer"
    if etype in {
        "life.mission.started",
        "loop.start",
        "round.start",
        "round.main.completed",
        "loop.done",
        "engineer.progress",
    } or etype.startswith("engineer"):
        return "engineer"
    if etype.startswith("manager") or etype.startswith("life.manager"):
        return "manager"
    return None


_CMD_PREFIXES = (
    "/bin/bash",
    "./",
    "bash",
    "python",
    "cd ",
    "rg ",
    "sed ",
    "find ",
    "ls ",
    "cat ",
    "grep ",
    "git ",
    "make ",
    "nvcc",
    "pytest",
    "echo ",
    "curl ",
    "npm ",
    "node ",
    "go ",
    "cargo ",
)


def _unwrap_shell(t: str) -> str:
    """Strip a ``/bin/bash -lc "…"`` (or ``bash -lc '…'``) wrapper so the panel
    shows the actual command, not the shell boilerplate."""
    m = re.search(r"-lc\s+(['\"])(.+)\1\s*$", t)
    if m:
        return m.group(2).strip()
    m = re.search(r"-lc\s+(.+)$", t)
    if m:
        return m.group(1).strip()
    return t


def _describe_engineer_progress(event: dict[str, Any]) -> str:
    kind = str(event.get("kind") or "").lower()
    summary = " ".join(str(event.get("action_summary") or "").split())
    if summary:
        return summary[:72]
    if kind == "reasoning":
        return "reasoning"
    if kind in {"assistant_message", "agent_message", "message"}:
        return "reporting progress"
    text = str(event.get("text") or "")
    t = " ".join(str(text or "").split())
    low = t.lower()
    is_cmd = t.startswith(("/bin/bash", "./")) or " -lc " in t or low.startswith(_CMD_PREFIXES)
    if is_cmd:
        cmd = _unwrap_shell(t)
        return "run · " + cmd[:72]
    return "thinking" + (f" · {t[:60]}" if t else "")


def _describe_event(event: dict[str, Any]) -> tuple[str, str]:
    """Return ``(label, status)`` for a role-activity event."""
    etype = canonical_event_type(event.get("canonical_type") or event.get("type"))
    status = str(event.get("status") or "")
    if etype.startswith("agent.io."):
        run_label = str(event.get("run_label") or "").lower()
        if run_label == "matcher":
            label = "matching skills"
        elif run_label == "idea-search":
            label = "searching candidate ideas"
        elif run_label == "venue-research":
            label = "researching target venue"
        elif "reviewer" in run_label or run_label.startswith("review"):
            label = "reviewing"
        elif "planner" in run_label or run_label.startswith("plan"):
            label = "planning"
        elif (
            "manager" in run_label
            or run_label.startswith("router")
            or run_label.startswith("chat-")
            or run_label.startswith("simple-")
        ):
            label = "handling your message"
        else:
            match = re.search(r"(?:^|[-_.])r(?:ound)?[-_.]?(\d+)", run_label)
            label = f"round {match.group(1)}" if match else "working"
        if etype.endswith("error"):
            return f"{label} failed", "blocked"
        if etype.endswith("complete"):
            failed = event.get("turn_failed") is True
            exit_code = event.get("exit_code")
            if isinstance(exit_code, int) and exit_code != 0:
                failed = True
            return (f"{label} failed", "blocked") if failed else (f"{label} done", "done")
        return label, "running"
    if etype == "engineer.progress":
        return _describe_engineer_progress(event), "running"
    if etype == "venue.research.started":
        return "researching target venue", "running"
    if etype == "venue.research.completed":
        return "venue research done", "done"
    if etype == "idea.search.started":
        return "searching candidate ideas", "running"
    if etype == "idea.search.completed":
        return "candidate ideas ready", "done"
    if etype == "round.review.started":
        return "reviewing", "running"
    if etype == "round.review.deferred":
        return "continuing before review", "running"
    if etype == "round.review.completed":
        return f"verdict {status or 'done'}", status or "done"
    if etype == "round.start":
        rnd = event.get("round_index")
        return (f"round {rnd}" if rnd is not None else "new round"), "running"
    if etype == "loop.start" or etype == "life.mission.started":
        return "starting mission", "running"
    if etype == "loop.done" or etype == "life.mission.completed":
        lab = "done" if (not status or status == "done") else f"done · {status}"
        return lab, status or "done"
    if etype.startswith("life.planner"):
        verdict = str(event.get("verdict") or event.get("decision") or "")
        if etype.endswith("start"):
            return "planning new work", "running"
        return (f"plan verdict {verdict}" if verdict else "planning done"), verdict or "done"
    if etype.startswith("manager") or etype.startswith("life.manager"):
        # Front-door decisions must read as a TERSE state token, never the raw
        # hold-decision prose (which lives in text/reason and would leak a
        # truncated sentence into the compact role panel).
        if etype.startswith("life.manager.intent"):
            if etype.endswith("started"):
                return "triaging", "running"
            if etype.endswith("failed"):
                return "triage failed", status or "blocked"
            vert = " ".join(str(event.get("vertical") or "").split())[:16]
            return (f"routed · {vert}" if vert else "routed"), status or "done"
        if etype == "life.manager.stage_decision":
            verb = " ".join(str(event.get("action") or "hold").split())[:24]
            # A stage decision is a settled verdict, not an in-flight Manager
            # call. Treating its empty status as active kept the project header
            # on `working` for 90 seconds after mission completion.
            return verb or "hold", status or "done"
        verb = " ".join(
            str(event.get("action") or event.get("decision") or event.get("verdict") or "").split()
        )[:24]
        return (verb or "hold"), status or ""
    # generic — a single terse token, NEVER a raw text/reason sentence: a
    # hold-decision paragraph sliced to N chars leaked a truncated sentence into
    # the compact panel. Prefer a recognized terse field, else the last dotted
    # segment of the event type; cap hard at a small width.
    tok = " ".join(
        str(
            event.get("action")
            or event.get("decision")
            or event.get("verdict")
            or event.get("phase")
            or ""
        ).split()
    )
    if not tok:
        tok = etype.rsplit(".", 1)[-1] if etype else ""
    return (tok[:24] or "idle"), status or ""


# How long an inactive role keeps showing its last terse label before it decays
# to a clean "idle". Slightly longer than ``active_window_s`` so a just-finished
# role still reads its terse terminal label ("done" / "verdict …") for a few
# minutes (recency) before going quiet — matching how Planner/Reviewer read once
# they have no recent events in the tail. Without this, an inactive role froze
# its last (possibly verbose) label until it scrolled out of the 200-line tail.
STALE_LABEL_WINDOW_S: float = 180.0
# A provider turn can legitimately remain silent while reasoning or waiting on
# a tool. Keep an unmatched agent.io.start active through the runner's default
# 45-minute hard-idle window instead of falsely showing "Waiting" after 90s.
INFLIGHT_CALL_ACTIVE_WINDOW_S: float = 50 * 60.0


def role_activity(
    life_dir: Path | str,
    *,
    now: float | None = None,
    active_window_s: float = 90.0,
    stale_window_s: float = STALE_LABEL_WINDOW_S,
) -> dict[str, RoleActivity]:
    """Latest activity per role, plus which role is acting right now.

    Reads the ``events.jsonl`` tail. A role is ``active`` when it owns the most
    recent activity event and that event is fresh (< ``active_window_s``). A role
    that is NOT active and whose last event is older than ``stale_window_s``
    decays its label to a clean ``"idle"`` (instead of freezing a stale/verbose
    label until it scrolls out of the tail) — ``active`` and ``age_s`` are left
    as recorded.
    """
    now = now if now is not None else time.time()
    life_dir = Path(life_dir)
    events = _tail_jsonl(life_dir / "events.jsonl")
    latest: dict[str, dict[str, Any]] = {}
    latest_order: dict[str, int] = {}
    inflight_calls: dict[str, str] = {}
    for index, ev in enumerate(events):
        role = _event_role(ev)
        event_type = canonical_event_type(
            ev.get("canonical_type") or ev.get("type")
        )
        call_id = str(ev.get("call_id") or "").strip()
        if event_type == "agent.io.start" and role and call_id:
            inflight_calls[call_id] = role
        elif event_type in {"agent.io.complete", "agent.io.error"} and call_id:
            inflight_calls.pop(call_id, None)
        if role is None:
            continue
        latest[role] = ev
        latest_order[role] = index
    inflight_roles = set(inflight_calls.values())

    out: dict[str, RoleActivity] = {}
    for role in ROLES:
        ev = latest.get(role)
        if ev is None:
            out[role] = RoleActivity(
                role=role, active=False, label="idle", status="idle", age_s=None
            )
            continue
        label, status = _describe_event(ev)
        status = status or "idle"
        ts = ev.get("ts") or ev.get("time")
        age = (now - float(ts)) if isinstance(ts, (int, float)) else None
        event_type = canonical_event_type(
            ev.get("canonical_type") or ev.get("type")
        )
        effective_active_window = (
            max(active_window_s, INFLIGHT_CALL_ACTIVE_WINDOW_S)
            if role in inflight_roles
            else active_window_s
        )
        active = status not in {"done", "blocked", "idle"} and (
            age is None or age <= effective_active_window
        )
        if not active and (age is None or age > stale_window_s):
            # At rest: no longer the actor and its last event is stale → decay
            # the (possibly verbose/terminal) label to a clean "idle" instead of
            # freezing it until it scrolls out of the tail.
            label = "idle"
        out[role] = RoleActivity(
            role=role, active=active, label=label or "idle", status=status, age_s=age
        )
    pipeline_roles = [role for role in ("planner", "engineer", "reviewer") if out[role].active]
    if len(pipeline_roles) > 1:
        winner = max(pipeline_roles, key=lambda role: latest_order.get(role, -1))
        for role in pipeline_roles:
            if role != winner:
                out[role] = replace(out[role], active=False)
    return out


__all__ = [
    "INFLIGHT_CALL_ACTIVE_WINDOW_S",
    "RoleActivity",
    "STALE_LABEL_WINDOW_S",
    "role_activity",
]
