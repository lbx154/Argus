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
    render_reset_cmd,
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


#: Slash commands surfaced to completion + the command palette + /help. (label,
#: one-line help). The dispatcher in ``dispatch_command`` is the source of truth;
#: this list mirrors it for the UI. Keep in sync when adding a command.
#:
#: A description of ``"alias of /x"`` marks a pure alias: ``_help_command_rows``
#: folds it into ``/x``'s row instead of listing it separately, so /help stays
#: readable while every real spelling still completes and is matched by the
#: unknown-command "did you mean" hint.
SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/help", "show this command reference"),
    ("/commands", "alias of /help"),
    ("/status", "overall state: daemon, four roles, backlog, journal summary"),
    ("/roles", "live manager/planner/engineer/reviewer status + backend/model"),
    ("/journal", "recent task journal entries (/journal N for more, default 10)"),
    ("/backlog", "pending tasks (/backlog all to include done/skipped)"),
    ("/add", "queue a task: /add <objective> [--once] [--cycles=N] [--budget=$X]"),
    ("/plan", "preview how an objective would be broken down, without queuing it"),
    ("/stop", "stop a task's auto-continue: /stop <item_id>"),
    ("/done", "mark a task done: /done <item_id>"),
    ("/skip", "alias of /done"),
    ("/rm", "alias of /done"),
    ("/note", "append a note to the journal: /note <text>"),
    ("/nudge", "inject guidance into the running task: /nudge <message>"),
    ("/inject", "alias of /nudge"),
    ("/notify", "alias of /nudge"),
    ("/run", "attach and live-follow the daemon draining the backlog"),
    ("/daemon", "control this cockpit's executor: /daemon [start|stop|status]"),
    ("/daemons", "list every live daemon across all projects"),
    ("/attach", "read-only follow another project's daemon: /attach <session-id>"),
    ("/doctor", "diagnose + fix \"why isn't anything running\""),
    ("/backend", "view/change the runner backend"),
    ("/config", "view/change this session's defaults (cycles, budget, effort...)"),
    ("/continuous", "manage auto-generate-new-work mode: /continuous [start|stop|status]"),
    ("/start", "shortcut for /continuous start <objective>"),
    ("/identity", "view/edit the operator identity card"),
    ("/reset", "drop the carried session thread; the next turn starts fresh"),
    ("/skills", "inspect/promote a skill: /skills [ls|promote <name>]"),
    ("/exit", "leave the cockpit (also: Ctrl-D, `退出`)"),
]

#: Grouping of the *primary* (non-alias) commands above, purely for /help
#: layout. Any command missing here still shows up (see ``_help_command_rows``)
#: so a forgotten entry degrades to "unsorted", never to "invisible".
_HELP_SECTIONS: list[tuple[str, tuple[str, ...]]] = [
    ("Everyday", ("/status", "/roles", "/journal", "/backlog")),
    ("Task management", ("/add", "/plan", "/stop", "/done", "/note", "/nudge", "/run")),
    ("Daemon & diagnostics", ("/daemon", "/daemons", "/attach", "/doctor")),
    ("Configuration", ("/backend", "/config", "/continuous", "/start", "/identity", "/reset", "/skills")),
    ("Other", ("/help", "/exit")),
]


def _help_command_rows() -> dict[str, tuple[str, str]]:
    """Fold alias rows (``"alias of /x"``) from ``SLASH_COMMANDS`` into their
    primary command. Returns ``{primary_cmd: (display_label, description)}``,
    where ``display_label`` includes any aliases (e.g. ``"/done (= /skip, /rm)"``).
    """
    aliases: dict[str, list[str]] = {}
    primaries: dict[str, str] = {}
    for cmd, desc in SLASH_COMMANDS:
        if desc.startswith("alias of "):
            aliases.setdefault(desc[len("alias of "):].strip(), []).append(cmd)
        else:
            primaries[cmd] = desc
    rows: dict[str, tuple[str, str]] = {}
    for cmd, desc in primaries.items():
        extra = aliases.get(cmd)
        label = f"{cmd}  (= {', '.join(extra)})" if extra else cmd
        rows[cmd] = (label, desc)
    return rows


def _closest_slash_command(cmd: str) -> str | None:
    """Best-effort "did you mean" suggestion for an unrecognized slash command,
    matched against every real spelling in ``SLASH_COMMANDS`` (aliases
    included) so a mistyped alias still resolves to a useful hint."""
    import difflib

    names = [c for c, _ in SLASH_COMMANDS]
    matches = difflib.get_close_matches(cmd.lower(), names, n=1, cutoff=0.5)
    return matches[0] if matches else None



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


def _life_dir_for(mem: Any) -> Path:
    """Resolve the per-project life-dir that holds ``events.jsonl``.

    Works for both ``MemoryBundle`` (``.project.root`` / ``.project_root``)
    and the bare ``LifeMemory`` facade (``.root``) used in tests.
    """
    project_root = getattr(mem, "project_root", None)
    if project_root is None:
        project = getattr(mem, "project", None)
        project_root = getattr(project, "root", None)
    if project_root is None:
        project_root = getattr(mem, "root", None)
    if project_root is None:
        raise AttributeError(
            "cannot resolve life-dir: memory has no project_root / project.root / root"
        )
    return Path(project_root)


def tail_mission_events(
    life_dir: Path | str,
    item_id: str,
    *,
    timeout: float = 600.0,
    theme: Any = None,
) -> dict[str, Any] | None:
    """Attach to the daemon by tailing ``<life_dir>/events.jsonl`` for one item.

    Polls the event log (seek + offset; tolerant of a missing file and of
    partial / malformed lines), filters to events whose ``item_id`` matches
    ``item_id``, and renders each relevant event with the shared follow
    formatter (:func:`apps.cli._follow._format_follow_event`) so the REPL and
    the standalone ``--follow`` view print identically.

    Returns the ``life.mission.completed`` event dict when seen. Returns
    ``None`` on timeout (does not raise) and on ``KeyboardInterrupt`` (the user
    stops *observing* — the mission keeps running in the daemon).
    """
    from ..apps.cli._follow import (
        _follow_layer_from_event,
        _format_follow_event,
        _read_backlog_rows,
        _select_backlog_row_by_id,
    )

    events_path = Path(life_dir) / "events.jsonl"
    backlog_path = Path(life_dir) / "backlog.jsonl"
    deadline = time.monotonic() + max(0.0, float(timeout))
    offset = 0
    current_layer = "engineer"
    current_mission: dict[str, str] = {"item_id": str(item_id), "title": "", "objective": ""}
    last_review: dict[str, Any] | None = None
    # We read from the start of the log and rely on the ``item_id`` filter to
    # isolate this mission. The backlog item was just enqueued with a freshly
    # minted id, so no earlier mission's events can collide — making this both
    # correct in production and naturally testable (a pre-written completed
    # event for this id is found immediately).

    try:
        while time.monotonic() < deadline:
            try:
                with events_path.open("r", encoding="utf-8") as fh:
                    fh.seek(offset)
                    chunk = fh.read()
                    offset = fh.tell()
            except FileNotFoundError:
                _sleep_until(deadline, 0.4)
                continue
            except OSError:
                _sleep_until(deadline, 0.4)
                continue

            saw_event = False
            for raw_line in chunk.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(event, dict):
                    continue
                ev_item = str(event.get("item_id") or "")
                # Render this mission's events AND the unlabelled engineer/reviewer
                # progress stream (engineer.progress carries no item_id) — that
                # stream is what shows "what it's doing" live. Only skip events
                # clearly tagged for a DIFFERENT item.
                if ev_item and ev_item != str(item_id):
                    continue
                saw_event = True
                current_layer = _follow_layer_from_event(event, current_layer)
                if str(event.get("type") or "") in {"life.mission.started", "life.mission.completed"}:
                    ctx_item_id = str(
                        event.get("item_id") or current_mission.get("item_id") or item_id
                    )
                    title = str(event.get("title") or current_mission.get("title") or "")
                    objective = str(
                        event.get("objective") or current_mission.get("objective") or ""
                    )
                    if ctx_item_id:
                        row = _select_backlog_row_by_id(
                            _read_backlog_rows(backlog_path),
                            ctx_item_id,
                        )
                        if row is not None:
                            title = str(row.get("title") or title)
                            objective = str(row.get("objective") or objective)
                    current_mission = {
                        "item_id": ctx_item_id,
                        "title": title,
                        "objective": objective,
                    }
                if str(event.get("type") or "") == "round.review.completed":
                    last_review = event
                rendered = _format_follow_event(
                    event,
                    current_layer,
                    mission_context=current_mission,
                    theme=theme,
                )
                if rendered:
                    print(rendered, flush=True)
                if str(event.get("type") or "") == "life.mission.completed":
                    # Attach the most recent reviewer verdict so the caller can
                    # surface the reviewer's conclusion (the sole done-ness
                    # authority) alongside the bare mission status — not just
                    # the engineer's last word.
                    if last_review is not None:
                        event.setdefault("_last_review", last_review)
                    return event
            # Only sleep when we drained the file without progress; if the
            # daemon is writing quickly we loop straight back and keep up.
            if not saw_event:
                _sleep_until(deadline, 0.4)
        return None
    except KeyboardInterrupt:
        note = "\n(stopped observing — mission keeps running in the daemon; /status to check)"
        print(theme.gray(note) if theme is not None else note, flush=True)
        return None


def _sleep_until(deadline: float, interval: float) -> None:
    """Sleep ``interval`` seconds but never past ``deadline`` (monotonic)."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return
    time.sleep(min(interval, remaining))


def follow_mission_live_roles(
    life_dir: Path | str,
    item_id: str | None,
    *,
    theme: Any = None,
    timeout: float = 3600.0,
    header: str | None = None,
    interval: float = 1.0,
) -> dict[str, Any] | None:
    """Live multi-agent view: pin the four-role panel and refresh it in place
    while a mission runs, so the operator watches Engineer / Reviewer / Planner /
    Manager work in real time WITHOUT typing ``/roles watch``.

    Reuses the ``/roles`` panel (per-role backend/model/effort + current
    activity, active role highlighted) driven off ``events.jsonl``. Detects the
    mission's ``life.mission.completed`` (attaching the last reviewer verdict as
    ``_last_review``) and returns it; ``None`` on Ctrl-C / timeout (the daemon
    keeps running). TTY-only — the caller falls back to the scrolling tail for
    non-interactive / piped output.
    """
    from ..cli.roles_status import render_roles_snapshot

    life_dir = Path(life_dir)
    events_path = life_dir / "events.jsonl"
    width = getattr(theme, "width", 80) if theme is not None else 80
    deadline = time.monotonic() + max(0.0, float(timeout))
    offset = 0
    last_review: dict[str, Any] | None = None
    completed: dict[str, Any] | None = None
    prev_lines = 0

    def _daemon_right() -> str:
        try:
            from ..daemon.life_worker import read_daemon_status
            st = read_daemon_status(life_dir)
            if getattr(st, "alive", False) and getattr(st, "pid", None):
                s = f"● daemon {st.pid}"
                return theme.bold_green(s) if theme is not None else s
        except Exception:  # noqa: BLE001
            pass
        s = "○ no daemon"
        return theme.gray(s) if theme is not None else s

    if header:
        print(theme.gray(header) if theme is not None else header, flush=True)
    try:
        sys.stdout.write("\x1b[?25l")  # hide cursor during in-place redraw
        sys.stdout.flush()
        while time.monotonic() < deadline:
            # Drain new events to spot completion + the latest reviewer verdict.
            try:
                with events_path.open("r", encoding="utf-8") as fh:
                    fh.seek(offset)
                    chunk = fh.read()
                    offset = fh.tell()
            except OSError:
                chunk = ""
            for raw in chunk.splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(ev, dict):
                    continue
                t = str(ev.get("type") or "")
                if t == "round.review.completed":
                    last_review = ev
                if t == "life.mission.completed":
                    ev_item = str(ev.get("item_id") or "")
                    if not item_id or not ev_item or ev_item == str(item_id):
                        completed = ev
            panel = render_roles_snapshot(
                life_dir, theme, width=width, header_right=_daemon_right()
            )
            n = panel.count("\n") + 1
            if prev_lines:
                # cursor up + clear to end of screen (robust against any wrap)
                sys.stdout.write(f"\x1b[{prev_lines}A\x1b[J")
            sys.stdout.write(panel + "\n")
            sys.stdout.flush()
            prev_lines = n
            if completed is not None:
                if last_review is not None:
                    completed.setdefault("_last_review", last_review)
                return completed
            time.sleep(interval)
        return None
    except KeyboardInterrupt:
        note = "\n(stopped observing — mission keeps running in the daemon; /status to check)"
        print(theme.gray(note) if theme is not None else note, flush=True)
        return None
    finally:
        try:
            sys.stdout.write("\x1b[?25h")  # restore cursor
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass


def _drain_available_bytes(fd: int, first: bytes) -> bytes:
    """After ``first`` byte(s) arrive, non-blocking-drain any more that are
    already buffered (multibyte CJK char, fast typing, paste) so the first
    keystroke is captured whole and echoes correctly when seeded into readline."""
    import select as _select
    data = bytes(first)
    while len(data) < 8192:
        try:
            r, _, _ = _select.select([fd], [], [], 0.0)
        except (OSError, ValueError):
            break
        if not r:
            break
        try:
            more = os.read(fd, 4096)
        except OSError:
            break
        if not more:
            break
        data += more
    return data


def read_message_with_live_cockpit(
    prompt: str,
    mem: Any,
    theme: Any,
    *,
    interval: float = 1.0,
) -> str | None:
    """Read one operator message while a LIVE four-role cockpit is pinned above
    the prompt — so the operator always sees what Manager / Planner / Engineer /
    Reviewer are doing WITHOUT ever typing ``/roles``.

    The panel refreshes in place ~1×/s; the moment the operator starts typing it
    is dismissed and the keystroke is handed to the normal (readline-editable)
    input path — CJK-safe. Degrades to a plain prompt on any of: not a TTY, no
    ``termios``, ``ARGUS_SKILL_COCKPIT_LIVE=0``, no life-dir, no live daemon, a
    too-short terminal, or any unexpected error (the core input path is never
    put at risk). The cursor-rewrite cockpit is opt-in via
    ``ARGUS_SKILL_COCKPIT_LIVE=1``; the default is the plain prompt."""
    from ..apps._input_helpers import read_pasted_message
    if not _live_cockpit_enabled():
        return read_pasted_message(prompt)
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return read_pasted_message(prompt)
    try:
        import termios
        import tty
    except Exception:  # noqa: BLE001 — no POSIX terminal control
        return read_pasted_message(prompt)
    try:
        life_dir = _life_dir_for(mem)
    except Exception:  # noqa: BLE001
        life_dir = None
    if life_dir is None:
        return read_pasted_message(prompt)
    # This path is explicit opt-in only. It uses terminal cursor rewrites, so the
    # default REPL input path stays boring and reliable.
    def _daemon_right() -> str:
        try:
            from ..daemon.life_worker import read_daemon_status
            st = read_daemon_status(life_dir)
            if getattr(st, "alive", False) and getattr(st, "pid", None):
                lab = f"● daemon {st.pid}"
                return theme.bold_green(lab) if theme is not None else lab
        except Exception:  # noqa: BLE001
            pass
        lab = "○ no daemon"
        return theme.gray(lab) if theme is not None else lab

    try:
        from ..daemon.life_worker import read_daemon_status
        st0 = read_daemon_status(life_dir)
        if not (getattr(st0, "alive", False) and getattr(st0, "pid", None)):
            return read_pasted_message(prompt)
    except Exception:  # noqa: BLE001
        return read_pasted_message(prompt)

    from ..cli.roles_status import render_roles_snapshot
    import select as _select

    width = getattr(theme, "width", 80) if theme is not None else 80
    # Need room for the panel (12) + hint (1) + prompt (1) + margin; else plain.
    try:
        import shutil
        rows = shutil.get_terminal_size((80, 24)).lines
    except Exception:  # noqa: BLE001
        rows = 24
    if rows < 16:
        return read_pasted_message(prompt)

    hint = ("Just start typing to chat · Ctrl-C refresh · Ctrl-D exit"
            if theme is not None else "(type to chat · Ctrl-D exits)")

    def _block() -> str:
        panel = render_roles_snapshot(life_dir, theme, width=width,
                                      header_right=_daemon_right())
        hint_line = "  " + (theme.dim(hint) if theme is not None else hint)
        return panel + "\n" + hint_line + "\n" + prompt

    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except Exception:  # noqa: BLE001
        return read_pasted_message(prompt)

    raw: bytes = b""
    interrupted = False
    up = 0
    try:
        tty.setcbreak(fd)  # disables ICANON + ECHO, keeps ISIG (Ctrl-C → SIGINT)
        sys.stdout.write("\x1b[?25l")
        block = _block()
        sys.stdout.write(block)
        sys.stdout.flush()
        up = block.count("\n")
        while True:
            try:
                r, _, _ = _select.select([fd], [], [], interval)
            except (OSError, ValueError):
                r = [fd]  # cannot poll → just read
            if r:
                try:
                    first = os.read(fd, 1)
                except OSError:
                    first = b""
                raw = _drain_available_bytes(fd, first)
                break
            block = _block()
            sys.stdout.write("\r\x1b[%dA\x1b[J" % up)  # up to panel top, clear region
            sys.stdout.write(block)
            sys.stdout.flush()
            up = block.count("\n")
    except KeyboardInterrupt:
        interrupted = True
    finally:
        try:
            sys.stdout.write("\r\x1b[%dA\x1b[J" % up)  # erase the transient cockpit
        except Exception:  # noqa: BLE001
            pass
        try:
            sys.stdout.write("\x1b[?25h")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:  # noqa: BLE001
            pass

    # ── classify the first keystroke ──────────────────────────────────────────
    if interrupted:
        return ""  # Ctrl-C while idle → just refresh the cockpit
    if raw == b"" or raw[:1] == b"\x04":
        return None  # Ctrl-D / EOF → quit
    if raw[:1] in (b"\r", b"\n"):
        return ""  # bare Enter → refresh
    if raw[:1] == b"\x1b" or raw[:1] in (b"\x7f", b"\x08"):
        # escape sequence (arrow/fn) or backspace → dismiss to a clean prompt
        return read_pasted_message(prompt)
    seed = raw.decode("utf-8", "replace")
    seed = "".join(ch for ch in seed if ch >= " " or ch == "\t").strip("\ufffd")
    if not seed:
        return read_pasted_message(prompt)
    # Seed readline so the first keystroke shows and stays fully editable.
    try:
        import readline

        def _hook() -> None:
            readline.insert_text(seed)
            readline.redisplay()

        readline.set_pre_input_hook(_hook)
        try:
            return read_pasted_message(prompt)
        finally:
            readline.set_pre_input_hook(None)
    except Exception:  # noqa: BLE001 — readline missing → prepend the seed
        rest = read_pasted_message(prompt)
        if rest is None:
            return seed
        return seed + rest


def _follow_events_stream(
    life_dir: Path | str,
    *,
    theme: Any = None,
    header: str | None = None,
    until_item_id: str | None = None,
    until_first_completion: bool = False,
) -> dict[str, Any] | None:
    """Stream-render ``events.jsonl`` until Ctrl-C (REPL ``--follow`` loop).

    Shared by continuous free-text mode and ``/run`` so the REPL has a single
    live-tail implementation. Mirrors the standalone
    :func:`apps.cli._core._cmd_follow` loop but renders every item (no
    per-item filter) and returns cleanly to the REPL on ``KeyboardInterrupt``.

    When ``until_item_id`` is given, stop tailing as soon as THAT item's
    ``life.mission.completed`` arrives and return it (with the last
    ``round.review.completed`` attached as ``_last_review``) — so a blocked
    verdict can be surfaced to the operator instead of scrolling past while the
    follow loop keeps spinning. Returns ``None`` on Ctrl-C / no match.
    """
    from ..apps.cli._follow import (
        _follow_layer_from_event,
        _format_follow_event,
        _read_backlog_rows,
        _select_backlog_row_by_id,
    )

    events_path = Path(life_dir) / "events.jsonl"
    backlog_path = Path(life_dir) / "backlog.jsonl"
    if header:
        print(theme.gray(header) if theme is not None else header, flush=True)
    fh = None
    current_layer = "engineer"
    current_mission: dict[str, str] = {"item_id": "", "title": "", "objective": ""}
    last_review: dict[str, Any] | None = None
    try:
        # Wait for the log to exist, then seek to its end so we only show
        # events produced from now on.
        while fh is None:
            try:
                fh = events_path.open("r", encoding="utf-8")
                fh.seek(0, os.SEEK_END)
            except FileNotFoundError:
                time.sleep(0.4)
            except OSError:
                return None
        while True:
            line = fh.readline()
            if not line:
                time.sleep(0.4)
                # Re-open on rotation (events.jsonl → events.jsonl.1).
                try:
                    if events_path.stat().st_ino != os.fstat(fh.fileno()).st_ino:
                        fh.close()
                        fh = events_path.open("r", encoding="utf-8")
                except OSError:
                    pass
                continue
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            if str(event.get("type") or "") in {"life.mission.started", "life.mission.completed"}:
                item_id = str(event.get("item_id") or current_mission.get("item_id") or "")
                title = str(event.get("title") or current_mission.get("title") or "")
                objective = str(
                    event.get("objective") or current_mission.get("objective") or ""
                )
                if item_id:
                    row = _select_backlog_row_by_id(
                        _read_backlog_rows(backlog_path),
                        item_id,
                    )
                    if row is not None:
                        title = str(row.get("title") or title)
                        objective = str(row.get("objective") or objective)
                current_mission = {
                    "item_id": item_id,
                    "title": title,
                    "objective": objective,
                }
            if str(event.get("type") or "") == "round.review.completed":
                last_review = event
            current_layer = _follow_layer_from_event(event, current_layer)
            rendered = _format_follow_event(
                event,
                current_layer,
                mission_context=current_mission,
                theme=theme,
            )
            if rendered:
                print(rendered, flush=True)
            if str(event.get("type") or "") == "life.mission.completed":
                if until_first_completion or (
                    until_item_id and str(event.get("item_id") or "") == until_item_id
                ):
                    if last_review is not None:
                        event.setdefault("_last_review", last_review)
                    return event
    except KeyboardInterrupt:
        note = "\n(stopped following — daemon keeps running; /status to check)"
        print(theme.gray(note) if theme is not None else note, flush=True)
    finally:
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass


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
_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "manager": ("manager", "管理", "经理", "前台"),
    "planner": ("planner", "计划", "规划"),
    "engineer": ("engineer", "工程", "执行"),
    "reviewer": ("reviewer", "评审", "验收"),
}
_EFFORT_VALUES = ("xhigh", "max", "high", "medium", "low")


def _live_cockpit_enabled() -> bool:
    return os.environ.get("ARGUS_SKILL_COCKPIT_LIVE", "0").strip() == "1"


def _live_follow_enabled() -> bool:
    return os.environ.get("ARGUS_SKILL_FOLLOW_LIVE", "0").strip() == "1"


def _maybe_handle_role_effort_text(
    mem: Any,
    text: str,
    chat_state: dict[str, Any],
) -> bool:
    """Handle simple operator config requests before they become Planner work.

    The REPL must not send "把四角色 effort 改成 xhigh" through the research
    pipeline. This conservative recognizer only fires when the text mentions
    Argus/roles plus an explicit effort value and a configuration verb.
    """
    raw = (text or "").strip()
    low = raw.casefold()
    if not raw:
        return False
    if not any(tok in low for tok in ("effort", "reasoning", "推理", "强度")):
        return False
    effort = next((v for v in _EFFORT_VALUES if v in low), "")
    if not effort:
        return False
    if not any(tok in low for tok in ("改", "设置", "设为", "默认", "change", "set", "config", "配置")):
        return False
    mentions_product = "argus" in low or "四角色" in low or "4角色" in low or "所有角色" in low
    roles: list[str] = []
    if any(tok in low for tok in ("四角色", "四个角色", "4角色", "所有角色", "全部角色", "all roles", "every role")):
        roles = list(_ROLE_EFFORT_ENVS)
    else:
        for role, aliases in _ROLE_ALIASES.items():
            if any(alias in low for alias in aliases):
                roles.append(role)
    if not roles and mentions_product:
        roles = list(_ROLE_EFFORT_ENVS)
    if not roles:
        return False

    for role in roles:
        os.environ[_ROLE_EFFORT_ENVS[role]] = effort
    cfg = chat_state.setdefault("config", dict(_CONFIG_DEFAULTS))
    for role in roles:
        cfg[f"{role}_effort"] = effort
    # The cached front-door runner captured the old namespace; rebuild it so
    # subsequent chat/simple turns also use the new effort.
    chat_state.pop("manager_runner", None)

    theme = chat_state.get("theme")
    role_names = " / ".join(role.title() for role in roles)
    line = f"Set {role_names} default reasoning effort to {effort}."
    print(("  " + theme.cyan("argus") + theme.dim(" ↳ ") + line) if theme is not None else line, flush=True)

    try:
        from ..daemon.life_worker import read_daemon_status

        st = read_daemon_status(_life_dir_for(mem))
        if getattr(st, "alive", False):
            msg = (
                "A running daemon won't hot-reload this; use /daemon restart --drain "
                "to fully apply at the next task boundary."
            )
            print(theme.gray("  " + msg) if theme is not None else msg, flush=True)
    except Exception:  # noqa: BLE001
        pass
    return True


_ROLE_BACKEND_ENVS: dict[str, str] = {
    "manager": "ARGUS_SKILL_MANAGER_BACKEND",
    "planner": "ARGUS_SKILL_PLANNER_BACKEND",
    "engineer": "ARGUS_SKILL_ENGINEER_BACKEND",
    "reviewer": "ARGUS_SKILL_REVIEWER_BACKEND",
}
# Recognized agent CLIs. Checked in order; first alias match wins.
_BACKEND_VALUE_ALIASES: dict[str, tuple[str, ...]] = {
    "claude": ("claude code", "claude-code", "claude"),
    "copilot": ("github copilot", "copilot"),
    "codex": ("codex",),
}
_BACKEND_SWITCH_VERBS = (
    "换", "切", "改", "设置", "设为", "默认",
    "switch", "change", "set", "use", "配置",
)


def _maybe_handle_backend_switch_text(
    mem: Any,
    text: str,
    chat_state: dict[str, Any],
) -> bool:
    """Handle "switch the CLI backend to X" free text before it becomes work.

    Mirrors ``_maybe_handle_role_effort_text``: a conservative recognizer that
    only fires when the text names one of the supported agent CLIs (codex /
    claude / copilot) AND a configuration verb AND either a role name, the
    word "后端"/"backend", or "默认" — so ordinary chat that merely mentions
    "copilot" or "codex" in passing is never misread as a config change.

    Flips ``ARGUS_SKILL_RUNNER_BACKEND`` (all roles) or a single role's
    ``ARGUS_SKILL_<ROLE>_BACKEND`` for THIS process only — the same
    env-var contract ``_runtime._SkillLoopRunner`` already reads. The running
    daemon is a separate process with its own environment snapshot, so it
    keeps the old backend until restarted (``/daemon restart --drain`` or its
    natural-language equivalent).
    """
    raw = (text or "").strip()
    low = raw.casefold()
    if not raw:
        return False
    backend = next(
        (
            name
            for name, aliases in _BACKEND_VALUE_ALIASES.items()
            if any(alias in low for alias in aliases)
        ),
        "",
    )
    if not backend:
        return False
    if not any(tok in low for tok in _BACKEND_SWITCH_VERBS):
        return False
    roles: list[str] = []
    for role, aliases in _ROLE_ALIASES.items():
        if any(alias in low for alias in aliases):
            roles.append(role)
    generic = any(
        tok in low
        for tok in ("后端", "backend", "默认", "所有角色", "全部角色", "all roles", "every role")
    )
    if not roles and not generic:
        return False

    from ..agent_cli.runner_backend import normalize_runner_backend

    normalized = normalize_runner_backend(backend)
    theme = chat_state.get("theme")
    if roles:
        for role in roles:
            os.environ[_ROLE_BACKEND_ENVS[role]] = normalized
        role_names = " / ".join(role.title() for role in roles)
        line = f"Set {role_names} CLI backend to {normalized}."
    else:
        os.environ["ARGUS_SKILL_RUNNER_BACKEND"] = normalized
        line = (
            f"Set Argus default CLI backend to {normalized} "
            "(roles without their own backend follow)."
        )

    cfg = chat_state.setdefault("config", dict(_CONFIG_DEFAULTS))
    cfg["runner_backend"] = normalized
    # The cached front-door runner captured the old backend; rebuild it so
    # subsequent chat/simple turns also use the new one.
    chat_state.pop("manager_runner", None)

    print(("  " + theme.cyan("argus") + theme.dim(" ↳ ") + line) if theme is not None else line, flush=True)

    try:
        from ..daemon.life_worker import read_daemon_status

        st = read_daemon_status(_life_dir_for(mem))
        if getattr(st, "alive", False):
            msg = (
                "A running daemon won't hot-reload this; use /daemon restart --drain "
                "to fully apply at the next task boundary."
            )
            print(theme.gray("  " + msg) if theme is not None else msg, flush=True)
    except Exception:  # noqa: BLE001
        pass
    return True


_ROLE_MODEL_ENVS: dict[str, str] = {
    # Manager REPL triage reuses the engineer route/model (see
    # ``_ensure_manager_runner`` and ``cli.roles_status._ROLE_MODEL_ENV``).
    "manager": "ARGUS_SKILL_ENGINEER_MODEL",
    "planner": "ARGUS_SKILL_PLAN_MODEL",
    "engineer": "ARGUS_SKILL_ENGINEER_MODEL",
    "reviewer": "ARGUS_SKILL_REVIEWER_MODEL",
}
# Known model ids per backend, as of this build. Not exhaustive — any model
# the underlying CLI supports already works via ARGUS_SKILL_<ROLE>_MODEL /
# ARGUS_SKILL_MODEL (agent_cli_runner passes --model straight through with no
# whitelist); this table only bounds what natural language can RECOGNIZE, so
# an unlisted model name still falls through untouched to the task/chat path
# instead of being silently mismatched.
_MODEL_VALUE_ALIASES: dict[str, tuple[str, ...]] = {
    "claude-sonnet-5": ("claude-sonnet-5", "claude sonnet 5"),
    "claude-sonnet-4.6": ("claude-sonnet-4.6", "claude sonnet 4.6"),
    "claude-sonnet-4.5": ("claude-sonnet-4.5", "claude sonnet 4.5"),
    "claude-haiku-4.5": ("claude-haiku-4.5", "claude haiku 4.5", "haiku"),
    "claude-opus-4.8": ("claude-opus-4.8", "claude opus 4.8"),
    "claude-opus-4.7": ("claude-opus-4.7", "claude opus 4.7"),
    "claude-opus-4.6": ("claude-opus-4.6", "claude opus 4.6"),
    "gpt-5.5": ("gpt-5.5", "gpt5.5"),
    "gpt-5.4": ("gpt-5.4", "gpt5.4"),
    "gpt-5.3-codex": ("gpt-5.3-codex", "gpt-5.3 codex", "gpt5.3-codex"),
    "gpt-5.4-mini": ("gpt-5.4-mini", "gpt-5.4 mini", "gpt5.4-mini"),
    "gpt-5-mini": ("gpt-5-mini", "gpt-5 mini", "gpt5-mini"),
    "gemini-3.1-pro-preview": (
        "gemini-3.1-pro-preview", "gemini 3.1 pro", "gemini-3.1-pro",
    ),
    "gemini-3.5-flash": ("gemini-3.5-flash", "gemini 3.5 flash"),
    "mai-code-1-flash-picker": (
        "mai-code-1-flash-picker", "mai-code-1-flash", "mai code",
    ),
}
_MODEL_SWITCH_VERBS = _BACKEND_SWITCH_VERBS  # same verb vocabulary as backend switch


def _maybe_handle_model_switch_text(
    mem: Any,
    text: str,
    chat_state: dict[str, Any],
) -> bool:
    """Handle "switch the model to X" free text before it becomes work.

    Same conservative shape as ``_maybe_handle_backend_switch_text``: fires
    only when the text names a known model id AND a configuration verb AND
    either a role name or "模型"/"model"/"默认" — so a message that just
    happens to mention a model name is never misread as a config change.
    Runs AFTER the backend-switch recognizer, so "换成 claude 后端" is still
    a backend switch, not a (non-matching) model one — only phrases that
    fail the backend recognizer's checks (e.g. name a full model id and say
    "模型") reach here.

    Sets ARGUS_SKILL_<ROLE>_MODEL for a named role, or the shared
    ARGUS_SKILL_MODEL (every role, unless a role already pins its own) when
    no role is named — the same env-var contract ``cli.roles_status``
    already resolves. This is how an operator on the copilot backend picks
    any model Copilot supports (claude/gpt/gemini/...), not just the
    gpt-5.5 default — the CLI plumbing already forwards --model verbatim
    with no whitelist; this recognizer just makes picking one a one-liner.
    """
    raw = (text or "").strip()
    low = raw.casefold()
    if not raw:
        return False
    # Pick the LONGEST matching alias across all models, not the first dict
    # entry — several ids share a prefix (e.g. "gpt-5.4" is itself a substring
    # of "gpt-5.4-mini"), so a naive first-match would misidentify the model.
    model = ""
    best_len = 0
    for name, aliases in _MODEL_VALUE_ALIASES.items():
        for alias in aliases:
            if alias in low and len(alias) > best_len:
                model, best_len = name, len(alias)
    if not model:
        return False
    if not any(tok in low for tok in _MODEL_SWITCH_VERBS):
        return False
    roles: list[str] = []
    for role, aliases in _ROLE_ALIASES.items():
        if any(alias in low for alias in aliases):
            roles.append(role)
    generic = any(
        tok in low
        for tok in ("模型", "model", "默认", "所有角色", "全部角色", "all roles", "every role")
    )
    if not roles and not generic:
        return False

    theme = chat_state.get("theme")
    if roles:
        seen_envs = {_ROLE_MODEL_ENVS[role] for role in roles}
        for env_var in seen_envs:
            os.environ[env_var] = model
        role_names = " / ".join(role.title() for role in roles)
        line = f"Set {role_names} model to {model}."
    else:
        os.environ["ARGUS_SKILL_MODEL"] = model
        line = (
            f"Set Argus default model to {model} "
            "(roles without their own model follow)."
        )

    cfg = chat_state.setdefault("config", dict(_CONFIG_DEFAULTS))
    cfg["model"] = model
    chat_state.pop("manager_runner", None)

    print(("  " + theme.cyan("argus") + theme.dim(" ↳ ") + line) if theme is not None else line, flush=True)

    try:
        from ..daemon.life_worker import read_daemon_status

        st = read_daemon_status(_life_dir_for(mem))
        if getattr(st, "alive", False):
            msg = (
                "A running daemon won't hot-reload this; use /daemon restart --drain "
                "to fully apply at the next task boundary."
            )
            print(theme.gray("  " + msg) if theme is not None else msg, flush=True)
    except Exception:  # noqa: BLE001
        pass
    return True


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





def _should_autospawn_on_boot(args: argparse.Namespace) -> bool:
    """Whether opening the cockpit should immediately start a daemon.

    The daemon is the executor, so even a bare fresh cockpit starts one
    immediately. The context-pollution guard is NOT "delay daemon start"; it is
    "start it against the resolved session bundle", never the legacy cwd project.
    """
    return not bool(getattr(args, "no_daemon", False))


def _autospawn_daemon_for_task(
    mem: Any,
    chat_state: dict[str, Any],
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
        rc = _spawn_daemon_from_cockpit(_build_worker_config(cfg_args, bundle=mem))
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

    rc = _spawn_daemon_from_cockpit(cfg)
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


# Sentinel stored in chat_state when a Manager runner cannot be built (or is
# not applicable, e.g. the memory backend). Lets us cache the "no front-end
# triage" decision so we don't retry the build on every line typed.
_MANAGER_RUNNER_UNAVAILABLE = object()


def _ensure_manager_runner(chat_state: dict[str, Any], mem: Any) -> Any:
    """Lazily build (and cache) a Manager-front-end runner for chat triage.

    The runner is used ONLY to classify free text as chat-vs-task and, when
    chat, to reply in-band BEFORE anything reaches the backlog. It is built
    once per REPL session and cached on ``chat_state["manager_runner"]``.

    Returns the runner, or ``None`` when front-end triage is not available
    (memory backend, or a build failure — in which case all free text falls
    through to the task path unchanged).
    """
    cached = chat_state.get("manager_runner")
    if cached is not None:
        return None if cached is _MANAGER_RUNNER_UNAVAILABLE else cached

    backend = chat_state.get("backend")
    # The memory backend has no real LLM runner; never triage — every line is
    # a task (preserves existing memory-backend behaviour and its tests).
    if backend == "memory":
        chat_state["manager_runner"] = _MANAGER_RUNNER_UNAVAILABLE
        return None

    try:
        from ..tools.capability_vault import resolve_route_model

        # ``manager_session_root`` MUST match the daemon's own
        # ``ns.manager_session_root = str(cfg.life_dir)`` (see
        # ``daemon/life_worker.py:_runner_namespace``) — otherwise this
        # front-door Manager (built once per REPL session, used for
        # SELF/TEAM routing + ``divide()``) reads/writes
        # ``research/PIPELINE_STATE.json`` and ``research/DOMAINS/*.json``
        # against a DIFFERENT root than the daemon that actually executes
        # the mission. That mismatch silently drops a Manager-authored
        # custom domain (e.g. an operator task that doesn't match any
        # built-in vertical) and logs a spurious
        # ``load_vertical(...): unknown/half-built vertical`` warning the
        # next time the daemon resolves the vertical from ITS (correct,
        # session-scoped) root. ``mem.project_root`` is the per-project
        # session dir; ``mem.root`` (used below for ``life_dir``, a
        # differently-scoped, currently-unread-by-this-path field) is the
        # GLOBAL ``~/.argus-skill`` root — do not conflate the two.
        session_root = getattr(mem, "project_root", None)
        ns = argparse.Namespace(
            backend=backend or "codex",
            engineer_model=os.environ.get("ARGUS_SKILL_ENGINEER_MODEL")
            or resolve_route_model("engineer"),
            reviewer_model=os.environ.get("ARGUS_SKILL_REVIEWER_MODEL"),
            engineer_reasoning_effort=os.environ.get(
                "ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "xhigh"
            ),
            reviewer_reasoning_effort=os.environ.get(
                "ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "xhigh"
            ),
            plan_mode="auto",
            plan_model=None,
            max_rounds=500,
            check=[],
            workdir=None,
            manager_session_root=str(session_root) if session_root else None,
            life_dir=getattr(mem, "root", None),
            stop_event=None,
        )
        from ..apps._runtime import build_life_runner

        runner = build_life_runner(ns)
    except Exception:  # noqa: BLE001 — triage is best-effort; fall back to task path
        chat_state["manager_runner"] = _MANAGER_RUNNER_UNAVAILABLE
        return None

    chat_state["manager_runner"] = runner
    return runner


def _derive_session_name(text: str, *, limit: int = 48) -> str:
    """Derive a short, human-readable session label from the first real task.

    Codex / Claude-Code name a session after its opening message. We mirror
    that: take the first non-empty line, collapse whitespace, and truncate.
    Naming is domain-agnostic plumbing (a picker label), so the harness may do
    it deterministically — no agent judgment required.
    """
    for raw in (text or "").splitlines():
        line = " ".join(raw.split()).strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def _maybe_name_session(chat_state: dict[str, Any], task_text: str) -> None:
    """Name the current session after its first real task (once, fail-soft).

    A resumed session keeps its original name (``session_named`` is already
    True). Only the first task in a freshly-minted, still-unnamed session sets
    the display_name shown in the resume picker.
    """
    if chat_state.get("session_named"):
        return
    sid = chat_state.get("session_id")
    gr = chat_state.get("global_root")
    if not sid or gr is None:
        return
    name = _derive_session_name(task_text)
    if not name:
        return
    try:
        from ..core.session import touch_session

        touch_session(gr, sid, display_name=name)
        chat_state["session_named"] = True
    except Exception:  # noqa: BLE001 — naming is cosmetic, never block the task
        pass


def _emit_manager_event(mem: Any, event: dict[str, Any]) -> None:
    try:
        from ..life.event_log import JsonlEventSink

        JsonlEventSink(None, life_dir=_life_dir_for(mem)).append(event)
    except Exception:  # noqa: BLE001
        pass


def _manager_divide_user_task(mem: Any, body: str, chat_state: dict[str, Any]) -> None:
    """Run Manager division for an operator-submitted task before enqueue.

    This is intentionally a USER-ENTRY gate. Planner-generated backlog items are
    already the Planner's decomposition and must not be routed back through
    Manager again.
    """
    intent_id = f"intent-{int(time.time() * 1000)}"
    _emit_manager_event(mem, {
        "type": "life.manager.intent.started",
        "agent_layer": "manager",
        "intent_id": intent_id,
        "source": "user",
        "objective": body,
        "text": "manager interpreting user task",
    })
    try:
        runner = _ensure_manager_runner(chat_state, mem)
        mgr = getattr(runner, "manager", None) if runner is not None else None
        if mgr is None:
            from ..manager import Manager

            # Match the primary path's root (see ``_ensure_manager_runner``):
            # the session-scoped project dir, NOT the git worktree — so a
            # degraded (no-runner) divide still persists vertical/domain state
            # where the daemon's mission execution will actually look for it.
            mgr = Manager(
                project_root=getattr(mem, "project_root", None) or Path.cwd(),
                runner=None,
            )
        division = mgr.divide(body, ask_on_new_domain=False)
        payload = {
            "type": "life.manager.intent.completed",
            "agent_layer": "manager",
            "intent_id": intent_id,
            "source": "user",
            "objective": body,
            "vertical": getattr(division, "vertical", ""),
            "kind": getattr(division, "kind", ""),
            "regular": bool(getattr(division, "regular", False)),
            "stages": list(getattr(division, "stages", []) or []),
            "reason": getattr(division, "headline", lambda: "")(),
            "text": (
                f"manager interpreted user task as "
                f"{getattr(division, 'vertical', '')}"
            ),
        }
        _emit_manager_event(mem, payload)
    except Exception as exc:  # noqa: BLE001
        payload = {
            "type": "life.manager.intent.failed",
            "agent_layer": "manager",
            "intent_id": intent_id,
            "source": "user",
            "objective": body,
            "error": f"{type(exc).__name__}: {exc}",
            "text": "manager intent interpretation failed",
        }
        _emit_manager_event(mem, payload)


def manager_triage(mem: Any, body: str, chat_state: dict[str, Any],
                   *, on_phase: Any = None) -> str | None:
    """Front-door route: one-Codex SELF work returns a reply; TEAM work returns
    ``None`` so the caller queues the Argus Planner/Engineer/Reviewer pipeline.

    ``on_phase(label, *, role=...)`` — optional callback invoked at the REAL
    phase transitions (classify → reply), so a live status line reflects what
    the Manager is actually doing rather than a timed cosmetic rotation.
    ``role`` is a best-effort extra (falls back to the plain one-arg call for
    any callback that does not accept it) naming which of the four roles
    drove this update, so the caller can retint a live spinner to match.
    """
    runner = _ensure_manager_runner(chat_state, mem)
    if runner is None or not hasattr(runner, "chat_reply_if_conversational"):
        return None
    captured: list[str] = []

    def _progress_label(event: dict[str, Any]) -> tuple[str, str] | None:
        try:
            from ..apps.cli._follow import _clean_follow_text
            txt = str(
                event.get("text")
                or event.get("title")
                or event.get("reason")
                or event.get("kind")
                or ""
            ).strip()
            if not txt:
                return None
            role = str(event.get("agent_layer") or "manager").strip() or "manager"
            title = {
                "manager": "Manager",
                "planner": "Planner",
                "engineer": "Engineer",
                "reviewer": "Reviewer",
            }.get(role, role.title())
            return role, title + " · " + _clean_follow_text(txt, limit=64)
        except Exception:  # noqa: BLE001
            return None

    def _emit_phase(role: str, label: str) -> None:
        if not callable(on_phase):
            return
        try:
            on_phase(label, role=role)
            return
        except TypeError:
            pass
        except Exception:  # noqa: BLE001 — a UI callback must never break triage
            return
        try:
            on_phase(label)
        except Exception:  # noqa: BLE001
            pass

    class _Capture:
        def handle_event(self, event: dict[str, Any]) -> None:
            try:
                etype = str(event.get("type") or "")
                if etype in {"loop.start", "engineer.progress"}:
                    parsed = _progress_label(event)
                    if parsed:
                        _emit_phase(*parsed)
                    return
                if etype != "round.main.completed":
                    return
                text = _extract_chat_reply_text(str(event.get("last_message") or ""))
                if text:
                    captured.append(text)
            except Exception:  # noqa: BLE001
                pass

    try:
        if runner.chat_reply_if_conversational(
            objective=body, sink=_Capture(),
            seed_thread_id=chat_state.get("last_thread_id"),
            phase_cb=on_phase,
        ):
            chat_state["last_thread_id"] = getattr(runner, "last_thread_id", None)
            return captured[0] if captured else "(no reply)"
    except TypeError:
        # Older runner without phase_cb support — retry without it (fail-soft).
        try:
            if runner.chat_reply_if_conversational(
                objective=body, sink=_Capture(),
                seed_thread_id=chat_state.get("last_thread_id"),
            ):
                chat_state["last_thread_id"] = getattr(runner, "last_thread_id", None)
                return captured[0] if captured else "(no reply)"
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001 — triage failure → treat as a task
        return None
    return None


def enqueue_mission(mem: Any, body: str, chat_state: dict[str, Any], *,
                    iterate: bool = True, max_cycles: int = 6,
                    budget: float = 30.0) -> tuple[Any | None, bool, int | None]:
    """Enqueue ``body`` as a head-priority mission (NO blocking tail — the caller
    decides whether to follow). Handles the blocked-continuation rewrite and, in
    continuous mode, persists the objective for the daemon. Returns
    ``(item, daemon_alive, daemon_pid)``. Shared by the line REPL and the TUI."""
    # Blocked-continuation: a reply to a just-blocked mission continues the same
    # objective (answer appended + queued to inbox), not a brand-new task.
    if chat_state.get("blocked_item_id"):
        prior = str(chat_state.get("last_objective") or body)
        chat_state.pop("blocked_item_id", None)
        chat_state.pop("blocked_question", None)
        try:
            from ..apps._inbox import queue_inbox_message
            queue_inbox_message(_life_dir_for(mem), body, source="repl.answer")
        except Exception:  # noqa: BLE001
            pass
        body = f"{prior}\n\nOperator reply: {body}"
    chat_state["last_objective"] = body
    _manager_divide_user_task(mem, body, chat_state)
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
            daemon_alive, daemon_pid = _autospawn_daemon_for_task(mem, chat_state)
        return None, daemon_alive, daemon_pid
    pending = mem.backlog.pending()
    head_priority = min((it.priority for it in pending), default=100)
    free_priority = min(head_priority - 1, -1)
    item = _add_only(mem, body, priority=free_priority, iterate=iterate,
                     iteration_max_cycles=max_cycles, iteration_budget_usd=budget)
    _maybe_name_session(chat_state, body)
    daemon_alive, daemon_pid = _daemon_alive_for(life_dir)
    if not daemon_alive and chat_state.get("auto_start_daemon_on_task"):
        daemon_alive, daemon_pid = _autospawn_daemon_for_task(mem, chat_state)
    return item, daemon_alive, daemon_pid


def _maybe_auto_promote_to_continuous(
    mem: Any, body: str, chat_state: dict[str, Any], theme: Any,
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
        if not classify(body):
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
    """Free-text input: Manager triage FIRST, then enqueue + attach.

    The Manager is the operator's first point of contact: every line is
    classified (conversation → answered in-band; task → queued for the 7×24
    daemon). A real task is injected at head priority; the REPL then attaches by
    tailing ``events.jsonl`` until the daemon reports completion. Supports
    ``--once`` / ``--cycles=N`` / ``--budget=$X`` inline flags.
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
    theme = chat_state.get("theme")

    if _maybe_handle_role_effort_text(mem, body, chat_state):
        return

    if _maybe_handle_backend_switch_text(mem, body, chat_state):
        return

    if _maybe_handle_model_switch_text(mem, body, chat_state):
        return

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

            reply = manager_triage(mem, body, chat_state, on_phase=_on_phase)
        if reply is not None:
            line = (("  " + theme.cyan("argus") + theme.dim(" ↳ ") + reply)
                    if theme is not None else f"  argus ↳ {reply}")
            print(line, flush=True)
            return

        # TEAM work reached this point — let the Manager judge whether it is
        # open-ended (STANDING) and should be auto-armed as a continuous
        # campaign, so the operator never has to manually pass
        # --continuous --objective for work like "optimize as many X as
        # possible". Only relevant the FIRST time a session goes standing;
        # once continuous, every later task already flows through the
        # existing continuous branch below unchanged.
        if not continuous:
            continuous = _maybe_auto_promote_to_continuous(mem, body, chat_state, theme)

    item, daemon_alive, daemon_pid = enqueue_mission(
        mem, body, chat_state, iterate=iterate, max_cycles=max_cycles, budget=budget)
    life_dir = _life_dir_for(mem)

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



def _daemon_alive_for(life_dir: Path | str) -> tuple[bool, int | None]:
    """(alive, pid) for the daemon owning ``life_dir`` — fail-soft to (False, None)."""
    try:
        from ..daemon.life_worker import read_daemon_status

        st = read_daemon_status(Path(life_dir))
        return bool(getattr(st, "alive", False)), getattr(st, "pid", None)
    except Exception:  # noqa: BLE001
        return False, None


def _no_executor_notice(item_id: str, theme: Any) -> str:
    """Honest message when a task is queued but no daemon will execute it.

    Replaces the old "queued — daemon executing" line (which lied when the
    auto-spawn had failed) AND avoids the 600s tail-wait freeze. The task is
    persisted, so it runs the moment an executor starts.
    """
    head = f"queued {item_id} — but NO daemon is running here, so it will NOT execute yet."
    body = (
        "   in this cockpit:  /daemon start   ·   diagnose:  /doctor\n"
        "   from another shell:  argus-skill --daemon\n"
        "   your task is saved and runs the moment a daemon starts."
    )
    if theme is not None:
        head_lines = theme.wrap_after(head, first_indent=2, hang_indent=2)
        head_out = "\u26a0 " + head_lines[0]
        if len(head_lines) > 1:
            head_out += "\n" + "\n".join(head_lines[1:])
        return theme.yellow(head_out) + "\n" + theme.gray(body)
    return f"\u26a0 {head}\n{body}"


def _extract_chat_reply_text(msg: str) -> str:
    """Pull the human reply out of a chat result (plain text, or JSON-wrapped)."""
    msg = (msg or "").strip()
    if msg.startswith("{") and msg.endswith("}"):
        try:
            data = json.loads(msg)
            for key in ("reply", "message", "text", "answer", "response"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        except Exception:  # noqa: BLE001
            pass
    return msg


class _ChatReplySink:
    """Clean display sink for the REPL chat fast-path.

    Prints ONLY the agent's reply, suppressing the loop/round/progress
    scaffolding ("🔧 round 1: main agent finished" etc.) that the full
    mission renderer emits — a greeting should read like a chat reply, not a
    mission trace. Fully fail-soft; tracks whether anything was shown.
    """

    def __init__(self, theme: Any = None) -> None:
        self.theme = theme
        self.replied = False

    def handle_event(self, event: dict[str, Any]) -> None:  # EventSink protocol
        try:
            if str(event.get("type") or "") != "round.main.completed":
                return  # swallow loop.start / progress / loop.done scaffolding
            text = _extract_chat_reply_text(str(event.get("last_message") or ""))
            if not text:
                return
            self.replied = True
            if self.theme is not None:
                print("  " + self.theme.cyan("argus") + self.theme.dim(" ↳ ")
                      + text, flush=True)
            else:
                print(f"  argus ↳ {text}", flush=True)
        except Exception:  # noqa: BLE001 — display must never break triage
            pass


def _format_completion(
    final: dict[str, Any],
    item_id: str,
    life_dir: Path | str,
    *,
    workdir: Path | str | None = None,
) -> list[str]:
    """Render the multi-line mission-completion footer.

    The bare ``status=`` line was the engineer's last word; the operator wants
    the **reviewer's** conclusion (the sole done-ness authority) and *where the
    result lives*. Lines:

      ``✅ <id> done · status=<s> · <n>r · cost=$<c>``
      ``   reviewer <verdict> (conf <c>): <reason>``   (only if a verdict exists)
      ``   record: <life_dir>``                         (journal/checkpoint/events)
      ``   workdir: <cwd>``                             (where code artifacts land)
    """
    status = str(final.get("status") or "?")
    head = f"✅ {item_id} done · status={status}"
    rounds = final.get("rounds")
    if isinstance(rounds, int) and rounds:
        head += f" · {rounds}r"
    try:
        cost = float(final.get("cost_usd") or 0.0)
    except (TypeError, ValueError):
        cost = 0.0
    if cost:
        head += f" · cost=${cost:.4f}"
    lines = [head]

    review = final.get("_last_review") or {}
    reason = str(review.get("reason") or "").strip()
    if reason:
        rstatus = str(review.get("status") or "").strip()
        conf = review.get("confidence")
        cpart = f" (conf {conf:.2f})" if isinstance(conf, (int, float)) else ""
        lead = "reviewer" + (f" {rstatus}" if rstatus else "") + cpart
        lines.append(f"   {lead}: {reason}")

    lines.append(f"   record: {life_dir}")
    wd = Path(workdir) if workdir is not None else Path.cwd()
    if str(wd) != str(life_dir):
        lines.append(f"   workdir: {wd}")
    return lines


def _record_mission_outcome(
    chat_state: dict[str, Any],
    completed_event: dict[str, Any],
) -> None:
    """Update REPL session stats from a tailed ``life.mission.completed`` event.

    The REPL no longer drives the supervisor, so timing / count come from the
    event the daemon wrote rather than from an in-process return value.
    """
    chat_state["mission_count"] = chat_state.get("mission_count", 0) + 1
    cost = completed_event.get("cost_usd")
    try:
        chat_state["last_cost_usd"] = float(cost) if cost is not None else None
    except (TypeError, ValueError):
        chat_state["last_cost_usd"] = None
    # Remember a blocked verdict so the next free-text reply continues THIS item
    # (answer injected) instead of being triaged as a brand-new objective. The
    # operator question is surfaced by ``_surface_blocked_question``. Cleared on
    # any non-blocked outcome.
    review = completed_event.get("_last_review") or {}
    if str(review.get("status") or completed_event.get("status") or "") == "blocked":
        chat_state["blocked_item_id"] = completed_event.get("item_id")
        chat_state["blocked_question"] = (
            str(review.get("operator_question") or "").strip()
            or str(review.get("reason") or "").strip()
        )
    else:
        chat_state.pop("blocked_item_id", None)
        chat_state.pop("blocked_question", None)


def _surface_blocked_question(chat_state: dict[str, Any], theme: Any) -> None:
    """Print the operator question for a just-blocked mission, if any. The
    operator answers by typing a normal reply — no slash command needed."""
    q = str(chat_state.get("blocked_question") or "").strip()
    if not q:
        return
    line = f"❓ Needs your call: {q} (reply to continue this task)"
    print(theme.yellow(line) if theme is not None else line, flush=True)


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
    """``/run`` — follow the daemon draining the backlog (live tail).

    Pre-fusion this drained the backlog in the foreground via
    ``render_run_command`` → ``_invoke_supervisor``. Since the 2026-06-26
    fusion the daemon is the sole executor, so ``/run`` attaches to it and
    live-renders every event until the operator hits Ctrl-C, returning to
    the REPL. ``opts`` are accepted for backward compatibility but the
    daemon owns the actual budget/iteration knobs now.
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
    width = getattr(theme, "width", 80) if theme is not None else 80

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
        print(render_roles_snapshot(life_dir, theme, width=width,
                                    header_right=_daemon_right(),
                                    show_config=True), flush=True)
        return

    # Live refresh: redraw the panel in place every ~1s until Ctrl-C. Only when
    # attached to a TTY (else fall back to a single snapshot).
    if not sys.stdout.isatty():
        print(render_roles_snapshot(life_dir, theme, width=width), flush=True)
        return
    hint = "Live · press Ctrl-C to return, then type" if theme is not None else "live · Ctrl-C to stop, then type"
    print(theme.dim(hint) if theme is not None else hint, flush=True)
    prev_lines = 0
    try:
        sys.stdout.write("\x1b[?25l")  # hide cursor during redraw
        while True:
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
    from ..manager import plan_mode

    from ..cli.live_status import LiveStatus
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
        "`switch the model to claude-sonnet-5`, `help me optimize this project`.",
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

    out.append(theme.gray(
        "Persistent live role panel (see it without typing /roles): "
        "ARGUS_SKILL_COCKPIT_LIVE=1"
    ))
    out.append(theme.gray(
        "Live-follow view while a task is running: ARGUS_SKILL_FOLLOW_LIVE=1"
    ))
    out.append("")
    out.append(theme.gray("Exit with /exit, Ctrl-D, or `退出`."))
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
    state = mem.init()
    os.environ["ARGUS_SKILL_AGENT_IO_LOG"] = str(mem.project.root / "events.jsonl")
    created: list[str] = []
    for scope, rows in state.items():
        for name, was_created in rows.items():
            if was_created:
                created.append(f"{scope}.{name}")
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
    chat_state["auto_start_daemon_on_task"] = (
        not bool(getattr(args, "no_daemon", False))
        and bool(getattr(args, "session_is_new", False))
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
    from ..cli.branding import render_logo
    from ..cli.theme import Theme

    theme = Theme.auto(force=getattr(args, "color", None))

    # Always-verbose: the lifetime-agent product positioning means the
    # operator wants to see every internal event (round.start, match.info,
    # skill.writeback, …). The earlier ``verbose``/``quiet`` toggles have
    # been removed; ``--verbose`` and ``--quiet`` flags are accepted but
    # ignored (kept for backward compat in scripts).

    backend_default = chat_state["backend"]
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
    if _should_autospawn_on_boot(args):
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
    print("  " + theme.gray("session ") + theme.cyan(str(_sid or "-")))
    try:
        from ..daemon.life_worker import read_daemon_status as _read_daemon_status
        _ds = _read_daemon_status(mem.project.root)
        _pid = str(_ds.pid) if getattr(_ds, "alive", False) and getattr(_ds, "pid", None) else "-"
    except Exception:  # noqa: BLE001
        _pid = "-"
    print("  " + theme.gray("daemon  ") + theme.cyan(_pid))
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

    base_prompt = theme.bold(theme.cyan("argus"))
    resume_marker = theme.dim(" ↻")  # subtle indicator when codex session is being reused

    while True:
        prompt = (
            theme.gray("╭─ ") + base_prompt
            + (resume_marker if chat_state.get("last_thread_id") else "")
            + "\n" + theme.gray("╰─ ")
        )
        try:
            raw = read_message_with_live_cockpit(prompt, mem, theme)
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

        if dispatch_command(line, raw, mem, chat_state, global_root, theme) == "exit":
            print(theme.gray("bye."))
            return 0


def dispatch_command(line, raw, mem, chat_state, global_root, theme) -> str | None:
    """Run one REPL line (slash command or free text). Shared by the line REPL
    and the TUI so both surfaces dispatch identically — handlers print to stdout;
    the TUI captures that. Returns "exit" to quit, else None."""
    alias, alias_note = _cockpit_cli_alias(line)
    if alias_note:
        print(theme.gray(alias_note) if theme is not None else alias_note)
    if alias is None and alias_note is not None:
        return None
    if alias is not None:
        line = alias
        raw = alias

    if not line.startswith("/"):
        _free_text_cmd(mem, raw, chat_state)
        return None
    try:
        tokens = shlex.split(line)
    except ValueError as exc:
        print(theme.red(f"parse error: {exc}"))
        return None
    cmd = tokens[0].lower()
    rest = tokens[1:]
    rest_text = line[len(tokens[0]):].lstrip()
    if True:
        if cmd in ("/help", "/commands"):
            sys.stdout.write(_render_help(theme))
            sys.stdout.flush()
            return None
        if cmd == "/status":
            _status_cmd(mem, chat_state)
            return None
        if cmd == "/roles":
            _roles_cmd(mem, chat_state, rest_text)
            return None
        if cmd == "/doctor":
            _doctor_cmd(mem, chat_state, global_root)
            return None
        if cmd == "/daemon":
            _daemon_cmd(mem, rest_text, chat_state)
            return None
        if cmd == "/daemons":
            _daemons_cmd(chat_state, global_root, mem.project.root)
            return None
        if cmd == "/attach":
            _attach_cmd(chat_state, global_root, rest_text)
            return None
        if cmd == "/plan":
            _plan_cmd(mem, chat_state, rest_text)
            return None
        if cmd == "/start":
            _continuous_cmd(mem, f"start {rest_text}".strip(), chat_state)
            return None
        if cmd == "/continuous":
            _continuous_cmd(mem, rest_text, chat_state)
            return None
        if cmd == "/identity":
            _identity_cmd(mem, rest, rest_text)
            return None
        if cmd == "/backlog":
            include_all = bool(rest) and rest[0].lower() == "all"
            _backlog_list_cmd(mem, include_all=include_all)
            return None
        if cmd == "/add":
            if not rest_text:
                print(theme.gray(
                    "usage: /add <objective>  "
                    "[--once] [--cycles=N] [--budget=$X]"
                ))
                return None
            cfg = chat_state.get("config", {})
            iterate, max_cycles, budget, body = _parse_add_flags(
                rest_text,
                default_iterate=cfg.get("iterate", True),
                default_cycles=cfg.get("cycles", 6),
                default_budget=cfg.get("budget", 30.0),
            )
            if not body:
                print(theme.gray("/add: empty objective after flags"))
                return None
            _manager_divide_user_task(mem, body, chat_state)
            _add_only(
                mem,
                body,
                iterate=iterate,
                iteration_max_cycles=max_cycles,
                iteration_budget_usd=budget,
            )
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
            _status_change_cmd(mem, cmd, rest[0])
            return None
        if cmd == "/journal":
            n = 10
            if rest:
                try:
                    n = int(rest[0])
                except ValueError:
                    print(theme.gray(f"usage: /journal [N]  (got: {rest[0]!r})"))
                    return None
            _journal_tail_cmd(mem, n)
            return None
        if cmd == "/note":
            if not rest_text:
                print(theme.gray("usage: /note <text>"))
                return None
            print(theme.gray(append_note(mem, rest_text)))
            return None
        if cmd in ("/nudge", "/inject", "/notify"):
            if not rest_text:
                print(theme.gray("usage: /nudge <message>  (one line, "
                                 "spliced into the next engineer round)"))
                return None
            from ..apps._inbox import queue_inbox_message
            queue_inbox_message(mem.project.root, rest_text, source="repl.nudge")
            print(theme.gray(
                f"nudge queued ({len(rest_text)} chars) → next mission round "
                f"will see it as operator guidance"
            ))
            return None
        if cmd == "/backend":
            _backend_cmd(rest, chat_state)
            return None
        if cmd == "/config":
            _config_cmd(rest, chat_state, life_dir=mem.project.root)
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
            _run_cmd(mem, rest, chat_state)
            return None
        if cmd == "/skills":
            _skills_cmd(mem, rest)
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
    "tail_mission_events",
    "_follow_events_stream",
    "follow_mission_live_roles",
    "read_message_with_live_cockpit",
    "_life_dir_for",
    "_record_mission_outcome",
]
