"""``argus-skill`` web/TUI backend API — thin FastAPI layer over the daemon.

The 7×24 daemon is a file-based pub/sub: it appends events to
``<life_dir>/events.jsonl`` and reads commands from ``backlog.jsonl`` /
``inbox.jsonl``. Both new frontends — the Ink terminal UI (``frontend/tui/``)
and the React web UI (``frontend/web/``) — are **clients of this one API**, so
neither reimplements backend logic.

Design rules (keep this layer dumb):
- Every endpoint DELEGATES to an existing ``argus_skill`` function. This module
  never parses event semantics or backlog schemas itself — it forwards dicts and
  calls the reused helpers (``list_sessions``, ``read_daemon_status``,
  ``role_activity``, ``resolve_all_roles``, ``_read_recent_jsonl_events``,
  ``LifeMemory.backlog``).
- Defaults to a ``127.0.0.1`` bind — unlike ``tools/dashboard.py`` which binds
  ``0.0.0.0`` with no auth. Expose to a LAN only via an explicit ``--web-host``.
- ``fastapi`` / ``uvicorn`` are the optional ``[web]`` extra; import them lazily
  inside :func:`create_app` / :func:`serve` so importing this module never
  hard-requires them.

M0 scope: ``GET /api/projects``, ``GET /api/projects/{sid}/snapshot``,
``GET /api/projects/{sid}/events``, ``WS /api/projects/{sid}/stream``.
Command POSTs (task/nudge/daemon start-stop/config) land in M1.
"""

# NB: deliberately NO ``from __future__ import annotations`` here — the nested
# FastAPI route handlers in create_app() annotate params with the locally-
# imported ``WebSocket``/``Query`` types, and stringized annotations would make
# FastAPI fail to resolve them (it reads annotations against module globals,
# where the lazily-imported fastapi symbols do not live). Runtime ``X | None``
# unions are fine on the required Python >=3.11.

import asyncio
import json
import mimetypes
import os
import queue
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any

from ..apps._inbox import count_pending_inbox_messages, queue_inbox_message
from ..apps._life_actions import add_backlog_item, append_note, parse_add_flags
from ..apps.cli._follow import _read_recent_jsonl_events, _read_recent_project_events
from ..cli.roles_status import RoleActivity, RoleConfig, resolve_all_roles, role_activity
from ..core import paths as core_paths
from ..core.config_snapshot import build_config_snapshot
from ..core.session import SessionMeta, list_sessions, read_session_meta
from ..core.transcript import first_operator_text, read_turns
from ..daemon.life_worker import (
    DaemonStatus,
    LifeWorkerConfig,
    read_continuous_state,
    read_daemon_status,
    spawn_detached_daemon,
    stop_daemon,
    write_continuous_config,
)
from ..life.memory import LifeMemory, _read_jsonl_tail_history
from ..tools.doctor import run_diagnostics

__all__ = [
    "create_app", "serve", "project_life_dir", "build_snapshot", "list_projects",
    "enqueue_task", "enqueue_nudge", "start_project_daemon", "stop_project_daemon",
    "set_continuous", "get_status", "get_journal", "add_project_note",
    "dispose_backlog", "stop_backlog_iteration", "get_doctor", "get_config",
    "get_identity", "get_transcript",
    "get_backlog_item",
    "list_project_artifacts", "get_project_artifact",
]

EVENT_FILE = "events.jsonl"
_SPEND_CACHE_TTL_SECONDS = 30.0
_SPEND_CACHE: dict[str, tuple[float, float]] = {}
_SPEND_CACHE_LOCK = threading.Lock()
_JOURNAL_TAIL_CACHE: dict[
    tuple[str, int],
    tuple[tuple[tuple[int, int, int] | None, tuple[int, int, int] | None], list[dict[str, Any]]],
] = {}
_JOURNAL_TAIL_CACHE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Pure helpers (no FastAPI import — unit-testable without the [web] extra)
# ---------------------------------------------------------------------------

def _global_root(global_root: Path | str | None) -> Path:
    return Path(global_root) if global_root is not None else core_paths.global_root()


def _manager_stream_heartbeat_seconds() -> float:
    """Silence interval before SSE reports that it is awaiting a model event.

    This is status, not invented chain-of-thought: the frame says only what the
    bridge can verify (the Manager turn is still alive and ACP has emitted
    nothing new). Set ``ARGUS_SKILL_MANAGER_STREAM_HEARTBEAT_S=0`` to disable.
    """
    raw = os.environ.get("ARGUS_SKILL_MANAGER_STREAM_HEARTBEAT_S", "10")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 10.0


def _iter_manager_stream_items(
    items: Any,
    *,
    heartbeat_s: float,
    clock: Any = None,
):
    """Drain Manager fragments and add honest heartbeat phase frames.

    ``items`` is queue-like solely to keep this helper deterministic in tests.
    Heartbeats never advance ``last_real_at``; therefore their ``quiet_s`` is
    the elapsed time since the most recent genuine phase/delta from the worker.
    A real event resets that clock, and the sentinel stops heartbeats at once.
    """
    now = clock or time.monotonic
    last_real_at = now()
    while True:
        try:
            item = items.get(timeout=heartbeat_s) if heartbeat_s > 0 else items.get()
        except queue.Empty:
            quiet_s = max(0, int(now() - last_real_at))
            yield {
                "type": "phase",
                "role": "manager",
                "label": f"Manager · waiting for the next model event · {quiet_s}s quiet",
                "heartbeat": True,
                "quiet_s": quiet_s,
            }
            continue
        if item is None:
            return
        last_real_at = now()
        yield item


def project_life_dir(sid: str, *, global_root: Path | str | None = None) -> Path | None:
    """Resolve ``<global_root>/projects/<sid>`` for a session id, or ``None`` if
    ``sid`` is unsafe or the project directory does not exist.

    Rejects path traversal: the resolved dir MUST live directly under
    ``<global_root>/projects``.
    """
    root = _global_root(global_root)
    projects = (root / "projects").resolve()
    try:
        life_dir = (projects / sid).resolve()
    except (OSError, ValueError):
        return None
    if life_dir.parent != projects:
        return None  # traversal / nested — reject
    if not life_dir.is_dir():
        return None
    return life_dir


def _daemon_dict(st: DaemonStatus) -> dict[str, Any]:
    return {
        "alive": bool(st.alive),
        "pid": st.pid,
        "started_at_iso": st.started_at_iso,
        "uptime_seconds": st.uptime_seconds,
        "backend": st.backend,
        "per_mission_cap_usd": st.per_mission_cap_usd,
        "daily_cap_usd": st.daily_cap_usd,
        "global_daily_cap_usd": st.global_daily_cap_usd,
    }


def _roles_list(
    configs: list[RoleConfig], activities: dict[str, RoleActivity]
) -> list[dict[str, Any]]:
    """Merge static RoleConfig with live RoleActivity into one flat list — the
    exact shape the frontends' roles panel consumes (no client re-derivation)."""
    out: list[dict[str, Any]] = []
    for c in configs:
        act = activities.get(c.role)
        out.append({
            "role": c.role,
            "backend": c.backend,
            "backend_label": c.backend_label,
            "model": c.model,
            "effort": c.effort,
            "active": bool(act.active) if act else False,
            "label": act.label if act else "idle",
            "status": act.status if act else "idle",
            "age_s": act.age_s if act else None,
        })
    return out


def _session_dict(meta: SessionMeta | None, sid: str) -> dict[str, Any]:
    if meta is None:
        return {"id": sid, "display_name": "", "objective": "", "last_active": 0.0, "cwd": ""}
    return {
        "id": meta.id,
        "display_name": meta.display_name,
        "objective": meta.objective,
        "last_active": meta.last_active,
        "cwd": meta.cwd,
    }


def _compact_backlog_item(item: Any) -> dict[str, Any]:
    """Small, presentation-safe backlog row for frequently-polled UI snapshots.

    Full objectives can be tens of kilobytes. Cockpits only need the title and
    lifecycle metadata; the full item remains available from the default
    snapshot and write endpoints.
    """
    objective = str(getattr(item, "objective", "") or "")
    title = str(getattr(item, "title", "") or "").strip()
    if not title:
        title = objective.splitlines()[0][:180]
    return {
        "id": str(getattr(item, "id", "")),
        "title": title,
        "objective": "" if title else objective[:240],
        "status": str(getattr(item, "status", "pending")),
        "priority": int(getattr(item, "priority", 100)),
        "max_cost_usd": float(getattr(item, "max_cost_usd", 0.0)),
        "iterate": bool(getattr(item, "iterate", False)),
        "pending_question": str(getattr(item, "pending_question", "") or "")[:500],
    }


def _settled_spend(mem: LifeMemory | None, life_dir: Path) -> float:
    """Bound the cost of deriving all-history spend from a large event log.

    EventJournal is intentionally canonical, but reconstructing years of rows on
    every 4–5 second UI poll is wasteful. A short process-local TTL preserves the
    same authority while keeping the live cockpit responsive.
    """
    if mem is None:
        return 0.0
    key = str(life_dir.resolve())
    now = time.monotonic()
    with _SPEND_CACHE_LOCK:
        cached = _SPEND_CACHE.get(key)
        if cached is not None and now - cached[0] < _SPEND_CACHE_TTL_SECONDS:
            return cached[1]
        try:
            total = float(mem.journal.total_cost_since(0))
        except Exception:  # noqa: BLE001
            total = 0.0
        _SPEND_CACHE[key] = (now, total)
        return total


def _stat_signature(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (int(getattr(stat, "st_ino", 0) or 0), int(stat.st_size), int(stat.st_mtime_ns))


def build_snapshot(
    sid: str,
    *,
    global_root: Path | str | None = None,
    events_limit: int = 80,
    compact: bool = False,
) -> dict[str, Any] | None:
    """One-shot project snapshot: session + daemon + roles + backlog + recent
    events. Returns ``None`` if the project does not exist. Fail-soft per
    sub-section — a broken backlog/roles read degrades to empty, never raises."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    root = _global_root(global_root)

    try:
        st = read_daemon_status(life_dir)
        daemon = _daemon_dict(st)
    except Exception:  # noqa: BLE001 — snapshot must never raise
        daemon = {"alive": False, "pid": None}

    try:
        roles = _roles_list(resolve_all_roles(env=os.environ), role_activity(life_dir))
    except Exception:  # noqa: BLE001
        roles = []

    # The daemon's persisted status.json ``backend`` is stamped once at boot and
    # goes stale — a daemon started before a backend switch keeps reporting the
    # old value (e.g. "codex" for a run whose roles now execute on copilot). The
    # roles list, resolved live above, is the truth. Align the daemon pill to the
    # engineer role's backend so both surfaces agree and a stale file can't
    # mislabel; fall back to the status field only if roles failed to resolve.
    eng = next((r for r in roles if r.get("role") == "engineer"), roles[0] if roles else None)
    if eng and eng.get("backend"):
        daemon["backend"] = eng["backend"]
        daemon["backend_label"] = eng.get("backend_label") or daemon.get("backend")

    items: list[Any] = []
    try:
        mem = LifeMemory.open(life_dir)
        items = list(mem.backlog.all())
        backlog = (
            [_compact_backlog_item(it) for it in items]
            if compact else [it.to_jsonable() for it in items]
        )
    except Exception:  # noqa: BLE001
        backlog = []
        mem = None

    # Authoritative settled spend for THIS project — the daemon journals the
    # per-mission cost_usd (engineer + reviewer + scientist + util + copilot),
    # so total_cost_since(0) is the real total across the whole history (not the
    # streamed buffer). Cheap: the journal is distilled, not the raw event log.
    spend_usd = _settled_spend(mem, life_dir)

    try:
        recent = _read_recent_project_events(life_dir, limit=events_limit)
    except Exception:  # noqa: BLE001
        recent = []

    snapshot = {
        "session": _session_dict(read_session_meta(root, sid), sid),
        "daemon": daemon,
        "roles": roles,
        "backlog": backlog,
        "recent_events": recent,
        "spend_usd": spend_usd,
    }
    if compact:
        try:
            cont = read_continuous_state(life_dir)
        except Exception:  # noqa: BLE001
            cont = None
        snapshot["continuous"] = (
            {
                "enabled": cont.enabled,
                "objective": cont.objective,
                "done_reason": cont.done_reason,
                "done_at": cont.done_at,
            }
            if cont is not None else {"enabled": False, "objective": ""}
        )
        snapshot["pending_questions"] = [
            _compact_backlog_item(it) for it in items
            if getattr(it, "pending_question", "")
        ]
    return snapshot


def list_projects(
    *, global_root: Path | str | None = None, limit: int | None = None,
    include_empty: bool = False,
) -> list[dict[str, Any]]:
    """Sessions worth showing in a picker (newest-active first), each enriched
    with a lightweight daemon-alive flag.

    ``include_empty=False`` (the default) hides the content-less shells that bare
    launches mint — but always keeps a project with a live daemon — so a picker
    shows real/running work, not thousands of empty litter dirs. ``limit`` caps
    the result to the N most-recently-active, bounding the per-item daemon-status
    reads (a real cost when a global root has accumulated thousands of sessions).
    """
    root = _global_root(global_root)
    out: list[dict[str, Any]] = []
    for meta in list_sessions(root, include_empty=include_empty):
        item = _session_dict(meta, meta.id)
        life_dir = root / "projects" / meta.id
        try:
            st = read_daemon_status(life_dir)
            item["daemon_alive"] = bool(st.alive)
            item["daemon_pid"] = st.pid
            item["uptime_seconds"] = st.uptime_seconds
        except Exception:  # noqa: BLE001
            item["daemon_alive"] = False
            item["daemon_pid"] = None
            item["uptime_seconds"] = None
        # Conversational sessions intentionally keep sparse session metadata.
        # Once a campaign exists, its persisted objective is the best picker
        # label/subtitle and should outrank the first greeting.
        try:
            cont = read_continuous_state(life_dir)
            campaign_objective = str(cont.objective or "").strip()
        except Exception:  # noqa: BLE001
            campaign_objective = ""
        if not item.get("objective") and campaign_objective:
            item["objective"] = campaign_objective
        # A human label for a picker: display name → objective → first operator
        # line → the session id.
        label = item.get("display_name") or item.get("objective") or ""
        if not label:
            try:
                label = first_operator_text(life_dir)[:60]
            except Exception:  # noqa: BLE001
                label = ""
        item["label"] = label or meta.id
        out.append(item)
        if limit and len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Command helpers (write side) — all go through the SAME reused functions the
# CLI uses, so the flock CAS / atomic writes are shared. Never write the
# backlog/inbox files directly. Each returns None if the project is unknown.
# ---------------------------------------------------------------------------

def enqueue_task(
    sid: str, text: str, *, global_root: Path | str | None = None
) -> dict[str, Any] | None:
    """Append a backlog task (honours inline ``--once/--cycles/--budget``).
    Goes through ``add_backlog_item`` → ``Backlog.add`` (flock CAS)."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    iterate, cycles, budget, cleaned = parse_add_flags(text)
    mem = LifeMemory.open(life_dir)
    item = add_backlog_item(
        mem, cleaned or text.strip(),
        iterate=iterate, iteration_max_cycles=cycles, iteration_budget_usd=budget,
    )
    return item.to_jsonable()


def enqueue_nudge(
    sid: str, text: str, *, global_root: Path | str | None = None, source: str = "web"
) -> bool | None:
    """Queue operator guidance to the inbox (also emits ``life.inbox.queued``
    so it shows on the live stream)."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    queue_inbox_message(life_dir, text.strip(), source=source)
    return True


def _worker_config_from_env(life_dir: Path, global_root: Path) -> LifeWorkerConfig:
    """Minimal daemon config from the current env caps/backend — mirrors what a
    fresh CLI launch would enforce. Resolve role models/efforts through the
    SAME persisted/env/vault precedence used by /config and the CLI; leaving
    these fields at ``LifeWorkerConfig``'s dataclass defaults silently launched
    gpt-5.5 while the cockpit reported a configured Sonnet model."""
    from ..core.knobs import resolve_role_model, resolve_role_reasoning_effort

    return LifeWorkerConfig(
        life_dir=life_dir,
        global_root=global_root,
        # Web/TUI sessions are isolated workspaces.  Detached daemons chdir to
        # `/`, so relying on process cwd makes vertical and stage resolution
        # diverge from the Manager front-door.  Pin execution to the session.
        project_workdir=life_dir,
        backend=os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex"),
        engineer_model=resolve_role_model(
            "engineer", role_env="ARGUS_SKILL_ENGINEER_MODEL",
        ),
        reviewer_model=resolve_role_model(
            "reviewer", role_env="ARGUS_SKILL_REVIEWER_MODEL",
        ),
        engineer_reasoning_effort=resolve_role_reasoning_effort(
            "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
        ),
        reviewer_reasoning_effort=resolve_role_reasoning_effort(
            "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
        ),
        per_mission_cap_usd=float(os.environ.get("ARGUS_SKILL_PER_MISSION_CAP_USD", "30.0")),
        daily_cap_usd=float(os.environ.get("ARGUS_SKILL_DAILY_CAP_USD", "180.0")),
        global_daily_cap_usd=float(os.environ.get("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "0.0")),
    )


def start_project_daemon(
    sid: str, *, global_root: Path | str | None = None
) -> dict[str, Any] | None:
    """Spawn this project's detached daemon (if not already alive). Blocking-ish
    (subprocess spawn) — call from a threadpool in the async endpoint."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    root = _global_root(global_root)
    st = read_daemon_status(life_dir)
    if st.alive:
        return {"rc": 0, "already_alive": True, "daemon": _daemon_dict(st)}
    rc = spawn_detached_daemon(_worker_config_from_env(life_dir, root), quiet=True)
    return {"rc": rc, "already_alive": False, "daemon": _daemon_dict(read_daemon_status(life_dir))}


def create_daemon(
    objective: str = "", *, name: str = "",
    global_root: Path | str | None = None,
) -> dict[str, Any]:
    """Mint a brand-new daemon (session). The objective is OPTIONAL: creating a
    daemon is starting a conversation with a fresh Manager, not configuring a
    research campaign. With no objective the session is created idle — the user
    just talks to it, and the Manager decides everything (reply to chat, or write
    its OWN objective and dispatch a mission). The daemon spawns lazily on the
    first real task (via POST /message), so an empty daemon leaves no idle
    executor. When an objective IS given, it's armed as a self-directed campaign
    and the daemon starts immediately (the web equivalent of ``--new
    --continuous --objective``). Blocking-ish (fs + fork) — call from a
    threadpool. Returns the new sid + daemon status.
    """
    import time as _time

    from ..core.session import SessionMeta, new_session_id, write_session_meta

    root = _global_root(global_root)
    sid = new_session_id()
    now = _time.time()
    obj = (objective or "").strip()
    life_dir = root / "projects" / sid
    write_session_meta(
        root,
        SessionMeta(
            id=sid, display_name=(name or "").strip(),
            created=now, last_active=now, cwd=str(life_dir), objective=obj,
        ),
    )
    life_dir.mkdir(parents=True, exist_ok=True)

    rc = 0
    spawned = False
    if obj:
        # Explicit objective → arm the self-directed campaign + start the daemon
        # now. The daemon hot-reloads continuous.json.
        write_continuous_config(life_dir, enabled=True, objective=obj)
        rc = spawn_detached_daemon(_worker_config_from_env(life_dir, root), quiet=True)
        spawned = True
    # else: idle session — no continuous, no eager spawn. The Manager (via
    # /message) writes objectives and lazily spawns the executor when needed.

    return {
        "sid": sid,
        "rc": rc,
        "spawned": spawned,
        "daemon": _daemon_dict(read_daemon_status(life_dir)),
        "objective": obj,
    }


def stop_project_daemon(
    sid: str, *, drain: bool = False, force: bool = False,
    global_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Stop this project's daemon. Blocking (waits up to the drain timeout) —
    call from a threadpool in the async endpoint."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    rc = stop_daemon(life_dir, drain=drain, force=force)
    return {"rc": rc}


def set_continuous(
    sid: str, *, enabled: bool, objective: str = "",
    global_root: Path | str | None = None,
) -> bool | None:
    """Start/stop this project's continuous (self-directed) campaign by writing
    the hot-reloadable ``continuous.json``."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    write_continuous_config(life_dir, enabled=enabled, objective=objective)
    return True


# ---------------------------------------------------------------------------
# Wave-1 read/inspect + backlog-lifecycle helpers — 1:1 with the Python
# cockpit's /status /journal /note /doctor /config /identity /transcript and
# the /done /skip /rm /stop backlog commands. All delegate; fail-soft per part.
# ---------------------------------------------------------------------------

def get_status(sid: str, *, global_root: Path | str | None = None) -> dict[str, Any] | None:
    """Composite of the Python /status view: identity, pending backlog + pending
    questions, recent journal, continuous, inbox count, daemon, active role."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    mem = LifeMemory.open(life_dir)

    def _safe(fn, default):  # noqa: ANN001
        try:
            return fn()
        except Exception:  # noqa: BLE001 — /status must never raise
            return default

    identity = _safe(lambda: mem.identity.read().strip(), "")
    items = _safe(lambda: mem.backlog.all(), [])
    pending = [it.to_jsonable() for it in items if it.status == "pending"]
    questions = [
        it.to_jsonable() for it in items
        if it.to_jsonable().get("pending_question")
    ]
    journal = _safe(lambda: [e.to_jsonable() for e in mem.journal.tail(3)], [])
    cont = _safe(lambda: read_continuous_state(life_dir), None)
    continuous = (
        {"enabled": cont.enabled, "objective": cont.objective,
         "done_reason": cont.done_reason, "done_at": cont.done_at}
        if cont is not None else {"enabled": False, "objective": ""}
    )
    inbox_pending = _safe(lambda: count_pending_inbox_messages(life_dir), 0)
    daemon = _safe(lambda: _daemon_dict(read_daemon_status(life_dir)),
                   {"alive": False, "pid": None})
    roles = _safe(lambda: _roles_list(resolve_all_roles(env=os.environ),
                                      role_activity(life_dir)), [])
    active = next((r["role"] for r in roles if r["active"]), None)
    return {
        "identity": identity,
        "backlog_pending": pending,
        "pending_questions": questions,
        "journal": journal,
        "continuous": continuous,
        "inbox_pending": inbox_pending,
        "daemon": daemon,
        "roles": roles,
        "active_role": active,
    }


def get_journal(
    sid: str, *, n: int = 10, global_root: Path | str | None = None
) -> list[dict[str, Any]] | None:
    """Recent journal entries (mission summaries / notes) — the /journal tail."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    event_path = life_dir / EVENT_FILE
    signature = (
        _stat_signature(event_path),
        _stat_signature(event_path.with_suffix(event_path.suffix + ".1")),
    )
    key = (str(life_dir.resolve()), max(1, n))
    with _JOURNAL_TAIL_CACHE_LOCK:
        cached = _JOURNAL_TAIL_CACHE.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
    try:
        rows = [e.to_jsonable() for e in LifeMemory.open(life_dir).journal.tail(max(1, n))]
    except Exception:  # noqa: BLE001
        rows = []
    with _JOURNAL_TAIL_CACHE_LOCK:
        _JOURNAL_TAIL_CACHE[key] = (signature, rows)
    return rows


_TEXT_ARTIFACT_SUFFIXES = {
    ".bib", ".cfg", ".csv", ".html", ".ini", ".json", ".jsonl", ".log",
    ".md", ".py", ".rst", ".sh", ".tex", ".toml", ".tsv", ".txt", ".yaml",
    ".yml",
}
_INLINE_IMAGE_MIMES = {"image/gif", "image/jpeg", "image/png", "image/webp"}


def _project_workspace(
    sid: str, *, global_root: Path | str | None = None,
) -> Path | None:
    root = _global_root(global_root)
    meta = read_session_meta(root, sid)
    if meta is None or not meta.cwd.strip():
        return None
    try:
        workspace = Path(meta.cwd).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return workspace if workspace.is_dir() else None


def _safe_artifact_path(workspace: Path, relative_path: str) -> tuple[str, Path] | None:
    raw = str(relative_path or "").strip().replace("\\", "/")
    if not raw or "\x00" in raw:
        return None
    # Parse as a URL-style path on every OS.  PurePosixPath conveniently
    # removes a harmless leading ``./`` without corrupting dotfiles (the old
    # ``lstrip('./')`` changed ``.env`` into ``env``).  Reject traversal before
    # normalization so ``a/../secret`` can never be made to look innocuous.
    rel = PurePosixPath(raw)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    normalized = rel.as_posix()
    if normalized in {"", "."}:
        return None
    try:
        resolved = (workspace / normalized).resolve(strict=False)
        resolved.relative_to(workspace)
    except (OSError, RuntimeError, ValueError):
        return None
    return normalized, resolved


def _latest_evidence_files(
    sid: str, *, global_root: Path | str | None = None,
) -> list[dict[str, str]]:
    # Evidence is scoped to the latest completed mission.  Never fall back to
    # an older mission merely because a newer result omitted evidence: that
    # would silently keep stale files allowlisted.  The filtered reverse reader
    # finds that mission even after thousands of progress events/user notes and
    # follows the single supported rollover.
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return []
    events = _read_jsonl_tail_history(
        life_dir / EVENT_FILE,
        1,
        predicate=lambda row: str(row.get("type") or "") == "life.mission.completed",
    )
    latest = events[-1] if events else {}
    report = latest.get("planner_report") if isinstance(latest, dict) else {}
    evidence = report.get("evidence_files") if isinstance(report, dict) else None
    if not isinstance(evidence, list) or not evidence:
        return []
    out: list[dict[str, str]] = []
    for item in evidence:
        if isinstance(item, dict) and str(item.get("path") or "").strip():
            out.append({
                "path": str(item["path"]).strip(),
                "why": str(item.get("why") or "").strip(),
            })
    return out


def _artifact_metadata(
    workspace: Path,
    relative_path: str,
    *,
    why: str = "",
    preview_bytes: int = 0,
) -> dict[str, Any] | None:
    safe = _safe_artifact_path(workspace, relative_path)
    if safe is None:
        return None
    normalized, resolved = safe
    try:
        exists = resolved.is_file()
        stat = resolved.stat() if exists else None
    except OSError:
        exists = False
        stat = None
    mime = mimetypes.guess_type(normalized)[0] or "application/octet-stream"
    suffix = resolved.suffix.lower()
    kind = (
        "text" if suffix in _TEXT_ARTIFACT_SUFFIXES
        else "image" if mime in _INLINE_IMAGE_MIMES
        else "pdf" if mime == "application/pdf"
        else "binary"
    )
    row: dict[str, Any] = {
        "path": normalized,
        "name": Path(normalized).name,
        "why": why,
        "exists": exists,
        "kind": kind,
        "mime": mime,
        "size": int(stat.st_size) if stat is not None else 0,
        "mtime": float(stat.st_mtime) if stat is not None else None,
    }
    if preview_bytes > 0 and exists and kind == "text":
        try:
            # Read only the preview window.  ``Path.read_bytes()[:limit]``
            # still allocates the whole file first and lets a huge allowlisted
            # log exhaust the API process.
            with resolved.open("rb") as fh:
                raw = fh.read(preview_bytes + 1)
            row["preview"] = raw[:preview_bytes].decode("utf-8", errors="replace")
            row["truncated"] = len(raw) > preview_bytes
        except OSError:
            row["preview"] = ""
            row["truncated"] = False
    return row


def list_project_artifacts(
    sid: str, *, global_root: Path | str | None = None,
) -> list[dict[str, Any]] | None:
    """Latest reviewer-approved evidence files, constrained to the project cwd."""
    if project_life_dir(sid, global_root=global_root) is None:
        return None
    workspace = _project_workspace(sid, global_root=global_root)
    if workspace is None:
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for evidence in _latest_evidence_files(sid, global_root=global_root):
        row = _artifact_metadata(workspace, evidence["path"], why=evidence["why"])
        if row is not None and row["path"] not in seen:
            seen.add(row["path"])
            rows.append(row)
    return rows


def get_project_artifact(
    sid: str,
    artifact_path: str,
    *,
    global_root: Path | str | None = None,
    preview_bytes: int = 128 * 1024,
) -> dict[str, Any] | None:
    """Metadata/preview for an allowlisted latest-result artifact."""
    artifacts = list_project_artifacts(sid, global_root=global_root)
    if artifacts is None:
        return None
    workspace = _project_workspace(sid, global_root=global_root)
    if workspace is None:
        return None
    safe_requested = _safe_artifact_path(workspace, artifact_path)
    if safe_requested is None:
        return None
    requested = safe_requested[0]
    allowed = next((row for row in artifacts if row["path"] == requested), None)
    if allowed is None or not allowed["exists"]:
        return None
    return _artifact_metadata(
        workspace,
        requested,
        why=str(allowed.get("why") or ""),
        preview_bytes=max(0, min(int(preview_bytes), 512 * 1024)),
    )


def _resolved_project_artifact(
    sid: str,
    artifact_path: str,
    *,
    global_root: Path | str | None = None,
) -> tuple[dict[str, Any], Path] | None:
    info = get_project_artifact(
        sid, artifact_path, global_root=global_root, preview_bytes=0,
    )
    workspace = _project_workspace(sid, global_root=global_root)
    if info is None or workspace is None:
        return None
    safe = _safe_artifact_path(workspace, str(info["path"]))
    if safe is None or not safe[1].is_file():
        return None
    return info, safe[1]


def add_project_note(
    sid: str, text: str, *, global_root: Path | str | None = None
) -> str | None:
    """Append a manual user.note to the timeline — the /note command."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    return append_note(LifeMemory.open(life_dir), text)


def get_backlog_item(
    sid: str, item_id: str, *, global_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Return one full backlog item (compact snapshots intentionally omit it)."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    try:
        item = next(
            (row for row in LifeMemory.open(life_dir).backlog.all() if row.id == item_id),
            None,
        )
    except Exception:  # noqa: BLE001
        return None
    return item.to_jsonable() if item is not None else None


def dispose_backlog(
    sid: str, item_id: str, op: str, *, global_root: Path | str | None = None
) -> dict[str, Any] | None:
    """Backlog disposition — /done (mark_done) / /skip / /rm (status=skipped).
    Returns the updated item, or None if the project or item is unknown."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    bl = LifeMemory.open(life_dir).backlog
    item = bl.mark_done(item_id) if op == "done" else bl.update(item_id, status="skipped")
    return item.to_jsonable() if item is not None else None


def stop_backlog_iteration(
    sid: str, item_id: str, *, global_root: Path | str | None = None
) -> dict[str, Any] | None:
    """/stop — disable a task's auto-iteration (does not delete it)."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    item = LifeMemory.open(life_dir).backlog.stop_iteration(item_id)
    return item.to_jsonable() if item is not None else None


def _daemon_log_tail(life_dir: Path, *, lines: int = 12) -> str:
    try:
        text = (life_dir / "daemon.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def get_doctor(sid: str, *, global_root: Path | str | None = None) -> dict[str, Any] | None:
    """Run the daemon-executor diagnostics — /doctor: ranked checks + the single
    recommended fix + a recent daemon.log tail. Reuses tools.doctor.run_diagnostics."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    root = _global_root(global_root)
    checks = run_diagnostics(life_dir, global_root=root)
    rows = [{"name": c.name, "ok": c.ok, "detail": c.detail, "fix": c.fix} for c in checks]
    # run_diagnostics returns checks ordered by recommendation priority, so the
    # first failing check is the root-cause fix to surface first.
    recommended = next((r for r in rows if not r["ok"]), None)
    return {"checks": rows, "recommended": recommended, "log_tail": _daemon_log_tail(life_dir)}


def get_config(*, global_root: Path | str | None = None) -> dict[str, Any]:  # noqa: ARG001
    """Runtime settings snapshot — /config: per-role backend/model/effort + every
    ARGUS_* knob. Env/process-global (not per-project). Reuses build_config_snapshot."""
    return build_config_snapshot(env=os.environ)


def get_identity(sid: str, *, global_root: Path | str | None = None) -> str | None:
    """The operator identity card text — /identity view (ensures a default)."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    mem = LifeMemory.open(life_dir)
    try:
        mem.identity.ensure_default()
        return mem.identity.read()
    except Exception:  # noqa: BLE001
        return ""


def get_transcript(
    sid: str, *, n: int = 20, global_root: Path | str | None = None
) -> list[dict[str, Any]] | None:
    """Recent operator↔argus conversation turns — for transcript replay / resume."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    try:
        return read_turns(life_dir, limit=max(1, n))
    except Exception:  # noqa: BLE001
        return []


async def tail_events(
    life_dir: Path,
    *,
    replay_limit: int = 40,
    poll_interval: float = 0.25,
):
    """Async generator: yield the last ``replay_limit`` events, then every new
    ``events.jsonl`` line as it is appended.

    Roll-safe: ``events.jsonl`` rotates to ``.jsonl.1`` at 100MB (the daemon's
    ``event_log`` writer), which shrinks/replaces the live file. We track
    ``(st_ino, st_size)`` and, on a shrink or inode change, restart the byte
    offset from 0 so the freshly-rotated log is followed without dropping or
    duplicating a truncated line. A partial trailing line (no ``\\n`` yet) is
    buffered until its newline arrives.
    """
    path = life_dir / EVENT_FILE

    # Fix the tail baseline BEFORE replaying, so an event appended between the
    # replay snapshot and the first poll is neither dropped nor duplicated:
    # replay covers up to `offset`, the tail covers everything strictly after.
    offset = 0
    inode: int | None = None
    if path.exists():
        stat = path.stat()
        offset = stat.st_size
        inode = stat.st_ino

    for ev in _read_recent_jsonl_events(path, limit=replay_limit):
        yield ev

    buf = b""

    while True:
        await asyncio.sleep(poll_interval)
        try:
            stat = path.stat()
        except OSError:
            continue  # file gone mid-roll — wait for it to reappear
        if stat.st_ino != inode or stat.st_size < offset:
            offset, inode, buf = 0, stat.st_ino, b""  # rotated/truncated → restart
        if stat.st_size <= offset:
            continue
        try:
            with path.open("rb") as fh:
                fh.seek(offset)
                chunk = fh.read()
                offset = fh.tell()
        except OSError:
            continue
        buf += chunk
        *complete, buf = buf.split(b"\n")  # keep the last (possibly partial) line
        for raw in complete:
            line = raw.strip()
            if not line:
                continue
            try:
                ev = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(ev, dict):
                yield ev


# ---------------------------------------------------------------------------
# FastAPI app (imports the [web] extra lazily)
# ---------------------------------------------------------------------------

def create_app(
    *, global_root: Path | str | None = None, auth_token: str | None = None
):
    """Build the FastAPI app. Requires the ``[web]`` extra (fastapi).

    ``auth_token`` (or env ``ARGUS_SKILL_WEB_TOKEN``) turns on bearer auth: when
    set, every command POST needs ``Authorization: Bearer <token>`` and the WS
    upgrade needs ``?token=<token>`` (browsers cannot set WS headers). With no
    token configured the API is unauthenticated — safe only behind the default
    ``127.0.0.1`` bind.
    """
    from fastapi import (
        Depends,
        FastAPI,
        Header,
        HTTPException,
        Query,
        Response,
        WebSocket,
        WebSocketDisconnect,
    )
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import FileResponse, StreamingResponse

    token = auth_token if auth_token is not None else os.environ.get("ARGUS_SKILL_WEB_TOKEN")

    app = FastAPI(title="argus-skill web API", version="0.1.0")

    # Localhost dev only: allow the Vite dev server + same-origin. Not a wildcard.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173", "http://127.0.0.1:5173",
            "http://localhost:8799", "http://127.0.0.1:8799",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _require_auth(authorization: str | None = Header(default=None)) -> None:
        if not token:
            return  # unauthenticated (localhost-only) mode
        if authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    def _resolve_or_404(sid: str) -> Path:
        life_dir = project_life_dir(sid, global_root=global_root)
        if life_dir is None:
            raise HTTPException(status_code=404, detail=f"unknown project: {sid}")
        return life_dir

    def _404_if_none(value, sid: str):
        if value is None:
            raise HTTPException(status_code=404, detail=f"unknown project: {sid}")
        return value

    class _TaskIn(BaseModel):
        text: str
        # Lazy daemon spawn (default on): queueing a task starts this project's
        # executor if none is alive — the same behaviour as the Python cockpit's
        # _autospawn_daemon_for_task, so `argus` + submit a task actually runs it.
        autostart_daemon: bool = True

    class _NudgeIn(BaseModel):
        text: str

    class _MessageIn(BaseModel):
        text: str

    class _CreateDaemonIn(BaseModel):
        objective: str = ""
        name: str = ""

    class _StopIn(BaseModel):
        drain: bool = False
        force: bool = False

    class _ContinuousIn(BaseModel):
        enabled: bool
        objective: str = ""

    class _NoteIn(BaseModel):
        text: str

    class _DisposeIn(BaseModel):
        op: str = "done"  # done | skip | rm

    # ── read endpoints (M0) ───────────────────────────────────────────────

    @app.get("/api/projects")
    def _projects(
        limit: int = Query(100, ge=1, le=2000),
        include_empty: bool = Query(False),
    ) -> dict[str, Any]:
        return {
            "projects": list_projects(
                global_root=global_root, limit=limit, include_empty=include_empty
            )
        }

    @app.post("/api/daemons", dependencies=[Depends(_require_auth)])
    async def _create_daemon(body: _CreateDaemonIn) -> dict[str, Any]:
        """Create a brand-new daemon (session). The objective is OPTIONAL — with
        none, the daemon is idle and the user just talks to the Manager (which
        writes its own objectives). Threadpool: fs writes + optional fork."""
        return await run_in_threadpool(
            create_daemon, body.objective, name=body.name, global_root=global_root,
        )

    @app.get("/api/projects/{sid}/snapshot")
    def _snapshot(
        sid: str,
        events_limit: int = Query(80, ge=1, le=500),
        compact: bool = Query(False),
    ) -> dict[str, Any]:
        return _404_if_none(
            build_snapshot(
                sid,
                global_root=global_root,
                events_limit=events_limit,
                compact=compact,
            ),
            sid,
        )

    @app.get("/api/projects/{sid}/events")
    def _events(sid: str, limit: int = Query(80, ge=1, le=1000)) -> dict[str, Any]:
        life_dir = _resolve_or_404(sid)
        return {"events": _read_recent_project_events(life_dir, limit=limit)}

    @app.get(
        "/api/projects/{sid}/artifacts",
        dependencies=[Depends(_require_auth)],
    )
    def _artifacts(sid: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "private, no-store"
        return {
            "artifacts": _404_if_none(
                list_project_artifacts(sid, global_root=global_root), sid,
            )
        }

    @app.get(
        "/api/projects/{sid}/artifact",
        dependencies=[Depends(_require_auth)],
    )
    def _artifact(
        sid: str,
        response: Response,
        path: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "private, no-store"
        artifact = get_project_artifact(sid, path, global_root=global_root)
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact unavailable or not allowlisted")
        return artifact

    @app.get(
        "/api/projects/{sid}/artifact/raw",
        dependencies=[Depends(_require_auth)],
    )
    def _artifact_raw(
        sid: str,
        path: str = Query(..., min_length=1),
        download: bool = Query(False),
    ):
        resolved = _resolved_project_artifact(sid, path, global_root=global_root)
        if resolved is None:
            raise HTTPException(status_code=404, detail="artifact unavailable or not allowlisted")
        info, file_path = resolved
        safe_inline = info["kind"] in {"image", "pdf"}
        media_type = (
            "application/octet-stream" if download
            else str(info["mime"]) if safe_inline
            else "text/plain; charset=utf-8"
        )
        return FileResponse(
            file_path,
            media_type=media_type,
            filename=str(info["name"]),
            content_disposition_type="attachment" if download else "inline",
            headers={
                # HTML/SVG and unknown binaries intentionally arrive as plain
                # text; prohibit browser MIME sniffing from turning them back
                # into executable content.
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )

    # ── command endpoints (M1, auth-gated) ────────────────────────────────

    @app.post("/api/projects/{sid}/tasks", dependencies=[Depends(_require_auth)])
    async def _post_task(sid: str, body: _TaskIn) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty task text")
        item = _404_if_none(enqueue_task(sid, body.text, global_root=global_root), sid)
        resp: dict[str, Any] = {"item": item}
        if body.autostart_daemon:
            # Lazy spawn (like the Python cockpit): queueing a task ensures the
            # executor is alive so the task actually runs. Idempotent — no-op if
            # a daemon is already alive. In a threadpool because spawn touches
            # the filesystem / forks a detached process.
            resp["daemon"] = await run_in_threadpool(
                start_project_daemon, sid, global_root=global_root
            )
        return resp

    @app.post("/api/projects/{sid}/nudge", dependencies=[Depends(_require_auth)])
    def _post_nudge(sid: str, body: _NudgeIn) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty nudge text")
        _404_if_none(enqueue_nudge(sid, body.text, global_root=global_root), sid)
        return {"ok": True}

    @app.post("/api/projects/{sid}/message", dependencies=[Depends(_require_auth)])
    async def _post_message(sid: str, body: _MessageIn) -> dict[str, Any]:
        """The Manager front-door: route natural language through the SAME triage
        the Python REPL uses. A conversational message ("你好") gets a Manager
        reply and never becomes a mission; only TEAM/complex work is enqueued.
        Runs in a threadpool because the Manager triage is a blocking LLM call.
        """
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty message")
        _resolve_or_404(sid)
        from .manager_bridge import manager_message

        result = await run_in_threadpool(
            manager_message, sid, body.text, global_root=global_root
        )
        # A task classification lazily spawns the executor, mirroring /tasks.
        if result.get("kind") == "task" and not result.get("daemon_alive"):
            result["daemon"] = await run_in_threadpool(
                start_project_daemon, sid, global_root=global_root
            )
        return result

    @app.post("/api/projects/{sid}/message/stream", dependencies=[Depends(_require_auth)])
    def _post_message_stream(sid: str, body: _MessageIn):
        """Streaming twin of ``/message`` (Server-Sent Events).

        The Manager turn is a blocking CLI call, but copilot/codex emit the reply
        as blocks *during* the turn and phase transitions fire live — the plain
        POST throws all that away, so the front-end looks frozen until the whole
        turn ends. Here we run ``manager_message`` on a worker thread with an
        ``on_fragment`` callback that pushes each block / phase onto a thread-safe
        queue; a synchronous generator drains the queue into SSE ``data:`` frames.
        A sync generator means Starlette runs it in a threadpool — no asyncio
        queue bridging, which keeps this robust and easy to reason about.

        Frame kinds: ``{"type":"phase",...}`` · ``{"type":"delta",...}`` ·
        ``{"type":"done","result":{...}}`` · ``{"type":"error","error":...}``.
        The blocking ``/message`` stays as the fallback for non-streaming clients.
        """
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty message")
        _resolve_or_404(sid)
        from .manager_bridge import manager_message

        q: "queue.Queue[dict | None]" = queue.Queue()

        def _run() -> None:
            def _on_fragment(kind: str, payload: dict) -> None:
                q.put({"type": kind, **payload})
            try:
                result = manager_message(
                    sid, body.text, global_root=global_root, on_fragment=_on_fragment
                )
                # Mirror the blocking endpoint: a task classification lazily spawns
                # the executor so streamed dispatch behaves like /message + /tasks.
                if result.get("kind") == "task" and not result.get("daemon_alive"):
                    try:
                        result["daemon"] = start_project_daemon(sid, global_root=global_root)
                    except Exception:  # noqa: BLE001 — surface the reply even if spawn hiccups
                        pass
                q.put({"type": "done", "result": result})
            except Exception as exc:  # noqa: BLE001
                q.put({"type": "error", "error": str(exc)})
            finally:
                q.put(None)  # sentinel: generator stops

        threading.Thread(target=_run, name=f"manager-stream-{sid}", daemon=True).start()

        def _gen():
            for item in _iter_manager_stream_items(
                q,
                heartbeat_s=_manager_stream_heartbeat_seconds(),
            ):
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/projects/{sid}/daemon/start", dependencies=[Depends(_require_auth)])
    async def _daemon_start(sid: str) -> dict[str, Any]:
        return _404_if_none(
            await run_in_threadpool(start_project_daemon, sid, global_root=global_root), sid
        )

    @app.post("/api/projects/{sid}/daemon/stop", dependencies=[Depends(_require_auth)])
    async def _daemon_stop(sid: str, body: _StopIn | None = None) -> dict[str, Any]:
        b = body or _StopIn()
        return _404_if_none(
            await run_in_threadpool(
                stop_project_daemon, sid, drain=b.drain, force=b.force, global_root=global_root
            ), sid,
        )

    @app.post("/api/projects/{sid}/continuous", dependencies=[Depends(_require_auth)])
    def _post_continuous(sid: str, body: _ContinuousIn) -> dict[str, Any]:
        _404_if_none(
            set_continuous(sid, enabled=body.enabled, objective=body.objective,
                           global_root=global_root),
            sid,
        )
        return {"ok": True}

    # ── Wave-1 read/inspect endpoints ─────────────────────────────────────

    @app.get("/api/projects/{sid}/status")
    def _status(sid: str) -> dict[str, Any]:
        return _404_if_none(get_status(sid, global_root=global_root), sid)

    @app.get("/api/projects/{sid}/journal")
    def _journal(sid: str, n: int = Query(10, ge=1, le=500)) -> dict[str, Any]:
        return {"journal": _404_if_none(get_journal(sid, n=n, global_root=global_root), sid)}

    @app.get("/api/projects/{sid}/doctor")
    def _doctor(sid: str) -> dict[str, Any]:
        return _404_if_none(get_doctor(sid, global_root=global_root), sid)

    @app.get("/api/projects/{sid}/config")
    def _config(sid: str) -> dict[str, Any]:
        _resolve_or_404(sid)  # validate the project exists
        return get_config(global_root=global_root)

    @app.get("/api/projects/{sid}/identity")
    def _identity(sid: str) -> dict[str, Any]:
        return {"identity": _404_if_none(get_identity(sid, global_root=global_root), sid)}

    @app.get("/api/projects/{sid}/transcript")
    def _transcript(sid: str, n: int = Query(20, ge=1, le=500)) -> dict[str, Any]:
        return {"turns": _404_if_none(get_transcript(sid, n=n, global_root=global_root), sid)}

    @app.get("/api/projects/{sid}/backlog/{item_id}")
    def _backlog_item(sid: str, item_id: str) -> dict[str, Any]:
        _resolve_or_404(sid)
        item = get_backlog_item(sid, item_id, global_root=global_root)
        if item is None:
            raise HTTPException(status_code=404, detail=f"unknown backlog item: {item_id}")
        return {"item": item}

    # ── Wave-1 write endpoints (auth-gated) ───────────────────────────────

    @app.post("/api/projects/{sid}/note", dependencies=[Depends(_require_auth)])
    def _post_note(sid: str, body: _NoteIn) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="empty note text")
        return {"result": _404_if_none(add_project_note(sid, body.text, global_root=global_root), sid)}

    @app.post("/api/projects/{sid}/backlog/{item_id}/dispose", dependencies=[Depends(_require_auth)])
    def _dispose(sid: str, item_id: str, body: _DisposeIn) -> dict[str, Any]:
        if body.op not in ("done", "skip", "rm"):
            raise HTTPException(status_code=400, detail="op must be done|skip|rm")
        _resolve_or_404(sid)
        item = dispose_backlog(sid, item_id, body.op, global_root=global_root)
        if item is None:
            raise HTTPException(status_code=404, detail=f"unknown backlog item: {item_id}")
        return {"item": item}

    @app.post("/api/projects/{sid}/backlog/{item_id}/stop", dependencies=[Depends(_require_auth)])
    def _stop_item(sid: str, item_id: str) -> dict[str, Any]:
        _resolve_or_404(sid)
        item = stop_backlog_iteration(sid, item_id, global_root=global_root)
        if item is None:
            raise HTTPException(status_code=404, detail=f"unknown backlog item: {item_id}")
        return {"item": item}

    # ── live event stream (M0) ────────────────────────────────────────────

    @app.websocket("/api/projects/{sid}/stream")
    async def _stream(ws: WebSocket, sid: str, replay: int = 40,
                      token_q: str | None = Query(default=None, alias="token")) -> None:
        life_dir = project_life_dir(sid, global_root=global_root)
        await ws.accept()
        if token and token_q != token:
            await ws.close(code=4401, reason="unauthorized")
            return
        if life_dir is None:
            await ws.close(code=4404, reason="unknown project")
            return
        try:
            async for ev in tail_events(life_dir, replay_limit=max(0, min(replay, 200))):
                await ws.send_json(ev)
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001 — a stream error must not crash the server
            try:
                await ws.close(code=1011)
            except Exception:  # noqa: BLE001
                pass

    # ── static web UI (optional) ──────────────────────────────────────────
    # When the React frontend has been built (`npm run build` in frontend/web),
    # serve it from the same origin so `argus-skill --web` gives API + UI on one
    # port. The /api routes above are registered first, so they always win; this
    # catch-all mount only handles the SPA shell + assets. Skipped silently when
    # the bundle is absent (API-only mode, e.g. the Vite dev server proxies here).
    web_dist = Path(__file__).resolve().parents[2] / "frontend" / "web" / "dist"
    if web_dist.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="web")

    return app


def serve(
    host: str = "127.0.0.1",
    port: int = 8799,
    *,
    global_root: Path | str | None = None,
    auth_token: str | None = None,
) -> int:
    """Run the API with uvicorn (blocking). Defaults to a localhost bind."""
    import uvicorn

    uvicorn.run(
        create_app(global_root=global_root, auth_token=auth_token),
        host=host, port=port, log_level="info",
    )
    return 0
