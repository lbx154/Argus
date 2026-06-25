"""Manager REPL — the interactive conversation entry point.

This is the single interactive surface the bare ``argus-skill`` command
drops into. It owns the slash-command dispatch and the free-text /
chat-fast-path conversation loop. The runtime infrastructure it drives
(runner factory, supervisor driver, event sink) lives in
``argus_skill.apps._runtime`` so the daemon / teammate paths never import
this interactive layer.

- ``run_manager_repl``         — public entry point invoked from
                                  ``apps.cli.main`` when the user types
                                  ``argus-skill`` with no subcommand.
                                  (Formerly ``run_life_chat_loop``.)
- ``run_life_supervisor`` etc. live in ``apps._runtime``; this module
  imports what it needs from there.

History: the original layout had a separate ``argus-skill chat
--life`` subcommand. As of Phase 5 (2026-05-08) the bare
``argus-skill`` command IS this REPL, and the chat / go / mission /
life / daemon / up subcommands have been deleted. One REPL, one
renderer, one help screen. The 2026-06-25 refactor moved the
conversation surface here under the Manager.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..apps._life_actions import (
    _continuous_session_error as _shared_continuous_session_error,
)
from ..apps._life_actions import (
    add_backlog_item,
    append_note,
    format_added_item,
    format_backlog_list,
    format_journal_tail,
    format_status_change,
    parse_add_flags,
    render_backend_cmd,
    render_config_cmd,
    render_identity_cmd,
    render_project_cmd,
    render_reset_cmd,
    render_run_command,
    render_skills_cmd,
    stop_iteration,
)
from ..apps._runtime import (
    _codex_preflight_warning,
    _CommonMemory,
    _format_daemon_mode_cell,
    _invoke_supervisor,
    _memory_global_root,  # noqa: F401 — kept for parity with the old module surface
    _resolve_global_root,
    _SplitMemory,
)
from ..core import paths as core_paths
from ..life import BacklogItem, LifeMemory, MemoryBundle

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Slash-command helpers (in-process; mirror the public CLI subcommands)
# ---------------------------------------------------------------------------

def _parse_add_flags(
    text: str,
    *,
    default_iterate: bool = True,
    default_cycles: int = 6,
    default_budget: float = 30.0,
) -> tuple[bool, int, float, str]:
    return parse_add_flags(
        text,
        default_iterate=default_iterate,
        default_cycles=default_cycles,
        default_budget=default_budget,
    )


def _add_only(
    mem: _CommonMemory,
    text: str,
    *,
    priority: int = 100,
    iterate: bool = True,
    iteration_max_cycles: int = 6,
    iteration_budget_usd: float = 30.0,
) -> BacklogItem:
    item = add_backlog_item(
        mem,
        text,
        priority=priority,
        iterate=iterate,
        iteration_max_cycles=iteration_max_cycles,
        iteration_budget_usd=iteration_budget_usd,
    )
    print(format_added_item(item), flush=True)
    return item


def _backend_cmd(tokens: list[str], chat_state: dict[str, Any]) -> None:
    print(render_backend_cmd(tokens, chat_state))


def _continuous_session_error(
    backend: str,
    continuous: bool,
    objective: str,
) -> str:
    return _shared_continuous_session_error(backend, continuous, objective)


_CONFIG_DEFAULTS: dict[str, Any] = {
    "iterate": True,
    "cycles": 6,
    "budget": 30.0,
    "per_mission_cap": 30.0,
    "daily_cap": 180.0,
    "continuous": False,
}


def _config_cmd(tokens: list[str], chat_state: dict[str, Any],
                life_dir: Path | None = None) -> None:
    """``/config [key=value ...]`` — view or change REPL-session defaults.

    These defaults apply to free-text input and ``/add``/``/run`` when
    the corresponding flag is not explicitly provided. The ``continuous``
    key is also persisted to disk so the background daemon picks it up.
    """
    print(render_config_cmd(tokens, chat_state, life_dir=life_dir))


def _identity_cmd(mem: _CommonMemory, tokens: list[str], rest_text: str) -> None:
    if not tokens:
        print(render_identity_cmd(mem, tokens, rest_text, empty_hint="edit"))
        return
    sub = tokens[0].lower()
    if sub == "edit":
        print("Enter new identity card. End with a single '.' on its own line:")
        lines: list[str] = []
        while True:
            try:
                ln = input("> ")
            except (EOFError, KeyboardInterrupt):
                print("\n(aborted, identity unchanged)")
                return
            if ln.strip() == ".":
                break
            lines.append(ln)
        new_text = "\n".join(lines).strip() + "\n"
        mem.identity.path.write_text(new_text, encoding="utf-8")
        print(f"identity card updated ({len(lines)} lines)")
        return
    print(render_identity_cmd(mem, tokens, rest_text))


def _project_cmd(mem: _CommonMemory, tokens: list[str], rest_text: str) -> None:
    print(render_project_cmd(mem, tokens, rest_text))


def _continuous_cmd(
    mem: _SplitMemory,
    arg_text: str,
    chat_state: dict[str, Any],
) -> None:
    from ..daemon.life_worker import (
        ContinuousConfigState,
        continuous_mode_error,
        read_continuous_config,
        read_continuous_state,
        write_continuous_config,
    )

    tokens = shlex.split(arg_text) if arg_text.strip() else []
    sub = tokens[0].lower() if tokens else "status"
    backend = str(chat_state.get("backend", "") or "codex")

    state = chat_state.get("continuous_state")
    if isinstance(state, ContinuousConfigState):
        current_objective = state.objective
    else:
        _, current_objective = read_continuous_config(mem.project.root)

    if sub in {"start", "on", "enable"}:
        objective = " ".join(tokens[1:]).strip() or current_objective
        error = continuous_mode_error(backend, True, objective)
        if error:
            print(error)
            return
        write_continuous_config(mem.project.root, enabled=True, objective=objective)
        updated = read_continuous_state(mem.project.root)
        chat_state["continuous_state"] = updated
        chat_state["continuous_objective"] = updated.objective
        chat_state.setdefault("config", dict(_CONFIG_DEFAULTS))["continuous"] = True
        print(
            f"continuous: on\n"
            f"objective: {updated.objective or '(none)'}"
        )
        return

    if sub in {"stop", "off", "pause"}:
        objective = " ".join(tokens[1:]).strip() or current_objective
        write_continuous_config(mem.project.root, enabled=False, objective=objective)
        updated = read_continuous_state(mem.project.root)
        chat_state["continuous_state"] = updated
        chat_state["continuous_objective"] = updated.objective
        chat_state.setdefault("config", dict(_CONFIG_DEFAULTS))["continuous"] = False
        print(
            f"continuous: off\n"
            f"objective: {updated.objective or '(none)'}"
        )
        return

    enabled, objective = read_continuous_config(mem.project.root)
    chat_state["continuous_state"] = ContinuousConfigState(
        enabled=enabled,
        objective=objective,
    )
    chat_state["continuous_objective"] = objective
    print(
        f"continuous: {'on' if enabled else 'off'}\n"
        f"objective: {objective or '(none)'}"
    )


def _backlog_list_cmd(mem: _CommonMemory, *, include_all: bool) -> None:
    print(format_backlog_list(mem, include_all=include_all))


def _status_change_cmd(mem: _CommonMemory, cmd: str, item_id: str) -> None:
    print(format_status_change(mem, cmd, item_id))


def _journal_tail_cmd(mem: _CommonMemory, n: int) -> None:
    print(format_journal_tail(mem, n))


def _free_text_cmd(
    mem: Any,
    text: str,
    chat_state: dict[str, Any],
) -> None:
    """Free-text input: enqueue at the head + run immediately on the current backend.

    Free-text typed at the prompt expresses intent ``run THIS now``, so we
    inject the new item with a priority that beats anything ``/add`` can
    produce, and then ask the supervisor to drain a single mission. This
    avoids the surprise of typing "hello" and watching an unrelated
    older backlog item run instead.

    Supports ``--once`` / ``--cycles=N`` / ``--budget=$X`` inline flags
    (same as ``/add``). When no flags are present, session-wide defaults
    from ``chat_state["config"]`` are used.

    When ``config["continuous"]`` is True, the supervisor runs in
    continuous improvement mode: the planner inspects the
    project after each completed task and generates new work until
    the project satisfies the objective.
    """
    cfg = chat_state.get("config", {})
    continuous = cfg.get("continuous", False)
    iterate, max_cycles, budget, body = _parse_add_flags(
        text,
        default_iterate=cfg.get("iterate", True),
        default_cycles=cfg.get("cycles", 6),
        default_budget=cfg.get("budget", 30.0),
    )
    body = body or text.strip()
    pending = mem.backlog.pending()
    head_priority = min((it.priority for it in pending), default=100)
    free_priority = min(head_priority - 1, -1)
    _add_only(
        mem,
        body,
        priority=free_priority,
        iterate=iterate,
        iteration_max_cycles=max_cycles,
        iteration_budget_usd=budget,
    )
    theme = chat_state.get("theme")
    if continuous:
        msg = (
            f"🔄 continuous mode on backend={chat_state['backend']} "
            f"(Ctrl-C to stop)..."
        )
        # Persist objective to disk so daemon can pick it up.
        chat_state["continuous_objective"] = body
        from ..daemon.life_worker import write_continuous_config
        write_continuous_config(
            mem.project.root,
            enabled=True,
            objective=body,
        )
    else:
        msg = f"running on backend={chat_state['backend']} (Ctrl-C to stop)..."
    print(theme.gray(msg) if theme else msg, flush=True)
    _invoke_and_track(
        mem=mem,
        chat_state=chat_state,
        once=not continuous,
        max_missions=999 if continuous else 1,
        per_mission_cap_usd=float(os.environ.get(
            "ARGUS_SKILL_PER_MISSION_CAP_USD",
            str(cfg.get("per_mission_cap", 30.0)),
        )),
        daily_cap_usd=float(os.environ.get(
            "ARGUS_SKILL_DAILY_CAP_USD",
            str(cfg.get("daily_cap", 180.0)),
        )),
        quiet=False,
        continuous=continuous,
        continuous_objective=body if continuous else "",
        open_ended=chat_state.get("open_ended", True),
        # Only single-shot operator free text is chat-eligible. In continuous
        # mode the typed text is a mission objective (and later missions are
        # planner work), so classification stays off.
        allow_chat_fast_path=not continuous,
    )


def _format_elapsed(seconds: float) -> str:
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins, secs = divmod(int(seconds), 60)
    if mins < 60:
        return f"{mins}m{secs:02d}s"
    hours, mins = divmod(mins, 60)
    return f"{hours}h{mins:02d}m{secs:02d}s"


def _invoke_and_track(
    *,
    mem: _SplitMemory,
    chat_state: dict[str, Any],
    once: bool,
    max_missions: int,
    per_mission_cap_usd: float,
    daily_cap_usd: float,
    quiet: bool,
    continuous: bool = False,
    continuous_objective: str = "",
    open_ended: bool = True,
    allow_chat_fast_path: bool = False,
) -> dict[str, Any]:
    """Run the supervisor and persist the resulting codex thread_id back
    into ``chat_state`` so the next mission resumes the same session.

    Also records wall-clock elapsed time and prints a one-line footer
    so the user sees how long each mission took.
    """
    seed = chat_state.get("last_thread_id")
    theme = chat_state.get("theme")
    if seed and not quiet:
        note = f"resuming codex session {seed[:12]}…"
        print(theme.gray(note) if theme else note)
    t0 = time.monotonic()
    summary, last_tid = _invoke_supervisor(
        mem=mem,
        backend=chat_state["backend"],
        once=once,
        max_missions=max_missions,
        per_mission_cap_usd=per_mission_cap_usd,
        daily_cap_usd=daily_cap_usd,
        quiet=quiet,
        seed_thread_id=seed,
        continuous=continuous,
        continuous_objective=continuous_objective,
        open_ended=open_ended,
        allow_chat_fast_path=allow_chat_fast_path,
    )
    elapsed = time.monotonic() - t0
    chat_state["last_thread_id"] = last_tid
    chat_state["last_elapsed_s"] = elapsed
    chat_state["total_elapsed_s"] = (
        chat_state.get("total_elapsed_s", 0.0) + elapsed
    )
    chat_state["mission_count"] = chat_state.get("mission_count", 0) + 1
    if not quiet:
        ran = int(summary.get("missions_run", 0)) if isinstance(summary, dict) else 0
        cost = float(summary.get("total_cost_usd", 0.0)) if isinstance(summary, dict) else 0.0
        footer = (
            f"⏱  elapsed {_format_elapsed(elapsed)}"
            + (f"  ·  missions={ran}" if ran else "")
            + (f"  ·  cost=${cost:.4f}" if cost else "")
        )
        print(theme.dim(footer) if theme else footer)

    # Surface auth failures prominently so the user knows to re-login
    # (the supervisor already set the stop event, but the REPL user
    # may not read stderr logs).
    if isinstance(summary, dict) and summary.get("stopped_by") == "auth_failure":
        warn = (
            "⚠  codex authentication failed — run `codex login` to "
            "refresh credentials, then restart the REPL or daemon."
        )
        print(theme.yellow(warn) if theme and hasattr(theme, "yellow") else warn)

    return summary


def _run_cmd(
    mem: _SplitMemory,
    opts: list[str],
    chat_state: dict[str, Any],
) -> None:
    output = render_run_command(mem, opts, chat_state)
    if not output:
        return
    print(output)


def _status_cmd(mem: _SplitMemory, chat_state: dict[str, Any] | None = None) -> None:
    """Lightweight status print (mirrors `argus-skill life status` output)."""
    from ..apps._inbox import count_pending_inbox_messages
    from ..daemon.life_worker import ContinuousConfigState, read_continuous_state

    identity = mem.identity.read().strip()
    if identity:
        first = identity.splitlines()[0][:80]
        print(f"identity: {first}{'…' if len(identity) > 80 else ''}")
    else:
        print("identity: (empty)")
    pending = mem.backlog.pending()
    print(f"backlog : {len(pending)} pending  "
          f"({len(mem.backlog.all())} total)")
    for it in pending[:5]:
        print(f"  - {it.id} (p={it.priority}): {it.title}")
    if len(pending) > 5:
        print(f"  … {len(pending) - 5} more")
    last = mem.journal.tail(3)
    if last:
        print("recent journal:")
        for e in last:
            ts_str = datetime.fromtimestamp(e.ts).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  [{ts_str}] {e.kind} — {e.title}")
    cont = None
    if chat_state is not None:
        cont = chat_state.get("continuous_state")
    if not isinstance(cont, ContinuousConfigState):
        cont = read_continuous_state(mem.project.root)
    print(f"continuous: {'on' if cont.enabled else 'off'}")
    print(f"inbox   : {count_pending_inbox_messages(mem.project.root)} pending")
    if cont.objective:
        print(f"  objective: {cont.objective}")
    if cont.done_reason:
        print(f"  done_reason: {cont.done_reason}")
    if cont.done_at:
        print(f"  done_at: {cont.done_at}")
    if chat_state is not None:
        started = chat_state.get("session_started_s")
        if started is not None:
            uptime = time.monotonic() - started
            count = int(chat_state.get("mission_count", 0))
            total = float(chat_state.get("total_elapsed_s", 0.0))
            last_e = chat_state.get("last_elapsed_s")
            line = f"timing : uptime {_format_elapsed(uptime)}"
            if count:
                line += (
                    f"  ·  {count} mission{'s' if count != 1 else ''}"
                    f" totaling {_format_elapsed(total)}"
                )
            if last_e is not None:
                line += f"  ·  last {_format_elapsed(last_e)}"
            print(line)
    # Background daemon status — surfaces the 7×24 worker so /status
    # answers "is anything running while I'm idle?".
    try:
        from ..apps.cli import _format_short_duration
        from ..daemon.life_worker import read_daemon_status
        ds = read_daemon_status(mem.project.root)
    except Exception:  # noqa: BLE001
        ds = None
    if ds is not None:
        if ds.alive and ds.pid is not None:
            up = _format_short_duration(ds.uptime_seconds or 0.0)
            print(f"daemon : alive (pid {ds.pid}, up {up}, "
                  f"backend {ds.backend or '?'})")
        else:
            print("daemon : not running   (start with `argus-skill --daemon`)")
            tid = chat_state.get("last_thread_id") if chat_state is not None else None
            if tid:
                print(f"codex  : resuming session {tid[:12]}…  (/reset to drop)")


# ---------------------------------------------------------------------------
# Help screen
# ---------------------------------------------------------------------------

def _render_help(theme) -> str:  # noqa: ANN001
    rows: list[tuple[str, str]] = [
        ("/help", "show this help"),
        ("/status", "summary of identity, backlog, recent journal"),
        ("/config [key=val ...]", "view/change session defaults "
                                  "(cycles, budget, continuous, daily_cap)"),
        ("/identity [edit|set …]", "view or update the identity card"),
        ("/project [set …]", "view or update the project card"),
        ("/start [objective]", "enable continuous mode "
                              "(alias of /continuous start)"),
        ("/continuous start|stop [objective]", "control continuous mode"),
        ("/backlog [all]", "list pending (or all) items"),
        ("/add <text> [--once] [--cycles=N] [--budget=$X]",
            "enqueue a mission (iterates by default until critic stops)"),
        ("/done|/skip|/rm <id>", "change item status"),
        ("/stop <id>", "disable iteration on an item (let it finish naturally)"),
        ("/journal [N]", "tail last N journal entries (default 10)"),
        ("/note <text>", "append a manual journal note"),
        ("/nudge <text>", "send live operator guidance — the next "
                          "engineer round will see it"),
        ("/run [opts]", "drain the backlog (foreground; Ctrl-C stops)"),
        ("/skills [ls|promote <name>]",
            "list global skills or promote a project skill to global"),
        ("/reset", "drop codex session — next mission starts fresh"),
        ("/backend", "show or change the backend (codex / memory)"),
        ("/exit  /quit  :q", "leave the REPL (Ctrl-D also works)"),
    ]
    width = max(len(k) for k, _ in rows)
    out: list[str] = []
    out.append(theme.bold("argus-skill")
               + theme.gray("  — unified lifetime-agent REPL"))
    out.append("")
    out.append(theme.gray("Slash commands:"))
    for key, desc in rows:
        out.append(f"  {theme.cyan(key.ljust(width))}  {theme.gray(desc)}")
    out.append("")
    out.append(theme.gray(
        "Free text (no leading '/') is appended to the backlog AND runs immediately."
    ))
    out.append(theme.gray(
        "Supports --once / --cycles=N / --budget=$X inline flags."
    ))
    out.append(theme.gray(
        "Use /config continuous=true to enable 24/7 continuous improvement mode."
    ))
    out.append(theme.gray(
        "In continuous mode the planner inspects the project after each "
        "task and generates new work until the objective is fully satisfied."
    ))
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Slash-command helpers + public REPL entry point — invoked by apps/cli.main
# ---------------------------------------------------------------------------

def _skills_cmd(mem: _CommonMemory, tokens: list[str]) -> None:
    """``/skills [ls|promote <name>]`` — inspect or promote a skill
    from the current project layer to the global layer."""
    print(render_skills_cmd(Path.cwd(), tokens))


def _seed_chat_state(
    args: argparse.Namespace,
    mem: LifeMemory | MemoryBundle,
    *,
    theme: Any,
) -> tuple[dict[str, Any], str | None]:
    from ..daemon.life_worker import ContinuousConfigState, read_continuous_state

    project_root = getattr(mem, "project_root", None)
    if project_root is None:
        project = getattr(mem, "project", None)
        project_root = getattr(project, "root", None)
    if project_root is None:
        project_root = getattr(mem, "root")

    backend_default = getattr(args, "backend", None) or os.environ.get(
        "ARGUS_SKILL_LIFE_BACKEND",
        "codex",
    )
    disk_state = read_continuous_state(Path(project_root))
    cli_continuous = bool(getattr(args, "continuous", False))
    cli_objective = str(getattr(args, "objective", "") or "").strip()
    disk_objective = disk_state.objective.strip()
    if cli_objective and not cli_continuous:
        error = _continuous_session_error(backend_default, False, cli_objective)
        if error:
            return {}, error

    if cli_continuous:
        continuous = True
        objective = cli_objective
        error = _continuous_session_error(backend_default, continuous, objective)
        if error:
            return {}, error
    else:
        objective = disk_objective if disk_state.enabled else ""
        continuous = disk_state.enabled
        if continuous and _continuous_session_error(backend_default, True, objective):
            continuous = False

    chat_state: dict[str, Any] = {
        "backend": backend_default,
        "theme": theme,
        # Codex CLI session id of the most recent mission. Reused as
        # ``resume_thread_id`` on the next mission so the codex CLI does
        # NOT spin up a fresh session for every prompt. Cleared by /reset.
        "last_thread_id": None,
        # Wall-clock timing — populated as missions run so /status and the
        # post-mission footer can report uptime / per-mission elapsed.
        "session_started_s": time.monotonic(),
        "mission_count": 0,
        "total_elapsed_s": 0.0,
        "last_elapsed_s": None,
        # Session-wide iteration/budget defaults. Changed via /config.
        # REPL-local only — does not affect the background daemon.
        "config": dict(_CONFIG_DEFAULTS),
        "continuous_objective": objective or disk_objective,
        # Lifetime semantics: keep generating work after project_done unless
        # the operator launched with --bounded.
        "open_ended": not bool(getattr(args, "bounded", False)),
    }
    chat_state["config"]["continuous"] = continuous
    chat_state["continuous_state"] = ContinuousConfigState(
        enabled=continuous,
        objective=objective or disk_objective,
        done_reason="" if continuous else disk_state.done_reason,
        done_at="" if continuous else disk_state.done_at,
    )
    return chat_state, None


def run_manager_repl(args: argparse.Namespace) -> int:
    """Drive the unified ``argus-skill`` REPL (the Manager conversation entry).

    Slash commands dispatch in-process — no daemon, no jsonl bus.
    Free text becomes a backlog item AND runs immediately on the
    current default backend.
    """
    try:
        global_root = _resolve_global_root(args)
        mem: MemoryBundle = MemoryBundle.for_cwd(Path.cwd(), global_root=global_root)
    except core_paths.PathResolutionError as exc:
        sys.stderr.write(f"argus-skill: {exc}\n")
        return 2
    state = mem.init()
    created: list[str] = []
    for scope, rows in state.items():
        for name, was_created in rows.items():
            if was_created:
                created.append(f"{scope}.{name}")
    theme = None  # populated in the locked body

    # Fail fast before we take the singleton lock if the current run
    # explicitly requests continuous mode that the backend cannot satisfy.
    chat_state, error = _seed_chat_state(args, mem, theme=theme)
    if error:
        sys.stderr.write(error + "\n")
        return 2

    # Singleton guard: two argus-skill REPLs running against the same
    # life-dir would race on backlog.jsonl rewrites and corrupt journal
    # appends mid-flight. Acquire an OS-level advisory lock per life-dir
    # so a second invocation gets a clear error instead of silent
    # corruption. The lock auto-releases when the process exits; we also
    # release explicitly via try/finally below.
    from ..core.daemon_lock import (
        DaemonAlreadyRunning,
        acquire_global_daemon_lock,
    )
    lock_path = mem.project.root / "repl.pid"
    try:
        repl_lock = acquire_global_daemon_lock(pid_path=lock_path)
    except DaemonAlreadyRunning as exc:
        sys.stderr.write(
            f"argus-skill: another REPL is already running here "
            f"(pid={exc.pid}, lock={exc.lock_path}).\n"
            f"  ↳ if that process is dead, remove {exc.lock_path} and retry.\n"
        )
        return 2

    try:
        return _run_manager_repl_locked(args, mem, created, chat_state=chat_state)
    finally:
        try:
            repl_lock.release()
        except Exception:  # noqa: BLE001
            log.exception("life REPL: failed to release singleton lock")


def _run_manager_repl_locked(
    args: argparse.Namespace,
    mem: _SplitMemory,
    created: list[str],
    *,
    chat_state: dict[str, Any],
) -> int:
    """The interactive REPL body. Split out so the singleton lock in
    :func:`run_manager_repl` cleanly wraps the entire loop with
    a try/finally release."""
    import readline  # noqa: F401 — enables line-editing for input()

    from ..apps._input_helpers import enable_bracketed_paste, read_pasted_message
    enable_bracketed_paste()
    from .. import __version__ as _argus_version
    from ..cli.branding import TAGLINE, render_logo
    from ..cli.theme import Theme

    theme = Theme.auto(force=getattr(args, "color", None))

    # Always-verbose: the lifetime-agent product positioning means the
    # operator wants to see every internal event (round.start, match.info,
    # skill.writeback, …). The earlier ``verbose``/``quiet`` toggles have
    # been removed; ``--verbose`` and ``--quiet`` flags are accepted but
    # ignored (kept for backward compat in scripts).

    backend_default = chat_state["backend"]
    chat_state["theme"] = theme

    # ── Auto-spawn 7×24 daemon ────────────────────────────────────
    # Lifetime-agent positioning means the daemon is the default. We
    # silently spawn one in the background unless the user opted out
    # with --no-daemon or one is already alive (idempotent: spawn is
    # a no-op when the singleton lock is held).
    auto_spawn_msg: str | None = None
    legacy_zombie_msg: str | None = None
    # Detect a pre-pivot ``python -m argus_skill daemon`` zombie still
    # writing to the legacy ``state/`` dir. Two independent daemons will
    # double-claim work and corrupt accounting, so we surface this loudly.
    legacy_status = mem.global_mem.root / "state" / "status.json"
    if legacy_status.exists():
        try:
            data = json.loads(legacy_status.read_text(encoding="utf-8"))
            zpid = int(data.get("daemon_pid") or 0)
            if zpid > 0:
                try:
                    os.kill(zpid, 0)
                    legacy_zombie_msg = (
                        f"legacy daemon detected (pid {zpid}, pre-pivot). "
                        f"Run: kill {zpid} && rm -rf {legacy_status.parent}"
                    )
                except OSError:
                    legacy_zombie_msg = None
        except Exception:  # noqa: BLE001
            pass
    if not getattr(args, "no_daemon", False):
        try:
            from ..apps.cli import _build_worker_config
            from ..daemon.life_worker import (
                read_daemon_status,
                spawn_detached_daemon,
                wait_for_daemon_status,
            )
            status = read_daemon_status(mem.project.root)
            if not status.alive:
                cfg = _build_worker_config(args)
                spawn_rc = spawn_detached_daemon(cfg)
                if spawn_rc == 0:
                    started = wait_for_daemon_status(mem.project.root)
                    if started is not None and started.pid is not None:
                        auto_spawn_msg = f"daemon auto-spawned (pid {started.pid})"
                    else:
                        auto_spawn_msg = "daemon auto-spawned"
        except Exception as exc:  # noqa: BLE001
            auto_spawn_msg = f"daemon auto-spawn skipped: {exc!s}"

    # ── Banner ─────────────────────────────────────────────────────
    print()
    print(render_logo(theme=theme))
    print()
    print("  " + theme.italic(theme.gray(TAGLINE))
          + "  " + theme.dim(f"v{_argus_version}"))
    print()
    rule = theme.dim("─" * min(theme.width - 2, 60))
    print("  " + rule)

    arrow = theme.dim("→")
    label = lambda s: theme.gray(f"{s:<10}")  # noqa: E731
    cfg = chat_state["config"]
    iter_status = theme.bold_green("on") if cfg["iterate"] else theme.bold("off")
    iter_detail = (
        f"default {cfg['cycles']} cycles · ${cfg['budget']:.0f} budget"
        f" · /add --once to opt out"
    )
    rows = [
        ("mode",    f"{theme.bold('life')}    " + theme.dim("in-process · no daemon")),
        ("backend", f"{theme.bold(backend_default)}   " + theme.dim("(memory or codex)")),
        ("backlog", f"{theme.bold(str(len(mem.backlog.pending())))} "
                    + theme.gray("pending")),
        ("iterate", iter_status + "    "
                    + theme.dim(iter_detail)),
        ("verbose", theme.bold_green("always") + " "
                    + theme.dim("(filter removed — every event is shown)")),
        ("state",   theme.cyan(str(mem.project.root))),
    ]
    # Replace the static "in-process · no daemon" hint with live daemon
    # status so the user sees whether the 7×24 worker is draining the
    # backlog in the background. The lifetime-agent positioning is only
    # honest if this is observable at a glance.
    rows[0] = ("mode", _format_daemon_mode_cell(theme, mem))
    for k, v in rows:
        print(f"  {label(k)} {arrow} {v}")
    if created:
        print(f"  {label('init')} {arrow} " + theme.dim("created ")
              + theme.cyan(", ".join(created)))
    if auto_spawn_msg:
        print(f"  {label('daemon')} {arrow} " + theme.dim(auto_spawn_msg))
    if legacy_zombie_msg:
        print(f"  {label('warn')} {arrow} " + theme.yellow(legacy_zombie_msg))
    # Preflight: surface codex-backend problems at launch, not mid-mission.
    if backend_default == "codex":
        warning = _codex_preflight_warning()
        if warning:
            print(f"  {label('warn')} {arrow} " + theme.yellow(warning))
    print("  " + rule)
    print()
    print("  " + theme.gray("free text runs immediately on the backend  ·  ")
          + theme.cyan("/help") + theme.gray(" for commands  ·  ")
          + theme.cyan("/exit") + theme.gray(" or Ctrl-D to leave"))
    print()

    base_prompt = theme.bold(theme.cyan("argus"))
    sep = theme.dim(" › ")
    resume_marker = theme.dim(" ↻")  # subtle indicator when codex session is being reused

    while True:
        prompt = (
            base_prompt + (resume_marker if chat_state.get("last_thread_id") else "") + sep
        )
        try:
            raw = read_pasted_message(prompt)
        except KeyboardInterrupt:
            print()
            continue
        if raw is None:
            print()
            print(theme.gray("bye."))
            return 0
        line = raw.strip()
        if not line:
            continue

        if line in ("/quit", "/exit", ":q", ":quit"):
            print(theme.gray("bye."))
            return 0

        if not line.startswith("/"):
            _free_text_cmd(mem, raw, chat_state)
            continue

        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            print(theme.red(f"parse error: {exc}"))
            continue
        cmd = tokens[0].lower()
        rest = tokens[1:]
        rest_text = line[len(tokens[0]):].lstrip()

        if cmd in ("/help", "/commands"):
            sys.stdout.write(_render_help(theme))
            sys.stdout.flush()
            continue
        if cmd == "/status":
            _status_cmd(mem, chat_state)
            continue
        if cmd == "/start":
            _continuous_cmd(mem, f"start {rest_text}".strip(), chat_state)
            continue
        if cmd == "/continuous":
            _continuous_cmd(mem, rest_text, chat_state)
            continue
        if cmd == "/identity":
            _identity_cmd(mem, rest, rest_text)
            continue
        if cmd == "/project":
            _project_cmd(mem, rest, rest_text)
            continue
        if cmd == "/backlog":
            include_all = bool(rest) and rest[0].lower() == "all"
            _backlog_list_cmd(mem, include_all=include_all)
            continue
        if cmd == "/add":
            if not rest_text:
                print(theme.gray(
                    "usage: /add <objective>  "
                    "[--once] [--cycles=N] [--budget=$X]"
                ))
                continue
            cfg = chat_state.get("config", {})
            iterate, max_cycles, budget, body = _parse_add_flags(
                rest_text,
                default_iterate=cfg.get("iterate", True),
                default_cycles=cfg.get("cycles", 6),
                default_budget=cfg.get("budget", 30.0),
            )
            if not body:
                print(theme.gray("/add: empty objective after flags"))
                continue
            _add_only(
                mem,
                body,
                iterate=iterate,
                iteration_max_cycles=max_cycles,
                iteration_budget_usd=budget,
            )
            continue
        if cmd == "/stop":
            if not rest:
                print(theme.gray("usage: /stop <item_id>"))
                continue
            print(stop_iteration(mem, rest[0]))
            continue
        if cmd in ("/done", "/skip", "/rm"):
            if not rest:
                print(theme.gray(f"usage: {cmd} <item_id>"))
                continue
            _status_change_cmd(mem, cmd, rest[0])
            continue
        if cmd == "/journal":
            n = 10
            if rest:
                try:
                    n = int(rest[0])
                except ValueError:
                    print(theme.gray(f"usage: /journal [N]  (got: {rest[0]!r})"))
                    continue
            _journal_tail_cmd(mem, n)
            continue
        if cmd == "/note":
            if not rest_text:
                print(theme.gray("usage: /note <text>"))
                continue
            print(theme.gray(append_note(mem, rest_text)))
            continue
        if cmd in ("/nudge", "/inject", "/notify"):
            if not rest_text:
                print(theme.gray("usage: /nudge <message>  (one line, "
                                 "spliced into the next engineer round)"))
                continue
            from ..apps._inbox import queue_inbox_message
            queue_inbox_message(mem.project.root, rest_text, source="repl.nudge")
            print(theme.gray(
                f"nudge queued ({len(rest_text)} chars) → next mission round "
                f"will see it as operator guidance"
            ))
            continue
        if cmd == "/backend":
            _backend_cmd(rest, chat_state)
            continue
        if cmd == "/config":
            _config_cmd(rest, chat_state, life_dir=mem.project.root)
            continue
        if cmd in ("/verbose", "/quiet"):
            print(theme.gray(
                "verbose is always on now (the toggle was removed). "
                "every event is rendered."
            ))
            continue
        if cmd == "/reset":
            print(theme.gray(render_reset_cmd(chat_state)))
            continue
        if cmd == "/run":
            _run_cmd(mem, rest, chat_state)
            continue
        if cmd == "/skills":
            _skills_cmd(mem, rest)
            continue
        print(theme.gray(f"unknown command: {cmd}  (try /help)"))


__all__ = [
    "run_manager_repl",
    "_run_manager_repl_locked",
    "_parse_add_flags",
    "_add_only",
    "_backend_cmd",
    "_continuous_session_error",
    "_CONFIG_DEFAULTS",
    "_config_cmd",
    "_identity_cmd",
    "_project_cmd",
    "_continuous_cmd",
    "_backlog_list_cmd",
    "_status_change_cmd",
    "_journal_tail_cmd",
    "_free_text_cmd",
    "_format_elapsed",
    "_invoke_and_track",
    "_run_cmd",
    "_status_cmd",
    "_render_help",
    "_skills_cmd",
    "_seed_chat_state",
]
