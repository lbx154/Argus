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


def _fmt_age(age_s: float | None) -> str:
    if age_s is None:
        return ""
    s = int(age_s)
    if s < 1:
        return "just now"
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    return f"{s // 3600}h ago"


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


def _effort_method(effort: str) -> str:
    return {
        "low": "dim", "medium": "cyan", "high": "yellow",
        "xhigh": "magenta", "max": "bold_red",
    }.get((effort or "").lower(), "yellow")


def _disp_width(text: str) -> int:
    """Printable column width (ANSI-stripped), counting CJK/full-width glyphs
    as 2 columns AND East-Asian *ambiguous*-width glyphs (``·`` U+00B7, ``●``
    U+25CF, arrows, …) as 2 — because a CJK-configured terminal renders those
    double-width.

    Under-counting ambiguous glyphs (treating them as 1) let the right-aligned
    ``roles · activity`` header render past what the width math reserved, so it
    wrapped to a 2nd physical row; the live in-place redraw counts *logical*
    lines, so it left a header copy behind on every ~80ms refresh — the panel
    "scrolled". Counting ambiguous as 2 is the SAFE direction: exact on a CJK
    terminal, a harmless slight over-estimate (extra right whitespace, never an
    overflow) on a non-CJK one."""
    import unicodedata
    s = _ANSI_STRIP(text)
    w = 0
    for ch in s:
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 1
    return w


def _clip_display(text: str, budget: int) -> str:
    """Trim a (plain, un-ANSI'd) string to at most ``budget`` display columns,
    CJK-aware, adding an ellipsis when it had to cut. Used to keep fixed panel
    lines from ever reaching the terminal edge (which would wrap and desync the
    in-place live redraw)."""
    import unicodedata
    if _disp_width(text) <= budget:
        return text
    acc, w = "", 0
    for ch in text:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 1
        if w + cw > budget - 1:
            break
        acc += ch
        w += cw
    return acc + "…"


def _clip_ansi_line(s: str, budget: int) -> str:
    """Clamp a possibly ANSI-colored line to at most ``budget`` display columns,
    keeping escape sequences intact (never cut mid-escape) and re-appending a
    reset if the cut happened inside a color run. This is the universal safety
    net that guarantees no panel line ever reaches the terminal edge — a wrapped
    line would desync the in-place live redraw and duplicate the header."""
    import re
    import unicodedata
    if budget <= 0:
        return ""
    if _disp_width(s) <= budget:
        return s
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    out: list[str] = []
    w = 0
    i = 0
    saw_color = False
    n = len(s)
    while i < n:
        m = ansi.match(s, i)
        if m:
            out.append(m.group())
            saw_color = True
            i = m.end()
            continue
        ch = s[i]
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 1
        if w + cw > budget:
            break
        out.append(ch)
        w += cw
        i += 1
    res = "".join(out)
    if saw_color:
        res += "\x1b[0m"
    return res


def format_roles_panel(
    theme: Any,
    configs: Sequence[RoleConfig],
    activities: Mapping[str, RoleActivity],
    *,
    header_right: str = "",
    width: int = 80,
    show_config: bool = False,
) -> str:
    """A compact, colored per-role panel: backend · model · effort, then the
    live activity on its own indented line. ``theme`` is optional (plain when
    ``None``); ``header_right`` is an already-styled right-aligned string
    (e.g. the daemon status)."""
    name_w = max((len(_ROLE_TITLE.get(c.role, c.role)) for c in configs),
                 default=8)
    lines: list[str] = []
    title_text = (
        "roles · backend / model / effort / activity"
        if show_config else
        "roles · activity"
    )
    # Keep every line strictly narrower than the terminal: a line that reaches the
    # FULL width auto-wraps to a second screen row, which throws off the live
    # in-place redraw's cursor-up count (→ duplicate header). On a narrow terminal
    # drop the right-hand daemon tag, then clip the title itself, so the title row
    # is always ≤ width-3 (a right safety margin, so even a residual
    # ambiguous-width miscount cannot push it to the terminal's last column and
    # wrap — the wrap the live redraw's logical-line count cannot erase).
    if header_right and (width - 5 - _disp_width(header_right)) < 8:
        header_right = ""  # no room for the tag on this row
    if header_right:
        title_text = _clip_display(title_text, max(8, width - 5 - _disp_width(header_right)))
        title = _paint(theme, "gray", title_text)
        gap = max(1, width - 5 - _disp_width(title_text) - _disp_width(header_right))
        lines.append("  " + title + " " * gap + header_right)
    else:
        title_text = _clip_display(title_text, max(8, width - 4))
        title = _paint(theme, "gray", title_text)
        lines.append("  " + title)
    lines.append("")
    for c in configs:
        act = activities.get(c.role)
        active = bool(act and act.active)
        dot_method = ROLE_COLOR_BOLD.get(c.role, "bold_green") if active else "gray"
        dot = _paint(theme, dot_method, "●" if active else "○")
        name_text = _ROLE_TITLE.get(c.role, c.role).ljust(name_w)
        # Colour = identity (always this role's hue), weight = activity (bold +
        # full colour only for the role acting right now; a quiet plain tint
        # otherwise) — so the eye tracks the "active" baton by brightness
        # while still recognising each role by its consistent hue at rest.
        name = role_paint(theme, c.role, name_text, bold=active)
        label = act.label if act else "idle"
        age = _fmt_age(act.age_s) if act else ""
        stat = act.status if act else "idle"
        tail = age + ((" · " + stat) if (age and stat and stat != "idle") else "")
        meta_plain = f"  ({tail})" if tail else ""
        if show_config:
            sep = _paint(theme, "dim", " · ")
            backend = _paint(theme, "cyan", c.backend_label)
            model = _paint(theme, "bold", c.model)
            if c.effort:
                effort = (_paint(theme, "dim", "effort ")
                          + _paint(theme, _effort_method(c.effort), c.effort))
            else:
                effort = _paint(theme, "dim", "effort —")
            lines.append(f"  {dot} {name}  {backend}{sep}{model}{sep}{effort}")
            # activity line (indented under the name). Clip the free-text label so a
            # long command never wraps — an in-place live redraw relies on a fixed
            # line count. Prefix "       ↳ " is 9 cols; keep the whole line ≤ width-2
            # (2-col margin) so it never reaches the terminal edge and wraps.
            budget = max(12, width - 11 - _disp_width(meta_plain))
            if _disp_width(label) > budget:
                label = _clip_display(label, budget)
            act_txt = label if active else _paint(theme, "dim", label)
            meta = _paint(theme, "dim", meta_plain) if tail else ""
            arrow = role_paint(theme, c.role, "↳", bold=True) if active else _paint(theme, "dim", "↳")
            lines.append(f"       {arrow} {act_txt}{meta}")
        else:
            # Default live view: one compact action row per role. Config belongs
            # in /roles, not in the constantly refreshed mission display.
            meta = _paint(theme, "dim", meta_plain) if tail else ""
            label_budget = max(10, width - 5 - name_w - _disp_width(meta_plain))
            if _disp_width(label) > label_budget:
                label = _clip_display(label, label_budget)
            act_txt = label if active else _paint(theme, "dim", label)
            lines.append(f"  {dot} {name}  {act_txt}{meta}")
    lines.append("")
    if show_config:
        # Footer env-var hint. Use the fully-detailed form when it fits; on a narrow
        # terminal fall back to a compact pointer (and finally clip) so this fixed
        # line never wraps and desyncs the in-place live redraw.
        hint_full = "change backend/model/effort: ARGUS_SKILL_<ROLE>_{BACKEND,MODEL,REASONING_EFFORT}"
        hint_short = "change backend/model/effort → ARGUS_SKILL_<ROLE>_*"
        if _disp_width(hint_full) <= width - 2:
            hint = hint_full
        elif _disp_width(hint_short) <= width - 2:
            hint = hint_short
        else:
            hint = _clip_display(hint_short, max(4, width - 3))
        lines.append("  " + _paint(theme, "dim", hint))
    # Universal safety net: guarantee no line ever reaches the terminal edge, so
    # the in-place live redraw's line count always matches the screen rows.
    return "\n".join(_clip_ansi_line(ln, width - 1) for ln in lines)


def _ANSI_STRIP(s: str) -> str:
    import re as _re
    return _re.sub(r"\x1b\[[0-9;]*m", "", s)


def format_roles_banner(
    theme: Any,
    configs: Sequence[RoleConfig] | None = None,
    *,
    label: str = "roles",
    collapse: bool = False,
    env: Mapping[str, str] | None = None,
    show_hint: bool = True,
) -> str:
    """Compact, config-only per-role block for the startup banner — one line per
    role: ``<name>  <backend> · <model> · effort <e>``. No live activity (all
    idle at launch); that lives in the on-demand ``/roles`` panel.

    Always lists all four roles by default so the operator explicitly sees each
    role's engine on launch. Pass ``collapse=True`` to fold identical roles into
    a single line when every role shares the same backend + model + effort.
    """
    configs = list(configs) if configs is not None else resolve_all_roles(env=env)
    if not configs:
        return ""
    lbl = _paint(theme, "gray", f"{label:<7}")
    sep = _paint(theme, "dim", " · ")

    def _cfg_span(c: RoleConfig) -> str:
        backend = _paint(theme, "cyan", c.backend_label)
        model = _paint(theme, "bold", c.model)
        if c.effort:
            eff = _paint(theme, "dim", "effort ") + _paint(theme, _effort_method(c.effort), c.effort)
        else:
            eff = _paint(theme, "dim", "effort —")
        return f"{backend}{sep}{model}{sep}{eff}"

    keys = {(c.backend_label, c.model, c.effort) for c in configs}
    if collapse and len(keys) == 1:
        # Four small role-coloured dots keep the "four roles" identity visible
        # even when every role shares one config line — a preview of the same
        # colour language the live panel uses once a mission is running.
        dots = " ".join(role_paint(theme, c.role, "●") for c in configs)
        one = _cfg_span(configs[0])
        hint = _paint(theme, "dim", "  ·  /roles for details") if show_hint else ""
        return f"  {lbl} {dots}  {one}{hint}"

    name_w = max(len(_ROLE_TITLE.get(c.role, c.role)) for c in configs)
    lines: list[str] = []
    for i, c in enumerate(configs):
        head = lbl if i == 0 else _paint(theme, "gray", " " * 7)
        name_text = _ROLE_TITLE.get(c.role, c.role).ljust(name_w)
        name = role_paint(theme, c.role, name_text)  # bold=True: static reference list
        lines.append(f"  {head} {name}  {_cfg_span(c)}")
    if show_hint:
        lines.append("  " + _paint(theme, "gray", " " * 7) + " "
                     + _paint(theme, "dim", "type /roles for backend / model / effort details"))
    return "\n".join(lines)


def format_prompt_activity_suffix(
    life_dir: Path | str | None, theme: Any = None,
) -> str:
    """Compact "● Role" suffix naming whichever role is active RIGHT NOW.

    This is the one piece of ambient status a single-agent tool (Codex CLI,
    Claude Code) structurally never needs to answer, since it only ever has
    one actor. Argus always has four (Manager/Planner/Engineer/Reviewer)
    cooperating on the SAME objective, so "who is actually driving this
    instant" is core status, not a nice-to-have — and today it was only
    visible only in the retired Python terminal UI or by manually typing
    ``/roles``. This makes the single highest-value bit
    of that panel (is anyone active, and which one) zero-config and always
    on, reusing the exact same ``role_activity`` source and ``●``/colour
    convention as ``/roles`` and the live cockpit panel.

    Returns "" when there is no life_dir, no role is currently active, or
    anything about reading ``events.jsonl`` fails — fail-soft, since this
    must never break the input prompt.
    """
    if not life_dir:
        return ""
    try:
        activities = role_activity(life_dir)
        active = next((a for a in activities.values() if a.active), None)
        if active is None:
            return ""
        dot = role_paint(theme, active.role, "●", bold=True)
        title = role_paint(theme, active.role, _ROLE_TITLE.get(active.role, active.role),
                           bold=True)
        return f"{dot} {title}"
    except Exception:  # noqa: BLE001
        return ""


def format_prompt_status_line(
    theme: Any, *, env: Mapping[str, str] | None = None,
    life_dir: Path | str | None = None,
) -> str:
    """One-line backend/model (+ active-role) summary for the REPEATING input
    prompt.

    The startup banner's ``format_roles_banner`` prints once and then scrolls
    out of view after the very first reply — an operator several turns into a
    conversation has no ambient way to see which engine is live without
    separately typing ``/roles``, and a model/backend switch (see
    ``_apply_config_intent`` in ``manager/config_intent.py``) only proved
    itself for that one turn. Surfacing this line in the prompt box itself
    (drawn every turn, right where the operator is about to type) keeps it
    persistently visible instead of a one-shot banner. Collapses to the
    shared value when every role agrees (the common case); a short "mixed"
    hint otherwise, since the full per-role breakdown already lives in
    ``/roles``.

    ``life_dir``, when given, additionally appends
    :func:`format_prompt_activity_suffix` — which of the four roles is
    active right now — so the multi-agent-specific "who's driving" status is
    on the same always-visible line, not tucked behind ``/roles`` or a live
    cockpit env var. Omitted (``None``, the default) reproduces the exact
    prior backend/model-only output, byte for byte.
    """
    configs = resolve_all_roles(env=env)
    if not configs:
        return ""
    keys = {(c.backend_label, c.model) for c in configs}
    if len(keys) == 1:
        c = configs[0]
        base = _paint(theme, "cyan", f"{c.backend_label} · {c.model}")
    else:
        base = _paint(theme, "dim", "mixed backends/models — /roles for details")
    suffix = format_prompt_activity_suffix(life_dir, theme)
    return f"{base}  {suffix}" if suffix else base


def render_roles_snapshot(
    life_dir: Path | str, theme: Any = None, *, width: int = 80,
    header_right: str = "", env: Mapping[str, str] | None = None,
    show_config: bool = False,
) -> str:
    """One-shot convenience: resolve configs + live activity and render."""
    configs = resolve_all_roles(env=env)
    activities = role_activity(life_dir)
    return format_roles_panel(theme, configs, activities,
                              header_right=header_right, width=width,
                              show_config=show_config)
