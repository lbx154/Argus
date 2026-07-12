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
import sys
import time
from pathlib import Path
from typing import Any

from ..apps._life_actions import (
    render_skills_cmd,
)
from ..apps._runtime import (
    _invoke_supervisor,
    _memory_global_root,  # noqa: F401 — kept for parity with the old module surface
    _resolve_global_root,
    _SplitMemory,
)
from ..core import paths as core_paths
from ..life import LifeMemory, MemoryBundle
from .config_intent import (
    _apply_config_intent,
    _render_live_role_overlay,
)
from .config_intent import (
    _front_door_classify as _front_door_classify_impl,
)
from .config_intent import (
    _maybe_handle_config_intent as _maybe_handle_config_intent_impl,
)
from .front_door import (
    _MANAGER_RUNNER_UNAVAILABLE,
    _accepts_keyword,
    _derive_session_name,
    _emit_manager_event,
    _ensure_manager_runner,
    _extract_chat_reply_text,
    _life_dir_for,
    _maybe_name_session,
    _with_manager_spinner,
)
from .front_door import (
    _manager_divide_user_task as _manager_divide_user_task_impl,
)
from .front_door import (
    manager_triage as _manager_triage_impl,
)
from .repl_commands import dispatch_command
from .repl_completion import (
    _format_completion,
    _format_elapsed,
    _no_executor_notice,
    _record_mission_outcome,
    _surface_blocked_question,
)
from .repl_follow import (
    _PANE_IDLE_VERBS,
    _TAIL_ROLE_TITLES,
    _TAIL_ROLE_VERBS,
    _TAIL_SPIN_INTERVAL,
    _TAIL_WAIT_VERBS,
    _follow_events_stream,
    _sleep_until,
    _tail_spin_interval,
    _TailPrinter,
    _TailWaitSpinner,
    _type_out_line,
    _wrap_plain,
    follow_mission_live_roles,
    tail_mission_events,
)
from .repl_free_text import FreeTextHooks, dispatch_free_text
from .repl_help import (
    _HELP_SECTIONS,
    SLASH_COMMANDS,
    _bottom_hint_line,
    _help_command_rows,
    _top_frame_line,
)
from .repl_input import (
    _build_slash_completer,
    _drain_available_bytes,
    _get_prompt_session,
    _live_cockpit_will_activate,
    _split_readline_safe_prompt,
    _use_prompt_toolkit_input,
    _visual_row_delta,
    read_message_prompt_toolkit,
    read_message_with_live_cockpit,
)
from .repl_ops import (
    _CONFIG_DEFAULTS,
    _ROLE_BACKEND_ENVS,
    _ROLE_EFFORT_ENVS,
    _ROLE_MODEL_ENVS,
    _add_only,
    _backend_cmd,
    _backlog_list_cmd,
    _cockpit_cli_alias,
    _config_cmd,
    _continuous_cmd,
    _continuous_session_error,
    _daemon_alive_for,
    _daemon_log_tail,
    _identity_cmd,
    _is_argus_cli_invocation,
    _journal_tail_cmd,
    _live_cockpit_enabled,
    _live_follow_enabled,
    _settings_cmd,
    _should_autospawn_on_boot,
    _spawn_daemon_from_cockpit,
    _status_change_cmd,
)
from .repl_ops import (
    _autospawn_daemon_for_task as _autospawn_daemon_for_task_impl,
)
from .repl_ops import (
    _daemon_cmd as _daemon_cmd_impl,
)
from .repl_session_ops import (
    _attach_cmd as _attach_cmd_impl,
)
from .repl_session_ops import (
    _daemons_cmd,
    _doctor_cmd,
    _print_transcript,
    _recent_daemon_log_tail,
    _rewrite_cockpit_daemon_fix,
)
from .repl_session_ops import (
    _plan_cmd as _plan_cmd_impl,
)
from .repl_session_ops import (
    _resume_cmd as _resume_cmd_impl,
)
from .repl_session_ops import (
    _roles_cmd as _roles_cmd_impl,
)
from .repl_session_ops import (
    _status_cmd as _status_cmd_impl,
)

log = logging.getLogger(__name__)

__all__ = [
    "SLASH_COMMANDS",
    "_build_slash_completer",
    "_drain_available_bytes",
    "_get_prompt_session",
    "_split_readline_safe_prompt",
    "_visual_row_delta",
    "dispatch_command",
    "run_manager_repl",
]




# ---------------------------------------------------------------------------
# Event-tail helpers (REPL = attach client; the daemon is the sole executor)
# ---------------------------------------------------------------------------
#
# Since the 2026-06-26 fusion, the REPL no longer executes missions in-process.
# Free text / ``/add`` write a backlog item + (for continuous) the objective to
# disk; the 7×24 daemon claims and runs them. The REPL then *attaches* by
# tailing ``<life_dir>/events.jsonl`` — the same jsonl bus the standalone
# ``argus-skill --follow`` view reads — and renders mission events with the
# shared formatter in ``apps.cli._follow`` so there is exactly one renderer.


def _manager_divide_user_task(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    *,
    theme: object | None = None,
    root_task_id: str | None = None,
) -> None:
    return _manager_divide_user_task_impl(
        mem,
        body,
        chat_state,
        theme=theme,
        root_task_id=root_task_id,
        ensure_runner=_ensure_manager_runner,
    )


def manager_triage(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    *,
    on_phase: Any = None,
    on_fragment: Any = None,
    route: str | None = None,
    root_task_id: str | None = None,
) -> str | None:
    return _manager_triage_impl(
        mem,
        body,
        chat_state,
        on_phase=on_phase,
        on_fragment=on_fragment,
        route=route,
        root_task_id=root_task_id,
        ensure_runner=_ensure_manager_runner,
    )


def _autospawn_daemon_for_task(
    mem: Any,
    chat_state: dict[str, Any],
) -> tuple[bool, int | None]:
    return _autospawn_daemon_for_task_impl(
        mem,
        chat_state,
        spawn_daemon=_spawn_daemon_from_cockpit,
    )


def _daemon_cmd(
    mem: _SplitMemory,
    arg_text: str,
    chat_state: dict[str, Any],
) -> None:
    return _daemon_cmd_impl(
        mem,
        arg_text,
        chat_state,
        spawn_daemon=_spawn_daemon_from_cockpit,
    )


def _front_door_classify(
    mem: Any,
    text: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
) -> tuple[Any, str]:
    return _front_door_classify_impl(
        mem,
        text,
        chat_state,
        root_task_id=root_task_id,
        ensure_runner=_ensure_manager_runner,
        accepts_keyword=_accepts_keyword,
    )


def _maybe_handle_config_intent(
    mem: Any,
    text: str,
    chat_state: dict[str, Any],
    *,
    on_confirm: Any = None,
    root_task_id: str | None = None,
) -> bool:
    return _maybe_handle_config_intent_impl(
        mem,
        text,
        chat_state,
        on_confirm=on_confirm,
        root_task_id=root_task_id,
        ensure_runner=_ensure_manager_runner,
        accepts_keyword=_accepts_keyword,
        apply_intent=_apply_config_intent,
    )


def enqueue_mission(mem: Any, body: str, chat_state: dict[str, Any], *,
                    iterate: bool = True, max_cycles: int = 6,
                    budget: float = 30.0, theme: object | None = None,
                    root_task_id: str | None = None,
                    ) -> tuple[Any | None, bool, int | None]:
    """Enqueue ``body`` as a head-priority mission (NO blocking tail — the caller
    decides whether to follow). Handles the blocked-continuation rewrite and, in
    continuous mode, persists the objective for the daemon. Returns
    ``(item, daemon_alive, daemon_pid)``. Shared by the line REPL and the TUI."""
    # Blocked-continuation: a reply to a just-blocked mission continues the same
    # objective (answer appended + queued to inbox), not a brand-new task.
    if chat_state.get("blocked_item_id"):
        prior = str(chat_state.get("last_objective") or body)
        blocked_id = chat_state.pop("blocked_item_id", None)
        chat_state.pop("blocked_question", None)
        try:
            from ..apps._inbox import queue_inbox_message
            queue_inbox_message(_life_dir_for(mem), body, source="repl.answer")
        except Exception:  # noqa: BLE001
            pass
        if blocked_id:
            # Resolve the persisted question (BacklogItem.pending_question —
            # see life/supervisor/_core.py's blocked-verdict handling) so
            # /status stops listing it as still awaiting an answer. Best-
            # effort: a failure here must never block the actual reply.
            try:
                mem.backlog.update(blocked_id, pending_question="")
            except Exception:  # noqa: BLE001
                pass
        body = f"{prior}\n\nOperator reply: {body}"
    chat_state["last_objective"] = body
    _manager_divide_user_task(
        mem,
        body,
        chat_state,
        theme=theme,
        root_task_id=root_task_id,
    )
    life_dir = _life_dir_for(mem)
    if chat_state.get("config", {}).get("continuous", False):
        # User task is a PROJECT objective, not an Engineer work item. Arm the
        # planner first; it will decompose into backlog items. This gives the
        # intended chain: User -> Manager -> Planner -> Engineer -> Reviewer.
        chat_state["continuous_objective"] = body
        from ..daemon.life_worker import write_continuous_config
        write_continuous_config(life_dir, enabled=True, objective=body)
        daemon_alive, daemon_pid = _daemon_alive_for(life_dir)
        if not daemon_alive and chat_state.get("auto_start_daemon_on_task"):
            daemon_alive, daemon_pid = _with_manager_spinner(
                theme, "Starting the executor daemon…",
                lambda: _autospawn_daemon_for_task(mem, chat_state),
            )
        return None, daemon_alive, daemon_pid
    pending = mem.backlog.pending()
    head_priority = min((it.priority for it in pending), default=100)
    free_priority = min(head_priority - 1, -1)
    item = _add_only(mem, body, priority=free_priority, iterate=iterate,
                     iteration_max_cycles=max_cycles, iteration_budget_usd=budget,
                     item_id=root_task_id)
    _maybe_name_session(chat_state, body)
    daemon_alive, daemon_pid = _daemon_alive_for(life_dir)
    if not daemon_alive and chat_state.get("auto_start_daemon_on_task"):
        daemon_alive, daemon_pid = _with_manager_spinner(
            theme, "Starting the executor daemon…",
            lambda: _autospawn_daemon_for_task(mem, chat_state),
        )
    return item, daemon_alive, daemon_pid


def _maybe_auto_promote_to_continuous(
    mem: Any,
    body: str,
    chat_state: dict[str, Any],
    theme: Any,
    *,
    root_task_id: str | None = None,
) -> bool:
    """Let the Manager judge whether ``body`` is open-ended work that should run
    as a STANDING (continuous) campaign, rather than a one-shot bounded
    mission — so the operator never has to manually pass
    ``--continuous --objective`` (or type ``/continuous start``) for work that
    is inherently open-ended (e.g. "optimize as many kernels as possible").
    Arms continuous mode the same way ``/continuous start <objective>`` does
    (``write_continuous_config`` — the daemon hot-reloads it, no restart).

    Fail-soft in every direction: no runner (memory backend, build failure), a
    classify error, an already-continuous session (caller only calls this when
    not yet continuous), or a config gate failure (empty objective / memory
    backend) all leave the task on its normal bounded (one-shot backlog) path.
    Returns True iff continuous mode was armed (``chat_state`` is mutated in
    that case, mirroring ``/continuous start``).
    """
    runner = _ensure_manager_runner(chat_state, mem)
    classify = getattr(runner, "classify_needs_continuous", None)
    if runner is None or not callable(classify):
        return False
    try:
        # This is a REAL (blocking) model round-trip. It runs AFTER the triage
        # spinner has exited, so without its own indicator the prompt freezes
        # here for a few seconds the instant a task is routed to the TEAM — the
        # "无动画空窗期 / 卡住" symptom. Wrap it in the same braille spinner so
        # the wait always animates (no-op on non-TTY / piped / NO_COLOR).
        from ..cli.live_status import LiveStatus
        from ..cli.roles_status import ROLE_COLOR_BOLD

        with LiveStatus(
            "Deciding if this is an ongoing goal…",
            theme=theme,
            accent=ROLE_COLOR_BOLD.get("manager", "magenta"),
        ):
            if root_task_id is None or not _accepts_keyword(
                classify,
                "root_task_id",
            ):
                is_standing = bool(classify(body))
            else:
                is_standing = bool(
                    classify(body, root_task_id=root_task_id)
                )
        if not is_standing:
            return False
    except Exception:  # noqa: BLE001 — classify failure must never force continuous
        return False

    from ..daemon.life_worker import continuous_mode_error, write_continuous_config

    backend = str(chat_state.get("backend") or "codex")
    if continuous_mode_error(backend, True, body):
        return False

    life_dir = _life_dir_for(mem)
    write_continuous_config(life_dir, enabled=True, objective=body)
    chat_state.setdefault("config", dict(_CONFIG_DEFAULTS))["continuous"] = True
    chat_state["continuous_objective"] = body
    msg = (
        "Manager decided this is open-ended work with no natural endpoint → "
        "automatically set it as a 7×24 continuous goal (continuous mode); "
        "the daemon will plan and advance autonomously until the goal is "
        "exhausted or you type /continuous stop."
    )
    print(
        ("  " + theme.cyan("argus") + theme.dim(" ↳ ") + msg) if theme is not None
        else f"  argus ↳ {msg}",
        flush=True,
    )
    return True


def _free_text_cmd(
    mem: Any,
    text: str,
    chat_state: dict[str, Any],
) -> None:
    return dispatch_free_text(
        mem,
        text,
        chat_state,
        hooks=FreeTextHooks(
            maybe_handle_config_intent=_maybe_handle_config_intent,
            life_dir_for=_life_dir_for,
            render_live_role_overlay=_render_live_role_overlay,
            live_cockpit_enabled=_live_cockpit_enabled,
            manager_triage=manager_triage,
            maybe_auto_promote_to_continuous=_maybe_auto_promote_to_continuous,
            enqueue_mission=enqueue_mission,
            no_executor_notice=_no_executor_notice,
            live_follow_enabled=_live_follow_enabled,
            follow_mission_live_roles=follow_mission_live_roles,
            follow_events_stream=_follow_events_stream,
            record_mission_outcome=_record_mission_outcome,
            surface_blocked_question=_surface_blocked_question,
            tail_mission_events=tail_mission_events,
            format_completion=_format_completion,
        ),
    )


def _invoke_and_track(
    *,
    mem: _SplitMemory,
    chat_state: dict[str, Any],
    once: bool,
    max_missions: int,
    per_mission_cap_usd: float,
    daily_cap_usd: float,
    global_daily_cap_usd: float,
    quiet: bool,
    continuous: bool = False,
    continuous_objective: str = "",
    open_ended: bool = True,
    allow_chat_fast_path: bool = False,
) -> dict[str, Any]:
    """Run the supervisor in-process and persist the codex thread_id.

    NOT in the interactive path anymore. Since the 2026-06-26 REPL/daemon
    fusion the REPL attaches to the daemon (see :func:`tail_mission_events`)
    instead of executing missions itself, so ``_free_text_cmd`` / ``_run_cmd``
    no longer call this. It is retained only because tests still exercise the
    in-process drive + thread-id bookkeeping directly; keep it side-effect
    compatible for them.

    Records wall-clock elapsed time and prints a one-line footer so callers
    that *do* drive a mission inline see how long it took.
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
        global_daily_cap_usd=global_daily_cap_usd,
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
        raw_cost = summary.get("total_cost_usd") if isinstance(summary, dict) else None
        pricing_status = (
            str(summary.get("pricing_status") or "")
            if isinstance(summary, dict)
            else ""
        )
        try:
            cost = float(raw_cost) if raw_cost is not None else None
        except (TypeError, ValueError):
            cost = None
        footer = (
            f"⏱  elapsed {_format_elapsed(elapsed)}"
            + (f"  ·  missions={ran}" if ran else "")
            + (
                f"  ·  cost=${cost:.4f}"
                f"{'+' if pricing_status in {'partial', 'unpriced'} else ''}"
                if cost is not None
                else (
                    f"  ·  cost={pricing_status}"
                    if pricing_status in {"partial", "unpriced"}
                    else ""
                )
            )
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
    chat_state: dict[str, Any],
) -> None:
    """``/run`` — follow the daemon draining the backlog (live tail).

    Pre-fusion this drained the backlog in the foreground via
    ``render_run_command`` → ``_invoke_supervisor``. Since the 2026-06-26
    fusion the daemon is the sole executor, so ``/run`` attaches to it and
    live-renders every event until the operator hits Ctrl-C, returning to
    the REPL.
    """
    theme = chat_state.get("theme")
    life_dir = _life_dir_for(mem)
    pending = len(mem.backlog.pending())
    header = (
        f"/run: following daemon draining {pending} pending item(s)  "
        f"(Ctrl-C returns to the REPL; the daemon keeps running)…"
    )
    _follow_events_stream(life_dir, theme=theme, header=header)


def _status_cmd(mem: _SplitMemory, chat_state: dict[str, Any] | None = None) -> None:
    return _status_cmd_impl(mem, chat_state, life_dir_for=_life_dir_for)


def _roles_cmd(mem: Any, chat_state: dict[str, Any], arg_text: str = "") -> None:
    return _roles_cmd_impl(
        mem,
        chat_state,
        arg_text,
        life_dir_for=_life_dir_for,
    )


def _plan_cmd(mem: Any, chat_state: dict[str, Any], objective: str) -> None:
    return _plan_cmd_impl(
        mem,
        chat_state,
        objective,
        ensure_runner=_ensure_manager_runner,
        free_text_cmd=_free_text_cmd,
    )


def _attach_cmd(chat_state: dict[str, Any], global_root: Any, target: str) -> None:
    return _attach_cmd_impl(
        chat_state,
        global_root,
        target,
        follow_events_stream=_follow_events_stream,
    )


def _resume_cmd(
    mem: Any,
    chat_state: dict[str, Any],
    global_root: Any,
    rest_text: str,
) -> None:
    return _resume_cmd_impl(
        mem,
        chat_state,
        global_root,
        rest_text,
        life_dir_for=_life_dir_for,
    )


def _render_help(theme) -> str:  # noqa: ANN001
    out: list[str] = []
    out.append(theme.bold("Argus") + theme.gray("  — one cockpit, one mode"))
    out.append("")
    for para in (
        "Type what you need in natural language. The Manager decides whether it "
        "is chat, status, resume, configuration, planning, or real work.",
        "Real work follows the single product path: Manager → Planner → "
        "Idea/Skill → Engineer → Reviewer.",
        "Examples: `resume last task`, `what are you doing now`, "
        "`pause for now`, `switch to the copilot backend`, "
        "`把backend换成 copilot`, `switch the model to claude-sonnet-5`, "
        "`effort 设为 high`, `help me optimize this project`.",
    ):
        for line in theme.wrap_after(para, first_indent=0, hang_indent=0):
            out.append(theme.gray(line))
        out.append("")

    # Command reference — every real spelling in SLASH_COMMANDS ends up here
    # (grouped when _HELP_SECTIONS knows it, otherwise in a catch-all bucket),
    # so /help can never silently omit a command that the dispatcher accepts.
    rows = _help_command_rows()
    label_width = max((len(label) for label, _desc in rows.values()), default=0) + 2
    out.append(theme.bold("Command reference") + theme.gray("  — beyond natural language, you can also type commands directly"))
    out.append("")
    for section, cmds in _HELP_SECTIONS:
        out.append(theme.gray(f"  {section}"))
        for cmd in cmds:
            label, desc = rows.pop(cmd, (cmd, ""))
            out.append(f"    {label:<{label_width}}{theme.gray(desc)}")
        out.append("")
    if rows:
        out.append(theme.gray("  Other"))
        for cmd, (label, desc) in rows.items():
            out.append(f"    {label:<{label_width}}{theme.gray(desc)}")
        out.append("")

    out.append(theme.bold("Sessions") + theme.gray("  — get back to a past conversation (run in your shell, not a cockpit command)"))
    out.append("")
    for label, desc in (
        ("argus-skill --continue", "resume the last session (prefers the one with a live daemon)"),
        ("argus-skill --resume", "pick a past session from a list (● live = still-running daemon)"),
        ("argus-skill --resume <id>", "jump straight to a specific session id"),
    ):
        out.append(f"    {label:<28}{theme.gray(desc)}")
    out.append(theme.gray("    note: a bare `argus-skill` opens a NEW session — it does not resume."))
    out.append("")

    out.append(theme.gray(
        "`/` command completion is ON by default (prompt_toolkit UI); set "
        "ARGUS_SKILL_NO_PROMPT_TOOLKIT=1 for the plain reader"
    ))
    out.append(theme.gray(
        "Persistent live role panel is ON by default; set "
        "ARGUS_SKILL_COCKPIT_LIVE=0 to opt out"
    ))
    out.append(theme.gray(
        "Live-follow view while a task is running is ON by default; set "
        "ARGUS_SKILL_FOLLOW_LIVE=0 to opt out"
    ))
    out.append("")
    out.append(theme.gray(
        "Keys:  Ctrl-C cancels the current input / interrupts thinking (back to the "
        "prompt)   ·   Ctrl-C twice, Ctrl-D, or /exit quits   ·   "
        "argus-skill --continue returns to the last session"
    ))
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Slash-command helpers + public REPL entry point — invoked by apps/cli.main
# ---------------------------------------------------------------------------

def _skills_cmd(tokens: list[str]) -> None:
    """``/skills [ls|promote <name>]`` — inspect or promote a skill
    from the current project layer to the global layer."""
    print(render_skills_cmd(tokens))


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

    default_continuous = (
        backend_default != "memory" and not bool(getattr(args, "bounded", False))
    )

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
    config_continuous = continuous or default_continuous

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
    chat_state["config"]["continuous"] = config_continuous
    chat_state["continuous_state"] = ContinuousConfigState(
        enabled=continuous,
        objective=objective or disk_objective,
        done_reason="" if continuous else disk_state.done_reason,
        done_at="" if continuous else disk_state.done_at,
    )
    return chat_state, None


def _reexec_into_session(
    sid: str, args: argparse.Namespace, *, global_root: Any = None,
) -> None:
    """Replace this process with ``argus-skill --resume <sid>`` — the REAL
    switch (session bundle, daemon association, banner + conversation replay),
    identical to relaunching it from the shell. Preserves ``--life-dir`` so a
    custom global root carries over.

    Also passes ``--resume-continuous`` when the TARGET session has a
    persisted, armed continuous campaign (``<target_life_dir>/continuous.json``)
    — without it, ``_should_autospawn_on_boot`` only eagerly starts a daemon
    when the target already has a NON-EMPTY backlog, so switching back into a
    continuous campaign that happened to fully drain its backlog between
    rounds (and whose daemon has since died) would silently leave it un-resumed
    even though the operator explicitly chose to switch back into exactly this
    session — a far more deliberate act than "a fresh/manual daemon" (the
    scenario ``--resume-continuous`` defaults off to guard against, per its
    own ``--help`` text). ``--continuous``/``--objective`` are NOT needed
    alongside it: the daemon's own boot path reads both ``enabled`` and
    ``objective`` from that same ``continuous.json`` once ``resume_intent``
    is true (see ``daemon.life_worker``'s ``resume_intent`` boot-suppression
    logic). Best-effort — any lookup failure just skips the flag, since a
    resumed session's OWN objective/backlog can still spawn/re-arm it live.

    Never returns on success (``os.execv``)."""
    argv = [sys.executable, "-m", "argus_skill", "--resume", sid]
    life_dir = getattr(args, "life_dir", None)
    if life_dir:
        argv += ["--life-dir", str(life_dir)]
    if bool(getattr(args, "no_daemon", False)):
        argv.append("--no-daemon")
    if global_root:
        try:
            from ..daemon.life_worker import read_continuous_config
            target_life_dir = Path(global_root) / "projects" / sid
            enabled, _objective = read_continuous_config(target_life_dir)
            if enabled:
                argv.append("--resume-continuous")
        except Exception:  # noqa: BLE001 — best-effort; never block the switch
            pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass
    os.execv(sys.executable, argv)


def run_manager_repl(args: argparse.Namespace) -> int:
    """Drive the unified ``argus-skill`` REPL (the Manager conversation entry).

    Slash commands dispatch in-process — no daemon, no jsonl bus.
    Free text becomes a backlog item AND runs immediately on the
    current default backend.
    """
    try:
        global_root = _resolve_global_root(args)
        # Session model: a resolved session id (from --new/--resume/--continue,
        # default new) keys this REPL's project + daemon. Fall back to the cwd
        # identity only if no session was resolved (legacy / picker-aborted).
        _session_id = getattr(args, "session_id", None)
        if _session_id:
            mem: MemoryBundle = MemoryBundle.for_cwd(
                Path.cwd(), global_root=global_root, fingerprint=_session_id
            )
        else:
            mem = MemoryBundle.for_cwd(Path.cwd(), global_root=global_root)
    except core_paths.PathResolutionError as exc:
        sys.stderr.write(f"argus-skill: {exc}\n")
        return 2
    mem.init()
    os.environ["ARGUS_SKILL_AGENT_IO_LOG"] = str(mem.project.root / "events.jsonl")
    # Housekeeping: prune stale projects (no live daemon/repl + untouched for
    # the retention window) to projects_trash/. Best-effort, never blocks boot.
    try:
        from ..core.project_gc import maybe_gc_stale_projects
        # Exclude THIS session: the sweep runs before repl.pid is written, so a
        # just-resolved --resume of a long-parked project is not-yet-live and
        # would otherwise be trashed out from under the session resuming it.
        maybe_gc_stale_projects(global_root, exclude={mem.project.root.name})
    except Exception:  # noqa: BLE001
        pass
    # Mark this session active (so the resume picker orders it first) and load
    # its meta for the banner.
    session_meta = None
    if _session_id:
        try:
            from ..core.session import read_session_meta, touch_session
            touch_session(global_root, _session_id)
            session_meta = read_session_meta(global_root, _session_id)
        except Exception:  # noqa: BLE001
            pass
    theme = None  # populated in the locked body

    # Fail fast before we take the singleton lock if the current run
    # explicitly requests continuous mode that the backend cannot satisfy.
    chat_state, error = _seed_chat_state(args, mem, theme=theme)
    if error:
        sys.stderr.write(error + "\n")
        return 2

    # Stash the resolved session so the first *real* task can name it (for the
    # resume picker). `session_named` is True iff this session already carries a
    # display_name — a resumed session keeps its original name.
    chat_state["session_id"] = _session_id
    chat_state["global_root"] = global_root
    # Lazy daemon spawn: any non-continuous session (fresh OR resumed) starts
    # its executor on the FIRST real task, not on boot — so an empty session
    # never leaves an idle daemon behind. Continuous mode still boots its daemon
    # eagerly (it generates its own work). Only spawns when none is alive.
    chat_state["auto_start_daemon_on_task"] = (
        not bool(getattr(args, "no_daemon", False))
        and not bool(getattr(args, "continuous", False))
    )
    chat_state["session_named"] = bool(
        session_meta is not None and getattr(session_meta, "display_name", "")
    )

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
        rc = _run_manager_repl_locked(args, mem, chat_state=chat_state)
    finally:
        try:
            repl_lock.release()
        except Exception:  # noqa: BLE001
            log.exception("life REPL: failed to release singleton lock")

    # `/resume <id>` requested switching into another session. The singleton
    # lock is now released (finally above), so re-exec into that session exactly
    # like `argus-skill --resume <id>` — this replaces the process and never
    # returns.
    switch_sid = chat_state.get("switch_to_session")
    if switch_sid:
        _reexec_into_session(
            str(switch_sid), args, global_root=chat_state.get("global_root"),
        )
    return rc


def _print_daemon_boot_status(
    theme: Any,
    *,
    legacy_zombie_msg: str | None,
    auto_spawn_msg: str | None,
    no_daemon_warning: str | None,
) -> None:
    """Surface the daemon boot outcome computed just before this call in
    ``_run_manager_repl_locked`` (auto-spawn success/failure, ``--no-daemon``
    with nothing running, a pre-pivot legacy zombie).

    Regression: these three messages were being silently COMPUTED and then
    discarded — confirmed via git blame, a "wip(argus): daemon autonomous
    changes" commit (06aa6914) dropped the print step while leaving the
    message-building logic in place, so the "warn loudly" / "surface this"
    intent documented at each assignment site never actually happened (ruff
    flagged all three as unused-variable, which is how this was caught).
    ``legacy_zombie_msg`` prints first — it is the most urgent of the three
    (risks two daemons double-claiming the same work), then the auto-spawn
    outcome. A no-op for every argument left ``None`` (the common case: a
    clean auto-spawn with no legacy zombie prints only the ``auto_spawn_msg``
    line, nothing extra)."""
    if legacy_zombie_msg:
        print("  " + (theme.yellow(legacy_zombie_msg) if theme is not None else legacy_zombie_msg))
    if auto_spawn_msg:
        print("  " + (theme.dim(auto_spawn_msg) if theme is not None else auto_spawn_msg))
    if no_daemon_warning:
        print("  " + (theme.yellow(no_daemon_warning) if theme is not None else no_daemon_warning))


def _run_manager_repl_locked(
    args: argparse.Namespace,
    mem: _SplitMemory,
    *,
    chat_state: dict[str, Any],
) -> int:
    """The interactive REPL body. Split out so the singleton lock in
    :func:`run_manager_repl` cleanly wraps the entire loop with
    a try/finally release."""
    import readline  # noqa: F401 — enables line-editing for input()

    from ..apps._input_helpers import enable_bracketed_paste
    enable_bracketed_paste()
    from ..cli.branding import render_logo
    from ..cli.theme import Theme

    theme = Theme.auto(force=getattr(args, "color", None))

    # Always-verbose: the lifetime-agent product positioning means the
    # operator wants to see every internal event (round.start, match.info,
    # skill.writeback, …). The earlier ``verbose``/``quiet`` toggles have
    # been removed; ``--verbose`` and ``--quiet`` flags are accepted but
    # ignored (kept for backward compat in scripts).

    chat_state["theme"] = theme

    # ── Daemon is the sole executor (mandatory) ───────────────────
    # Since the 2026-06-26 REPL/daemon fusion the daemon is the ONLY thing
    # that drains the backlog — the REPL just enqueues + attaches. So we
    # MUST ensure a daemon is alive before attaching, otherwise every item
    # the operator submits sits pending forever. Auto-spawn one unless the
    # user opted out with --no-daemon (in which case we warn loudly).
    auto_spawn_msg: str | None = None
    no_daemon_warning: str | None = None
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
    if _should_autospawn_on_boot(args, mem):
        try:
            from ..apps.cli import _build_worker_config
            from ..daemon.life_worker import (
                read_daemon_status,
                spawn_detached_daemon,
                wait_for_daemon_status,
            )
            status = read_daemon_status(mem.project.root)
            if not status.alive:
                cfg = _build_worker_config(args, bundle=mem)
                spawn_rc = spawn_detached_daemon(cfg, quiet=True)
                if spawn_rc == 0:
                    from ..cli.live_status import LiveStatus
                    with LiveStatus(
                        "Starting daemon executor…",
                        theme=theme,
                        phrases=["Starting daemon executor…", "Waiting for the executor to come online…"],
                        hint="",
                    ):
                        started = wait_for_daemon_status(mem.project.root)
                    if started is not None and started.pid is not None:
                        auto_spawn_msg = f"daemon auto-spawned (pid {started.pid})"
                    else:
                        # Spawn returned success but the daemon never
                        # published a live pid — surface this so the operator
                        # knows their backlog will not drain on its own.
                        no_daemon_warning = (
                            "daemon spawn did not confirm alive — backlog may "
                            "not execute. Run /doctor for why + the fix."
                        )
                else:
                    no_daemon_warning = (
                        "daemon auto-spawn failed — backlog will NOT be "
                        "executed. Run /doctor for why + the fix "
                        "(often a rate-limited backend)."
                    )
        except Exception as exc:  # noqa: BLE001
            auto_spawn_msg = f"daemon auto-spawn skipped: {exc!s}"
            no_daemon_warning = (
                "daemon not confirmed running — backlog may not execute. "
                "Run /doctor for why + the fix."
            )
    else:
        # --no-daemon: the REPL no longer executes missions, so without a
        # daemon nothing drains the backlog. Warn unless one happens to be
        # alive already (e.g. launched separately via `argus-skill --daemon`).
        try:
            from ..daemon.life_worker import read_daemon_status
            status = read_daemon_status(mem.project.root)
        except Exception:  # noqa: BLE001
            status = None
        if (
            getattr(args, "no_daemon", False)
            and (status is None or not getattr(status, "alive", False))
        ):
            no_daemon_warning = (
                "--no-daemon: NO executor running — submitted items will sit "
                "pending forever. Start the executor here with `/daemon start` "
                "or from another shell with `argus-skill --daemon`."
            )

    global_root = chat_state.get("global_root")

    # ── Banner (minimal) ───────────────────────────────────────────
    print()
    print(render_logo(theme=theme))
    _sid = getattr(args, "session_id", None) or getattr(mem.project, "fingerprint", "")
    try:
        from ..daemon.life_worker import read_daemon_status as _read_daemon_status
        _ds = _read_daemon_status(mem.project.root)
        _pid = str(_ds.pid) if getattr(_ds, "alive", False) and getattr(_ds, "pid", None) else "-"
    except Exception:  # noqa: BLE001
        _pid = "-"
    # One dim subtitle row (secondary text) — session + daemon together, on the
    # same column-2 grid as the wordmark, ``·``-separated. No fixed-width labels.
    print(
        "  " + theme.gray("session ") + theme.cyan(str(_sid or "-"))
        + theme.dim("  ·  ") + theme.gray("daemon ") + theme.cyan(_pid)
    )
    _print_daemon_boot_status(
        theme,
        legacy_zombie_msg=legacy_zombie_msg,
        auto_spawn_msg=auto_spawn_msg,
        no_daemon_warning=no_daemon_warning,
    )
    # Per-role backend / model / reasoning-effort — surfaced on the banner so
    # the operator sees which engine each role runs on without typing /roles.
    try:
        from ..cli.roles_status import format_roles_banner
        roles_block = format_roles_banner(theme, collapse=True, show_hint=False)
        if roles_block:
            print(roles_block)
    except Exception:  # noqa: BLE001 — banner must never break on this
        pass
    print()

    # If this session already has a saved conversation (i.e. we're resuming it,
    # not opening a fresh one), replay the last few turns so the operator sees
    # where they left off. A brand-new session has no transcript → nothing shown.
    try:
        if _print_transcript(
            _life_dir_for(mem), theme, limit=6,
            header=theme.gray("↩ resuming — recent conversation:") if theme is not None else "↩ resuming — recent conversation:",
        ):
            print()
    except Exception:  # noqa: BLE001
        pass

    base_prompt = theme.bold(theme.cyan("argus"))
    resume_marker = theme.dim(" ↻")  # subtle indicator when codex session is being reused
    try:
        from ..cli.roles_status import format_prompt_status_line
    except Exception:  # noqa: BLE001 — prompt must never fail to build over this
        format_prompt_status_line = None  # type: ignore[assignment]
    # Resolved once (cheap attribute lookups, no I/O) so every turn's status
    # line can also show WHICH of the four roles is active right now — see
    # ``format_prompt_activity_suffix``. ``None`` (fail-soft) just reproduces
    # the prior backend/model-only line.
    try:
        _prompt_life_dir = _life_dir_for(mem)
    except Exception:  # noqa: BLE001
        _prompt_life_dir = None

    # Known limitation (verified via pty capture, not just theorized): if the
    # operator types enough text to WRAP the input onto a second terminal
    # row, readline's own redraw doesn't know a hint line already occupies
    # that row and can leave a visual fragment of it behind while they're
    # still typing. It always self-corrects the instant they press Enter
    # (see the post-input clear below), so this is a momentary cosmetic
    # artifact on long single-line input in a narrow terminal, not a lasting
    # one — fixing it fully would mean tracking wrap state on every
    # keystroke, effectively reimplementing part of readline itself, which
    # is out of scope for this pass.

    # Double-Ctrl-C to exit: a single Ctrl-C cancels the current input /
    # interrupts thinking and stays; a SECOND consecutive Ctrl-C (nothing typed
    # in between) quits. Any successful input re-disarms it.
    pending_exit = False
    while True:
        # Backend/model status resolved fresh every turn (not just once in
        # the startup banner) so a config switch (see _apply_config_intent)
        # keeps showing on every subsequent turn, not just the one right
        # after the switch.
        status = ""
        if format_prompt_status_line is not None:
            try:
                status = format_prompt_status_line(theme, life_dir=_prompt_life_dir)
            except Exception:  # noqa: BLE001
                status = ""
        # Top edge of the input frame — a full-width ``╭─ argus ─────…`` rule
        # (see _top_frame_line) that brackets the input together with the
        # ``╰─ `` prefix below. Only banner_row's WIDTH changes vs the old
        # short elbow; the multi-line prompt STRUCTURE and the 3-column
        # ``╰─ `` prefix the cursor math keys off are unchanged.
        banner_row = _top_frame_line(
            theme,
            base_prompt + (resume_marker if chat_state.get("last_thread_id") else ""),
        )
        input_row_prefix = theme.cyan("╰─ ")
        # BUG FIX (two rounds — both confirmed live, not just in a pty capture):
        #
        # Round 1: the original version folded the hint-line-below-the-input
        # redraw trick INTO the single string handed to input()/readline —
        # box + "\n" + hint + cursor_up_and_forward, all as one "prompt". That
        # put a cursor-repositioning escape code AFTER the last literal "\n"
        # in what readline parses as the prompt, so readline's own row
        # bookkeeping (counts embedded newlines to learn where editing
        # starts) and the ACTUAL cursor position (moved by the escape code to
        # a different row) permanently disagreed.
        #
        # Round 2: moving the redraw entirely to a pre-print with an EMPTY
        # prompt (so readline never sees an embedded newline) still broke,
        # because ``cursor_up_and_forward(1, 3)`` lands the physical cursor
        # at COLUMN 3 (past "╰─ ") while an empty prompt makes readline
        # assume it started at column 0 — a 3-column disagreement invisible
        # while simply echoing typed characters, but exposed the moment
        # readline does ANY internal redraw (confirmed live: the "╰─ " prefix
        # vanished and got overwritten by the typed text the instant a
        # redraw fired, e.g. while a LiveStatus spinner raced it).
        #
        # Fix: pre-print only what is NOT on the input row (banner above +
        # a blank placeholder for the input row + the hint below), jump back
        # up to COLUMN 0 of the (still blank) input row — matching readline's
        # own assumption exactly — and then hand "╰─ " itself to input() as
        # the real prompt, so readline's bookkeeping and the physical cursor
        # agree from the start; any internal redraw reprints "╰─ " correctly
        # instead of losing it.
        #
        # Round 3: gating this on ``_live_cockpit_enabled()`` (an env-var-only
        # check) was ALSO wrong — that flag can be true while
        # ``read_message_with_live_cockpit`` still silently falls back to a
        # PLAIN ``input()`` underneath (no daemon running, a short terminal,
        # no termios, etc.). In that fallback, whatever ``prompt`` this branch
        # built gets handed to plain ``input()`` — so choosing the OLD
        # multi-row combined-prompt form here (meant only for when the fancy
        # panel truly renders and manages its own redraws) reintroduced
        # exactly the Round-1 corruption (confirmed live under
        # ``--no-daemon``: default env, no daemon, "╰─ " still vanished
        # mid-keystroke). ``_live_cockpit_will_activate`` mirrors every guard
        # ``read_message_with_live_cockpit`` itself checks, so this branch and
        # that function can never disagree about which path is really live.
        use_ptk = _use_prompt_toolkit_input() and not chat_state.get("prompt_toolkit_disabled")
        if use_ptk:
            # prompt_toolkit is the default engine and owns ALL rendering: the
            # live 4-role panel is drawn ABOVE the input by
            # read_message_prompt_toolkit's refreshing message and the `/` menu
            # floats below. Hand it only the ╭─/╰─ box — NO manual pre-print
            # (that would double-draw and fight prompt_toolkit's own redraws).
            prompt = banner_row + "\n" + input_row_prefix
        else:
            live_cockpit = _live_cockpit_will_activate(mem)
            if live_cockpit:
                prompt = (
                    banner_row
                    + "\n" + input_row_prefix
                    + "\n" + _bottom_hint_line(theme, status)
                    + theme.cursor_up_and_forward(1, 3)
                )
            else:
                sys.stdout.write(
                    banner_row
                    + "\n"  # blank placeholder input row — filled in by input() below
                    + "\n" + _bottom_hint_line(theme, status)
                    + "\n" + theme.cursor_up_and_forward(2, 0)
                )
                sys.stdout.flush()
                prompt = input_row_prefix
        try:
            if use_ptk:
                raw = read_message_prompt_toolkit(prompt, mem, theme, chat_state)
            else:
                raw = read_message_with_live_cockpit(prompt, mem, theme)
        except KeyboardInterrupt:
            if pending_exit:
                print()
                print(theme.gray("bye."))
                return 0
            pending_exit = True
            print()
            print(theme.gray("(Ctrl-C again to exit  ·  Ctrl-D or /exit also quits)"))
            continue
        pending_exit = False  # a successful read re-disarms the double-Ctrl-C exit
        if not use_ptk and theme.enabled and sys.stdout.isatty():
            # The operator's Enter already advanced the real terminal cursor
            # exactly one row past wherever their typing visually ended
            # (accounting for wrapping automatically — this is the terminal's
            # own line-discipline echo, not something we compute), which is
            # precisely where the now-stale hint line from _bottom_hint_line
            # sits. Clear it in place before anything else prints.
            sys.stdout.write("\r\x1b[2K")
            sys.stdout.flush()
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

        try:
            if dispatch_command(line, raw, mem, chat_state, global_root, theme) == "exit":
                print(theme.gray("bye."))
                return 0
        except KeyboardInterrupt:
            # Ctrl-C while the Manager is thinking / tailing: interrupt THIS
            # turn and return to the prompt — never exit the cockpit. The
            # background daemon mission keeps running (use /daemon stop for it).
            print()
            print(theme.gray(
                "⎋ interrupted — back to the prompt  ·  the background daemon "
                "mission keeps running (use /daemon stop to stop it)"
            ))
            continue
        # `/resume <id>` requested switching into another session: leave the
        # loop cleanly so run_manager_repl re-execs `argus-skill --resume <id>`
        # once the singleton lock is released.
        if chat_state.get("switch_to_session"):
            return 0


__all__ = [
    "run_manager_repl",
    "_run_manager_repl_locked",
    "_add_only",
    "_backend_cmd",
    "_continuous_session_error",
    "_CONFIG_DEFAULTS",
    "_ROLE_BACKEND_ENVS",
    "_ROLE_EFFORT_ENVS",
    "_ROLE_MODEL_ENVS",
    "_config_cmd",
    "_identity_cmd",
    "_continuous_cmd",
    "_autospawn_daemon_for_task",
    "_cockpit_cli_alias",
    "_daemon_alive_for",
    "_daemon_cmd",
    "_daemon_log_tail",
    "_is_argus_cli_invocation",
    "_live_cockpit_enabled",
    "_live_follow_enabled",
    "_settings_cmd",
    "_should_autospawn_on_boot",
    "_spawn_daemon_from_cockpit",
    "_backlog_list_cmd",
    "_status_change_cmd",
    "_journal_tail_cmd",
    "_free_text_cmd",
    "_format_completion",
    "_format_elapsed",
    "_invoke_and_track",
    "_run_cmd",
    "_status_cmd",
    "_roles_cmd",
    "_doctor_cmd",
    "_rewrite_cockpit_daemon_fix",
    "_recent_daemon_log_tail",
    "_plan_cmd",
    "_daemons_cmd",
    "_attach_cmd",
    "_print_transcript",
    "_resume_cmd",
    "_render_help",
    "_skills_cmd",
    "_seed_chat_state",
    "tail_mission_events",
    "_follow_events_stream",
    "follow_mission_live_roles",
    "_PANE_IDLE_VERBS",
    "_TAIL_ROLE_TITLES",
    "_TAIL_ROLE_VERBS",
    "_TAIL_SPIN_INTERVAL",
    "_TAIL_WAIT_VERBS",
    "_TailPrinter",
    "_TailWaitSpinner",
    "_sleep_until",
    "_tail_spin_interval",
    "_type_out_line",
    "_wrap_plain",
    "read_message_with_live_cockpit",
    "_MANAGER_RUNNER_UNAVAILABLE",
    "_accepts_keyword",
    "_derive_session_name",
    "_emit_manager_event",
    "_ensure_manager_runner",
    "_extract_chat_reply_text",
    "_life_dir_for",
    "_manager_divide_user_task",
    "_apply_config_intent",
    "_front_door_classify",
    "_maybe_handle_config_intent",
    "_render_live_role_overlay",
    "_maybe_name_session",
    "_with_manager_spinner",
    "manager_triage",
    "_record_mission_outcome",
    "_no_executor_notice",
    "_surface_blocked_question",
    "dispatch_command",
]
