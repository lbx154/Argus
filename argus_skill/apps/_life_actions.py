"""Shared non-interactive life-command helpers."""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..life import BacklogItem

_LIFE_BACKENDS = ("codex", "claude", "copilot", "opencode", "pi", "memory")


def format_backlog_list(mem: Any, *, include_all: bool) -> str:
    items = mem.backlog.all() if include_all else [
        i for i in mem.backlog.all() if i.status == "pending"
    ]
    if not items:
        return "(backlog is empty)"
    lines = [
        (
            f"  {it.status:<8}  {it.id}  "
            f"p={it.priority:<4}  "
            f"{it.title}"
        )
        for it in items
    ]
    return "\n".join(lines)


def parse_add_flags(
    text: str,
    *,
    defaults: Mapping[str, Any],
) -> tuple[bool, int, str]:
    """Strip ``--once`` / ``--cycles=N`` from an /add body."""
    iterate = bool(defaults.get("iterate", DEFAULT_LIFE_CONFIG["iterate"]))
    max_cycles = int(defaults.get("cycles", DEFAULT_LIFE_CONFIG["cycles"]))
    tokens = text.split()
    keep: list[str] = []
    for tok in tokens:
        low = tok.lower()
        if low == "--once":
            iterate = False
            continue
        if low.startswith("--cycles="):
            try:
                max_cycles = max(1, int(low.split("=", 1)[1]))
            except ValueError:
                pass
            continue
        keep.append(tok)
    return iterate, max_cycles, " ".join(keep).strip()


def add_backlog_item(
    mem: Any,
    text: str,
    *,
    item_id: str | None = None,
    priority: int = 100,
    iterate: bool = True,
    iteration_max_cycles: int = 6,
) -> BacklogItem:
    text = text.strip()
    title = text.splitlines()[0][:60].strip() or "(untitled)"
    return mem.backlog.add(BacklogItem.new(
        item_id=item_id,
        title=title,
        objective=text,
        priority=priority,
        tags=[],
        iterate=iterate,
        iteration_max_cycles=iteration_max_cycles,
    ))


def format_status_change(mem: Any, cmd: str, item_id: str) -> str:
    if cmd == "/done":
        ok = mem.backlog.mark_done(item_id) is not None
    elif cmd == "/skip":
        ok = mem.backlog.update(item_id, status="skipped") is not None
    else:  # /rm
        ok = mem.backlog.remove(item_id)
    return f"{cmd[1:]}: {item_id}  {'ok' if ok else '(not found)'}"


def format_journal_tail(mem: Any, n: int) -> str:
    entries = mem.journal.tail(n)
    if not entries:
        return "(journal is empty)"
    lines: list[str] = []
    for e in entries:
        from datetime import datetime

        ts = datetime.fromtimestamp(e.ts).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"  [{ts}] {e.kind:<14} {e.title}")
        if e.summary:
            lines.append(f"      {e.summary}")
    return "\n".join(lines)


def append_note(mem: Any, text: str) -> str:
    note_id = uuid.uuid4().hex[:12]
    try:
        from ..life.event_log import JsonlEventSink

        project = getattr(mem, "project", None)
        root = getattr(project, "root", None) or getattr(mem, "root", None)
        if root is not None:
            JsonlEventSink(None, life_dir=Path(root)).append({
                "type": "user.note",
                "id": note_id,
                "title": "manual note",
                "summary": text.strip(),
                "tags": [],
            })
    except Exception:  # noqa: BLE001
        pass
    return f"note appended (id={note_id})"


def stop_iteration(mem: Any, item_id: str) -> str:
    stopped = mem.backlog.stop_iteration(item_id)
    if stopped is None:
        return f"/stop: no item with id {item_id!r}"
    return f"iteration disabled for {stopped.id}: {stopped.title}  (status={stopped.status})"


def _format_elapsed(seconds: float) -> str:
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m{secs:02d}s"


def render_run_command(
    mem: Any,
    opts: Sequence[str],
    chat_state: dict[str, Any],
) -> str:
    """Run the shared foreground supervisor flow for remote command clients."""
    from ._runtime import _invoke_supervisor

    cfg = chat_state.get("config", {})
    from ..core.knobs import resolve_budget_caps

    global_budget = resolve_budget_caps().global_daily_cap_usd
    parser = argparse.ArgumentParser(prog="/run", add_help=False)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--backend",
        choices=_LIFE_BACKENDS,
        default=chat_state.get("backend", "codex"),
    )
    parser.add_argument(
        "--max-missions",
        type=int,
        default=int(cfg.get("cycles", 6)),
    )
    parser.add_argument(
        "--global-daily-cap-usd",
        type=float,
        default=global_budget,
    )
    parser.add_argument("--quiet", action="store_true")
    try:
        args = parser.parse_args(list(opts))
    except SystemExit:
        return ""

    lines = [
        (
            f"/run: backend={args.backend}  "
            f"max_missions={'1 (once)' if args.once else args.max_missions}  "
            f"global_daily_cap=${args.global_daily_cap_usd:.2f}"
        ),
        "       (foreground; Ctrl-C requests graceful stop)",
    ]
    use_seed = args.backend == chat_state.get("backend")
    seed = chat_state.get("last_thread_id") if use_seed else None
    started = time.monotonic()
    summary, last_thread_id = _invoke_supervisor(
        mem=mem,
        backend=args.backend,
        once=args.once,
        max_missions=args.max_missions,
        global_daily_cap_usd=args.global_daily_cap_usd,
        quiet=args.quiet,
        seed_thread_id=seed,
    )
    elapsed = time.monotonic() - started
    if use_seed:
        chat_state["last_thread_id"] = last_thread_id
    chat_state["last_elapsed_s"] = elapsed
    chat_state["total_elapsed_s"] = chat_state.get("total_elapsed_s", 0.0) + elapsed
    if isinstance(summary, dict):
        summary.setdefault("elapsed_s", round(elapsed, 3))
    lines.extend(
        [
            "",
            "--- /run summary ---",
            json.dumps(summary, indent=2, default=str),
            f"/run elapsed {_format_elapsed(elapsed)}",
        ]
    )
    return "\n".join(lines)


DEFAULT_LIFE_CONFIG: dict[str, Any] = {
    "iterate": True,
    "cycles": 6,
    "continuous": False,
    "manager_effort": "xhigh",
    "planner_effort": "xhigh",
    "engineer_effort": "xhigh",
    "reviewer_effort": "high",
}

_ROLE_EFFORT_ENVS: dict[str, str] = {
    "manager_effort": "ARGUS_SKILL_MANAGER_REASONING_EFFORT",
    "planner_effort": "ARGUS_SKILL_PLANNER_REASONING_EFFORT",
    "engineer_effort": "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
    "reviewer_effort": "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
}
_EFFORT_VALUES = {"low", "medium", "high", "xhigh", "max"}


def render_config_cmd(
    tokens: Sequence[str],
    chat_state: dict[str, Any],
    *,
    life_dir: Path | None = None,
) -> str:
    cfg = chat_state.setdefault("config", dict(DEFAULT_LIFE_CONFIG))
    if not tokens:
        config_lines = [
            "session config (continuous syncs to daemon, others are process-local):"
        ]
        for key, value in cfg.items():
            if isinstance(value, float):
                config_lines.append(
                    f"  {key:20s} = ${value:.2f}" if key != "iterate" else f"  {key:20s} = {value}"
                )
            elif isinstance(value, bool):
                config_lines.append(f"  {key:20s} = {'on' if value else 'off'}")
            else:
                config_lines.append(f"  {key:20s} = {value}")
        config_lines.append("")
        config_lines.append(
            "  usage: /config cycles=10 budget=50 daily_cap=300 engineer_effort=xhigh"
        )
        return "\n".join(config_lines)

    lines: list[str] = []
    sync_continuous = False
    for tok in tokens:
        if "=" not in tok:
            lines.append(f"  skip: {tok!r} — expected key=value")
            continue
        key, _, val = tok.partition("=")
        key = key.strip().lower().replace("-", "_")
        if key not in DEFAULT_LIFE_CONFIG:
            lines.append(
                f"  unknown key: {key!r}  "
                f"(valid: {', '.join(sorted(DEFAULT_LIFE_CONFIG))})"
            )
            continue
        expected = (
            bool
            if isinstance(DEFAULT_LIFE_CONFIG[key], bool)
            else type(DEFAULT_LIFE_CONFIG[key])
        )
        try:
            val = val.strip().lstrip("$")
            if expected is bool:
                parsed: Any = val.lower() in {"true", "on", "yes", "1"}
            elif expected is int:
                parsed = max(1, int(val))
            elif expected is str:
                parsed = val.strip().lower()
                if key in _ROLE_EFFORT_ENVS and parsed not in _EFFORT_VALUES:
                    raise ValueError
            else:
                parsed = max(0.0, float(val))
        except ValueError:
            lines.append(f"  bad value for {key}: {val!r}")
            continue
        if key == "continuous" and parsed:
            lines.append(
                "  use /continuous start <objective> so Manager can author "
                "the Planner/Engineer handoff"
            )
            continue
        if key in _ROLE_EFFORT_ENVS:
            # Persist too — an env-var-only switch used to only last for
            # THIS process; the daemon (a separate process) never saw it
            # until restarted, and even the cockpit forgot it on its own next
            # launch. core.knobs.resolve_role_reasoning_effort now checks
            # this file whenever no env var is set, so "change it once via
            # /config" holds across restarts too.
            from ..core.knob_store import write_persisted_knob

            if not write_persisted_knob(_ROLE_EFFORT_ENVS[key], str(parsed)):
                lines.append(f"  failed to persist {key}; nothing changed")
                continue
            os.environ[_ROLE_EFFORT_ENVS[key]] = str(parsed)
            chat_state.pop("manager_runner", None)
        cfg[key] = parsed
        if key == "continuous":
            sync_continuous = True
        if isinstance(parsed, float):
            lines.append(f"  {key} = ${parsed:.2f}")
        elif isinstance(parsed, bool):
            lines.append(f"  {key} = {'on' if parsed else 'off'}")
        else:
            lines.append(f"  {key} = {parsed}")
    if life_dir is not None and sync_continuous:
        from ..daemon.life_worker import (
            disable_continuous_config,
            write_continuous_config,
        )

        if cfg.get("continuous", False):
            write_continuous_config(
                life_dir,
                enabled=True,
                objective=chat_state.get("continuous_objective", ""),
            )
        else:
            disable_continuous_config(life_dir)
        lines.append("  (synced to daemon — takes effect within seconds)")
    return "\n".join(lines)


def render_backend_cmd(tokens: Sequence[str], chat_state: dict[str, Any]) -> str:
    from ..daemon.life_worker import ContinuousConfigState

    if not tokens:
        return (
            f"backend: {chat_state.get('backend')}  "
            f"({' | '.join(_LIFE_BACKENDS)})"
        )
    new = tokens[0].lower()
    if new == "opencod":
        new = "opencode"
    if new in _LIFE_BACKENDS:
        state = chat_state.get("continuous_state")
        if isinstance(state, ContinuousConfigState):
            continuous = state.enabled
            objective = state.objective if state.enabled else ""
        else:
            continuous = bool(chat_state.get("config", {}).get("continuous", False))
            objective = str(chat_state.get("continuous_objective", "") or "")
        error = _continuous_session_error(new, continuous, objective)
        if error:
            return error
        chat_state["backend"] = new
        return f"backend: {new}"
    return (
        f"backend {new!r} is not available. "
        f"Use one of: {', '.join(_LIFE_BACKENDS)}."
    )


def _continuous_session_error(
    backend: str,
    continuous: bool,
    objective: str,
) -> str:
    from ..daemon.life_worker import continuous_mode_error

    error = continuous_mode_error(backend, continuous, objective)
    if error:
        return f"argus-skill: {error}"
    return ""


def render_identity_cmd(
    mem: Any,
    tokens: Sequence[str],
    rest_text: str,
    *,
    empty_hint: str = "set",
) -> str:
    if not tokens:
        text = mem.identity.read().strip()
        return text or f"(identity empty — try /identity {empty_hint})"
    sub = tokens[0].lower()
    if sub == "set":
        body = rest_text[len("set"):].lstrip() if rest_text.lower().startswith("set") else ""
        if not body:
            return "usage: /identity set <text>"
        mem.identity.path.write_text(body.rstrip() + "\n", encoding="utf-8")
        return "identity card updated"
    return f"unknown /identity subcommand: {sub}"


def render_reset_cmd(chat_state: dict[str, Any]) -> str:
    old = chat_state.get("last_thread_id")
    chat_state["last_thread_id"] = None
    if old:
        return f"reset: dropped codex session {str(old)[:12]}…  next mission will start fresh"
    return "reset: no active codex session"


def render_skills_cmd(tokens: Sequence[str]) -> str:
    op = (tokens[0].lower() if tokens else "ls")
    if op in ("ls", "list"):
        from ..core import paths as core_paths
        from ..skills.store import SkillStore

        global_store = SkillStore(core_paths.shared_skills_root())
        rows = global_store.list_summaries()
        if not rows:
            return "(no global skills)"
        return "\n".join(f"- {s['path']}" for s in rows)
    return f"unknown /skills subcommand: {op}  (try ls)"
