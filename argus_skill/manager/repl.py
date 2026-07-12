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
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..apps._life_actions import (
    append_note,
    parse_add_flags,
    render_skills_cmd,
)
from ..apps._runtime import (
    _invoke_supervisor,
    _memory_global_root,  # noqa: F401 — kept for parity with the old module surface
    _resolve_global_root,
    _SplitMemory,
)
from ..core import paths as core_paths
from ..life import BacklogItem, LifeMemory, MemoryBundle
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


def _render_live_role_overlay(
    life_dir: Path | str, theme: Any, *, active_role: str, label: str,
) -> str:
    """A truthful "roles · activity" snapshot for the SELF quick-reply spinner
    (the ``LiveStatus`` block in ``_free_text_cmd``), marking ``active_role``
    active directly from the SAME phase signal the spinner itself is driven
    by — NOT from ``events.jsonl``.

    The SELF quick-reply path (Manager answers a simple chat turn itself, no
    Planner/Engineer/Reviewer hand-off) deliberately never journals its
    progress to ``events.jsonl`` (see ``_Capture``/``_simple_quick_reply`` —
    avoids mission-log noise for a one-line "你好"). That means
    ``role_activity()`` — the ONLY data source the pre-turn panel
    (``read_message_prompt_toolkit`` / ``read_message_with_live_cockpit``)
    reads — has no way to know this turn is happening at all, so that panel
    keeps showing every role "idle" for the entire live turn: not just stale,
    a direct, visible self-contradiction with the correctly-labeled spinner
    right below it (live-confirmed: "Manager idle" shown while "Manager ·
    SELF: ... 6s" spun beneath it, prompting "你不要只做摆设" — don't just
    make this decorative). This builds a SEPARATE, correct snapshot for that
    narrow window by overriding just one role's entry in an otherwise-real
    ``role_activity()`` read (so Planner/Engineer/Reviewer still show their
    true last-known state, not a blanket fake "idle")."""
    from ..cli.roles_status import (
        ROLES,
        RoleActivity,
        format_roles_panel,
        resolve_all_roles,
        role_activity,
    )

    try:
        activities = dict(role_activity(life_dir))
    except Exception:  # noqa: BLE001
        activities = {}
    for r in ROLES:
        activities.setdefault(
            r, RoleActivity(role=r, active=False, label="idle", status="idle", age_s=None),
        )
    role = (active_role or "").strip().lower()
    if role in activities:
        activities[role] = RoleActivity(
            role=role, active=True, label=label, status="running", age_s=0.0,
        )
    configs = resolve_all_roles(env=os.environ)
    width = theme.live_width() if theme is not None and hasattr(theme, "live_width") else 80
    return format_roles_panel(theme, configs, activities, width=width)


def _front_door_classify(
    mem: Any,
    text: str,
    chat_state: dict[str, Any],
    *,
    root_task_id: str | None = None,
) -> "tuple[Any, str]":
    """ONE merged LLM call for the cockpit front-door: returns
    ``(ConfigIntent | None, route)`` where route is ``"simple"``/``"complex"``.

    Replaces the old sequential config-intent + route classify (two copilot
    cold-starts → one) — see ``Manager.classify_front_door`` /
    ``life.router.classify_front_door``. Fail-soft: no runner, no manager, or any
    error → ``(None, "complex")`` so the message flows through the normal
    task path unchanged (never swallow real work on a classify hiccup)."""
    try:
        runner = _ensure_manager_runner(chat_state, mem)
        mgr = getattr(runner, "manager", None) if runner is not None else None
        if mgr is None or not hasattr(mgr, "classify_front_door"):
            return None, "complex"
        if root_task_id is None or not _accepts_keyword(
            mgr.classify_front_door,
            "root_task_id",
        ):
            intent, route = mgr.classify_front_door(text)
        else:
            intent, route = mgr.classify_front_door(
                text,
                root_task_id=root_task_id,
            )
        return intent, (route if route in ("simple", "complex") else "complex")
    except Exception:  # noqa: BLE001 — a classify hiccup must never break the turn
        return None, "complex"


def _maybe_handle_config_intent(
    mem: Any,
    text: str,
    chat_state: dict[str, Any],
    *,
    on_confirm: Any = None,
    root_task_id: str | None = None,
) -> bool:
    """Recognize + apply a natural-language change to one of Argus's OWN runtime
    knobs (a role's backend/model/effort, a budget cap, or the safe_mode/
    show_reasoning/telegram toggles) BEFORE it becomes work.

    One low-reasoning LLM call decides intent (Manager.classify_config_intent →
    life.router.classify_config_intent) — there is NO keyword/regex matching, so
    a request phrased any way is caught and a bare mention of a model/backend is
    not misread as a switch. Fail-soft: no runner, a classify error, or a NONE
    verdict all return False, and the text flows on to the normal chat/task path.
    Returns True iff it applied a change (and the turn is done).

    ``on_confirm(line)`` — optional sink for the confirmation line(s). When given
    (the web/TUI cockpit front-door), the confirmation is handed to it INSTEAD of
    printed to stdout, so a non-REPL surface can show it as a chat reply. Default
    ``None`` keeps the line-REPL's print behaviour byte-for-byte."""
    runner = _ensure_manager_runner(chat_state, mem)
    mgr = getattr(runner, "manager", None) if runner is not None else None
    if mgr is None or not hasattr(mgr, "classify_config_intent"):
        return False
    try:
        if root_task_id is None or not _accepts_keyword(
            mgr.classify_config_intent,
            "root_task_id",
        ):
            intent = mgr.classify_config_intent(text)
        else:
            intent = mgr.classify_config_intent(
                text,
                root_task_id=root_task_id,
            )
    except Exception:  # noqa: BLE001 — a classify hiccup must never break the turn
        return False
    if intent is None:
        return False
    return _apply_config_intent(mem, intent, chat_state, on_confirm=on_confirm)


def _apply_config_intent(
    mem: Any, intent: Any, chat_state: dict[str, Any], *, on_confirm: Any = None
) -> bool:
    """Apply a parsed ConfigIntent: set the env var(s), persist via knob_store
    (so a running daemon reads the switch immediately), confirm, and ground the
    Manager with a note. Returns True iff a change was applied."""
    from ..core.knob_store import write_persisted_knob

    theme = chat_state.get("theme")

    def _confirm(line: str) -> None:
        if callable(on_confirm):
            try:
                on_confirm(line)  # cockpit: surface as a chat reply, not stdout
            except Exception:  # noqa: BLE001 — a UI sink must never break the apply
                pass
        else:
            print(("  " + theme.cyan("argus") + theme.dim(" ↳ ") + line)
                  if theme is not None else line, flush=True)
        try:
            append_note(mem, line)
        except Exception:  # noqa: BLE001 — a grounding nicety, never fatal
            pass

    def _set(env_var: str, value: str) -> None:
        os.environ[env_var] = value
        write_persisted_knob(env_var, value)

    knob = intent.knob
    roles = list(intent.roles)

    if knob == "backend":
        from ..agent_cli.runner_backend import normalize_runner_backend

        value = normalize_runner_backend(intent.value)
        if roles:
            for role in roles:
                _set(_ROLE_BACKEND_ENVS[role], value)
            _confirm(f"Set {' / '.join(r.title() for r in roles)} CLI backend to {value}.")
        else:
            _set("ARGUS_SKILL_RUNNER_BACKEND", value)
            _confirm(f"Set Argus default CLI backend to {value} "
                     "(roles without their own backend follow).")
        chat_state.pop("manager_runner", None)
        return True

    if knob == "model":
        value = intent.value
        if roles:
            for env_var in {_ROLE_MODEL_ENVS[role] for role in roles}:
                _set(env_var, value)
            _confirm(f"Set {' / '.join(r.title() for r in roles)} model to {value}.")
        else:
            _set("ARGUS_SKILL_MODEL", value)
            _confirm(f"Set Argus default model to {value} "
                     "(roles without their own model follow).")
        chat_state.pop("manager_runner", None)
        return True

    if knob == "effort":
        value = intent.value.strip().lower()
        target = roles or list(_ROLE_EFFORT_ENVS)
        # A reasoning-effort knob is a silent no-op on a non-reasoning model —
        # reject with a grounded explanation instead of pretending to apply it.
        from ..cli.roles_status import resolve_role_config

        rcfg = {r: resolve_role_config(r, env=os.environ) for r in target}
        applicable = [r for r in target if rcfg[r].effort is not None]
        if not applicable:
            models = ", ".join(sorted({rcfg[r].model for r in target}))
            _confirm(f"Current model ({models}) is non-reasoning — reasoning effort "
                     "does not apply, so I left it unchanged.")
            return True
        for role in applicable:
            _set(_ROLE_EFFORT_ENVS[role], value)
        _confirm(f"Set {' / '.join(r.title() for r in applicable)} reasoning effort to {value}.")
        chat_state.pop("manager_runner", None)
        return True

    if knob in ("per_mission_cap", "daily_cap"):
        m = re.search(r"\d+(?:\.\d+)?", intent.value)
        if m is None:
            return False
        env_var = ("ARGUS_SKILL_PER_MISSION_CAP_USD" if knob == "per_mission_cap"
                   else "ARGUS_SKILL_DAILY_CAP_USD")
        _set(env_var, m.group(0))
        _confirm(f"Set {env_var} = {m.group(0)}.")
        return True

    quota_knobs = {
        "max_daemons": "ARGUS_SKILL_MAX_ACTIVE_DAEMONS",
        "codex_daily_requests": "ARGUS_SKILL_CODEX_DAILY_CALL_CAP",
        "copilot_daily_requests": "ARGUS_SKILL_COPILOT_DAILY_CALL_CAP",
        "copilot_daily_premium": "ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP",
    }
    if knob in quota_knobs:
        m = re.search(r"\d+(?:\.\d+)?", intent.value)
        if m is None:
            return False
        env_var = quota_knobs[knob]
        from ..core.knobs import normalize_cockpit_knob_value

        value = normalize_cockpit_knob_value(env_var, m.group(0))
        _set(env_var, value)
        _confirm(f"Set {env_var} = {value}.")
        return True

    if knob in ("safe_mode", "show_reasoning", "telegram"):
        env_var = {
            "safe_mode": "ARGUS_SKILL_SAFE_MODE",
            "show_reasoning": "ARGUS_SKILL_SHOW_REASONING",
            "telegram": "ARGUS_SKILL_ENABLE_TELEGRAM",
        }[knob]
        v = intent.value.strip().lower()
        on = v in ("on", "1", "true", "yes", "enable", "enabled",
                   "开", "打开", "开启", "启用")
        off = v in ("off", "0", "false", "no", "disable", "disabled",
                    "关", "关闭", "关掉", "停用", "禁用")
        if on == off:  # neither recognized, or contradictory — don't guess
            return False
        val = "1" if on else "0"
        _set(env_var, val)
        _confirm(f"Set {env_var} = {val} ({'on' if on else 'off'}).")
        return True

    return False


def _free_text_cmd(
    mem: Any,
    text: str,
    chat_state: dict[str, Any],
) -> None:
    """Free-text input: Manager triage FIRST, then enqueue + attach.

    The Manager is the operator's first point of contact: every line is
    classified (conversation → answered in-band; task → queued for the 7×24
    daemon). A real task is injected at head priority; the REPL then attaches by
    tailing ``events.jsonl`` until the daemon reports completion. Supports
    ``--once`` / ``--cycles=N`` / ``--budget=$X`` inline flags.
    """
    cfg = chat_state.get("config", {})
    continuous = cfg.get("continuous", False)
    iterate, max_cycles, budget, body = parse_add_flags(
        text,
        defaults=cfg,
    )
    body = body or text.strip()
    theme = chat_state.get("theme")
    root_task_id = BacklogItem.new_id()

    # Natural-language change to one of Argus's own runtime knobs (backend /
    # model / effort / budget cap / a toggle)? One LLM intent call decides —
    # no keyword/regex matching — before the text becomes research work.
    if _maybe_handle_config_intent(
        mem,
        body,
        chat_state,
        root_task_id=root_task_id,
    ):
        return

    # Persist this turn to the session transcript (for /resume replay + labels).
    # The config-switch handlers above already returned, so only real chat/task
    # turns are logged. Fail-soft: transcript I/O must never break the REPL.
    from ..core import transcript as _transcript
    _tlife: Any = None
    try:
        _tlife = _life_dir_for(mem)
        _transcript.append_turn(_tlife, "operator", body)
    except Exception:  # noqa: BLE001
        _tlife = None

    # Manager front door — answer conversation, route tasks. Skipped only for a
    # blocked-continuation answer (which must continue the task, not be re-chatted).
    if not chat_state.get("blocked_item_id"):
        # Live status while the Manager thinks — the label is driven by the REAL
        # phase (classify → reply / hand-off), not a timed cosmetic rotation, so
        # it honestly reflects what the Manager is doing. No-op on non-TTY.
        from ..cli.live_status import LiveStatus
        from ..cli.roles_status import ROLE_COLOR_BOLD, resolve_role_config

        # The manager's ACTUAL configured backend — never hardcode "Codex" here;
        # it silently lied whenever the operator was on claude/copilot (this is
        # only the pre-first-event placeholder anyway; a real on_phase update
        # below permanently replaces it — see LiveStatus._current_label).
        _manager_backend_label = resolve_role_config(
            "manager", env=os.environ,
        ).backend_label

        # Print a TRUTHFUL "roles" snapshot above the spinner, marking Manager
        # active from the first phase onward (see _render_live_role_overlay's
        # docstring for why: the SELF quick-reply path never journals to
        # events.jsonl, so without this override the panel printed before this
        # prompt keeps claiming every role "idle" for the WHOLE live turn —
        # not just stale, a direct on-screen contradiction of the spinner
        # right below it). Gated by the same _live_cockpit_enabled() flag as
        # the rest of the live-panel feature (an extension of it, not a
        # separate one) plus the usual TTY/theme guards — never shown on
        # piped output, and always cleaned up in `finally` even if the
        # Manager's turn raises or is Ctrl-C'd.
        _overlay_lines = 0
        if (
            _live_cockpit_enabled()
            and theme is not None and theme.enabled
            and sys.stdout.isatty()
        ):
            try:
                _overlay_life_dir = _life_dir_for(mem)
                _overlay = _render_live_role_overlay(
                    _overlay_life_dir, theme,
                    active_role="manager", label="Deciding SELF / TEAM…",
                )
                if _overlay:
                    sys.stdout.write(_overlay + "\n")
                    sys.stdout.flush()
                    _overlay_lines = _overlay.count("\n") + 1
            except Exception:  # noqa: BLE001 — this overlay must never break chat
                _overlay_lines = 0

        reply = None
        try:
            with LiveStatus(
                "Deciding SELF / TEAM…",
                theme=theme,
                phrases=[
                    "Deciding SELF / TEAM…",
                    f"Waiting for {_manager_backend_label}'s first event…",
                ],
                phrase_interval=10.0,
                accent=ROLE_COLOR_BOLD.get("manager", "magenta"),
            ) as _live:
                # Retint the spinner glyph to whichever role drove this update (the
                # SAME hue it wears in the banner / /roles panel / follow feed) —
                # the label text itself stays plain, so there is no risk of a
                # nested ANSI reset truncating its styling.
                def _on_phase(label: str, *, role: str | None = None) -> None:
                    accent = ROLE_COLOR_BOLD.get((role or "").strip().lower())
                    if accent:
                        _live.update_role(accent, label)
                    else:
                        _live.update(label)

                reply = manager_triage(
                    mem,
                    body,
                    chat_state,
                    on_phase=_on_phase,
                    root_task_id=root_task_id,
                )
        finally:
            # Erase the overlay (LiveStatus already erased its OWN line on
            # exit — it uses "\r\x1b[2K", which clears in place without
            # moving the cursor to a new row — so the cursor is sitting
            # exactly _overlay_lines rows below the overlay's first row).
            if _overlay_lines:
                try:
                    sys.stdout.write(f"\r\x1b[{_overlay_lines}A\x1b[J")
                    sys.stdout.flush()
                except Exception:  # noqa: BLE001
                    pass
        if reply is not None:
            line = (("  " + theme.cyan("argus") + theme.dim(" ↳ ") + reply)
                    if theme is not None else f"  argus ↳ {reply}")
            print(line, flush=True)
            if _tlife is not None:
                _transcript.append_turn(_tlife, "argus", reply)
            return

        # TEAM work reached this point — let the Manager judge whether it is
        # open-ended (STANDING) and should be auto-armed as a continuous
        # campaign, so the operator never has to manually pass
        # --continuous --objective for work like "optimize as many X as
        # possible". Only relevant the FIRST time a session goes standing;
        # once continuous, every later task already flows through the
        # existing continuous branch below unchanged.
        if not continuous:
            continuous = _maybe_auto_promote_to_continuous(
                mem,
                body,
                chat_state,
                theme,
                root_task_id=root_task_id,
            )

    item, daemon_alive, daemon_pid = enqueue_mission(
        mem, body, chat_state, iterate=iterate, max_cycles=max_cycles,
        budget=budget, theme=theme, root_task_id=root_task_id)
    life_dir = _life_dir_for(mem)
    if _tlife is not None:
        _transcript.append_turn(
            _tlife, "argus",
            f"→ queued for the daemon (task {getattr(item, 'id', '') or '?'})",
        )

    if continuous:
        if not daemon_alive:
            print(
                _no_executor_notice(
                    getattr(item, "id", "planner-objective"),
                    theme,
                ),
                flush=True,
            )
            if chat_state.get("daemon_autostart_error"):
                msg = str(chat_state.pop("daemon_autostart_error"))
                print(
                    theme.yellow("   " + msg) if theme is not None else f"   {msg}",
                    flush=True,
                )
            return
        queued = (
            "objective handed to Planner — "
            f"daemon (pid {daemon_pid}) planning/executing "
            f"(continuous on backend={chat_state.get('backend')})"
        )
        print(theme.gray(queued) if theme is not None else queued, flush=True)
        # Multi-agent live view: pin the four-role panel and refresh it in place
        # (interactive TTY). Falls back to the scrolling event tail when piped /
        # non-interactive so tests and logs are unchanged.
        if sys.stdout.isatty() and _live_follow_enabled():
            final = follow_mission_live_roles(
                life_dir, None, theme=theme,
                header="following daemon (Ctrl-C stops observing; daemon keeps running)…",
            )
        else:
            final = _follow_events_stream(
                life_dir,
                theme=theme,
                header="following daemon (Ctrl-C to stop observing; daemon keeps running)…",
                until_item_id=None,
                until_first_completion=True,
            )
        if final is not None:
            _record_mission_outcome(chat_state, final)
            _surface_blocked_question(chat_state, theme)
        return

    if not daemon_alive:
        # No executor: do NOT print "daemon executing" (a lie) and do NOT enter
        # the 600s event-tail wait (which would just freeze on a log that never
        # grows — the original "卡住" symptom). Tell the operator the truth and
        # the one command that fixes it; the task is safely queued meanwhile.
        print(_no_executor_notice(item.id, theme), flush=True)
        if chat_state.get("daemon_autostart_error"):
            msg = str(chat_state.pop("daemon_autostart_error"))
            print(
                theme.yellow("   " + msg) if theme is not None else f"   {msg}",
                flush=True,
            )
        return

    queued = (
        f"queued {item.id} — daemon (pid {daemon_pid}) executing  "
        f"(Ctrl-C stops observing, not the task)"
    )
    print(theme.gray(queued) if theme is not None else queued, flush=True)

    if sys.stdout.isatty() and _live_follow_enabled():
        final = follow_mission_live_roles(life_dir, item.id, theme=theme)
    else:
        final = tail_mission_events(life_dir, item.id, theme=theme)
    if final is not None:
        _record_mission_outcome(chat_state, final)
        for line in _format_completion(final, item.id, life_dir):
            print(theme.dim(line) if theme is not None else line, flush=True)
        _surface_blocked_question(chat_state, theme)
    else:
        note = (
            f"{item.id} still running (no completion within the observe window) "
            f"— use /status to check on the daemon."
        )
        print(theme.gray(note) if theme is not None else note, flush=True)


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
    """Lightweight status print (mirrors `argus-skill life status` output)."""
    from ..apps._inbox import count_pending_inbox_messages
    from ..daemon.life_worker import ContinuousConfigState, read_continuous_state

    # Fixed label width so every "label: value" row's colon lines up in one
    # column instead of drifting per-line (each print used to hand-pick its
    # own padding, which fell out of sync as fields were added over time).
    _LBL = 10

    identity = mem.identity.read().strip()
    if identity:
        first = identity.splitlines()[0][:80]
        print(f"{'identity':<{_LBL}}: {first}{'…' if len(identity) > 80 else ''}")
    else:
        print(f"{'identity':<{_LBL}}: (empty)")
    # Every backlog item whose mission ended "blocked" on a reviewer question
    # the operator hasn't answered yet (BacklogItem.pending_question — set by
    # life/supervisor/_core.py, cleared by enqueue_mission once answered).
    # Surfaced FIRST and unconditionally (not tucked behind a live REPL
    # session's chat_state) so it is visible after a fresh `argus` launch,
    # after a daemon-only run, or when more than one item is waiting — none
    # of which the old chat_state-only ``blocked_question`` could show.
    pending_qs = [it for it in mem.backlog.all() if (it.pending_question or "").strip()]
    if pending_qs:
        print(f"{'questions':<{_LBL}}: {len(pending_qs)} awaiting your answer")
        for it in pending_qs[:5]:
            print(f"  ❓ ({it.id}) {it.pending_question.strip()[:160]}")
        if len(pending_qs) > 5:
            print(f"  … {len(pending_qs) - 5} more")
    pending = mem.backlog.pending()
    print(f"{'backlog':<{_LBL}}: {len(pending)} pending  "
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
    print(f"{'continuous':<{_LBL}}: {'on' if cont.enabled else 'off'}")
    print(f"{'inbox':<{_LBL}}: {count_pending_inbox_messages(mem.project.root)} pending")
    _SUBLBL = 11  # fits "done_reason", the longest of this nested trio
    if cont.objective:
        print(f"  {'objective':<{_SUBLBL}}: {cont.objective}")
    if cont.done_reason:
        print(f"  {'done_reason':<{_SUBLBL}}: {cont.done_reason}")
    if cont.done_at:
        print(f"  {'done_at':<{_SUBLBL}}: {cont.done_at}")
    if chat_state is not None:
        started = chat_state.get("session_started_s")
        if started is not None:
            uptime = time.monotonic() - started
            count = int(chat_state.get("mission_count", 0))
            total = float(chat_state.get("total_elapsed_s", 0.0))
            last_e = chat_state.get("last_elapsed_s")
            line = f"{'timing':<{_LBL}}: uptime {_format_elapsed(uptime)}"
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
            # ds.backend is "codex" (a real CLI backend — historically named
            # after the first one supported) vs "memory" (deterministic test
            # double). It is NOT which real CLI is actually configured per
            # role (that's ARGUS_SKILL_RUNNER_BACKEND, shown correctly in
            # /roles). Printing the raw "codex" here reads as "this daemon is
            # calling the Codex CLI" even when running claude/copilot, which
            # contradicts /roles right next to it — so relabel the real-mode
            # case instead of echoing the misleading literal string.
            backend_label = (
                "memory (test)" if ds.backend == "memory" else "live — see /roles"
            )
            print(f"{'daemon':<{_LBL}}: alive (pid {ds.pid}, up {up}, "
                  f"backend {backend_label})")
        else:
            print(f"{'daemon':<{_LBL}}: not running   (start with `/daemon start`)")
            tid = chat_state.get("last_thread_id") if chat_state is not None else None
            if tid:
                print(f"{'codex':<{_LBL}}: reusing the previous session  (/reset to start fresh)")
    # Compact four-role action line. Backend/model/effort live in /roles, not in
    # every status snapshot. Fail-soft (never breaks /status).
    try:
        from ..cli.roles_status import resolve_all_roles, role_activity
        acts = role_activity(_life_dir_for(mem))
        active = next((r for r in ("engineer", "reviewer", "planner", "manager")
                       if acts.get(r) and acts[r].active), None)
        cfgs = {c.role: c for c in resolve_all_roles()}
        if active and active in cfgs:
            print(f"{'roles':<{_LBL}}: ● {active} · {acts[active].label[:40]}"
                  f"   (/roles for details)")
        else:
            print(f"{'roles':<{_LBL}}: idle   (/roles for details)")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Help screen
# ---------------------------------------------------------------------------

def _roles_cmd(mem: Any, chat_state: dict[str, Any], arg_text: str = "") -> None:
    """`/roles` — show each role's backend / model / reasoning-effort and what
    it is doing right now. ``/roles watch`` live-refreshes until Ctrl-C."""
    theme = chat_state.get("theme")
    from ..cli.roles_status import render_roles_snapshot
    life_dir = _life_dir_for(mem)

    def _daemon_right() -> str:
        try:
            from ..daemon.life_worker import read_daemon_status
            st = read_daemon_status(mem.project.root)
            if getattr(st, "alive", False) and getattr(st, "pid", None):
                s = f"● daemon {st.pid}"
                return theme.bold_green(s) if theme is not None else s
            s = "○ no daemon"
            return theme.gray(s) if theme is not None else s
        except Exception:  # noqa: BLE001
            return ""

    watch = arg_text.strip().lower() in ("watch", "-w", "--watch", "live", "-f")
    if not watch:
        width = theme.live_width() if theme is not None else 80
        print(render_roles_snapshot(life_dir, theme, width=width,
                                    header_right=_daemon_right(),
                                    show_config=True), flush=True)
        return

    # Live refresh: redraw the panel in place every ~1s until Ctrl-C. Only when
    # attached to a TTY (else fall back to a single snapshot).
    if not sys.stdout.isatty():
        width = theme.live_width() if theme is not None else 80
        print(render_roles_snapshot(life_dir, theme, width=width), flush=True)
        return
    hint = "Live · press Ctrl-C to return, then type" if theme is not None else "live · Ctrl-C to stop, then type"
    print(theme.dim(hint) if theme is not None else hint, flush=True)
    prev_lines = 0
    try:
        sys.stdout.write("\x1b[?25l")  # hide cursor during redraw
        while True:
            # Re-queried every redraw (see ``Theme.live_width``) — ``/roles
            # watch`` can sit open for a long time, well past any terminal
            # resize, and a width fixed at function entry would wrap this
            # padded header the moment it disagrees with the real terminal.
            width = theme.live_width() if theme is not None else 80
            panel = render_roles_snapshot(life_dir, theme, width=width,
                                          header_right=_daemon_right(),
                                          show_config=True)
            n = panel.count("\n") + 1
            if prev_lines:
                # cursor up, then clear from cursor to end of screen so no stale
                # (possibly wrapped) rows are left behind → no duplicate header.
                sys.stdout.write(f"\x1b[{prev_lines}A\x1b[J")
            sys.stdout.write(panel + "\n")
            sys.stdout.flush()
            prev_lines = n
            time.sleep(1.0)
    except KeyboardInterrupt:
        print()
        return
    finally:
        try:
            sys.stdout.write("\x1b[?25h")  # restore cursor
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass


def _doctor_cmd(mem: Any, chat_state: dict[str, Any], global_root: Any) -> None:
    """`/doctor` — diagnose why no daemon / why auto-spawn failed, with fixes."""
    theme = chat_state.get("theme")
    try:
        from ..tools.doctor import render_report, run_diagnostics

        checks = run_diagnostics(mem.project.root, global_root=global_root)
        out = render_report(checks, theme)
        out = _rewrite_cockpit_daemon_fix(out)
        tail = _recent_daemon_log_tail(mem.project.root)
        if tail:
            out = out.rstrip() + "\n\n" + tail
    except Exception as exc:  # noqa: BLE001 — doctor must never crash the REPL
        out = f"/doctor failed: {type(exc).__name__}: {exc}"
    print(out, flush=True)


def _rewrite_cockpit_daemon_fix(text: str) -> str:
    """Doctor runs inside the cockpit; prefer the cockpit-native start command."""
    return text.replace(
        "run: argus-skill --daemon",
        "run: /daemon start  (or argus-skill --daemon from another shell)",
    )


def _recent_daemon_log_tail(
    life_dir: Path | str,
    *,
    max_age_seconds: float = 900.0,
) -> str:
    path = Path(life_dir) / "daemon.log"
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return ""
    if age > max_age_seconds:
        return ""
    return _daemon_log_tail(life_dir)


def _plan_cmd(mem: Any, chat_state: dict[str, Any], objective: str) -> None:
    """`/plan <objective>` — preview a step plan, then optionally queue it.

    Codex/Claude-Code/Cursor parity: see HOW the agent would approach the work
    before anything reaches the backlog. Drafting the plan never executes work.
    """
    theme = chat_state.get("theme")
    if not objective.strip():
        msg = "usage: /plan <objective>  — preview a step plan before queuing it"
        print(theme.gray(msg) if theme is not None else msg, flush=True)
        return
    runner = _ensure_manager_runner(chat_state, mem)
    from ..cli.live_status import LiveStatus
    from ..manager import plan_mode
    with LiveStatus(
        "drafting a plan…",
        theme=theme,
        phrases=["Understanding the goal…", "Breaking down steps…", "Drafting a plan…"],
    ):
        plan = plan_mode.draft_plan(runner, objective)
    print(plan_mode.render_plan(plan, theme), flush=True)
    if getattr(plan, "error", ""):
        note = "plan was not queued because drafting failed; fix the runner or rephrase the objective and try /plan again."
        print(theme.gray(note) if theme is not None else note, flush=True)
        return
    # Ask before queuing — the whole point of a preview is approval.
    prompt = "queue this plan as a task? [y/N] "
    try:
        ans = input(theme.cyan(prompt) if theme is not None else prompt)
    except (EOFError, KeyboardInterrupt):
        ans = ""
    if ans.strip().lower() in ("y", "yes"):
        _free_text_cmd(mem, objective, chat_state)
    else:
        note = "plan not queued (nothing executed). Edit the objective and /plan again, or just type it to run."
        print(theme.gray(note) if theme is not None else note, flush=True)


def _daemons_cmd(chat_state: dict[str, Any], global_root: Any, current_root: Any) -> None:
    """`/daemons` — list every live daemon across all projects (cross-project)."""
    theme = chat_state.get("theme")
    try:
        from ..apps.cli import _format_short_duration
        from ..core.session import live_daemon_sessions
        from ..daemon.life_worker import read_daemon_status

        sessions = live_daemon_sessions(global_root)
    except Exception as exc:  # noqa: BLE001
        print(f"/daemons failed: {type(exc).__name__}: {exc}", flush=True)
        return
    if not sessions:
        msg = "no live daemons running. Start one: argus-skill --daemon"
        print(theme.gray(msg) if theme is not None else msg, flush=True)
        return
    print(theme.bold("live daemons") if theme is not None else "live daemons", flush=True)
    for s in sessions:
        proj = Path(global_root) / "projects" / s.id
        try:
            st = read_daemon_status(proj)
            up = _format_short_duration(st.uptime_seconds or 0.0)
            pid = st.pid
        except Exception:  # noqa: BLE001
            up, pid = "?", "?"
        name = s.display_name or (s.objective[:36] if s.objective else "(unnamed)")
        here = "  (this session)" if str(proj) == str(current_root) else ""
        line = f"  ● {s.id}  pid {pid}  up {up}  ·  {name}{here}"
        print(theme.green(line) if theme is not None else line, flush=True)
    tip = "attach to one:  /attach <id>   ·   or relaunch:  argus-skill --resume <id>"
    print(theme.dim(tip) if theme is not None else tip, flush=True)


def _attach_cmd(chat_state: dict[str, Any], global_root: Any, target: str) -> None:
    """`/attach <id>` — live-follow another project's daemon (read-only tail)."""
    theme = chat_state.get("theme")
    if not target.strip():
        msg = "usage: /attach <session-id>   (see /daemons)"
        print(theme.gray(msg) if theme is not None else msg, flush=True)
        return
    target = target.strip()
    try:
        from ..core.session import live_daemon_sessions

        live = live_daemon_sessions(global_root)
    except Exception:  # noqa: BLE001
        live = []
    match = next((s.id for s in live if s.id == target), None) \
        or next((s.id for s in live if s.id.startswith(target)), None)
    if match is None:
        msg = f"no live daemon matches {target!r}. See /daemons."
        print(theme.yellow(msg) if theme is not None else msg, flush=True)
        return
    proj = Path(global_root) / "projects" / match
    print((theme.gray if theme is not None else str)(
        f"following daemon {match} (Ctrl-C to stop observing; it keeps running)…"
    ), flush=True)
    _follow_events_stream(proj, theme=theme, header=None)


def _print_transcript(
    life_dir: Any, theme: Any, *, limit: int | None = None, header: str | None = None
) -> bool:
    """Print a session's saved operator↔argus conversation. Returns True if any."""
    from ..core import transcript as _transcript

    turns = _transcript.read_turns(life_dir, limit=limit)
    if not turns:
        return False
    if header:
        print(theme.bold(header) if theme is not None else header, flush=True)
    for t in turns:
        text = str(t.get("text") or "").strip()
        if not text:
            continue
        if t.get("role") == "operator":
            tag = theme.cyan("you ›") if theme is not None else "you ›"
        else:
            tag = (theme.cyan("argus") + theme.dim(" ↳")) if theme is not None else "argus ↳"
        print(f"  {tag} {text}", flush=True)
    return True


def _resume_cmd(
    mem: Any, chat_state: dict[str, Any], global_root: Any, rest_text: str
) -> None:
    """`/resume` — switch into the PREVIOUS conversation (the most recent other
    session with saved chat). ``/resume <id>`` switches into a specific session;
    ``/resume list`` shows all resumable sessions (labelled by first message).

    Switching re-execs ``argus-skill --resume <id>`` (after releasing the
    singleton lock), so it is a REAL switch — session bundle, daemon
    association, cwd, banner + conversation replay — identical to relaunching
    from the shell, not a read-only preview."""
    theme = chat_state.get("theme")
    _gray = theme.gray if theme is not None else (lambda s: s)
    from ..core import transcript as _transcript
    from ..core.session import list_sessions, live_daemon_sessions

    def _projdir(sid: str) -> Path:
        return Path(global_root) / "projects" / sid

    def _label(s: Any) -> str:
        if s.display_name:
            return s.display_name
        if s.objective:
            return s.objective[:50]
        first = _transcript.first_operator_text(_projdir(s.id)).strip()
        first = " ".join(first.split())
        return (first[:50] + ("…" if len(first) > 50 else "")) if first else "(unnamed)"

    try:
        sessions = list_sessions(global_root, include_empty=False)
        live = {s.id for s in live_daemon_sessions(global_root)}
    except Exception:  # noqa: BLE001
        sessions, live = [], set()

    # Current session id — excluded when defaulting to "the previous conversation".
    try:
        cur_sid = Path(_life_dir_for(mem)).name if mem is not None else None
    except Exception:  # noqa: BLE001
        cur_sid = None

    def _switch_to(sid: str) -> None:
        if sid == cur_sid:
            print(_gray(f"Already in session {sid} — nothing to switch to."), flush=True)
            return
        meta = next((s for s in sessions if s.id == sid), None)
        label = _label(meta) if meta is not None else ""
        tail = f"  ·  {label}" if label and label != "(unnamed)" else ""
        # Flag the switch; the REPL loop leaves cleanly and run_manager_repl
        # re-execs `argus-skill --resume <sid>` once the singleton lock is
        # released — a real switch (daemon association + cwd + replay), not a
        # read-only preview.
        chat_state["switch_to_session"] = sid
        msg = f"↩ switching to session {sid}{tail} …"
        print((theme.cyan(msg) if theme is not None else msg), flush=True)

    def _show_list() -> None:
        if not sessions:
            print(_gray("No resumable sessions yet."), flush=True)
            return
        now = time.time()
        print(theme.bold("Resumable sessions") if theme is not None else "resumable sessions:", flush=True)
        for s in sessions[:20]:
            age = max(0.0, now - (s.last_active or 0))
            age_s = (f"{int(age // 86400)}d" if age >= 86400
                     else f"{int(age // 3600)}h" if age >= 3600
                     else f"{int(age // 60)}m")
            mark = "● live" if s.id in live else "      "
            print(_gray(f"  {mark}  {s.id}  {age_s:>4} ago  ·  {_label(s)}"), flush=True)
        print(_gray(
            "Switch into one:  /resume <id>   ·   or from the shell:  argus-skill --resume <id>"
        ), flush=True)

    target = (rest_text or "").strip()

    if target.lower() in ("list", "ls", "all"):
        _show_list()
        return

    if not target:
        # Default: switch into the PREVIOUS conversation — the most recent OTHER
        # session that actually holds a saved conversation.
        prior = next(
            (s.id for s in sessions
             if s.id != cur_sid and _transcript.has_transcript(_projdir(s.id))),
            None,
        )
        if prior is None:
            print(_gray("No previous conversation yet — `/resume list` to see all sessions."), flush=True)
            return
        _switch_to(prior)
        return

    match = next((s.id for s in sessions if s.id == target), None) \
        or next((s.id for s in sessions if s.id.startswith(target)), None)
    if match is None:
        msg = f"no session matches {target!r} — `/resume list` to see them."
        print(theme.yellow(msg) if theme is not None else msg, flush=True)
        return
    _switch_to(match)


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
    "_maybe_name_session",
    "_with_manager_spinner",
    "manager_triage",
    "_record_mission_outcome",
    "_no_executor_notice",
    "_surface_blocked_question",
    "dispatch_command",
]
