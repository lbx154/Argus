"""Work-item queueing, configuration, and read-only diagnostic queries."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from ..apps._inbox import count_pending_inbox_messages, queue_inbox_message
from ..apps._life_actions import add_backlog_item, append_note, parse_add_flags
from ..core.config_snapshot import build_config_snapshot
from ..core.provider_quota import provider_usage_snapshot
from ..core.role_config import resolve_all_roles
from ..core.session import (
    session_lifecycle_lock,
)
from ..core.transcript import read_turns
from ..daemon.life_worker import read_continuous_state
from ..daemon.state import read_daemon_status
from ..life.memory import BacklogItem, LifeMemory
from ..life.role_activity import role_activity
from . import project_state
from .daemon_lifecycle import start_project_daemon
from .daemon_services import DaemonStatusReader, ProjectDaemonStarter
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

    item = manager_bounded_handoff(
        sid,
        objective,
        _persist,
        global_root=global_root,
        root_task_id=item_id,
        # Reuse the existing handoff's session_title; no naming-only model call.
        name_session=False,
    )
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
    start_daemon: ProjectDaemonStarter = start_project_daemon,
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
            response["daemon"] = start_daemon(
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


def get_status(
    sid: str,
    *,
    global_root: Path | str | None = None,
    read_status: DaemonStatusReader = read_daemon_status,
) -> dict[str, Any] | None:
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
            read_status(life_dir), life_dir=life_dir
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


_MODEL_OPTION_LIMIT = 64
_MODEL_SEEN_WINDOW_S = 30 * 24 * 3600


def _catalog_model_ids() -> list[str]:
    """Model ids the pi harness knows: PI_CODING_AGENT_DIR/models.json, else the installed harness dir."""
    import json as _json

    candidates = []
    configured = os.environ.get("PI_CODING_AGENT_DIR", "").strip()
    if configured:
        candidates.append(Path(configured) / "models.json")
    candidates.append(Path.home() / ".argus-skill" / "argus-pi" / "models.json")
    candidates.append(Path.home() / ".pi" / "agent" / "models.json")
    for path in candidates:
        try:
            if not path.is_file():
                continue
            raw = path.read_bytes()[:262144]
            providers = _json.loads(raw).get("providers", {})
        except (OSError, ValueError, AttributeError):
            continue
        ids: list[str] = []
        if isinstance(providers, dict):
            for config in providers.values():
                rows = config.get("models") if isinstance(config, dict) else None
                for row in rows or []:
                    model = row.get("id") if isinstance(row, dict) else None
                    if isinstance(model, str) and model and model not in ids:
                        ids.append(model)
        if ids:
            return ids
    return []


def _seen_model_ids(global_root: Path | str | None, *, now: float | None = None) -> dict[str, float]:
    """Models that answered on this home in the last thirty days, with the newest time each."""
    import json as _json

    root = _global_root(global_root)
    since = (now if now is not None else time.time()) - _MODEL_SEEN_WINDOW_S
    seen: dict[str, float] = {}
    try:
        files = sorted((root / "projects").glob("*/usage.jsonl"))
    except OSError:
        return seen
    for path in files[:200]:
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                start = max(0, size - 200_000)
                handle.seek(start)
                tail = handle.read().decode("utf-8", "ignore")
        except OSError:
            continue
        lines = tail.splitlines()
        if start:
            lines = lines[1:]  # the first line of a mid-file read is a torn record
        for line in lines:
            try:
                row = _json.loads(line)
            except ValueError:
                continue
            model = str(row.get("model") or "").strip()
            ts = row.get("completed_at") or row.get("recorded_at") or 0
            if not model or not isinstance(ts, (int, float)) or ts < since:
                continue
            if row.get("error") and not row.get("output_tokens"):
                continue  # a model that only ever failed is not an option
            seen[model] = max(seen.get(model, 0.0), float(ts))
    return seen


def model_options(global_root: Path | str | None = None) -> list[dict[str, Any]]:
    """What the quick picker offers instead of a text box.

    The harness catalog first, then models that have actually answered on
    this home (a catalog can lag the provider: gpt-6-astra answered for weeks
    before any catalog listed it), then whatever the knobs currently name.
    Bare model ids only; never provider URLs or credentials.
    """
    from ..core.knob_store import read_persisted_knobs

    options: dict[str, dict[str, Any]] = {}
    for model in _catalog_model_ids():
        options[model] = {"model": model, "source": "catalog"}
    for model, ts in sorted(_seen_model_ids(global_root).items(), key=lambda kv: -kv[1]):
        options.setdefault(model, {"model": model, "source": "seen"})["last_used_at"] = ts
    try:
        persisted = read_persisted_knobs()
    except Exception:  # noqa: BLE001 - a corrupt store still leaves the catalog
        persisted = {}
    for knob in ("ARGUS_SKILL_MODEL", *ROLE_MODEL_KNOBS, "ARGUS_SKILL_FIGURE_MODEL"):
        model = str(os.environ.get(knob) or persisted.get(knob) or "").strip()
        if model and model.lower() not in {"auto", "inherit", "default"}:
            options.setdefault(model, {"model": model, "source": "current"})
    rows = list(options.values())
    rows.sort(key=lambda row: (-(row.get("last_used_at") or 0.0), row["model"]))
    return rows[:_MODEL_OPTION_LIMIT]


def get_config(
    *,
    project_state_dir: Path | str | None = None,
    global_root: Path | str | None = None,
) -> dict[str, Any]:
    """Runtime settings snapshot with the host-global USD budget and the model options."""
    snapshot = build_config_snapshot(env=os.environ)
    try:
        snapshot["model_options"] = model_options(global_root)
    except Exception:  # noqa: BLE001 - the snapshot must never fail on the options
        snapshot["model_options"] = []
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
    "figure_model": "ARGUS_SKILL_FIGURE_MODEL",
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
    "global_daily_tokens": "ARGUS_SKILL_GLOBAL_DAILY_TOKEN_CAP",
    "max_daemons": "ARGUS_SKILL_MAX_ACTIVE_DAEMONS",
    "daemon_limit": "ARGUS_SKILL_MAX_ACTIVE_DAEMONS",
    "codex_daily_requests": "ARGUS_SKILL_CODEX_DAILY_CALL_CAP",
    "copilot_daily_requests": "ARGUS_SKILL_COPILOT_DAILY_CALL_CAP",
    "copilot_daily_premium": "ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP",
    "safe_mode": "ARGUS_SKILL_SAFE_MODE",
    "show_reasoning": "ARGUS_SKILL_SHOW_REASONING",
    "telegram": "ARGUS_SKILL_ENABLE_TELEGRAM",
}


# The knobs that pin one role (or one host-side task) to its own model. A
# role knob wins over ARGUS_SKILL_MODEL, so a picker that sets only the shared
# knob changes nothing on a host whose roles were pinned at setup; the trial
# host showed "gpt-6-astra" chosen while every role still ran gemini.
# ARGUS_SKILL_FIGURE_MODEL is a route override and ARGUS_SKILL_MAP_MODEL a
# host-side draw; both are left alone.
ROLE_MODEL_KNOBS: tuple[str, ...] = (
    "ARGUS_SKILL_MANAGER_MODEL", "ARGUS_SKILL_MANAGER_REPLY_MODEL", "ARGUS_SKILL_PLAN_MODEL",
    "ARGUS_SKILL_PLAN_PREVIEW_MODEL", "ARGUS_SKILL_ENGINEER_MODEL", "ARGUS_SKILL_REVIEWER_MODEL",
    "ARGUS_SKILL_SUPERVISOR_MODEL", "ARGUS_SKILL_CURATOR_MODEL", "ARGUS_SKILL_FRONTDOOR_MODEL",
    "ARGUS_SKILL_REWRITE_MODEL", "ARGUS_SKILL_BOUNDED_DAG_MODEL", "ARGUS_SKILL_REFLECTION_MODEL",
)


def role_model_pins(persisted: Mapping[str, str] | None = None) -> dict[str, str]:
    """Role knobs that currently pin a model of their own (env first, then persisted)."""
    from ..core.knob_store import read_persisted_knobs

    store = persisted if persisted is not None else read_persisted_knobs()
    pins: dict[str, str] = {}
    for knob in ROLE_MODEL_KNOBS:
        value = str(os.environ.get(knob) or store.get(knob) or "").strip()
        if value and value.lower() not in {"auto", "inherit", "default", ""}:
            pins[knob] = value
    return pins


def set_operator_config(
    name: str,
    value: str,
    *,
    project_state_dir: Path | str | None = None,
    global_root: Path | str | None = None,
    apply_to_roles: bool = False,
) -> dict[str, Any]:
    from ..core.knob_store import write_persisted_knob, write_persisted_knobs
    from ..core.knobs import cockpit_editable_names, normalize_cockpit_knob_value

    raw = (name or "").strip()
    env_name = _CONFIG_ALIASES.get(raw.lower(), raw.upper())
    allowed = set(cockpit_editable_names()) | {"ARGUS_SKILL_RUNNER_BACKEND"}
    if env_name not in allowed:
        raise ValueError(f"config key is not cockpit-editable: {raw}")
    val = normalize_cockpit_knob_value(env_name, value)
    released: list[str] = []
    if apply_to_roles and env_name == "ARGUS_SKILL_MODEL":
        # The shared choice is meant for every role: release the role pins so
        # they follow it. A pin the operator sets afterwards in the role table
        # wins again, as before.
        released = sorted(role_model_pins())
        if released and not write_persisted_knobs({knob: "" for knob in released}):
            raise RuntimeError("role model pins could not be released")
        for knob in released:
            os.environ.pop(knob, None)
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
    return {
        "name": env_name, "value": val,
        "released_role_pins": released,
        "role_pins": role_model_pins(),
        "restart_required": env_name not in {
            "ARGUS_SKILL_MAP_MODEL", "ARGUS_SKILL_MAP_REASONING_EFFORT",
            "ARGUS_SKILL_MAP_REVIEW_REASONING_EFFORT",
            "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD",
            "ARGUS_SKILL_GLOBAL_DAILY_TOKEN_CAP",
        },
    }


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

    optional = {"global_daily_tokens"}
    unknown = sorted(set(values) - _BUDGET_BATCH_ALIASES - optional)
    if unknown:
        raise ValueError(f"unsupported budget setting(s): {', '.join(unknown)}")
    normalized: dict[str, str] = {}
    for alias in _BUDGET_BATCH_ALIASES | (optional & set(values)):
        if alias not in values:
            raise ValueError(f"missing budget setting: {alias}")
        env_name = _CONFIG_ALIASES[alias]
        normalized[env_name] = normalize_cockpit_knob_value(
            env_name,
            str(values[alias]),
        )
    from ..core.operator_context import IntakeDecision, persist_intake_decision

    # Budget caps are ordinary config.json knobs now (budget.json retired) — write
    # the whole normalized batch (caps + quota knobs) to the knob_store.
    if not write_persisted_knobs(normalized):
        raise RuntimeError("budget settings could not be persisted")
    for key, value in normalized.items():
        os.environ[key] = value
    rendered = ", ".join(f"{key}={normalized[key]}" for key in sorted(normalized))
    try:
        persist_intake_decision(project_state_dir, rendered, IntakeDecision(
            kind="preference", scope="project", applies_to_roles=("manager", "planner"),
            preference_kind="workflow", preference_value=rendered), source="web.config.budget")
    except OSError:
        # The authoritative setting is already saved. A failed optional context
        # note must not tell the user that the budget change failed.
        pass
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


def run_skill_command(
    tokens: list[str], *, global_root: Path | None = None,
    project_state: Path | None = None, workdir: Path | None = None,
) -> str:
    from ..apps._life_actions import render_skills_cmd

    return render_skills_cmd(tokens, global_root=global_root, project_state=project_state, workdir=workdir)


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
