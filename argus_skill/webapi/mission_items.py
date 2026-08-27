"""Work-item queueing, config, and read-only diagnostic queries.

Extracted from ``server.py`` as part of a behavior-preserving decomposition.
Public names remain re-exported from ``server`` for backward compatibility.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from ..apps._inbox import count_pending_inbox_messages, queue_inbox_message
from ..apps._life_actions import add_backlog_item, append_note, parse_add_flags
from ..core.config_snapshot import build_config_snapshot
from ..core.provider_quota import provider_usage_snapshot
from ..core.role_config import resolve_all_roles
from ..core.session import (
    SessionMeta,
    read_session_meta,
    session_lifecycle_lock,
    update_session_meta,
)
from ..core.transcript import read_turns
from ..daemon.life_worker import read_continuous_state
from ..life.memory import BacklogItem, LifeMemory
from ..life.role_activity import role_activity
from . import project_state
from ._server_module import server_module as _srv
from .diagnostics import run_diagnostics

_global_root = project_state.resolve_global_root
_roles_list = project_state.roles_list
_daemon_dict = project_state.daemon_dict
_stat_signature = project_state.stat_signature
project_life_dir = project_state.project_life_dir


# Duplicated trivial literal (matches server.py's ``EVENT_FILE``) to avoid a
# circular import; this is a filename constant, not business logic.
EVENT_FILE = "events.jsonl"
_JOURNAL_TAIL_CACHE: dict[
    tuple[str, int],
    tuple[
        tuple[tuple[int, int, int] | None, tuple[int, int, int] | None],
        float,
        list[dict[str, Any]],
    ],
] = {}
_JOURNAL_TAIL_CACHE_LOCK = threading.Lock()
_JOURNAL_TAIL_CACHE_TTL_S = 2.0
_JOURNAL_TAIL_CACHE_MAX_ENTRIES = 256


def _enqueue_task_unlocked(
    sid: str, text: str, *, global_root: Path | str | None = None
) -> dict[str, Any] | None:
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    from ..apps._life_actions import DEFAULT_LIFE_CONFIG

    iterate, cycles, cleaned = parse_add_flags(
        text,
        defaults=DEFAULT_LIFE_CONFIG,
    )
    objective = cleaned or text.strip()
    item_id = BacklogItem.new_id()
    from .manager_dispatch import manager_bounded_handoff

    mem = LifeMemory.open(life_dir)

    def _persist(execution_task: str, division: Any):
        # The Manager handoff has already classified and committed this task.
        # Persist that fact on the backlog item so the daemon's backlog guard
        # does not classify the same claimed item a second time.  The duplicate
        # Manager turn can race with an operator nudge or daemon restart,
        # leaving the item ``running`` while the supervisor reports
        # ``backlog_empty`` indefinitely.
        from ..life.supervisor.backlog_guard import decision_evidence

        manager_decision = decision_evidence(division) or {"routed": True}
        return add_backlog_item(
            mem,
            execution_task,
            item_id=item_id,
            iterate=iterate,
            iteration_max_cycles=cycles,
            manager_decision=manager_decision,
        )

    should_name = not bool(
        (
            read_session_meta(_global_root(global_root), sid) or SessionMeta(id=sid)
        ).display_name.strip()
    )
    item = manager_bounded_handoff(
        sid,
        objective,
        _persist,
        global_root=global_root,
        root_task_id=item_id,
        # Naming is cosmetic and deterministic below. Do not spend another
        # front-door model call whose generic process label can overwrite the
        # actual task name.
        name_session=False,
    )
    if should_name:
        from ..manager.front_door import _derive_session_name

        fallback_name = _derive_session_name(objective, limit=32)

        def _fill_name(meta: SessionMeta) -> None:
            if not meta.display_name.strip():
                meta.display_name = fallback_name

        update_session_meta(_global_root(global_root), sid, _fill_name)
    return item.to_jsonable()


def enqueue_task(
    sid: str,
    text: str,
    *,
    global_root: Path | str | None = None,
    lifecycle_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Append one Manager-authored task while excluding delete/restore races."""
    root = _global_root(global_root)
    lock_root = _global_root(lifecycle_root) if lifecycle_root is not None else root
    with session_lifecycle_lock(lock_root, sid):
        return _enqueue_task_unlocked(sid, text, global_root=root)


def enqueue_task_command(
    sid: str,
    text: str,
    *,
    autostart_daemon: bool,
    global_root: Path | str | None = None,
    lifecycle_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Atomically enqueue and optionally start before deletion can move the project."""
    root = _global_root(global_root)
    lock_root = _global_root(lifecycle_root) if lifecycle_root is not None else root
    with session_lifecycle_lock(lock_root, sid):
        item = _enqueue_task_unlocked(sid, text, global_root=root)
        if item is None:
            return None
        response: dict[str, Any] = {"item": item}
        if autostart_daemon:
            response["daemon"] = _srv().start_project_daemon(
                sid,
                global_root=root,
                resume_continuous=False,
                reclaim_idle=True,
            )
        return response


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
    items = _safe(lambda: mem.backlog.active(), [])
    pending = [it.to_jsonable() for it in items if it.status == "pending"]
    questions = [it.to_jsonable() for it in items if it.to_jsonable().get("pending_question")]
    journal = _safe(lambda: [e.to_jsonable() for e in mem.journal.tail(3)], [])
    cont = _safe(lambda: read_continuous_state(life_dir), None)
    continuous = (
        {
            "enabled": cont.enabled,
            "objective": cont.objective,
            "done_reason": cont.done_reason,
            "done_at": cont.done_at,
        }
        if cont is not None
        else {"enabled": False, "objective": ""}
    )
    inbox_pending = _safe(lambda: count_pending_inbox_messages(life_dir), 0)
    daemon = _safe(
        lambda: _daemon_dict(
            _srv().read_daemon_status(life_dir), life_dir=life_dir
        ),
        {"alive": False, "pid": None},
    )
    roles = _safe(
        lambda: _roles_list(resolve_all_roles(env=os.environ), role_activity(life_dir)), []
    )
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
        "request_usage": provider_usage_snapshot(root=_global_root(global_root)),
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
    now = time.monotonic()
    with _JOURNAL_TAIL_CACHE_LOCK:
        cached = _JOURNAL_TAIL_CACHE.get(key)
        if cached is not None and (
            cached[0] == signature or now - cached[1] < _JOURNAL_TAIL_CACHE_TTL_S
        ):
            return cached[2]
    try:
        rows = [e.to_jsonable() for e in LifeMemory.open(life_dir).journal.tail(max(1, n))]
    except Exception:  # noqa: BLE001
        rows = []
    with _JOURNAL_TAIL_CACHE_LOCK:
        _JOURNAL_TAIL_CACHE.pop(key, None)
        _JOURNAL_TAIL_CACHE[key] = (signature, time.monotonic(), rows)
        while len(_JOURNAL_TAIL_CACHE) > _JOURNAL_TAIL_CACHE_MAX_ENTRIES:
            del _JOURNAL_TAIL_CACHE[next(iter(_JOURNAL_TAIL_CACHE))]
    return rows


def add_project_note(sid: str, text: str, *, global_root: Path | str | None = None) -> str | None:
    """Append a manual user.note to the timeline — the /note command."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    return append_note(LifeMemory.open(life_dir), text)


def get_backlog_item(
    sid: str,
    item_id: str,
    *,
    global_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Return one full backlog item (compact snapshots intentionally omit it)."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    try:
        item = next(
            (row for row in LifeMemory.open(life_dir).backlog.history() if row.id == item_id),
            None,
        )
    except Exception:  # noqa: BLE001
        return None
    return item.to_jsonable() if item is not None else None


def abort_project_mission(
    sid: str,
    *,
    reason: str = "",
    requested_by: str = "operator",
    global_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """Request an immediate abort for this project's current mission."""
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    from ..life.memory import request_running_item_abort

    requested, item_id = request_running_item_abort(
        life_dir,
        reason=reason or "operator requested immediate stop",
        requested_by=requested_by,
    )
    if requested:
        return {
            "requested": True,
            "item_id": item_id,
            "message": f"Stop requested for running task {item_id}.",
        }
    if item_id is not None:
        return {
            "requested": False,
            "item_id": item_id,
            "message": f"Could not persist stop request for running task {item_id}.",
            "error": "mission abort request could not be persisted",
        }
    return {
        "requested": False,
        "item_id": None,
        "message": "No running task to abort. Pending tasks were left unchanged.",
    }


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
    recommended fix + a recent daemon.log tail."""
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


def get_config(
    *,
    project_state_dir: Path | str | None = None,
    global_root: Path | str | None = None,
) -> dict[str, Any]:
    """Runtime settings snapshot with the host-global USD budget."""
    snapshot = build_config_snapshot(env=os.environ)
    if project_state_dir is None:
        return snapshot
    from ..core.knobs import resolve_budget_caps

    budget = resolve_budget_caps(
        project_state_dir=project_state_dir,
        global_root=global_root,
    )
    values = {
        "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": (
            budget.global_daily_cap_usd,
            "global:config.json",
        ),
    }
    for row in snapshot.get("operator_knobs", []):
        name = row.get("name")
        if name in values:
            value, source = values[name]
            row["value"] = str(value)
            row["source"] = source
    return snapshot


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


_CONFIG_ALIASES = {
    "backend": "ARGUS_SKILL_RUNNER_BACKEND",
    "engineer_backend": "ARGUS_SKILL_ENGINEER_BACKEND",
    "reviewer_backend": "ARGUS_SKILL_REVIEWER_BACKEND",
    "planner_backend": "ARGUS_SKILL_PLANNER_BACKEND",
    "manager_backend": "ARGUS_SKILL_MANAGER_BACKEND",
    # Which provider catalog the multi-provider CLIs buy from. Without these
    # the operator can pick the backend from the cockpit but not the account
    # behind it, which is how a Pi pointed at a non-default provider ends up
    # unusable with no visible setting to blame.
    "pi_provider": "ARGUS_SKILL_PI_PROVIDER",
    "opencode_provider": "ARGUS_SKILL_OPENCODE_PROVIDER",
    "model": "ARGUS_SKILL_MODEL",
    "engineer_model": "ARGUS_SKILL_ENGINEER_MODEL",
    "reviewer_model": "ARGUS_SKILL_REVIEWER_MODEL",
    "planner_model": "ARGUS_SKILL_PLAN_MODEL",
    "manager_model": "ARGUS_SKILL_MANAGER_MODEL",
    "manager_reply_model": "ARGUS_SKILL_MANAGER_REPLY_MODEL",
    "frontdoor_model": "ARGUS_SKILL_FRONTDOOR_MODEL",
    "engineer_effort": "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
    "reviewer_effort": "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
    "planner_effort": "ARGUS_SKILL_PLANNER_REASONING_EFFORT",
    "manager_effort": "ARGUS_SKILL_MANAGER_REASONING_EFFORT",
    "global_daily_cap": "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD",
    "max_daemons": "ARGUS_SKILL_MAX_ACTIVE_DAEMONS",
    "daemon_limit": "ARGUS_SKILL_MAX_ACTIVE_DAEMONS",
    "codex_daily_requests": "ARGUS_SKILL_CODEX_DAILY_CALL_CAP",
    "copilot_daily_requests": "ARGUS_SKILL_COPILOT_DAILY_CALL_CAP",
    "copilot_daily_premium": "ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP",
    "safe_mode": "ARGUS_SKILL_SAFE_MODE",
    "show_reasoning": "ARGUS_SKILL_SHOW_REASONING",
    "telegram": "ARGUS_SKILL_ENABLE_TELEGRAM",
}


def set_operator_config(
    name: str,
    value: str,
    *,
    project_state_dir: Path | str | None = None,
    global_root: Path | str | None = None,
) -> dict[str, Any]:
    from ..core.knob_store import write_persisted_knob
    from ..core.knobs import cockpit_editable_names, normalize_cockpit_knob_value

    raw = (name or "").strip()
    env_name = _CONFIG_ALIASES.get(raw.lower(), raw.upper())
    allowed = set(cockpit_editable_names()) | {"ARGUS_SKILL_RUNNER_BACKEND"}
    if env_name not in allowed:
        raise ValueError(f"config key is not cockpit-editable: {raw}")
    val = normalize_cockpit_knob_value(env_name, value)
    if project_state_dir is not None:
        from ..core.operator_context import IntakeDecision, persist_intake_decision

        persist_intake_decision(
            project_state_dir,
            f"{env_name}={val}",
            IntakeDecision(
                kind="preference",
                scope="project",
                applies_to_roles="all",
                preference_kind="workflow",
                preference_value=f"{env_name}={val}",
            ),
            source="web.config",
        )
    # Budget caps are ordinary config.json knobs now (budget.json retired) — they
    # fall through to the generic knob_store write path below like any other knob.
    if not write_persisted_knob(env_name, val):
        raise RuntimeError(f"config setting could not be persisted: {env_name}")
    os.environ[env_name] = val
    return {"name": env_name, "value": val, "restart_required": True}


_BUDGET_BATCH_ALIASES = frozenset(
    {
        "global_daily_cap",
        "codex_daily_requests",
        "copilot_daily_requests",
        "copilot_daily_premium",
    }
)


def set_budget_config(
    values: dict[str, str],
    *,
    project_state_dir: Path | str,
    global_root: Path | str,
) -> dict[str, Any]:
    from ..core.knob_store import write_persisted_knobs
    from ..core.knobs import normalize_cockpit_knob_value

    unknown = sorted(set(values) - _BUDGET_BATCH_ALIASES)
    if unknown:
        raise ValueError(f"unsupported budget setting(s): {', '.join(unknown)}")
    normalized: dict[str, str] = {}
    for alias in _BUDGET_BATCH_ALIASES:
        if alias not in values:
            raise ValueError(f"missing budget setting: {alias}")
        env_name = _CONFIG_ALIASES[alias]
        normalized[env_name] = normalize_cockpit_knob_value(
            env_name,
            str(values[alias]),
        )
    from ..core.operator_context import IntakeDecision, persist_intake_decision

    rendered = ", ".join(f"{key}={normalized[key]}" for key in sorted(normalized))
    persist_intake_decision(
        project_state_dir,
        rendered,
        IntakeDecision(
            kind="preference",
            scope="project",
            applies_to_roles=("manager", "planner"),
            preference_kind="workflow",
            preference_value=rendered,
        ),
        source="web.config.budget",
    )
    # Budget caps are ordinary config.json knobs now (budget.json retired) — write
    # the whole normalized batch (caps + quota knobs) to the knob_store.
    for key, value in normalized.items():
        os.environ[key] = value
    if not write_persisted_knobs(normalized):
        raise RuntimeError("budget settings could not be persisted")
    return {"values": dict(normalized), "restart_required": True}


def set_identity(
    sid: str,
    text: str,
    *,
    global_root: Path | str | None = None,
) -> bool | None:
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return None
    mem = LifeMemory.open(life_dir)
    mem.identity.path.parent.mkdir(parents=True, exist_ok=True)
    mem.identity.path.write_text((text or "").rstrip() + "\n", encoding="utf-8")
    return True


def run_skill_command(tokens: list[str]) -> str:
    from ..apps._life_actions import render_skills_cmd

    return render_skills_cmd(tokens)


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
