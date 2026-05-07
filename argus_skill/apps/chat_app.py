"""``argus-skill chat`` — interactive console REPL for the running daemon.

Tails ``state-dir/outbox.jsonl`` in a background thread (printing
formatted events live, with the same icon-based formatter Telegram uses)
while a foreground readline loop accepts the same slash-commands the
Telegram bot accepts (``/run``, ``/inject``, ``/skip``, ``/status``,
``/verbose``, ``/quiet``, ``/stop``, ``/help``). Plain text without ``/``
is buffered as an inject (matching the Telegram default).

The REPL is a *client* — closing it does NOT stop the daemon, and you
can have multiple chat clients open at once. Type ``/exit`` (or Ctrl-D)
to leave; the daemon keeps running.
"""
from __future__ import annotations

import argparse
import json
import readline  # noqa: F401 — importing enables line-editing for input()
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ..daemon.bus import BusCommand, JsonlCommandBus
from ..cli import default_theme, render_event_for_terminal
from ..cli.theme import Theme
from ..telegram.notifier import (
    _USER_FACING_EVENTS,
    _VERBOSE_EVENTS,
    format_event_message,
)
from ..telegram.poller import parse_command_text
from ._input_helpers import read_pasted_message

_HELP_TEXT = (
    "/run <task>          start a task (queue daemon)\n"
    "/inject <text>       add hint for the current round (or buffer for next /run)\n"
    "/skip                skip the currently-running task / approach\n"
    "/status              ask the daemon to dump its status\n"
    "/verbose             show internal lifecycle events (round.start, match.info, …)\n"
    "/quiet               hide internal events (default)\n"
    "/stop                shut the daemon down\n"
    "Mission mode (LoopEngine daemon):\n"
    "  /review <criteria>   set/append the reviewer's grading criteria\n"
    "  /plan <direction>    guide the planner's next follow-up\n"
    "  /mode auto|off|record  switch plan mode (auto = planner active; chaining gated by --auto-follow-up)\n"
    "/exit | Ctrl-D       leave the chat (daemon keeps running)\n"
    "<plain text>         buffered as an inject hint for the next round\n"
)


def add_chat_subcommand(sub: argparse._SubParsersAction) -> None:
    chat_p = sub.add_parser(
        "chat",
        help="interactive REPL talking to a running daemon (events + commands in one terminal)",
    )
    chat_p.add_argument("--state-dir", default=".argus-skill",
                        help="daemon state-dir (where inbox.jsonl + outbox.jsonl live)")
    chat_p.add_argument("--verbose", dest="verbose", action="store_true", default=None,
                        help="start in verbose mode (show internal events)")
    chat_p.add_argument("--quiet", dest="verbose", action="store_false",
                        help="start in quiet mode (only user-facing events)")
    chat_p.add_argument("--no-plain-text-inject", action="store_true",
                        help="drop plain text instead of buffering it as /inject")
    chat_p.add_argument("--from-start", action="store_true",
                        help="replay the entire outbox, not just events from now on")
    chat_p.add_argument("--color", dest="color", action="store_true", default=None,
                        help="force ANSI colors on (auto-detect by default)")
    chat_p.add_argument("--no-color", dest="color", action="store_false",
                        help="disable ANSI colors (auto-detect by default)")


def cmd_chat(args: argparse.Namespace) -> int:
    state = Path(args.state_dir).expanduser().resolve()
    if not state.is_dir():
        print(f"state-dir {state} not found — is the daemon running?", file=sys.stderr)
        return 2
    inbox = state / "inbox.jsonl"
    outbox = state / "outbox.jsonl"
    status_path = state / "status.json"

    theme = Theme.auto(force=getattr(args, "color", None))

    daemon_pid: int | None = None
    detected_mode: str | None = None
    banner_kwargs: dict = {"mode": None}
    if status_path.exists():
        try:
            st = json.loads(status_path.read_text())
            daemon_pid = st.get("daemon_pid")
            mode = st.get("mode")
            detected_mode = mode
            if mode == "mission":
                banner_kwargs = {
                    "mode": "mission",
                    "mission_id": st.get("mission_id"),
                    "mission_status": st.get("mission_status"),
                    "plan_mode": st.get("plan_mode"),
                    "auto_follow_up": st.get("auto_follow_up"),
                    "objective": st.get("mission_objective"),
                    "max_rounds": st.get("max_rounds"),
                }
            else:
                banner_kwargs = {"mode": "queue"}
        except (json.JSONDecodeError, OSError) as exc:
            print(f"warning: cannot read {status_path}: {exc}", file=sys.stderr)
    else:
        print(f"warning: no status.json in {state} — daemon may not be running", file=sys.stderr)

    # Branded startup banner. When called from `argus-skill go` the
    # logo + tagline have already been printed, so we render only the
    # mission status block here.
    from .. import __version__ as _argus_version
    from ..cli.branding import render_startup_banner
    _compact = bool(getattr(args, "compact_banner", False))
    print(render_startup_banner(
        theme=theme,
        version=_argus_version,
        state_dir=str(state),
        daemon_pid=daemon_pid,
        show_logo=not _compact,
        show_hint=not _compact,
        **banner_kwargs,
    ))
    # Tri-state verbose:
    #   --verbose / --quiet → explicit;
    #   neither            → auto: mission mode = on, queue mode = off.
    if args.verbose is None:
        initial_verbose = detected_mode == "mission"
    else:
        initial_verbose = bool(args.verbose)

    bus = JsonlCommandBus(str(inbox))
    stop_event = threading.Event()
    state_lock = threading.Lock()
    verbose_local = [initial_verbose]

    def _allowed(event_type: str) -> bool:
        with state_lock:
            v = verbose_local[0]
        if v:
            return event_type in _VERBOSE_EVENTS or event_type not in _USER_FACING_EVENTS
        return event_type in _USER_FACING_EVENTS

    def _print_above_prompt(line: str) -> None:
        # Clear current input line, write the message, then redraw the
        # prompt with whatever the user has typed so far. This is the
        # standard pattern for chat REPLs that interleave async output
        # with sync input editing. Multi-line messages need each line
        # printed separately so the redrawn prompt sits below.
        try:
            buf = readline.get_line_buffer()
        except Exception:  # noqa: BLE001
            buf = ""
        sys.stdout.write("\r\033[2K" + line + "\n> " + buf)
        sys.stdout.flush()

    def _tail_outbox() -> None:
        if not outbox.exists():
            offset = 0
        elif args.from_start:
            offset = 0
        else:
            offset = outbox.stat().st_size
        while not stop_event.is_set():
            try:
                if not outbox.exists():
                    time.sleep(0.3)
                    continue
                size = outbox.stat().st_size
                if size < offset:
                    offset = 0  # rotated/truncated externally
                if size == offset:
                    time.sleep(0.2)
                    continue
                with outbox.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(offset)
                    chunk = fh.read()
                    offset = fh.tell()
                for raw in chunk.splitlines():
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        record = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    # JsonlEventSink wraps payloads as {"event": {...}}
                    # for events and {"stream": "...", "line": "..."} for
                    # raw stream lines. We only render events here.
                    if not isinstance(record, dict):
                        continue
                    event = record.get("event")
                    if not isinstance(event, dict):
                        continue
                    et = str(event.get("type", ""))
                    if not _allowed(et):
                        continue
                    _print_above_prompt(render_event_for_terminal(event, theme=theme))
            except Exception:  # noqa: BLE001
                time.sleep(0.5)

    tailer = threading.Thread(target=_tail_outbox, daemon=True)
    tailer.start()

    plain_as_inject = not args.no_plain_text_inject
    rc = 0
    try:
        while True:
            line = read_pasted_message("> ")
            if line is None:
                print()  # newline after Ctrl-D so the goodbye reads cleanly
                break
            stripped = line.strip()
            if not stripped:
                continue
            if stripped in ("/exit", "/quit", ":q", ":quit"):
                break
            if stripped in ("/help", "/commands"):
                sys.stdout.write(_HELP_TEXT)
                sys.stdout.flush()
                continue
            # For multi-line input we keep the original newlines so a
            # pasted code block / JSON body / stack trace forwards intact
            # to the daemon. ``parse_command_text`` only looks at the
            # first whitespace-separated token to detect /-commands, so
            # this preserves command-detection while letting plain text
            # carry newlines.
            payload = line if "\n" in line else stripped
            cmd = parse_command_text(text=payload, plain_text_as_inject=plain_as_inject)
            if cmd is None:
                print("(unrecognized — try /help)")
                continue
            # Mirror the verbose/quiet toggle in the local filter so the
            # user sees the effect immediately, not just on the daemon side.
            if cmd.kind == "verbose":
                with state_lock:
                    verbose_local[0] = True
            elif cmd.kind == "quiet":
                with state_lock:
                    verbose_local[0] = False
            try:
                bus.publish(BusCommand(
                    kind=cmd.kind,
                    text=cmd.text,
                    source="cli-chat",
                    ts=time.time(),
                ))
            except Exception as exc:  # noqa: BLE001
                print(f"(failed to publish: {exc})")
                rc = 1
                break
    except KeyboardInterrupt:
        print()  # newline after ^C
    finally:
        stop_event.set()

    print(f"bye — daemon (pid={daemon_pid}) keeps running")
    return rc


__all__ = ["add_chat_subcommand", "cmd_chat"]


# Module-level alias to keep type-checkers happy with the protocol-shaped
# event helpers we re-import above.
_ = (Any,)
