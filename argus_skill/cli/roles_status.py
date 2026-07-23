"""Resolve, for each agent role, its backend / model / reasoning-effort and its
live activity — so the cockpit can show *what every role is doing right now*.

Argus runs four cooperating roles (plus a curator pool):

* **Manager**  — front door: classifies operator free text as chat vs task,
  approves distilled skills, decides stage transitions.
* **Planner**  — when the backlog is empty, plans the next batch of work
  (continuous mode) and drives paper finalization dispatch.
* **Engineer** — L1: writes code / runs commands, one round at a time.
* **Reviewer** — L2: structured done / continue / blocked verdict.

Each role independently resolves three knobs at runtime, all surfaced here:

* **backend** — ``ARGUS_SKILL_{ROLE}_BACKEND`` → ``ARGUS_SKILL_RUNNER_BACKEND``
  → ``ARGUS_SKILL_LIFE_BACKEND`` → a persisted ``/backend`` switch
  (``core.knob_store``) → ``codex`` (one of Codex / Claude Code / Copilot / OpenCode;
  ``memory`` in tests).
* **model** — ``ARGUS_SKILL_{ROLE}_MODEL`` (``ARGUS_SKILL_PLAN_MODEL`` for the
  planner) → ``ARGUS_SKILL_MODEL`` → a persisted ``/backend``/``/config``
  switch → the capability-vault route → the ``gpt-5.5`` default.
* **reasoning effort** — ``ARGUS_SKILL_{ROLE}_REASONING_EFFORT`` → a persisted
  switch → ``xhigh``. Only meaningful for reasoning models (gpt-5.x /
  o-series); shown as ``—`` for a non-reasoning model.

All three precedence chains are canonically implemented once in
``core.knobs`` (``resolve_role_backend`` / ``resolve_role_model`` /
``resolve_role_reasoning_effort``) — this module calls them rather than
re-implementing the precedence, so a persisted switch is honored consistently
everywhere it's read, not just wherever it happened to be made.

Live activity is derived from the project's ``events.jsonl`` tail (no new
telemetry): the newest event mapped to each role, the role that is active right
now, and a short human label of what it is doing.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

# Public role order (front-to-back through a mission's lifecycle).
ROLES: tuple[str, ...] = ("manager", "planner", "engineer", "reviewer")

_BACKEND_LABEL = {
    "codex": "Codex",
    "claude": "Claude Code",
    "copilot": "Copilot",
    "opencode": "OpenCode",
    "memory": "memory",
}

# Which vault route + env overrides each role reads for its model. The Manager's
# Manager triage runner reuses the engineer route/effort (see front_door._ensure_manager_
# runner), so we mirror that here.
_ROLE_ROUTE = {
    "manager": "engineer",
    "planner": "planner",
    "engineer": "engineer",
    "reviewer": "reviewer",
    "curator": "curator",
}
_ROLE_MODEL_ENV = {
    "manager": "ARGUS_SKILL_ENGINEER_MODEL",
    "planner": "ARGUS_SKILL_PLAN_MODEL",
    "engineer": "ARGUS_SKILL_ENGINEER_MODEL",
    "reviewer": "ARGUS_SKILL_REVIEWER_MODEL",
    "curator": "ARGUS_SKILL_CURATOR_MODEL",
}
_ROLE_EFFORT_ENV = {
    "manager": "ARGUS_SKILL_MANAGER_REASONING_EFFORT",
    "planner": "ARGUS_SKILL_PLANNER_REASONING_EFFORT",
    "engineer": "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
    "reviewer": "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
    "curator": "ARGUS_SKILL_CURATOR_REASONING_EFFORT",
}

_ROLE_DESC = {
    "manager": "front door · triages chat/tasks, approves skills",
    "planner": "queues new work when backlog is empty, final-gate routing",
    "engineer": "L1 execution · writes code / runs commands",
    "reviewer": "L2 acceptance · done / continue / blocked",
    "curator": "skill-pool upkeep · distill / write-back",
}


# ── config resolution ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class RoleConfig:
    role: str
    backend: str          # normalized: codex / claude / copilot / opencode / memory
    backend_label: str    # display: Codex / Claude Code / Copilot / OpenCode
    model: str
    effort: str | None    # None → not a reasoning model (effort N/A)
    desc: str


def _normalize_backend(raw: str) -> str:
    raw = (raw or "").strip().lower()
    if raw == "memory":
        return "memory"
    try:
        from ..agent_cli.runner_backend import normalize_runner_backend
        return normalize_runner_backend(raw or None)
    except Exception:  # noqa: BLE001 — never fail the display
        return raw or "codex"


def _resolve_backend(role: str, env: Mapping[str, str]) -> str:
    from ..core.knobs import resolve_role_backend

    requested = resolve_role_backend(role, env=env)
    normalized = _normalize_backend(requested)
    if normalized == "memory":
        return normalized
    from ..agent_cli.runner_backend import resolve_available_runner

    configured = (
        str(env.get(f"ARGUS_SKILL_{role.upper()}_RUNNER_BIN", "") or "").strip()
        or str(env.get("ARGUS_SKILL_RUNNER_BIN", "") or "").strip()
    )
    effective, _runner_bin = resolve_available_runner(
        requested,
        configured or None,
    )
    return effective


def runner_backend_label(env: Mapping[str, str] | None = None) -> str:
    """Display label of the *current* runner backend, resolved from
    ``ARGUS_SKILL_RUNNER_BACKEND`` →
    ``ARGUS_SKILL_LIFE_BACKEND`` → a persisted ``/backend`` switch → ``codex``.

    Used by user-facing copy (status phrases, the Manager chat identity) so the
    single-worker SELF path names the backend the operator actually configured
    instead of a hardcoded "Codex". Fail-soft to "Codex" so a resolution hiccup
    never breaks the line it decorates.
    """
    env = env if env is not None else os.environ
    try:
        backend = _resolve_backend("manager", env)
        return _BACKEND_LABEL.get(backend, backend or "Codex")
    except Exception:  # noqa: BLE001 — display copy must never crash
        return "Codex"


def _resolve_model(role: str, env: Mapping[str, str]) -> str:
    from ..core.knobs import resolve_role_model

    try:
        return resolve_role_model(
            _ROLE_ROUTE.get(role, "text"),
            role_env=_ROLE_MODEL_ENV.get(role, ""),
            env=env,
        )
    except Exception:  # noqa: BLE001
        return "gpt-5.5"


def is_reasoning_model(model: str) -> bool:
    """True when ``model`` supports a reasoning-effort knob (gpt-5.x / o-series).

    A non-reasoning model (e.g. a plain chat model) has no effort setting, so
    the display shows ``—`` rather than a misleading value.
    """
    m = (model or "").strip().lower()
    if not m:
        return False
    if m.startswith("gpt-5") or m.startswith("gpt5"):
        return True
    if re.match(r"^o[1-9]", m):  # o1 / o3 / o4 …
        return True
    return "reason" in m


def _resolve_effort(role: str, model: str, env: Mapping[str, str]) -> str | None:
    if not is_reasoning_model(model):
        return None
    from ..core.knobs import resolve_role_reasoning_effort

    role_env = _ROLE_EFFORT_ENV.get(role, "")
    if role == "manager":
        # Manager triage reuses the engineer effort; Manager._core also
        # defaults to xhigh. Check manager's own knob (env, then a persisted
        # switch) before falling back to engineer's (same two layers), so an
        # explicit manager-specific switch on EITHER layer still wins.
        val = resolve_role_reasoning_effort(role_env, env=env, default="")
        if val:
            return val
        return resolve_role_reasoning_effort(
            "ARGUS_SKILL_ENGINEER_REASONING_EFFORT", env=env, default="xhigh",
        )
    return resolve_role_reasoning_effort(role_env, env=env, default="xhigh")


def resolve_role_config(role: str, *, env: Mapping[str, str] | None = None) -> RoleConfig:
    env = env if env is not None else os.environ
    backend = _resolve_backend(role, env)
    model = _resolve_model(role, env)
    effort = _resolve_effort(role, model, env)
    return RoleConfig(
        role=role,
        backend=backend,
        backend_label=_BACKEND_LABEL.get(backend, backend or "codex"),
        model=model,
        effort=effort,
        desc=_ROLE_DESC.get(role, ""),
    )


def resolve_all_roles(
    roles: Sequence[str] = ROLES, *, env: Mapping[str, str] | None = None
) -> list[RoleConfig]:
    return [resolve_role_config(r, env=env) for r in roles]


# ── live activity (from events.jsonl) ─────────────────────────────────────

@dataclass(frozen=True)
class RoleActivity:
    role: str
    active: bool          # the role acting right now
    label: str            # short "what it is doing"
    status: str           # running / idle / done / blocked / …
    age_s: float | None   # seconds since the driving event


def _tail_jsonl(path: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    # Reuse the reverse chunk reader used by persistent life memory. Reading
    # ``Path.read_text()`` here used to load every retained 100 MiB event-log
    # generation merely to show four role labels when switching Web projects.
    from ..life.memory import _read_jsonl_tail_history

    return _read_jsonl_tail_history(path, limit)


def _event_role(event: dict[str, Any]) -> str | None:
    layer = event.get("agent_layer")
    if isinstance(layer, str) and layer in ROLES:
        return layer
    etype = str(event.get("type") or "")
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
        if ("manager" in label or label.startswith("router")
                or label.startswith("chat-") or label.startswith("simple-")):
            return "manager"
        return "engineer"
    if etype.startswith("life.planner."):
        return "planner"
    if etype == "round.review.deferred":
        return "engineer"
    if etype.startswith("round.review") or etype.startswith("reviewer"):
        return "reviewer"
    if etype in {"life.mission.started", "loop.start", "round.start",
                 "round.main.completed", "loop.done", "engineer.progress"} \
            or etype.startswith("engineer"):
        return "engineer"
    if etype.startswith("manager") or etype.startswith("life.manager"):
        return "manager"
    return None


_CMD_PREFIXES = ("/bin/bash", "./", "bash", "python", "cd ", "rg ", "sed ",
                 "find ", "ls ", "cat ", "grep ", "git ", "make ", "nvcc",
                 "pytest", "echo ", "curl ", "npm ", "node ", "go ", "cargo ")


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
    is_cmd = (t.startswith(("/bin/bash", "./")) or " -lc " in t
              or low.startswith(_CMD_PREFIXES))
    if is_cmd:
        cmd = _unwrap_shell(t)
        return "run · " + cmd[:72]
    return "thinking" + (f" · {t[:60]}" if t else "")


def _describe_event(event: dict[str, Any]) -> tuple[str, str]:
    """Return ``(label, status)`` for a role-activity event."""
    etype = str(event.get("type") or "")
    status = str(event.get("status") or "")
    if etype.startswith("agent.io."):
        run_label = str(event.get("run_label") or "").lower()
        if run_label == "matcher":
            label = "matching skills"
        elif run_label == "idea-search":
            label = "searching candidate ideas"
        elif "reviewer" in run_label or run_label.startswith("review"):
            label = "reviewing"
        elif "planner" in run_label or run_label.startswith("plan"):
            label = "planning"
        elif ("manager" in run_label or run_label.startswith("router")
              or run_label.startswith("chat-") or run_label.startswith("simple-")):
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
            str(event.get("action") or event.get("decision")
                or event.get("verdict") or "").split()
        )[:24]
        return (verb or "hold"), status or ""
    # generic — a single terse token, NEVER a raw text/reason sentence: a
    # hold-decision paragraph sliced to N chars leaked a truncated sentence into
    # the compact panel. Prefer a recognized terse field, else the last dotted
    # segment of the event type; cap hard at a small width.
    tok = " ".join(
        str(event.get("action") or event.get("decision")
            or event.get("verdict") or event.get("phase") or "").split()
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


def role_activity(life_dir: Path | str, *, now: float | None = None,
                  active_window_s: float = 90.0,
                  stale_window_s: float = STALE_LABEL_WINDOW_S) -> dict[str, RoleActivity]:
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
    for index, ev in enumerate(events):
        role = _event_role(ev)
        if role is None:
            continue
        latest[role] = ev
        latest_order[role] = index

    out: dict[str, RoleActivity] = {}
    for role in ROLES:
        ev = latest.get(role)
        if ev is None:
            out[role] = RoleActivity(role=role, active=False, label="idle",
                                     status="idle", age_s=None)
            continue
        label, status = _describe_event(ev)
        status = status or "idle"
        ts = ev.get("ts") or ev.get("time")
        age = (now - float(ts)) if isinstance(ts, (int, float)) else None
        active = (
            status not in {"done", "blocked", "idle"}
            and (age is None or age <= active_window_s)
        )
        if not active and (age is None or age > stale_window_s):
            # At rest: no longer the actor and its last event is stale → decay
            # the (possibly verbose/terminal) label to a clean "idle" instead of
            # freezing it until it scrolls out of the tail.
            label = "idle"
        out[role] = RoleActivity(role=role, active=active,
                                 label=label or "idle",
                                 status=status, age_s=age)
    pipeline_roles = [
        role for role in ("planner", "engineer", "reviewer")
        if out[role].active
    ]
    if len(pipeline_roles) > 1:
        winner = max(pipeline_roles, key=lambda role: latest_order.get(role, -1))
        for role in pipeline_roles:
            if role != winner:
                out[role] = replace(out[role], active=False)
    return out


# ── rendering (theme is duck-typed: any object with red/green/…/bold/dim) ──

_ROLE_TITLE = {
    "manager": "Manager", "planner": "Planner",
    "engineer": "Engineer", "reviewer": "Reviewer", "curator": "Curator",
}

# Fixed role → hue. The SAME role always gets the SAME colour everywhere it is
# named — startup banner, ``/roles`` panel, the live spinner, and the scrolling
# event feed — so an operator builds a one-glance colour→role association
# instead of re-reading text every time (this is the "colour = identity" half
# of a colour=identity / weight=activity split; see ``role_paint``). Chosen to
# avoid the two hues this codebase already uses for other meanings (bold_green
# = "active now" dot, plain yellow = warnings) by pairing intensity with role
# instead of overloading a single hue for two concepts.
ROLE_COLOR: dict[str, str] = {
    "manager": "cyan",
    "planner": "magenta",
    "engineer": "green",
    "reviewer": "yellow",
}
ROLE_COLOR_BOLD: dict[str, str] = {
    "manager": "bold_cyan",
    "planner": "bold_magenta",
    "engineer": "bold_green",
    "reviewer": "bold_yellow",
}


def _paint(theme: Any, method: str, text: str) -> str:
    if theme is None or not text:
        return text
    fn = getattr(theme, method, None)
    if not callable(fn):
        return text
    try:
        return str(fn(text))
    except Exception:  # noqa: BLE001
        return text


def role_paint(theme: Any, role: str, text: str, *, bold: bool = True) -> str:
    """Paint ``text`` in ``role``'s signature hue (see ``ROLE_COLOR``).

    ``bold=True`` (default) uses the full bold variant — for a static
    reference (the startup banner) or the currently-active role in a live
    panel. ``bold=False`` renders a softer plain tint for an idle role, so it
    still carries its identity colour at rest without competing for
    attention with whichever role is active right now. Unknown roles (e.g.
    ``curator``) fall through unpainted.
    """
    table = ROLE_COLOR_BOLD if bold else ROLE_COLOR
    method = table.get((role or "").strip().lower())
    return _paint(theme, method, text) if method else text

