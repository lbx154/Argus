"""Shared non-interactive life-command helpers."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Sequence

from ..life import BacklogItem, JournalEntry


def format_backlog_list(mem: Any, *, include_all: bool) -> str:
    items = mem.backlog.all() if include_all else [
        i for i in mem.backlog.all() if i.status == "pending"
    ]
    if not items:
        return "(backlog is empty)"
    lines = [
        (
            f"  {it.status:<8}  {it.id}  "
            f"p={it.priority:<4}  cap=${it.max_cost_usd:.2f}  "
            f"{it.title}"
        )
        for it in items
    ]
    return "\n".join(lines)


def parse_add_flags(
    text: str,
    *,
    default_iterate: bool = True,
    default_cycles: int = 6,
    default_budget: float = 30.0,
) -> tuple[bool, int, float, str]:
    """Strip ``--once`` / ``--cycles=N`` / ``--budget=$X`` from an /add body."""
    iterate = default_iterate
    max_cycles = default_cycles
    budget = default_budget
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
        if low.startswith("--budget="):
            raw = low.split("=", 1)[1].lstrip("$")
            try:
                budget = max(0.0, float(raw))
            except ValueError:
                pass
            continue
        keep.append(tok)
    return iterate, max_cycles, budget, " ".join(keep).strip()


def add_backlog_item(
    mem: Any,
    text: str,
    *,
    priority: int = 100,
    iterate: bool = True,
    iteration_max_cycles: int = 6,
    iteration_budget_usd: float = 30.0,
) -> BacklogItem:
    text = text.strip()
    title = text.splitlines()[0][:60].strip() or "(untitled)"
    return mem.backlog.add(BacklogItem.new(
        title=title,
        objective=text,
        priority=priority,
        max_cost_usd=30.0,
        tags=[],
        iterate=iterate,
        iteration_max_cycles=iteration_max_cycles,
        iteration_budget_usd=iteration_budget_usd,
    ))


def format_added_item(item: BacklogItem) -> str:
    iter_blurb = (
        f", iter≤{item.iteration_max_cycles} ${item.iteration_budget_usd:.1f}"
        if item.iterate else ", once"
    )
    return (
        f"added {item.id}: {item.title}  "
        f"(priority={item.priority}, max_cost=${item.max_cost_usd:.2f}{iter_blurb})"
    )


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
    entry = JournalEntry.new(
        kind="user_note",
        title="manual note",
        summary=text.strip(),
        tags=[],
    )
    mem.journal.append(entry)
    return f"note appended (id={entry.id})"


def stop_iteration(mem: Any, item_id: str) -> str:
    stopped = mem.backlog.stop_iteration(item_id)
    if stopped is None:
        return f"/stop: no item with id {item_id!r}"
    return f"iteration disabled for {stopped.id}: {stopped.title}  (status={stopped.status})"


def render_run_command(
    mem: Any,
    opts: Sequence[str],
    chat_state: dict[str, Any],
) -> str:
    """Run the shared foreground supervisor flow and render its transcript."""
    from ..manager.repl import _format_elapsed
    from ._runtime import _invoke_supervisor

    cfg = chat_state.get("config", {})
    p = argparse.ArgumentParser(prog="/run", add_help=False)
    p.add_argument("--once", action="store_true")
    p.add_argument(
        "--backend",
        choices=("codex",),
        default=chat_state.get("backend", "codex"),
    )
    p.add_argument("--max-missions", type=int,
                   default=int(cfg.get("cycles", 6)))
    p.add_argument("--per-mission-cap-usd", type=float,
                   default=float(cfg.get("per_mission_cap", 30.0)))
    p.add_argument("--daily-cap-usd", type=float,
                   default=float(cfg.get("daily_cap", 180.0)))
    p.add_argument("--quiet", action="store_true")
    try:
        run_args = p.parse_args(list(opts))
    except SystemExit:
        return ""

    lines = [
        (
            f"/run: backend={run_args.backend}  "
            f"max_missions={'1 (once)' if run_args.once else run_args.max_missions}  "
            f"per_mission_cap=${run_args.per_mission_cap_usd:.2f}  "
            f"daily_cap=${run_args.daily_cap_usd:.2f}"
        ),
        "       (foreground; Ctrl-C requests graceful stop)",
    ]

    use_seed = run_args.backend == chat_state.get("backend")
    seed = chat_state.get("last_thread_id") if use_seed else None
    theme = chat_state.get("theme")
    if seed and not run_args.quiet:
        note = f"resuming codex session {seed[:12]}…"
        lines.append(theme.gray(note) if theme else note)
    t0 = time.monotonic()
    summary, last_tid = _invoke_supervisor(
        mem=mem,
        backend=run_args.backend,
        once=run_args.once,
        max_missions=run_args.max_missions,
        per_mission_cap_usd=run_args.per_mission_cap_usd,
        daily_cap_usd=run_args.daily_cap_usd,
        quiet=run_args.quiet,
        seed_thread_id=seed,
    )
    elapsed = time.monotonic() - t0
    if use_seed:
        chat_state["last_thread_id"] = last_tid
    chat_state["last_elapsed_s"] = elapsed
    chat_state["total_elapsed_s"] = chat_state.get("total_elapsed_s", 0.0) + elapsed
    if isinstance(summary, dict):
        summary.setdefault("elapsed_s", round(elapsed, 3))
    lines.extend([
        "",
        "--- /run summary ---",
        json.dumps(summary, indent=2, default=str),
        theme.dim(f"⏱  /run elapsed {_format_elapsed(elapsed)}")
        if theme else f"⏱  /run elapsed {_format_elapsed(elapsed)}",
    ])
    return "\n".join(lines)


_CONFIG_DEFAULTS: dict[str, Any] = {
    "iterate": True,
    "cycles": 6,
    "budget": 30.0,
    "per_mission_cap": 30.0,
    "daily_cap": 180.0,
    "continuous": False,
}


def render_config_cmd(
    tokens: Sequence[str],
    chat_state: dict[str, Any],
    *,
    life_dir: Path | None = None,
) -> str:
    cfg = chat_state.setdefault("config", dict(_CONFIG_DEFAULTS))
    if not tokens:
        config_lines = [
            "session config (continuous syncs to daemon, others are REPL-local):"
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
        config_lines.append("  usage: /config cycles=10 budget=50 daily_cap=300")
        return "\n".join(config_lines)

    lines: list[str] = []
    sync_continuous = False
    for tok in tokens:
        if "=" not in tok:
            lines.append(f"  skip: {tok!r} — expected key=value")
            continue
        key, _, val = tok.partition("=")
        key = key.strip().lower().replace("-", "_")
        if key not in _CONFIG_DEFAULTS:
            lines.append(
                f"  unknown key: {key!r}  "
                f"(valid: {', '.join(sorted(_CONFIG_DEFAULTS))})"
            )
            continue
        expected = bool if isinstance(_CONFIG_DEFAULTS[key], bool) else type(_CONFIG_DEFAULTS[key])
        try:
            val = val.strip().lstrip("$")
            if expected is bool:
                parsed: Any = val.lower() in {"true", "on", "yes", "1"}
            elif expected is int:
                parsed = max(1, int(val))
            else:
                parsed = max(0.0, float(val))
        except ValueError:
            lines.append(f"  bad value for {key}: {val!r}")
            continue
        if key == "continuous" and parsed:
            backend = str(chat_state.get("backend", "") or "codex")
            current_objective = str(chat_state.get("continuous_objective", "") or "")
            error = _continuous_session_error(backend, True, current_objective)
            if error:
                lines.append(error)
                continue
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
        from ..daemon.life_worker import write_continuous_config

        write_continuous_config(
            life_dir,
            enabled=cfg.get("continuous", False),
            objective=chat_state.get("continuous_objective", ""),
        )
        lines.append("  (synced to daemon — takes effect within seconds)")
    return "\n".join(lines)


def render_backend_cmd(tokens: Sequence[str], chat_state: dict[str, Any]) -> str:
    from ..daemon.life_worker import ContinuousConfigState

    if not tokens:
        return f"backend: {chat_state.get('backend')}  (memory or codex)"
    new = tokens[0].lower()
    if new in {"codex", "memory"}:
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
    return f"backend {new!r} is not available. Use `codex` or `memory`."


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


def _project_card_path(mem: Any) -> Path:
    project = getattr(mem, "project", None)
    card = getattr(project, "project_card", None)
    if card is not None:
        return Path(getattr(card, "path", card))
    card = getattr(mem, "project_card", None)
    if card is not None:
        return Path(getattr(card, "path", card))
    root = getattr(project, "root", None)
    if root is None:
        root = getattr(mem, "root", None)
    if root is None:
        raise AttributeError("memory object does not expose a project card path")
    return Path(root) / "project.md"


def render_project_cmd(
    mem: Any,
    tokens: Sequence[str],
    rest_text: str,
    *,
    empty_hint: str = "set",
) -> str:
    path = _project_card_path(mem)
    if not tokens:
        text = path.read_text(encoding="utf-8").strip() if path.exists() else ""
        return text or f"(project card empty — try /project {empty_hint})"
    sub = tokens[0].lower()
    if sub == "set":
        body = rest_text[len("set"):].lstrip() if rest_text.lower().startswith("set") else ""
        if not body:
            return "usage: /project set <text>"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.rstrip() + "\n", encoding="utf-8")
        return "project card updated"
    return f"unknown /project subcommand: {sub}"


def render_reset_cmd(chat_state: dict[str, Any]) -> str:
    old = chat_state.get("last_thread_id")
    chat_state["last_thread_id"] = None
    if old:
        return f"reset: dropped codex session {str(old)[:12]}…  next mission will start fresh"
    return "reset: no active codex session"


def render_skills_cmd(cwd: Path, tokens: Sequence[str]) -> str:
    op = (tokens[0].lower() if tokens else "ls")
    if op in ("ls", "list"):
        from ..core import paths as core_paths
        from ..skills.store import SkillStore

        global_store = SkillStore(core_paths.skills_global_root())
        rows = global_store.list_summaries()
        if not rows:
            return "(no global skills)"
        return "\n".join(
            f"- {s['name']}  ({s.get('category') or '-'})  {s['description']}"
            for s in rows
        )
    if op == "promote":
        if len(tokens) < 2:
            return "usage: /skills promote <name> [--to-vertical <vertical>]"
        name = tokens[1]
        # ``--to-vertical <v>`` files the skill into that vertical's SOURCE dir
        # (verticals/<v>/skills/); otherwise it goes to builtin_skills/. Both
        # write into the argus repo and commit (the Manager-tidy direction,
        # exposed for manual use).
        to_vertical: str | None = None
        rest = list(tokens[2:])
        if "--to-vertical" in rest:
            i = rest.index("--to-vertical")
            if i + 1 >= len(rest):
                return "usage: /skills promote <name> --to-vertical <vertical>"
            to_vertical = rest[i + 1].strip().lower()

        from ..core import paths as core_paths
        from ..manager.skill_tidy import commit_to_source, write_skill_to_source
        from ..skills.store import SkillStore
        from ..skills.vertical_select import VERTICALS

        if to_vertical is not None and (
            to_vertical not in VERTICALS or to_vertical == "research"
        ):
            known = ", ".join(v for v in VERTICALS if v != "research")
            return f"unknown/invalid vertical {to_vertical!r}; known: {known}"

        runtime = SkillStore(core_paths.skills_global_root())
        target = None
        role = ""
        for s in runtime.list_summaries():
            if s["name"].casefold() == name.casefold():
                target = runtime.load(s["path"])
                role = s.get("role") or ""
                break
        if target is None:
            return f"no skill named {name!r} in the runtime library"

        try:
            if to_vertical is not None:
                dest = write_skill_to_source(
                    target, "vertical", vertical=to_vertical, role=role
                )
                label = f"verticals/{to_vertical}"
            else:
                dest = write_skill_to_source(target, "global", role=role)
                label = "builtin"
        except Exception as exc:  # noqa: BLE001
            return f"promote failed: {exc}"
        if dest is None:
            return "promote failed: invalid target"

        committed = commit_to_source(
            [dest], f"chore(skills): promote '{target.name}' into {label} [manual]"
        )
        tail = "" if committed else " (written, but git commit failed — commit manually)"
        return f"promoted {target.name} → {label} source ({dest}){tail}"
    return f"unknown /skills subcommand: {op}  (try ls | promote)"
