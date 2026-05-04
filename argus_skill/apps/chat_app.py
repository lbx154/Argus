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
from ..telegram.notifier import (
    _USER_FACING_EVENTS,
    _VERBOSE_EVENTS,
    format_event_message,
)
from ..telegram.poller import parse_command_text

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
    "  /mode auto|off|record  switch plan mode (auto = unattended chaining)\n"
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


def cmd_chat(args: argparse.Namespace) -> int:
    state = Path(args.state_dir).expanduser().resolve()
    if not state.is_dir():
        print(f"state-dir {state} not found — is the daemon running?", file=sys.stderr)
        return 2
    inbox = state / "inbox.jsonl"
    outbox = state / "outbox.jsonl"
    status_path = state / "status.json"

    daemon_pid: int | None = None
    detected_mode: str | None = None
    if status_path.exists():
        try:
            st = json.loads(status_path.read_text())
            daemon_pid = st.get("daemon_pid")
            mode = st.get("mode")
            detected_mode = mode
            if mode == "mission":
                # Mission daemon writes a different status shape.
                obj = (st.get("mission_objective") or "")[:60]
                print(
                    f"argus-skill chat → {state}\n"
                    f"  mission: id={st.get('mission_id')} "
                    f"status={st.get('mission_status')} "
                    f"plan_mode={st.get('plan_mode')}\n"
                    f"  objective: {obj}"
                )
            else:
                print(
                    f"argus-skill chat → {state}\n"
                    f"  daemon: pid={daemon_pid}  status={st.get('current_status')}  "
                    f"queue={st.get('queue_size')}  done={st.get('tasks_done')}"
                )
        except (json.JSONDecodeError, OSError) as exc:
            print(f"warning: cannot read {status_path}: {exc}", file=sys.stderr)
    else:
        print(f"warning: no status.json in {state} — daemon may not be running", file=sys.stderr)

    # Tri-state verbose:
    #   --verbose / --quiet → explicit;
    #   neither            → auto: mission mode = on, queue mode = off.
    if args.verbose is None:
        initial_verbose = detected_mode == "mission"
    else:
        initial_verbose = bool(args.verbose)

    print("type /help for commands, /exit (or Ctrl-D) to leave\n")

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
        # with sync input editing.
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
                    _print_above_prompt(format_event_message(event))
            except Exception:  # noqa: BLE001
                time.sleep(0.5)

    tailer = threading.Thread(target=_tail_outbox, daemon=True)
    tailer.start()

    plain_as_inject = not args.no_plain_text_inject
    rc = 0
    try:
        while True:
            try:
                line = input("> ")
            except EOFError:
                print()  # newline after Ctrl-D so the goodbye reads cleanly
                break
            line = line.strip()
            if not line:
                continue
            if line in ("/exit", "/quit", ":q", ":quit"):
                break
            if line in ("/help", "/commands"):
                sys.stdout.write(_HELP_TEXT)
                sys.stdout.flush()
                continue
            cmd = parse_command_text(text=line, plain_text_as_inject=plain_as_inject)
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
