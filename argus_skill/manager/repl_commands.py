"""Slash-command controller shared by the line REPL and TUI."""

from __future__ import annotations

import shlex
import sys
from typing import Any

from ..apps._life_actions import render_reset_cmd, stop_iteration
from .repl_help import _closest_slash_command


def dispatch_command(
    line: str,
    raw: str,
    mem: Any,
    chat_state: dict[str, Any],
    global_root: Any,
    theme: Any,
) -> str | None:
    """Dispatch one slash command or free-text turn through REPL handlers."""
    from . import repl

    alias, alias_note = repl._cockpit_cli_alias(line)
    if alias_note:
        print(theme.gray(alias_note) if theme is not None else alias_note)
    if alias is None and alias_note is not None:
        return None
    if alias is not None:
        line = alias
        raw = alias

    if not line.startswith("/"):
        repl._free_text_cmd(mem, raw, chat_state)
        return None
    try:
        tokens = shlex.split(line)
    except ValueError as exc:
        print(theme.red(f"parse error: {exc}"))
        return None
    cmd = tokens[0].lower()
    rest = tokens[1:]
    rest_text = line[len(tokens[0]):].lstrip()
    if cmd in ("/help", "/commands"):
        sys.stdout.write(repl._render_help(theme))
        sys.stdout.flush()
        return None
    if cmd == "/status":
        repl._status_cmd(mem, chat_state)
        return None
    if cmd == "/roles":
        repl._roles_cmd(mem, chat_state, rest_text)
        return None
    if cmd == "/doctor":
        repl._doctor_cmd(mem, chat_state, global_root)
        return None
    if cmd == "/daemon":
        repl._daemon_cmd(mem, rest_text, chat_state)
        return None
    if cmd == "/daemons":
        repl._daemons_cmd(chat_state, global_root, mem.project.root)
        return None
    if cmd == "/attach":
        repl._attach_cmd(chat_state, global_root, rest_text)
        return None
    if cmd == "/resume":
        repl._resume_cmd(mem, chat_state, global_root, rest_text)
        return None
    if cmd == "/plan":
        repl._plan_cmd(mem, chat_state, rest_text)
        return None
    if cmd == "/start":
        repl._continuous_cmd(mem, f"start {rest_text}".strip(), chat_state)
        return None
    if cmd == "/continuous":
        repl._continuous_cmd(mem, rest_text, chat_state)
        return None
    if cmd == "/identity":
        repl._identity_cmd(mem, rest, rest_text)
        return None
    if cmd == "/backlog":
        include_all = bool(rest) and rest[0].lower() == "all"
        repl._backlog_list_cmd(mem, include_all=include_all)
        return None
    if cmd == "/add":
        from .front_door import ManagerHandoffError

        if not rest_text:
            print(theme.gray(
                "usage: /add <objective>  "
                "[--once] [--cycles=N] [--budget=$X]"
            ))
            return None
        cfg = chat_state.get("config", {})
        iterate, max_cycles, budget, body = repl.parse_add_flags(
            rest_text,
            defaults=cfg,
        )
        if not body:
            print(theme.gray("/add: empty objective after flags"))
            return None
        try:
            repl.manager_bounded_handoff(
                mem,
                body,
                chat_state,
                lambda execution_body, division: repl._add_only(
                    mem,
                    execution_body,
                    iterate=iterate,
                    iteration_max_cycles=max_cycles,
                    iteration_budget_usd=budget,
                ),
                theme=theme,
                ensure_runner=repl._ensure_manager_runner,
            )
        except ManagerHandoffError as exc:
            print(theme.red(str(exc)) if theme is not None else str(exc))
            return None
        return None
    if cmd == "/stop":
        if not rest:
            print(theme.gray("usage: /stop <item_id>"))
            return None
        print(stop_iteration(mem, rest[0]))
        return None
    if cmd in ("/done", "/skip", "/rm"):
        if not rest:
            print(theme.gray(f"usage: {cmd} <item_id>"))
            return None
        repl._status_change_cmd(mem, cmd, rest[0])
        return None
    if cmd == "/journal":
        n = 10
        if rest:
            try:
                n = int(rest[0])
            except ValueError:
                print(theme.gray(f"usage: /journal [N]  (got: {rest[0]!r})"))
                return None
        repl._journal_tail_cmd(mem, n)
        return None
    if cmd == "/note":
        if not rest_text:
            print(theme.gray("usage: /note <text>"))
            return None
        print(theme.gray(repl.append_note(mem, rest_text)))
        return None
    if cmd in ("/nudge", "/inject", "/notify"):
        if not rest_text:
            print(theme.gray(
                "usage: /nudge <message>  "
                "(one line, spliced into the next engineer round)"
            ))
            return None
        from ..apps._inbox import queue_inbox_message

        queue_inbox_message(mem.project.root, rest_text, source="repl.nudge")
        print(theme.gray(
            f"nudge queued ({len(rest_text)} chars) → next mission round "
            f"will see it as operator guidance"
        ))
        return None
    if cmd == "/backend":
        repl._backend_cmd(rest, chat_state)
        return None
    if cmd == "/config":
        repl._config_cmd(rest, chat_state, life_dir=mem.project.root)
        return None
    if cmd in ("/verbose", "/quiet"):
        print(theme.gray(
            "verbose is always on now (the toggle was removed). "
            "every event is rendered."
        ))
        return None
    if cmd == "/reset":
        print(theme.gray(render_reset_cmd(chat_state)))
        return None
    if cmd == "/run":
        repl._run_cmd(mem, chat_state)
        return None
    if cmd == "/skills":
        repl._skills_cmd(rest)
        return None
    hint = _closest_slash_command(cmd)
    if hint:
        print(theme.gray(
            f"unknown command: {cmd}  — did you mean {hint}?  "
            f"(/help lists every command)"
        ))
    else:
        print(theme.gray(f"unknown command: {cmd}  (/help lists every command)"))
    return None


__all__ = ["dispatch_command"]
