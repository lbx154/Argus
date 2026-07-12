"""Daemon, configuration, and local command helpers for the Manager REPL."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import sys
from pathlib import Path
from typing import Any

from ..apps._life_actions import (
    _continuous_session_error as _shared_continuous_session_error,
)
from ..apps._life_actions import (
    add_backlog_item,
    format_added_item,
    format_backlog_list,
    format_journal_tail,
    format_status_change,
    render_backend_cmd,
    render_config_cmd,
    render_identity_cmd,
)
from ..apps._runtime import _CommonMemory, _env_flag, _SplitMemory
from ..life import BacklogItem
from .front_door import _life_dir_for

log = logging.getLogger(__name__)

def _add_only(
    mem: _CommonMemory,
    text: str,
    *,
    priority: int = 100,
    iterate: bool = True,
    iteration_max_cycles: int = 6,
    iteration_budget_usd: float = 30.0,
    item_id: str | None = None,
) -> BacklogItem:
    item = add_backlog_item(
        mem,
        text,
        item_id=item_id,
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
    "manager_effort": "xhigh",
    "planner_effort": "xhigh",
    "engineer_effort": "xhigh",
    "reviewer_effort": "xhigh",
}

_ROLE_EFFORT_ENVS: dict[str, str] = {
    "manager": "ARGUS_SKILL_MANAGER_REASONING_EFFORT",
    "planner": "ARGUS_SKILL_PLANNER_REASONING_EFFORT",
    "engineer": "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
    "reviewer": "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
}
def _live_cockpit_enabled() -> bool:
    """Default ON: the four-role idle-prompt panel shows automatically —
    the operator's explicit, standing requirement is that multi-role
    progress be visible without any manual step (no ``/roles``, no env var).
    Set ``ARGUS_SKILL_COCKPIT_LIVE=0`` to opt back OUT to the plain prompt
    (e.g. for scripting/logging where a redrawing panel is unwanted)."""
    return _env_flag("ARGUS_SKILL_COCKPIT_LIVE", True)


def _live_follow_enabled() -> bool:
    """Default ON: watching a mission run shows the live four-role panel —
    same reasoning as ``_live_cockpit_enabled``. Set
    ``ARGUS_SKILL_FOLLOW_LIVE=0`` to opt back OUT to the plain scrolling
    event tail."""
    return _env_flag("ARGUS_SKILL_FOLLOW_LIVE", True)


_ROLE_BACKEND_ENVS: dict[str, str] = {
    "manager": "ARGUS_SKILL_MANAGER_BACKEND",
    "planner": "ARGUS_SKILL_PLANNER_BACKEND",
    "engineer": "ARGUS_SKILL_ENGINEER_BACKEND",
    "reviewer": "ARGUS_SKILL_REVIEWER_BACKEND",
}
_ROLE_MODEL_ENVS: dict[str, str] = {
    # Manager REPL triage reuses the engineer route/model (see
    # ``_ensure_manager_runner`` and ``cli.roles_status._ROLE_MODEL_ENV``).
    "manager": "ARGUS_SKILL_ENGINEER_MODEL",
    "planner": "ARGUS_SKILL_PLAN_MODEL",
    "engineer": "ARGUS_SKILL_ENGINEER_MODEL",
    "reviewer": "ARGUS_SKILL_REVIEWER_MODEL",
}
def _settings_cmd(chat_state: dict[str, Any]) -> None:
    """The full runtime-settings view rendered by ``/config`` (no args): every
    role's backend/model/effort plus every ``ARGUS_*`` knob, grouped, marking
    which are editable by natural language (leading ``NL``) vs which need an env
    var. The one in-REPL view covering EVERYTHING NL can change (incl. the
    safe_mode / show_reasoning / telegram toggles ``/roles`` doesn't show)."""
    from functools import partial

    from ..cli.roles_status import _paint
    from ..core.config_snapshot import build_config_snapshot
    from ..core.knobs import cockpit_editable_names

    theme = chat_state.get("theme")
    _p = partial(_paint, theme)  # reuse roles_status' theme-duck-typing helper

    try:
        snap = build_config_snapshot()
    except Exception:  # noqa: BLE001 — a display command, never fatal
        log.exception("repl: /config settings view failed to build config snapshot")
        print(_p("gray", "  (failed to render settings)"), flush=True)
        return

    # NL-changeable env names = the cockpit-editable surface (single source of
    # truth: the cockpit=True flag in knobs.py). Deriving it here keeps this marker
    # set from drifting against the switch handlers — it correctly excludes
    # curator/matcher/LIFE_BACKEND, which no recognizer sets.
    nl = set(cockpit_editable_names())

    out: list[str] = [""]
    out.append("  " + _p("bold", "Argus runtime settings"))
    out.append("  " + _p("dim",
        "Editable by natural language: model, effort, backend, per-mission cap, "
        "daily cap, daemon limit, provider request caps, safe_mode, "
        "show_reasoning, telegram"))
    out.append("  " + _p("dim",
        "Others: export ARGUS_SKILL_*  (full list: argus-skill --config-help)"))
    out.append("")

    # Session-only defaults (REPL-local; the *_cap here are the per-cockpit
    # dispatch defaults, separate from the ARGUS_SKILL_*_CAP_USD knobs below).
    cfg = chat_state.get("config") or {}
    sess = []
    for key in ("cycles", "iterate", "continuous", "budget",
                "per_mission_cap", "daily_cap"):
        if key in cfg:
            v = cfg[key]
            v = ("on" if v else "off") if isinstance(v, bool) else v
            sess.append(f"{key}={v}")
    if sess:
        out.append("  " + _p("gray", "session (this cockpit):  ")
                   + _p("dim", "   ".join(sess)))
        out.append("")

    # Roles table (backend / model / effort are all NL-changeable).
    roles = snap.get("roles", [])
    rw = max((len(str(r.get("role", ""))) for r in roles), default=8) + 2
    bw = max((len(str(r.get("backend_label", r.get("backend", "")))) for r in roles),
             default=7) + 2
    mw = max((len(str(r.get("model", ""))) for r in roles), default=14) + 2
    out.append("  " + _p("gray",
        "role".ljust(rw) + "backend".ljust(bw) + "model".ljust(mw) + "effort"))
    for r in roles:
        eff = r.get("reasoning_effort")
        eff = str(eff) if eff not in (None, "") else "—"
        out.append("  " + str(r.get("role", "")).ljust(rw)
                   + str(r.get("backend_label", r.get("backend", ""))).ljust(bw)
                   + str(r.get("model", "")).ljust(mw) + eff)
    out.append("")

    # Knobs — grouped; a leading "NL" marks the NL-changeable ones.
    out.append("  " + _p("gray", "Knobs  (leading NL = editable by natural language)"))
    knobs = snap.get("operator_knobs", [])
    width = max((len(k.get("name", "")) for k in knobs), default=30)
    groups: dict[str, list] = {}
    for k in knobs:  # collect by group so each [header] prints exactly once
        groups.setdefault(str(k.get("group", "")), []).append(k)
    for g, items in groups.items():
        out.append("   " + _p("dim", f"[{g}]"))
        for k in items:
            is_nl = k.get("name") in nl
            mark = _p("green", "NL") if is_nl else "  "
            nm = str(k.get("name", "")).ljust(width)
            nm = nm if is_nl else _p("dim", nm)
            out.append(f"     {mark}  {nm}  {str(k.get('value', ''))}")

    out.append("")
    out.append("  " + _p("dim", "To change:  NL rows — say it in plain language "
                                "(e.g. \"把单任务预算改成 50\", \"enable safe mode\");"))
    out.append("  " + _p("dim", "            others — export ARGUS_SKILL_…, "
                                "or /config <key>=<value>."))
    out.append("")
    print("\n".join(out), flush=True)


def _config_cmd(tokens: list[str], chat_state: dict[str, Any],
                life_dir: Path | None = None) -> None:
    """``/config [key=value ...]`` — view or change REPL-session defaults.

    With NO args, renders the full runtime-settings view (:func:`_settings_cmd`):
    every role's backend/model/effort + every ARGUS_* knob, marking which are
    natural-language-editable. With ``key=value`` args, sets the REPL-session
    default. The ``continuous`` key is also persisted to disk so the background
    daemon picks it up.
    """
    if not tokens:
        _settings_cmd(chat_state)
        return
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


def _should_autospawn_on_boot(args: argparse.Namespace, mem: Any = None) -> bool:
    """Whether opening the cockpit should immediately start a daemon.

    ONLY when there is actually work to run: a continuous 7×24 campaign, an
    explicit ``--objective``, or a resumed session that already has a pending
    backlog. A plain fresh chat session does NOT spawn a daemon on boot — it
    spawns one LAZILY on the first real task (see ``_autospawn_daemon_for_task``
    / ``auto_start_daemon_on_task``), so browsing / chatting / just looking
    around never leaves an idle daemon behind (one per empty session was the
    daemon-proliferation the operator hit).
    """
    if bool(getattr(args, "no_daemon", False)):
        return False
    if (bool(getattr(args, "continuous", False))
            or bool(getattr(args, "resume_continuous", False))
            or str(getattr(args, "objective", "") or "").strip()):
        return True
    try:
        if mem is not None and mem.backlog.pending():
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _autospawn_daemon_for_task(
    mem: Any,
    chat_state: dict[str, Any],
    *,
    spawn_daemon: Any = None,
) -> tuple[bool, int | None]:
    """Start THIS session's daemon after the first real task is queued.

    The REPL no longer auto-spawns for an empty fresh session, but the daemon is
    still the sole executor. Once a task exists, start the daemon against the
    same ``mem`` bundle so it drains the new session, not the legacy cwd project.
    Fail-soft and report the error through ``chat_state`` for the caller to show.
    """
    life_dir = _life_dir_for(mem)
    alive, pid = _daemon_alive_for(life_dir)
    if alive:
        return alive, pid
    backend = str(
        chat_state.get("backend")
        or os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex")
    )
    continuous = bool(chat_state.get("config", {}).get("continuous", False))
    objective = str(chat_state.get("continuous_objective") or "").strip()
    try:
        from ..daemon.life_worker import continuous_mode_error
        error = continuous_mode_error(backend, continuous, objective)
        if error:
            chat_state["daemon_autostart_error"] = error
            return False, None
        from ..apps.cli import _build_worker_config
        from ..daemon.life_worker import wait_for_daemon_status

        cfg_args = argparse.Namespace(
            backend=backend,
            continuous=continuous,
            objective=objective,
            resume_continuous=continuous,
            bounded=not bool(chat_state.get("open_ended", True)),
        )
        spawn = spawn_daemon or _spawn_daemon_from_cockpit
        rc = spawn(_build_worker_config(cfg_args, bundle=mem))
        if rc != 0:
            chat_state["daemon_autostart_error"] = (
                f"daemon auto-start failed (rc={rc}); run /doctor"
            )
            return False, None
        st = wait_for_daemon_status(life_dir)
        if st is not None and getattr(st, "alive", False) and getattr(st, "pid", None):
            return True, getattr(st, "pid", None)
        chat_state["daemon_autostart_error"] = (
            "daemon auto-start returned success but no live pid was confirmed"
        )
    except Exception as exc:  # noqa: BLE001
        chat_state["daemon_autostart_error"] = (
            f"daemon auto-start skipped: {type(exc).__name__}: {exc}"
        )
    return False, None


def _continuous_cmd(
    mem: _SplitMemory,
    arg_text: str,
    chat_state: dict[str, Any],
) -> None:
    from ..daemon.life_worker import (
        ContinuousConfigState,
        continuous_mode_error,
        disable_continuous_config,
        read_continuous_config,
        read_continuous_state,
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
        requested_objective = " ".join(tokens[1:]).strip()
        objective = requested_objective or current_objective
        error = continuous_mode_error(backend, True, objective)
        if error:
            print(error)
            return
        from .front_door import ManagerHandoffError, manager_continuous_handoff

        try:
            objective = manager_continuous_handoff(
                mem,
                requested_objective,
                chat_state,
            )
        except ManagerHandoffError as exc:
            print(str(exc))
            return
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
        disable_continuous_config(mem.project.root)
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


def _is_argus_cli_invocation(text: str) -> bool:
    alias, note = _cockpit_cli_alias(text)
    return alias is not None or note is not None


def _cockpit_cli_alias(text: str) -> tuple[str | None, str | None]:
    """Map pasted shell invocations to cockpit commands.

    Operators often paste the exact fix shown by a CLI hint (for example
    ``argus-skill --daemon``) into the already-open cockpit. Treating that as a
    research task is never useful, so command-shaped input is intercepted before
    manager triage.
    """
    stripped = text.strip()
    if not stripped:
        return None, None
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return None, None
    if not tokens:
        return None, None

    args: list[str]
    exe = Path(tokens[0]).name
    if exe in {"argus-skill", "argus"}:
        args = tokens[1:]
    elif (
        len(tokens) >= 3
        and Path(tokens[0]).name.startswith("python")
        and tokens[1] == "-m"
        and tokens[2].replace("-", "_") == "argus_skill"
    ):
        args = tokens[3:]
    else:
        return None, None

    if not args or any(a in {"-h", "--help"} for a in args):
        return (
            "/help",
            "inside cockpit: using /help instead of queuing a shell command.",
        )
    if "--status" in args:
        return (
            "/status",
            "inside cockpit: using /status instead of queuing a shell command.",
        )
    if "--daemon-stop" in args:
        stop_args = []
        if "--drain" in args:
            stop_args.append("--drain")
        if "--force" in args:
            stop_args.append("--force")
        suffix = (" " + " ".join(stop_args)) if stop_args else ""
        return (
            f"/daemon stop{suffix}",
            "inside cockpit: using /daemon stop instead of queuing a shell command.",
        )
    if "--daemon" in args:
        return (
            "/daemon start",
            "inside cockpit: using /daemon start instead of queuing a shell command.",
        )
    if "--follow" in args:
        return "/run", "inside cockpit: using /run instead of queuing a shell command."
    if "--watch" in args:
        return (
            None,
            "already in the cockpit. Use /status for state, /run to follow, "
            "or /help for commands.",
        )
    return (
        None,
        "this is the cockpit, not a shell. Shell-shaped argus-skill input was "
        "not queued; use /help.",
    )


def _daemon_cmd(
    mem: _SplitMemory,
    arg_text: str,
    chat_state: dict[str, Any],
    *,
    spawn_daemon: Any = None,
) -> None:
    """``/daemon`` — control the executor bound to this cockpit/session."""
    try:
        tokens = shlex.split(arg_text) if arg_text.strip() else []
    except ValueError as exc:
        print(f"parse error: {exc}")
        return
    sub = tokens[0].lower() if tokens else "status"
    opts = tokens[1:] if tokens else []
    life_dir = _life_dir_for(mem)

    from ..apps.cli import _format_short_duration
    from ..daemon.life_worker import (
        continuous_mode_error,
        read_daemon_status,
        stop_daemon,
        wait_for_daemon_status,
    )

    def _print_status() -> None:
        st = read_daemon_status(life_dir)
        if st.alive and st.pid is not None:
            up = _format_short_duration(st.uptime_seconds or 0.0)
            # Same relabeling as _status_cmd / --status: st.backend is the
            # real-vs-memory-test toggle, not the per-role CLI (see /roles).
            backend_label = "memory (test)" if st.backend == "memory" else "live — see /roles"
            print(
                f"daemon: alive (pid {st.pid}, up {up}, "
                f"backend {backend_label})"
            )
        else:
            print("daemon: not running. Start it with /daemon start")

    if sub in {"status", "show", "ls"}:
        _print_status()
        return

    if sub in {"stop", "off", "down"}:
        rc = stop_daemon(life_dir, drain="--drain" in opts, force="--force" in opts)
        if rc == 0:
            print("daemon: stopped")
        else:
            print(f"daemon: stop did not complete (rc={rc}); see the message above.")
        return

    if sub in {"restart", "reload"}:
        stop_daemon(life_dir, drain="--drain" in opts, force="--force" in opts)
        sub = "start"

    if sub not in {"start", "on", "up"}:
        print("usage: /daemon [status|start|stop|restart] [--drain] [--force]")
        return

    existing = read_daemon_status(life_dir)
    if existing.alive and existing.pid is not None:
        up = _format_short_duration(existing.uptime_seconds or 0.0)
        print(f"daemon: already running (pid {existing.pid}, up {up})")
        return

    backend = str(
        chat_state.get("backend")
        or os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex")
    )
    continuous = bool(chat_state.get("config", {}).get("continuous", False))
    objective = str(chat_state.get("continuous_objective") or "").strip()
    if continuous and not objective:
        # BUG FIX: chat_state["config"]["continuous"] defaults to True for any
        # ordinary bare launch (see _seed_chat_state's default_continuous —
        # backend != memory and not --bounded), even when the operator never
        # typed /continuous start <objective> or --objective. The original
        # boot-time autospawn (_build_worker_config from CLI args) tolerates
        # this fine because it reads args.continuous, which is False unless
        # --continuous was passed. /daemon start|restart used chat_state's
        # value instead and hard-failed with "--continuous requires a
        # non-empty --objective" — regressing an already-working daemon to
        # NO daemon at all on a plain `/daemon restart --drain`. Since there
        # is no real objective to plan toward yet, don't ask for continuous
        # planning here; the operator can still turn it on later with
        # `/continuous start <objective>`.
        continuous = False
    error = continuous_mode_error(backend, continuous, objective)
    if error:
        print(error)
        return

    cfg_args = argparse.Namespace(
        backend=backend,
        continuous=continuous,
        objective=objective,
        resume_continuous=continuous,
        bounded=not bool(chat_state.get("open_ended", True)),
    )
    try:
        from ..apps.cli import _build_worker_config
        cfg = _build_worker_config(cfg_args, bundle=mem)
    except AttributeError:
        from ..daemon.life_worker import LifeWorkerConfig
        cfg = LifeWorkerConfig(
            life_dir=life_dir,
            global_root=(
                Path(chat_state["global_root"]) if chat_state.get("global_root") else None
            ),
            project_workdir=Path.cwd(),
            backend=backend,
            continuous=continuous,
            continuous_objective=objective,
            resume_continuous=continuous,
            continuous_open_ended=bool(chat_state.get("open_ended", True)),
        )

    spawn = spawn_daemon or _spawn_daemon_from_cockpit
    rc = spawn(cfg)
    if rc != 0:
        print(f"daemon: start failed (rc={rc}). Run /doctor for why + the fix.")
        tail = _daemon_log_tail(life_dir)
        if tail:
            print(tail)
        return
    from ..cli.live_status import LiveStatus
    with LiveStatus(
        "Starting daemon…",
        theme=chat_state.get("theme"),
        phrases=["Starting daemon…", "Waiting for the executor to come online…"],
        hint="",
    ):
        started = wait_for_daemon_status(life_dir)
    if started is not None and started.alive and started.pid is not None:
        print(f"daemon: started (pid {started.pid})")
    else:
        print(
            "daemon: spawn returned success but no live pid was confirmed. "
            "Run /doctor."
        )
        tail = _daemon_log_tail(life_dir)
        if tail:
            print(tail)


def _spawn_daemon_from_cockpit(cfg: Any, *, quiet: bool = True) -> int:
    """Start a daemon from the cockpit without forking from TUI worker threads."""
    import threading

    if threading.current_thread() is threading.main_thread():
        from ..daemon.life_worker import spawn_detached_daemon

        return spawn_detached_daemon(cfg, quiet=quiet)

    import subprocess

    from ..daemon.life_worker import _config_payload

    payload = json.dumps(_config_payload(cfg), ensure_ascii=False)
    code = (
        "import json, sys; "
        "from argus_skill.daemon.life_worker import _config_from_payload, "
        "spawn_detached_daemon; "
        "cfg = _config_from_payload(json.loads(sys.stdin.read())); "
        f"raise SystemExit(spawn_detached_daemon(cfg, quiet={quiet!r}))"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            input=payload,
            text=True,
            capture_output=True,
            timeout=12,
            cwd=str(Path.cwd()),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"daemon: helper spawn failed: {type(exc).__name__}: {exc}")
        return 2
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.stderr.strip():
        print(proc.stderr.strip())
    return int(proc.returncode)


def _daemon_log_tail(
    life_dir: Path | str,
    *,
    max_lines: int = 14,
    max_chars: int = 3000,
) -> str:
    """Small daemon.log tail for failed cockpit starts."""
    path = Path(life_dir) / "daemon.log"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"daemon log: {path} (not readable yet)"
    text = text.strip()
    if not text:
        return f"daemon log: {path} (empty)"
    lines = text.splitlines()[-max_lines:]
    body = "\n".join(lines)
    if len(body) > max_chars:
        body = "…" + body[-max_chars:]
    return f"daemon log tail ({path}):\n{body}"


def _backlog_list_cmd(mem: _CommonMemory, *, include_all: bool) -> None:
    print(format_backlog_list(mem, include_all=include_all))


def _status_change_cmd(mem: _CommonMemory, cmd: str, item_id: str) -> None:
    print(format_status_change(mem, cmd, item_id))


def _journal_tail_cmd(mem: _CommonMemory, n: int) -> None:
    print(format_journal_tail(mem, n))

def _daemon_alive_for(life_dir: Path | str) -> tuple[bool, int | None]:
    """(alive, pid) for the daemon owning ``life_dir`` — fail-soft to (False, None)."""
    try:
        from ..daemon.life_worker import read_daemon_status

        st = read_daemon_status(Path(life_dir))
        return bool(getattr(st, "alive", False)), getattr(st, "pid", None)
    except Exception:  # noqa: BLE001
        return False, None

__all__ = [
    "_CONFIG_DEFAULTS",
    "_ROLE_BACKEND_ENVS",
    "_ROLE_EFFORT_ENVS",
    "_ROLE_MODEL_ENVS",
    "_add_only",
    "_autospawn_daemon_for_task",
    "_backend_cmd",
    "_backlog_list_cmd",
    "_cockpit_cli_alias",
    "_config_cmd",
    "_continuous_cmd",
    "_continuous_session_error",
    "_daemon_alive_for",
    "_daemon_cmd",
    "_daemon_log_tail",
    "_identity_cmd",
    "_is_argus_cli_invocation",
    "_journal_tail_cmd",
    "_live_cockpit_enabled",
    "_live_follow_enabled",
    "_settings_cmd",
    "_should_autospawn_on_boot",
    "_spawn_daemon_from_cockpit",
    "_status_change_cmd",
]
