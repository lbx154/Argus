"""Free-text Manager dispatch and daemon observation flow."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Callable

from ..apps._life_actions import parse_add_flags
from ..life import BacklogItem


@dataclass(frozen=True)
class FreeTextHooks:
    maybe_handle_config_intent: Callable[..., bool]
    life_dir_for: Callable[[Any], Any]
    render_live_role_overlay: Callable[..., str]
    live_cockpit_enabled: Callable[[], bool]
    manager_triage: Callable[..., str | None]
    maybe_auto_promote_to_continuous: Callable[..., bool]
    enqueue_mission: Callable[..., tuple[Any | None, bool, int | None]]
    no_executor_notice: Callable[[str, Any], str]
    live_follow_enabled: Callable[[], bool]
    follow_mission_live_roles: Callable[..., dict[str, Any] | None]
    follow_events_stream: Callable[..., dict[str, Any] | None]
    record_mission_outcome: Callable[[dict[str, Any], dict[str, Any]], None]
    surface_blocked_question: Callable[[dict[str, Any], Any], None]
    tail_mission_events: Callable[..., dict[str, Any] | None]
    format_completion: Callable[..., list[str]]


def dispatch_free_text(
    mem: Any,
    text: str,
    chat_state: dict[str, Any],
    *,
    hooks: FreeTextHooks,
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
    if hooks.maybe_handle_config_intent(
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
        _tlife = hooks.life_dir_for(mem)
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
        # right below it). Gated by the same hooks.live_cockpit_enabled() flag as
        # the rest of the live-panel feature (an extension of it, not a
        # separate one) plus the usual TTY/theme guards — never shown on
        # piped output, and always cleaned up in `finally` even if the
        # Manager's turn raises or is Ctrl-C'd.
        _overlay_lines = 0
        if (
            hooks.live_cockpit_enabled()
            and theme is not None and theme.enabled
            and sys.stdout.isatty()
        ):
            try:
                _overlay_life_dir = hooks.life_dir_for(mem)
                _overlay = hooks.render_live_role_overlay(
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

                reply = hooks.manager_triage(
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
            continuous = hooks.maybe_auto_promote_to_continuous(
                mem,
                body,
                chat_state,
                theme,
                root_task_id=root_task_id,
            )

    item, daemon_alive, daemon_pid = hooks.enqueue_mission(
        mem, body, chat_state, iterate=iterate, max_cycles=max_cycles,
        budget=budget, theme=theme, root_task_id=root_task_id)
    life_dir = hooks.life_dir_for(mem)
    if _tlife is not None:
        _transcript.append_turn(
            _tlife, "argus",
            f"→ queued for the daemon (task {getattr(item, 'id', '') or '?'})",
        )

    if continuous:
        if not daemon_alive:
            print(
                hooks.no_executor_notice(
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
        if sys.stdout.isatty() and hooks.live_follow_enabled():
            final = hooks.follow_mission_live_roles(
                life_dir, None, theme=theme,
                header="following daemon (Ctrl-C stops observing; daemon keeps running)…",
            )
        else:
            final = hooks.follow_events_stream(
                life_dir,
                theme=theme,
                header="following daemon (Ctrl-C to stop observing; daemon keeps running)…",
                until_item_id=None,
                until_first_completion=True,
            )
        if final is not None:
            hooks.record_mission_outcome(chat_state, final)
            hooks.surface_blocked_question(chat_state, theme)
        return

    if not daemon_alive:
        # No executor: do NOT print "daemon executing" (a lie) and do NOT enter
        # the 600s event-tail wait (which would just freeze on a log that never
        # grows — the original "卡住" symptom). Tell the operator the truth and
        # the one command that fixes it; the task is safely queued meanwhile.
        print(hooks.no_executor_notice(item.id, theme), flush=True)
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

    if sys.stdout.isatty() and hooks.live_follow_enabled():
        final = hooks.follow_mission_live_roles(life_dir, item.id, theme=theme)
    else:
        final = hooks.tail_mission_events(life_dir, item.id, theme=theme)
    if final is not None:
        hooks.record_mission_outcome(chat_state, final)
        for line in hooks.format_completion(final, item.id, life_dir):
            print(theme.dim(line) if theme is not None else line, flush=True)
        hooks.surface_blocked_question(chat_state, theme)
    else:
        note = (
            f"{item.id} still running (no completion within the observe window) "
            f"— use /status to check on the daemon."
        )
        print(theme.gray(note) if theme is not None else note, flush=True)


__all__ = ["FreeTextHooks", "dispatch_free_text"]
