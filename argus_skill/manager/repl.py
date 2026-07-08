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
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

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
    _CommonMemory,
    _env_flag,
    _invoke_supervisor,
    _memory_global_root,  # noqa: F401 — kept for parity with the old module surface
    _resolve_global_root,
    _SplitMemory,
)
from ..core import paths as core_paths
from ..core.knobs import resolve_role_model
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
    ("/resume", "switch into the previous conversation (/resume list = all; /resume <id> = one)"),
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
    ("Daemon & diagnostics", ("/daemon", "/daemons", "/attach", "/resume", "/doctor")),
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


def _bottom_hint_line(theme: Any, status: str) -> str:  # noqa: ANN001
    """Left hint + right-aligned backend/model, one row under the input —
    the pattern this session confirmed both Codex CLI and Claude Code use
    (captured live via a pty + pyte terminal emulator), as opposed to
    Copilot CLI's heavier full alternate-screen approach. Right side is
    dropped on narrow terminals rather than truncated into nonsense.

    Uses ``theme.live_width()`` (re-queries the tty), NOT the cached
    ``theme.width`` snapshot taken once at startup: this line's length is
    padded to exactly fill the terminal, and every "move up N rows" redraw
    around it (the live-cockpit panel, the readline handoff) assumes one
    physical row per logical line. If the operator resizes their terminal —
    or a stale ``COLUMNS`` env var disagreed with the tty from the start —
    a line padded for the WRONG width wraps into two physical rows and
    desyncs that row count, confirmed live via pty+pyte: the input row and
    a wrapped fragment of this very hint line visually collided on the same
    row ("╰─ 你er send · /help commands").
    """
    from ..cli.theme import visible_len
    width = theme.live_width()
    left = theme.dim("Enter send · /help commands")
    if not status or width < 60:
        return "  " + left
    pad = width - visible_len(left) - visible_len(status) - 4
    if pad < 1:
        return "  " + left
    return "  " + left + (" " * pad) + status


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


_TAIL_ROLE_TITLES: dict[str, str] = {
    "manager": "Manager",
    "planner": "Planner",
    "engineer": "Engineer",
    "reviewer": "Reviewer",
    "critic": "Reviewer",
}
# Per-role present-continuous vocabulary for the passive-tail idle spinner.
# The ROLE shown is always real (set from the live event stream); only the
# specific verb rotates through that role's own list so a silent gap reads as
# lively, role-appropriate motion instead of a frozen cursor. We deliberately
# DON'T echo the last log line here — it just repeated the scrollback.
_TAIL_ROLE_VERBS: dict[str, tuple[str, ...]] = {
    "manager": (
        "deciding", "routing", "triaging", "delegating", "coordinating",
        "dispatching", "orchestrating", "weighing options", "assessing",
        "choosing the vertical",
    ),
    "planner": (
        "planning", "scoping", "sequencing", "decomposing", "strategizing",
        "mapping the work", "prioritizing", "outlining", "drafting the plan",
        "laying out steps",
    ),
    "engineer": (
        "working", "coding", "implementing", "editing", "building",
        "wiring things up", "refactoring", "debugging", "running checks",
        "testing", "patching", "iterating", "tracing", "digging in",
        "reproducing", "instrumenting",
    ),
    "reviewer": (
        "reviewing", "checking", "verifying", "auditing", "validating",
        "inspecting", "cross-checking", "vetting", "weighing the verdict",
        "judging",
    ),
    "critic": (
        "reviewing", "checking", "verifying", "auditing", "validating",
        "inspecting", "cross-checking", "vetting", "weighing the verdict",
        "judging",
    ),
}
# Shown before the very first event arrives (no role known yet).
_TAIL_WAIT_VERBS: tuple[str, ...] = (
    "waiting for the daemon's first event",
    "attaching to the live run",
    "connecting to the daemon",
    "standing by",
    "warming up",
)
# The ALWAYS-animated bottom line of the live role panel rotates these when no
# single role is currently active — so that line is never static (the operator
# demanded constant motion when there's no fresh output). Honest-neutral: it
# says the team is between steps, not that a specific role is working.
_PANE_IDLE_VERBS: tuple[str, ...] = (
    "standing by",
    "watching for the next step",
    "between steps",
    "holding the line",
    "listening for the agents",
)
# How long each verb stays before rotating to the next (seconds). The glyph
# still spins at _TAIL_SPIN_INTERVAL; only the WORD changes this slowly, so it
# stays readable.
_TAIL_PHRASE_INTERVAL = 2.5


def _tail_spin_interval() -> float:
    """The single, shared braille-frame cadence for EVERY spinner in the CLI.

    All braille circles must advance at one frequency (the ``LiveStatus``
    canonical ~12 fps); the passive tails used to poll-sleep 0.4 s between
    ticks, so their glyph crawled at ~2.5 fps and looked visibly slower than
    the manager/live-panel spinners. Source the rate from ``live_status`` so
    there is one source of truth."""
    try:
        from ..cli.live_status import _INTERVAL
        return max(0.02, float(_INTERVAL))
    except Exception:  # noqa: BLE001 — the spinner must never break observing
        return 0.08


_TAIL_SPIN_INTERVAL = _tail_spin_interval()


class _TailWaitSpinner:
    """A manually-ticked, single-line braille spinner for the scrolling event
    tails (:func:`tail_mission_events` / :func:`_follow_events_stream`).

    The live four-role panel animates itself, but this passive tail is the
    fallback path (non-TTY/piped output, or ``ARGUS_SKILL_FOLLOW_LIVE=0``)
    that would otherwise just blink an empty cursor between the daemon's
    events — the "只有光标闪烁没有内容" dead window. This paints a status
    line during each idle gap and erases it before any real event line
    prints, so the wait always animates without disturbing the scrollback.

    The ROLE shown is real — :meth:`set_activity` tracks the role that is
    genuinely active from the event stream — while that role's own
    present-continuous vocabulary (:data:`_TAIL_ROLE_VERBS`) rotates slowly so
    the gap reads as role-appropriate motion (e.g. "Engineer implementing…",
    "Reviewer verifying…") instead of a frozen cursor or a repeated log line.
    Before the first event it rotates a "waiting for the daemon" vocabulary.

    No-op on non-TTY / piped / NO_COLOR / ARGUS_SKILL_NO_SPINNER (same gate as
    ``LiveStatus``). Driven by hand — no background thread — so it can never
    race the tail's own ``print`` calls: ``tick`` during a sleep, ``clear``
    immediately before printing a real event (and once on exit).
    """

    def __init__(self, theme: Any = None, *, stream: Any = None,
                 enabled: bool | None = None) -> None:
        self._theme = theme
        self._stream = stream if stream is not None else sys.stdout
        try:
            from ..cli.live_status import FRAMES, _spinner_enabled
            self._frames = FRAMES or "|/-\\"
            self._enabled = (
                _spinner_enabled(self._stream) if enabled is None else bool(enabled)
            )
        except Exception:  # noqa: BLE001 — the spinner must never break observing
            self._frames = "|/-\\"
            self._enabled = bool(enabled)
        self._i = 0
        self._painted = False
        self._start = time.monotonic()
        self._layer: str | None = None

    def set_activity(self, layer: str | None, note: str = "") -> None:
        """Record the REAL current role so the idle label shows role-appropriate
        motion. ``note`` is accepted for call-site compatibility but ignored —
        we deliberately don't echo the last log line (it just repeats the
        scrollback)."""
        lay = (layer or "").strip().lower() or None
        if lay is not None:
            self._layer = lay

    def _width(self) -> int:
        if self._theme is not None and hasattr(self._theme, "live_width"):
            try:
                return max(20, int(self._theme.live_width()))
            except Exception:  # noqa: BLE001
                pass
        w = getattr(self._theme, "width", 80) if self._theme is not None else 80
        try:
            return max(20, int(w))
        except Exception:  # noqa: BLE001
            return 80

    def _label(self) -> str:
        """The status text for this instant: role + a slowly-rotating
        present-continuous verb from that role's vocabulary."""
        elapsed = time.monotonic() - self._start
        step = int(elapsed / _TAIL_PHRASE_INTERVAL)
        if self._layer is None:
            verb = _TAIL_WAIT_VERBS[step % len(_TAIL_WAIT_VERBS)]
            return f"{verb[:1].upper()}{verb[1:]}…"
        title = _TAIL_ROLE_TITLES.get(self._layer, self._layer.title())
        verbs = _TAIL_ROLE_VERBS.get(self._layer) or ("working",)
        verb = verbs[step % len(verbs)]
        return f"{title} {verb}…"

    def tick(self) -> None:
        """Paint / advance the in-place status line (no-op when disabled)."""
        if not self._enabled:
            return
        glyph = self._frames[self._i % len(self._frames)]
        self._i += 1
        elapsed = int(time.monotonic() - self._start)
        label = self._label()
        plain = f"{glyph} {label}  ({elapsed}s · Ctrl-C to stop observing)"
        # Never let the status line reach the terminal edge: a wrapped line
        # would survive the next tick's single-row erase and stack up.
        if len(plain) > self._width() - 1:
            plain = f"{glyph} {label}"
            if len(plain) > self._width() - 1:
                plain = plain[: self._width() - 1].rstrip()
        body = plain[len(glyph):]  # everything after the (1-col) glyph
        g = self._theme.bold_cyan(glyph) if self._theme is not None else glyph
        try:
            self._stream.write("\r\x1b[2K" + g + body)
            self._stream.flush()
            self._painted = True
        except Exception:  # noqa: BLE001
            pass

    def clear(self) -> None:
        """Erase the status line before a real event prints (or on exit)."""
        if not (self._enabled and self._painted):
            return
        try:
            self._stream.write("\r\x1b[2K")
            self._stream.flush()
        except Exception:  # noqa: BLE001
            pass
        self._painted = False


def _type_out_line(line: str, *, stream: Any = None) -> None:
    """Print one committed line with a capped character-by-character "typing"
    animation, for an AI feel. Falls back to a plain, instant print when
    disabled / non-TTY (so piped output and tests are byte-for-byte unchanged).

    The total animation time is hard-capped (long reasoning never crawls the
    log); Ctrl-C during the animation raises straight through so the operator
    can always interrupt.
    """
    out = stream if stream is not None else sys.stdout
    enabled = False
    try:
        from ..cli.live_status import _spinner_enabled
        enabled = (
            _spinner_enabled(out)
            and os.environ.get("ARGUS_SKILL_TYPEWRITER", "1").strip().lower()
            not in ("0", "false", "no", "off")
        )
    except Exception:  # noqa: BLE001 — animation must never break the tail
        enabled = False
    if not enabled or not line:
        print(line, flush=True, file=out)
        return
    # Cap total time; short verdicts feel snappy, a 240-char line still lands
    # in well under half a second.
    per_char = min(0.006, 0.35 / max(1, len(line)))
    try:
        for ch in line:
            out.write(ch)
            out.flush()
            if per_char > 0:
                time.sleep(per_char)
        out.write("\n")
        out.flush()
    except KeyboardInterrupt:
        out.write("\n")
        out.flush()
        raise
    except Exception:  # noqa: BLE001
        print(line, flush=True, file=out)


def _wrap_plain(text: str, width: int, indent: str = "    ") -> list[str]:
    """Word-wrap PLAIN ``text`` to at most ``width`` DISPLAY columns per row so
    nothing is truncated at the terminal edge (the reasoning pane's fix for the
    "仍有截断" chop). Display-width aware (CJK / full-width count as 2), breaks on
    spaces where possible and hard-breaks an over-long token; continuation rows
    are indented. Colour is applied by the caller (one hue per role entry)."""
    import unicodedata

    def _cw(ch: str) -> int:
        return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1

    if width <= 6 or not text:
        return [text]
    rows: list[str] = []
    cur: list[str] = []
    cur_w = 0
    last_space = -1  # index in `cur` just AFTER the most recent space
    for ch in text:
        c = _cw(ch)
        if cur_w + c > width and cur:
            if last_space > 0:
                head = "".join(cur[:last_space]).rstrip()
                tail = "".join(cur[last_space:])
                rows.append(head)
                cur = list(indent + tail)
            else:
                rows.append("".join(cur))
                cur = list(indent)
            cur_w = sum(_cw(x) for x in cur)
            last_space = -1
        cur.append(ch)
        cur_w += c
        if ch == " ":
            last_space = len(cur)
    if cur:
        rows.append("".join(cur))
    return rows or [text]


class _TailPrinter:
    """Prints tail event lines, collapsing copilot-style streamed messages.

    The stream adapter (``adapters/stream_progress``) forwards copilot's
    incremental ``assistant.message_delta`` beats as ``engineer.progress``
    agent_message events that share a ``message_id`` and carry ``replace=True``
    — each an ever-growing prefix of the same message, then a final full copy
    (and a short message arrives as one delta PLUS the final = two identical
    copies). A naive tail prints every one, producing the duplicated +
    fragmented '💭' lines the operator sees.

    This coalesces them: a ``replace``+``message_id`` line is held (only the
    latest — i.e. longest — kept) and committed exactly once, when a different
    message starts, a non-``replace`` line arrives, the stream goes idle, or
    the tail exits. Lines without ``replace`` (codex/claude complete beats,
    tool/command/mission events) print immediately, unchanged. It also owns the
    idle spinner so a committed line always erases the spinner first.
    """

    def __init__(self, spinner: "_TailWaitSpinner") -> None:
        self._spinner = spinner
        self._pending_mid: str | None = None
        self._pending_line: str | None = None
        self._pending_at: float = 0.0
        # Settle a paused stream only after it has been silent this long. An
        # actively-streaming message (token beats arriving faster than this)
        # is therefore shown as ONE settled line, never the 200 mid-stream
        # fragments the copilot/codex delta stream used to spray.
        self._idle_commit_after = 0.5

    def _raw_print(self, line: str, *, typewriter: bool = False) -> None:
        self._spinner.clear()
        if typewriter:
            _type_out_line(line)
        else:
            print(line, flush=True)

    def _commit_pending(self) -> None:
        if self._pending_line is not None:
            # The settled reasoning/verdict line types out for an AI feel;
            # tool/command lines (handled in feed) stay instant.
            self._raw_print(self._pending_line, typewriter=True)
        self._pending_mid = None
        self._pending_line = None

    def feed(self, event: dict[str, Any], rendered: str | None) -> None:
        """Handle one rendered event line (``None`` = nothing to show)."""
        # Keep the idle spinner's ROLE honest: record which role is really
        # acting now so a silent gap animates that role's own vocabulary. We do
        # NOT surface the log text itself (it just repeats the scrollback).
        layer = str(event.get("agent_layer") or "").strip().lower() or None
        self._spinner.set_activity(layer)
        if rendered is None:
            return
        mid = str(event.get("message_id") or "")
        if bool(event.get("replace")) and mid:
            # A streamed chunk. Start-of-new-message flushes the previous one;
            # within one message keep the LONGEST rendering seen (the backend
            # ends with a full copy, so this converges on the complete text and
            # is robust whether beats are growing prefixes or raw fragments).
            if self._pending_mid is not None and mid != self._pending_mid:
                self._commit_pending()
            if self._pending_line is None or len(rendered) >= len(self._pending_line):
                self._pending_line = rendered
            self._pending_mid = mid
            self._pending_at = time.monotonic()
            return
        # A complete line: commit any in-flight streamed message first so
        # ordering is preserved, then print this one instantly.
        self._commit_pending()
        self._raw_print(rendered)

    def flush_idle(self) -> None:
        """Deliberately a NO-OP. A streamed message is settled only when the
        NEXT message starts (new message_id), a non-replace line arrives, or on
        :meth:`flush` (exit / completion) — combined with keep-longest, that
        yields exactly one full line per message. Committing on an idle gap
        used to split a SLOW real stream (token beats arriving seconds apart)
        back into the very fragments this coalescer exists to remove."""
        return

    def flush(self) -> None:
        """Commit any held line (tail exit / completion / Ctrl-C)."""
        self._commit_pending()


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
    # An -ing status line animates during the idle gaps so the wait never looks
    # like a frozen blinking cursor (this passive tail is the fallback path;
    # the live role panel is the default when attached to a real terminal,
    # see ARGUS_SKILL_FOLLOW_LIVE).
    spinner = _TailWaitSpinner(theme)
    printer = _TailPrinter(spinner)
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
                spinner.tick()
                _sleep_until(deadline, _TAIL_SPIN_INTERVAL)
                continue
            except OSError:
                spinner.tick()
                _sleep_until(deadline, _TAIL_SPIN_INTERVAL)
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
                printer.feed(event, rendered)
                if str(event.get("type") or "") == "life.mission.completed":
                    # Attach the most recent reviewer verdict so the caller can
                    # surface the reviewer's conclusion (the sole done-ness
                    # authority) alongside the bare mission status — not just
                    # the engineer's last word.
                    if last_review is not None:
                        event.setdefault("_last_review", last_review)
                    printer.flush()
                    spinner.clear()
                    return event
            # Only sleep when we drained the file without progress; if the
            # daemon is writing quickly we loop straight back and keep up.
            if not saw_event:
                printer.flush_idle()
                spinner.tick()
                _sleep_until(deadline, _TAIL_SPIN_INTERVAL)
        printer.flush()
        spinner.clear()
        return None
    except KeyboardInterrupt:
        printer.flush()
        spinner.clear()
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
    activity, active role highlighted) driven off ``events.jsonl``. Press
    ``Ctrl+O`` to unfold a live, coalesced reasoning feed under the panel (the
    agents' thinking, streamed fragments collapsed into clean lines); press it
    again to collapse back to the dashboard. Detects the mission's
    ``life.mission.completed`` (attaching the last reviewer verdict as
    ``_last_review``) and returns it; ``None`` on Ctrl-C / timeout (the daemon
    keeps running). TTY-only — the caller falls back to the scrolling tail for
    non-interactive / piped output.
    """
    from ..cli.roles_status import (
        _clip_ansi_line,
        _disp_width,
        role_activity,
        role_paint,
        render_roles_snapshot,
    )
    from ..apps.cli._follow import (
        _follow_layer_from_event,
        _format_follow_event,
    )
    import select as _select
    import shutil
    from collections import deque as _deque

    life_dir = Path(life_dir)
    events_path = life_dir / "events.jsonl"
    deadline = time.monotonic() + max(0.0, float(timeout))
    offset = 0
    last_review: dict[str, Any] | None = None
    completed: dict[str, Any] | None = None
    prev_lines = 0
    # ── Ctrl+O expand: a reasoning pane pinned UNDER the role panel ──
    # Each agent message is accumulated by message_id (robust to both growing-
    # prefix and raw-fragment stream shapes) and shown as ONE clean, wrapped
    # line once it settles — no live typing, no half-word fragments, no
    # duplicates. The in-flight message stays hidden until it completes; the
    # panel + animated verb line cover the "still thinking" window.
    expanded = _env_flag("ARGUS_SKILL_FOLLOW_EXPAND", False)
    committed: "_deque[tuple[str, str]]" = _deque(maxlen=400)  # (role, plain line)
    current_layer = "engineer"
    pane_start = time.monotonic()  # anchors the -ing verb rotation
    # Accumulate the in-flight message until it settles:
    _cur = {"mid": None, "role": "engineer", "text": ""}
    # Scrollback: `off` = visual rows scrolled UP from the bottom (0 = follow the
    # latest). Cache the wrapped rows so we don't re-wrap all history every tick.
    scroll = {"off": 0, "prev_total": 0, "budget": 10}
    _vcache: dict[str, Any] = {"n": -1, "w": -1, "rows": []}

    def _accumulate(prev: str, new: str) -> str:
        # Robust to BOTH stream shapes: growing prefixes (replace with the fuller
        # copy) and raw non-overlapping fragments (append). A shorter stale
        # duplicate is ignored.
        new = new or ""
        if not prev:
            return new
        if new.startswith(prev):
            return new
        if prev.startswith(new):
            return prev
        return prev + new

    def _settle_current() -> None:
        """Move the in-flight streaming message into history as one clean line
        (JSON verdicts parsed via the shared formatter), then reset."""
        text = str(_cur["text"] or "")
        if _cur["mid"] is not None and text.strip():
            ev = {
                "type": "engineer.progress", "kind": "agent_message",
                "text": text, "agent_layer": _cur["role"],
            }
            line = _format_follow_event(ev, str(_cur["role"]), theme=None, full=True)
            if line:
                committed.append((str(_cur["role"]), line))
        _cur["mid"] = None
        _cur["text"] = ""

    def _feed_pane(ev: dict) -> None:
        nonlocal current_layer
        current_layer = _follow_layer_from_event(ev, current_layer)
        etype = str(ev.get("type") or "")
        if etype == "engineer.progress" and str(ev.get("kind") or "") == "agent_message":
            mid = str(ev.get("message_id") or "")
            text = str(ev.get("text") or "")
            if bool(ev.get("replace")) and mid:
                if _cur["mid"] is not None and mid != _cur["mid"]:
                    _settle_current()  # a new message started → settle the old
                _cur["mid"] = mid
                _cur["role"] = current_layer
                _cur["text"] = _accumulate(str(_cur["text"] or ""), text)
                return
            # A non-replace complete agent_message: settle any in-flight one, then
            # add this whole message as history.
            _settle_current()
            line = _format_follow_event(dict(ev), current_layer, theme=None, full=True)
            if line:
                committed.append((current_layer, line))
            return
        # Any other rendered event (tool / command / reasoning / planner verdict):
        # settle the in-flight thought first so ordering is preserved.
        line = _format_follow_event(ev, current_layer, theme=None, full=True)
        if line:
            _settle_current()
            committed.append((current_layer, line))

    # Raw-cbreak keyboard so Ctrl+O is caught without Enter (ISIG kept, so Ctrl+C
    # still interrupts). No-op on non-TTY / no-termios → panel only, no toggle.
    _kbd_fd: int | None = None
    _kbd_old = None
    _mouse_on = False
    _mouse_enabled = _env_flag("ARGUS_SKILL_FOLLOW_MOUSE", True)
    try:
        if sys.stdin.isatty() and sys.stdout.isatty():
            import termios
            import tty
            _kbd_fd = sys.stdin.fileno()
            _kbd_old = termios.tcgetattr(_kbd_fd)
            tty.setcbreak(_kbd_fd)
            if _mouse_enabled:
                # Enable button + SGR mouse reporting so the wheel scrolls the
                # pane (buttons 64/65). SGR (1006) is text-safe to parse.
                # ARGUS_SKILL_FOLLOW_MOUSE=0 disables it (keeps native text
                # selection / copy) — then use the keyboard scroll keys.
                sys.stdout.write("\x1b[?1000h\x1b[?1006h")
                sys.stdout.flush()
                _mouse_on = True
    except Exception:  # noqa: BLE001 — keyboard is optional; never break observing
        _kbd_fd = None

    # Braille spinner pinned in the panel header so the view visibly ANIMATES
    # even before the daemon claims the task / emits its first event — otherwise
    # the pre-first-event window is a static idle panel that feels frozen. Fall
    # back to an ASCII spinner if the shared frames are unavailable.
    try:
        from ..cli.live_status import FRAMES as _SPIN_FRAMES
    except Exception:  # noqa: BLE001 — the spinner must never break observing
        _SPIN_FRAMES = "|/-\\"
    frame_i = 0
    # Animate at the ONE shared braille cadence (see _TAIL_SPIN_INTERVAL) so
    # every spinner in the CLI spins at an identical frequency; each tick still
    # drains events, so completion detection stays responsive.
    tick = _TAIL_SPIN_INTERVAL

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

    def _verb_line(width: int, glyph: str) -> str:
        """The ALWAYS-animated bottom line: a spinning braille glyph + a
        rotating -ing verb. When a role is actively working it names that role
        in its colour (``⠹ Planner planning…``); otherwise it rotates a neutral
        standing-by phrase so this line is NEVER static — the glyph advances
        every redraw and the word rotates, so there is always visible motion
        even between steps."""
        try:
            acts = role_activity(life_dir)
        except Exception:  # noqa: BLE001
            acts = {}
        active = next(
            (r for r, a in acts.items() if getattr(a, "active", False)), None
        )
        step = int((time.monotonic() - pane_start) / _TAIL_PHRASE_INTERVAL)
        g = theme.bold_cyan(glyph) if theme is not None else glyph
        if active:
            title = _TAIL_ROLE_TITLES.get(active, active.title())
            verbs = _TAIL_ROLE_VERBS.get(active) or ("working",)
            label = f"{title} {verbs[step % len(verbs)]}…"
            if theme is not None:
                label = role_paint(theme, active, label)
        else:
            phrase = _PANE_IDLE_VERBS[step % len(_PANE_IDLE_VERBS)]
            label = f"{phrase[:1].upper()}{phrase[1:]}…"
            if theme is not None:
                label = theme.gray(label)
        line = f"  {g} {label}"
        return _clip_ansi_line(line, max(1, width - 1)) if theme is not None \
            else line[: max(1, width - 1)]

    def _build_block(width: int, spin_p: str, glyph: str) -> str:
        panel = render_roles_snapshot(
            life_dir, theme, width=width,
            header_right=spin_p + "  " + _daemon_right(),
        )
        lines: list[str] = [panel]
        if _kbd_fd is not None:
            if expanded:
                rows = shutil.get_terminal_size((80, 24)).lines
                panel_h = panel.count("\n") + 1
                # Reserve rows for: separator, up/down indicators, verb line.
                budget = max(3, rows - panel_h - 6)
                scroll["budget"] = budget
                # (Re)wrap ALL settled history — cached by (count, width) so we
                # only re-wrap when a message settles or the terminal resizes.
                if _vcache["n"] != len(committed) or _vcache["w"] != width:
                    v: list[str] = []
                    for role, plain in committed:
                        for vr in _wrap_plain(plain, max(8, width - 1)):
                            v.append(role_paint(theme, role, vr)
                                     if theme is not None else vr)
                    _vcache["rows"] = v
                    _vcache["n"] = len(committed)
                    _vcache["w"] = width
                visual: list[str] = _vcache["rows"]
                total = len(visual)
                # Keep a scrolled-up view anchored as new rows arrive at the
                # bottom (don't yank the operator back down); when following
                # (off==0) stay pinned to the latest.
                if scroll["off"] > 0 and total > scroll["prev_total"]:
                    scroll["off"] += total - scroll["prev_total"]
                scroll["prev_total"] = total
                max_off = max(0, total - budget)
                scroll["off"] = max(0, min(scroll["off"], max_off))
                off = scroll["off"]
                end = total - off
                start = max(0, end - budget)
                window = visual[start:end]

                title = "  ── reasoning · Ctrl+O collapse"
                if total > budget:
                    title += " · wheel·↑↓·PgUp/PgDn scroll" + (
                        " · End=latest" if off > 0 else "")
                title += " "
                sep = title + "─" * max(0, width - _disp_width(title) - 1)
                lines.append(theme.gray(sep) if theme is not None else sep)

                if window:
                    if start > 0:
                        ind = f"  ▲ {start} more line(s) above"
                        lines.append(theme.dim(ind) if theme is not None else ind)
                    lines.extend(window)
                    if off > 0:
                        ind = f"  ▼ {off} more below · press End/G to follow"
                        lines.append(theme.dim(ind) if theme is not None else ind)
                else:
                    hint = "  (waiting for the agents' first thoughts…)"
                    lines.append(theme.gray(hint) if theme is not None else hint)
                lines.append(_verb_line(width, glyph))
            else:
                lines.append(_verb_line(width, glyph))
                hint = "  Ctrl+O expand reasoning · Ctrl+C stop observing"
                lines.append(theme.dim(hint) if theme is not None else hint)
        return "\n".join(lines)

    if header:
        print(theme.gray(header) if theme is not None else header, flush=True)
    try:
        sys.stdout.write("\x1b[?25l")  # hide cursor during in-place redraw
        sys.stdout.flush()
        while time.monotonic() < deadline:
            # Drain new events to spot completion + the latest reviewer verdict,
            # and feed the coalescer that powers the Ctrl+O reasoning pane.
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
                _feed_pane(ev)
                t = str(ev.get("type") or "")
                if t == "round.review.completed":
                    last_review = ev
                if t == "life.mission.completed":
                    ev_item = str(ev.get("item_id") or "")
                    if not item_id or not ev_item or ev_item == str(item_id):
                        completed = ev
                        # Settle the last in-flight thought so it appears in the
                        # final frame (mission end is a safe settle point — not
                        # mid-stream, so it can't re-fragment).
                        _settle_current()
            spin = _SPIN_FRAMES[frame_i % len(_SPIN_FRAMES)]
            frame_i += 1
            spin_p = theme.bold_cyan(spin) if theme is not None else spin
            # Re-queried every redraw, not hoisted above the loop — the
            # operator can resize their terminal WHILE a mission runs, and a
            # width baked in once at function entry would wrap this padded
            # header line on the next redraw, undercounting ``n`` below and
            # desyncing the "move up N rows" erase (same class of bug fixed
            # for the idle-prompt panel via ``Theme.live_width``).
            width = theme.live_width() if theme is not None else 80
            block = _build_block(width, spin_p, spin)
            n = block.count("\n") + 1
            if prev_lines:
                # cursor up + clear to end of screen (robust against any wrap)
                sys.stdout.write(f"\x1b[{prev_lines}A\x1b[J")
            sys.stdout.write(block + "\n")
            sys.stdout.flush()
            prev_lines = n
            if completed is not None:
                if last_review is not None:
                    completed.setdefault("_last_review", last_review)
                return completed
            # Wait up to `tick`, waking early on a keystroke so Ctrl+O is snappy.
            if _kbd_fd is not None:
                try:
                    r, _, _ = _select.select([_kbd_fd], [], [], tick)
                except (OSError, ValueError):
                    r = []
                if r:
                    try:
                        data = os.read(_kbd_fd, 256)
                    except OSError:
                        data = b""
                    if b"\x0f" in data:  # Ctrl+O toggles the reasoning pane
                        expanded = not expanded
                    if expanded:
                        # Scrollback controls (arrows + PgUp/PgDn + Home/End,
                        # plus vim-style k/j/g/G, plus the mouse wheel). `off` is
                        # measured from the bottom; End/G resumes following.
                        pg = max(1, scroll["budget"] - 1)
                        if b"\x1b[A" in data or b"k" in data:
                            scroll["off"] += 1
                        if b"\x1b[B" in data or b"j" in data:
                            scroll["off"] -= 1
                        if b"\x1b[5~" in data:            # PageUp
                            scroll["off"] += pg
                        if b"\x1b[6~" in data:            # PageDown
                            scroll["off"] -= pg
                        # Mouse wheel (SGR 1006): button 64 = up, 65 = down.
                        scroll["off"] += 3 * (data.count(b"\x1b[<64;"))
                        scroll["off"] -= 3 * (data.count(b"\x1b[<65;"))
                        if b"\x1b[H" in data or b"\x1b[1~" in data or b"g" in data:
                            scroll["off"] = 10 ** 9       # top (clamped on render)
                        if b"\x1b[F" in data or b"\x1b[4~" in data or b"G" in data:
                            scroll["off"] = 0             # follow the latest
                        if scroll["off"] < 0:
                            scroll["off"] = 0
            else:
                time.sleep(tick)
        return None
    except KeyboardInterrupt:
        note = "\n(stopped observing — mission keeps running in the daemon; /status to check)"
        print(theme.gray(note) if theme is not None else note, flush=True)
        return None
    finally:
        _settle_current()  # don't lose the last in-flight thought on exit
        if _mouse_on:
            try:
                sys.stdout.write("\x1b[?1006l\x1b[?1000l")  # disable mouse tracking
                sys.stdout.flush()
            except Exception:  # noqa: BLE001
                pass
        if _kbd_fd is not None and _kbd_old is not None:
            try:
                import termios
                termios.tcsetattr(_kbd_fd, termios.TCSADRAIN, _kbd_old)
            except Exception:  # noqa: BLE001
                pass
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


def _live_cockpit_will_activate(mem: Any) -> bool:
    """True iff ``read_message_with_live_cockpit`` will actually render the
    fancy cbreak-mode cockpit panel for the current turn, rather than
    silently falling back to a plain ``input()`` call underneath.

    Mirrors — as a single shared check — every early-return guard at the top
    of that function (env opt-out, TTY-ness, ``termios`` availability,
    resolvable life-dir, a live daemon, a tall-enough terminal). This exists
    because the REPL's own prompt construction must decide, BEFORE calling
    ``read_message_with_live_cockpit``, whether it is safe to pre-print a
    plain-prompt layout itself and hand that function a bare single-row
    prompt: ``_live_cockpit_enabled()`` alone is NOT sufficient for that
    decision, since it only reflects the ``ARGUS_SKILL_COCKPIT_LIVE`` opt-out
    and says nothing about the daemon/terminal-size conditions checked here —
    trusting it alone let a stale multi-row prompt (meant only for the
    fancy-panel path, which manages its own redraws) slip into the plain
    ``input()`` fallback and corrupt the display the moment readline did any
    internal redraw (confirmed live: the "╰─ " prefix vanished mid-keystroke
    whenever the daemon happened to be down, e.g. under ``--no-daemon``).
    Keeping this as one shared helper means the two call sites can never
    drift out of sync on what "will the fancy panel actually show" means.
    """
    if not _live_cockpit_enabled():
        return False
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        import termios  # noqa: F401
        import tty  # noqa: F401
    except Exception:  # noqa: BLE001 — no POSIX terminal control
        return False
    try:
        life_dir = _life_dir_for(mem)
    except Exception:  # noqa: BLE001
        life_dir = None
    if life_dir is None:
        return False
    try:
        from ..daemon.life_worker import read_daemon_status
        st0 = read_daemon_status(life_dir)
        if not (getattr(st0, "alive", False) and getattr(st0, "pid", None)):
            return False
    except Exception:  # noqa: BLE001
        return False
    try:
        import shutil
        rows = shutil.get_terminal_size((80, 24)).lines
    except Exception:  # noqa: BLE001
        rows = 24
    if rows < 16:
        return False
    return True


_CURSOR_UP_RE = re.compile(r"\x1b\[(\d*)A")
_CURSOR_DOWN_RE = re.compile(r"\x1b\[(\d*)B")


def _visual_row_delta(text: str) -> int:
    """Net terminal ROWS ``text`` moves the cursor down when printed as-is.

    Plain ``str.count("\\n")`` underestimates this whenever ``text`` also
    embeds a cursor-repositioning escape (e.g. ``theme.cursor_up_and_forward``,
    used by the caller of :func:`read_message_with_live_cockpit` to land the
    cursor back on the input row after printing a multi-line prompt for
    readline's benefit): the trailing ``\\x1b[<n>A`` moves the cursor UP by
    ``n`` rows AFTER the newlines already advanced it down, so the true final
    row is ``n`` less than the newline count alone suggests. Using the
    newline-only count to erase-and-redraw this block on the next refresh
    then overshoots upward by that same ``n`` every cycle, eating one extra
    (real, previously-printed) row above the block each time — exactly the
    "banner disappears one line per refresh" bug this fixes. Handles
    ``\\x1b[<n>B`` (cursor down) symmetrically for completeness.
    """
    delta = text.count("\n")
    for m in _CURSOR_UP_RE.finditer(text):
        delta -= int(m.group(1) or 1)
    for m in _CURSOR_DOWN_RE.finditer(text):
        delta += int(m.group(1) or 1)
    return delta


def _split_readline_safe_prompt(prompt: str, theme: Any) -> tuple[str, str] | None:
    """Turn a ``banner\\ninput_prefix\\nhint+cursor-escape`` prompt (built for
    the live-cockpit's own cursor-controlled ``_block()`` rendering) into a
    ``(pre_print_text, bare_input_prompt)`` pair that's safe to hand to
    input()/readline instead.

    Feeding the ORIGINAL 3-line, escape-laden string straight to readline
    corrupts the display the instant readline does its own internal redraw:
    readline counts the embedded ``"\\n"``s to learn where editing starts,
    but the trailing ``cursor_up_and_forward`` escape moves the cursor to a
    DIFFERENT row than that count implies (live-reproduced via pty+pyte:
    typing "hello" rendered progressively as "h" -> "he" -> "el h" ->
    "ellh" -> "ello", eating characters and the input-row prefix). This is
    the same "Round 1" class of bug already fixed for the non-live-cockpit
    prompt path — reproduced here: print everything except the input row
    directly, land the cursor on a blank input row via
    ``cursor_up_and_forward(2, 0)``, and return the bare input-row prefix
    for the caller to use as the real prompt.

    Returns ``None`` (caller should use ``prompt`` as-is) if it is not
    shaped as expected (defensive — must never break the input path)."""
    from ..apps._input_helpers import _ANSI_RE

    parts = prompt.split("\n", 2)
    if len(parts) != 3:
        return None
    banner_line, input_prefix, rest = parts
    # ``_ANSI_RE`` only matches bracketed ``\x1b[...`` sequences, so it strips
    # the ``\x1b[<n>A`` / ``\x1b[<n>C`` halves of a trailing
    # ``cursor_up_and_forward`` escape but NOT the bare "\r" it unconditionally
    # emits between them (``theme.cursor_up_and_forward`` always appends "\r"
    # to reset to column 0, even when ``forward=0`` skips the "\x1b[nC" part
    # entirely) — that stray literal carriage return is not ANSI, so the
    # regex leaves it sitting in ``rest_clean``. Currently harmless only
    # because it happens to be followed immediately by a row-advancing "\n"
    # (both reset column 0; ONLCR makes the "\n" alone equivalent), but
    # relying on that adjacency is fragile — a hint line has no legitimate
    # reason to contain a raw "\r", so drop it explicitly.
    rest_clean = _ANSI_RE.sub("", rest).replace("\r", "")
    pre_print = (
        banner_line + "\n"
        + "\n"  # blank placeholder input row — filled in by input() below
        + rest_clean + "\n"
        + theme.cursor_up_and_forward(2, 0)
    )
    return pre_print, input_prefix


def _build_slash_completer():
    """A prompt_toolkit completer offering the slash commands (with their
    one-line descriptions) as a live dropdown — but ONLY when the current line
    starts with ``/``, so ordinary chat never pops a menu. Primary spellings
    only; ``alias of …`` rows are folded out (their target already shows)."""
    from prompt_toolkit.completion import Completer, Completion

    class _SlashCompleter(Completer):
        def get_completions(self, document, complete_event):  # noqa: ANN001
            word = document.text_before_cursor
            if not word.startswith("/"):
                return
            for cmd, desc in SLASH_COMMANDS:
                if desc.startswith("alias of "):
                    continue
                if cmd.startswith(word):
                    yield Completion(
                        cmd,
                        start_position=-len(word),
                        display=cmd,
                        display_meta=desc,
                    )

    return _SlashCompleter()


def _get_prompt_session(chat_state: dict[str, Any], mem: Any):
    """Lazily build + cache the prompt_toolkit ``PromptSession`` used for the
    line REPL's TTY input: live slash-command completion + per-session history.
    Cached on ``chat_state`` so history/completer persist for the session."""
    sess = chat_state.get("prompt_session")
    if sess is not None:
        return sess
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory, InMemoryHistory

    try:
        hist: Any = FileHistory(str(_life_dir_for(mem) / "repl_history"))
    except Exception:  # noqa: BLE001 — history is best-effort
        hist = InMemoryHistory()

    # prompt_toolkit only auto-triggers completion on INSERT (buffer.py) and a
    # lone exact match is discarded — so editing a slash command mid-line (e.g.
    # backspace then retype) leaves no dropdown. Re-run completion after a
    # delete when the line is a slash command, so the menu always reflects the
    # current text.
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()

    def _retrigger_slash(buf: Any) -> None:
        try:
            if buf.text.lstrip().startswith("/"):
                buf.complete_state = None
                buf.start_completion()
        except Exception:  # noqa: BLE001 — completion is best-effort
            pass

    @kb.add("backspace")
    def _(event: Any) -> None:  # noqa: ANN401
        event.current_buffer.delete_before_cursor()
        _retrigger_slash(event.current_buffer)

    @kb.add("delete")
    def _(event: Any) -> None:  # noqa: ANN401
        event.current_buffer.delete()
        _retrigger_slash(event.current_buffer)

    sess = PromptSession(
        completer=_build_slash_completer(),
        history=hist,
        complete_while_typing=True,
        key_bindings=kb,
    )
    chat_state["prompt_session"] = sess
    return sess


def _use_prompt_toolkit_input() -> bool:
    """True iff the default TTY input should use prompt_toolkit — the default
    cockpit input engine: `/` completion + honest Ctrl-C + resumable
    conversations, all in one driver. This ONLY controls the INPUT engine;
    whether the live 4-role panel is also drawn above it is a SEPARATE flag
    (``_live_cockpit_enabled`` — see ``_panel_text`` in
    :func:`read_message_prompt_toolkit`) that this function deliberately does
    not consult. Both happen to default to "on" today, but keeping them
    independent means turning either one off (``ARGUS_SKILL_NO_PROMPT_TOOLKIT=1``
    for the input engine, ``ARGUS_SKILL_COCKPIT_LIVE=0`` for the panel) never
    silently flips the other. Off for: non-TTY / piped stdin,
    ``ARGUS_SKILL_NO_PROMPT_TOOLKIT=1`` (falls back to the legacy cbreak panel /
    plain reader), or a missing prompt_toolkit."""
    if os.environ.get("ARGUS_SKILL_NO_PROMPT_TOOLKIT") == "1":
        return False
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
    except Exception:  # noqa: BLE001
        return False
    try:
        import prompt_toolkit  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def read_message_prompt_toolkit(
    prompt: str,
    mem: Any,
    theme: Any,
    chat_state: dict[str, Any],
) -> str | None:
    """Read one operator line via prompt_toolkit — the unified cockpit input.

    Prints a status line ABOVE the input (as scrollback, repainted each turn)
    and hands prompt_toolkit only the short ╭─/╰─ box, so the ``/``
    completion menu positions cleanly BELOW the input — one engine, no
    cbreak/prompt_toolkit tug-of-war. That status line is the full live
    four-role panel by default (``_live_cockpit_enabled``, same flag the
    legacy cbreak engine honours) — this input engine and the panel are
    independent choices (see ``_use_prompt_toolkit_input``), but both
    default to showing it automatically; ``ARGUS_SKILL_COCKPIT_LIVE=0``
    drops back to the same lightweight one-liner the plain engine falls
    back to (``format_prompt_status_line``).

    Returns the line, or ``None`` on EOF (Ctrl-D). Ctrl-C raises
    ``KeyboardInterrupt`` (the caller arms double-Ctrl-C exit). Any init/render
    failure falls back to the plain, always-reliable reader."""
    from ..apps._input_helpers import read_pasted_message

    try:
        from prompt_toolkit import ANSI

        session = _get_prompt_session(chat_state, mem)
    except Exception:  # noqa: BLE001 — never let the input path die
        return read_pasted_message(prompt)

    # Resolve the life-dir once; the panel callable re-renders fresh each refresh.
    try:
        life_dir = _life_dir_for(mem)
    except Exception:  # noqa: BLE001
        life_dir = None

    def _panel_text() -> str:
        """Status text drawn above the prompt (empty if unavailable).

        Full four-role panel by default (``_live_cockpit_enabled``); falls
        back to the same compact one-liner the plain engine shows if the
        operator opts out (``ARGUS_SKILL_COCKPIT_LIVE=0``) or panel
        rendering itself fails, so turning the panel off doesn't also erase
        all status visibility."""
        if life_dir is None:
            return ""
        if _live_cockpit_enabled():
            try:
                import shutil

                from ..cli.roles_status import render_roles_snapshot
                width = shutil.get_terminal_size((80, 24)).columns
                return render_roles_snapshot(life_dir, theme, width=width) + "\n"
            except Exception:  # noqa: BLE001 — fall back to the one-liner below
                pass
        try:
            from ..cli.roles_status import format_prompt_status_line
            s = format_prompt_status_line(theme, life_dir=life_dir)
            return (s + "\n") if s else ""
        except Exception:  # noqa: BLE001
            return ""

    # Print the status ABOVE the input as ordinary scrollback — NOT folded into
    # the prompt `message`. Folding a tall panel into the message pushed the
    # input line near the bottom, so prompt_toolkit flipped the `/` completion
    # menu ABOVE the input. With it printed above and only the short ╭─/╰─ box
    # handed to prompt_toolkit, the menu positions cleanly BELOW the input
    # (its normal behaviour). Trade-off: it repaints each turn (when you
    # return to the prompt), not sub-second while idle.
    try:
        panel = _panel_text()
        if panel:
            sys.stdout.write(panel)
            sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass

    try:
        return session.prompt(ANSI(prompt))
    except EOFError:
        return None
    except KeyboardInterrupt:
        # Intentionally propagates to the caller (arms the double-Ctrl-C exit).
        raise
    except Exception:  # noqa: BLE001 — never let a PT hiccup kill the cockpit
        # An odd terminal, a resize race, or any prompt_toolkit-internal error
        # must NOT crash the REPL. Disable PT for the rest of the session and
        # fall back to the plain, always-reliable reader for this and every
        # subsequent turn.
        chat_state.pop("prompt_session", None)
        chat_state["prompt_toolkit_disabled"] = True
        return read_pasted_message(prompt)


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
    put at risk). ON by default — showing multi-role progress automatically,
    with no manual step required, is the whole point; set
    ``ARGUS_SKILL_COCKPIT_LIVE=0`` to opt back OUT to the plain prompt (e.g.
    for scripting/logging where a redrawing panel is unwanted)."""
    from ..apps._input_helpers import read_pasted_message
    if not _live_cockpit_will_activate(mem):
        return read_pasted_message(prompt)
    # _live_cockpit_will_activate() already proved these import cleanly (that is
    # part of what it checks), but a successful import in ITS scope does not
    # make the names available here — each scope that uses termios/tty needs
    # its own import.
    import termios
    import tty
    life_dir = _life_dir_for(mem)

    # This path is ON by default. It uses terminal cursor rewrites — every
    # unsupported condition above degrades to the boring, always-reliable
    # plain input path instead, so enabling it by default never risks the
    # core input path itself.
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

    import select as _select

    from ..cli.roles_status import render_roles_snapshot

    hint = ("Just start typing to chat · Ctrl-C refresh · Ctrl-D exit"
            if theme is not None else "(type to chat · Ctrl-D exits)")

    def _block() -> str:
        # Re-queried every refresh (not hoisted above the loop): the operator
        # can resize their terminal while this panel idles, and a width
        # baked in once at the top would silently wrap the padded
        # "roles · activity" header line on the next redraw — desyncing the
        # "move up N rows" erase math the same way a stale ``theme.width``
        # did (see ``Theme.live_width``).
        width = theme.live_width() if theme is not None else 80
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
        up = _visual_row_delta(block)
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
            up = _visual_row_delta(block)
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
    # ``prompt`` here is still the multi-line, escape-laden string the caller
    # built for THIS function's own _block() rendering (banner row + "\n" +
    # input-row prefix + "\n" + bottom hint line + a trailing
    # cursor_up_and_forward escape) — never meant to be handed to
    # input()/readline directly. Every return below that hands off to
    # read_pasted_message(prompt) used to do exactly that, corrupting the
    # display the instant readline redrew internally: readline counts the
    # embedded "\n"s to learn where editing starts, but the trailing escape
    # moves the ACTUAL cursor to a different row than that count implies —
    # the exact "Round 1" class of bug already fixed for the non-live-cockpit
    # prompt path (see the caller). Reproduce that fix's pattern here: print
    # everything except the input row directly, land the cursor on a blank
    # input row, and hand read_pasted_message only the bare input-row prefix.
    _split = _split_readline_safe_prompt(prompt, theme)
    if _split is not None:
        _pre_print, prompt = _split
        sys.stdout.write(_pre_print)
        sys.stdout.flush()
    if interrupted:
        # Ctrl-C while idle: propagate so the REPL loop arms double-Ctrl-C exit
        # (the panel re-renders on the next loop iteration anyway).
        raise KeyboardInterrupt
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
    # -ing status line during the idle gaps: this passive tail is the DEFAULT
    # follow path, so without it the pre-first-event wait is a frozen blinking
    # cursor. Cleared before every real event line and on exit.
    spinner = _TailWaitSpinner(theme)
    printer = _TailPrinter(spinner)
    try:
        # Wait for the log to exist, then seek to its end so we only show
        # events produced from now on.
        while fh is None:
            try:
                fh = events_path.open("r", encoding="utf-8")
                fh.seek(0, os.SEEK_END)
            except FileNotFoundError:
                spinner.tick()
                time.sleep(_TAIL_SPIN_INTERVAL)
            except OSError:
                return None
        while True:
            line = fh.readline()
            if not line:
                printer.flush_idle()
                spinner.tick()
                time.sleep(_TAIL_SPIN_INTERVAL)
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
            printer.feed(event, rendered)
            if str(event.get("type") or "") == "life.mission.completed":
                if until_first_completion or (
                    until_item_id and str(event.get("item_id") or "") == until_item_id
                ):
                    if last_review is not None:
                        event.setdefault("_last_review", last_review)
                    printer.flush()
                    spinner.clear()
                    return event
    except KeyboardInterrupt:
        printer.flush()
        spinner.clear()
        note = "\n(stopped following — daemon keeps running; /status to check)"
        print(theme.gray(note) if theme is not None else note, flush=True)
    finally:
        printer.flush()
        spinner.clear()
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
        "daily cap, safe_mode, show_reasoning, telegram"))
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
            engineer_model=resolve_role_model(
                "engineer",
                role_env="ARGUS_SKILL_ENGINEER_MODEL",
            ),
            reviewer_model=resolve_role_model(
                "reviewer",
                role_env="ARGUS_SKILL_REVIEWER_MODEL",
            ),
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


def _with_manager_spinner(theme: object | None, label: str, fn: Callable[[], Any]) -> Any:
    """Run blocking ``fn`` while showing the cockpit's manager-tinted braille
    spinner, so a model round-trip on the TEAM-handoff path never looks frozen.
    No-op animation on non-TTY / piped / NO_COLOR (LiveStatus gates itself).

    ``fn`` runs EXACTLY once: if the spinner cannot be built we fall back to a
    bare call, but an exception from ``fn`` itself propagates unchanged."""
    try:
        from ..cli.live_status import LiveStatus
        from ..cli.roles_status import ROLE_COLOR_BOLD

        cm = LiveStatus(
            label, theme=theme, accent=ROLE_COLOR_BOLD.get("manager", "magenta")
        )
    except Exception:  # noqa: BLE001 — spinner setup only; never mask fn
        return fn()
    with cm:
        return fn()


def _manager_divide_user_task(
    mem: Any, body: str, chat_state: dict[str, Any], *, theme: object | None = None
) -> None:
    """Run Manager division for an operator-submitted task before enqueue.

    This is intentionally a USER-ENTRY gate. Planner-generated backlog items are
    already the Planner's decomposition and must not be routed back through
    Manager again.

    ``Manager.divide`` makes a blocking model round-trip (``decide_vertical``), so
    the caller passes ``theme`` to keep the cockpit's spinner animating during it
    — otherwise the TEAM-handoff window looks frozen.
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
        division = _with_manager_spinner(
            theme, "Manager choosing the vertical…", lambda: mgr.divide(body, ask_on_new_domain=False)
        )
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
                    budget: float = 30.0, theme: object | None = None,
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
    _manager_divide_user_task(mem, body, chat_state, theme=theme)
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
                     iteration_max_cycles=max_cycles, iteration_budget_usd=budget)
    _maybe_name_session(chat_state, body)
    daemon_alive, daemon_pid = _daemon_alive_for(life_dir)
    if not daemon_alive and chat_state.get("auto_start_daemon_on_task"):
        daemon_alive, daemon_pid = _with_manager_spinner(
            theme, "Starting the executor daemon…",
            lambda: _autospawn_daemon_for_task(mem, chat_state),
        )
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
            is_standing = bool(classify(body))
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


def _maybe_handle_config_intent(mem: Any, text: str, chat_state: dict[str, Any]) -> bool:
    """Recognize + apply a natural-language change to one of Argus's OWN runtime
    knobs (a role's backend/model/effort, a budget cap, or the safe_mode/
    show_reasoning/telegram toggles) BEFORE it becomes work.

    One low-reasoning LLM call decides intent (Manager.classify_config_intent →
    life.router.classify_config_intent) — there is NO keyword/regex matching, so
    a request phrased any way is caught and a bare mention of a model/backend is
    not misread as a switch. Fail-soft: no runner, a classify error, or a NONE
    verdict all return False, and the text flows on to the normal chat/task path.
    Returns True iff it applied a change (and the turn is done)."""
    runner = _ensure_manager_runner(chat_state, mem)
    mgr = getattr(runner, "manager", None) if runner is not None else None
    if mgr is None or not hasattr(mgr, "classify_config_intent"):
        return False
    try:
        intent = mgr.classify_config_intent(text)
    except Exception:  # noqa: BLE001 — a classify hiccup must never break the turn
        return False
    if intent is None:
        return False
    return _apply_config_intent(mem, intent, chat_state)


def _apply_config_intent(mem: Any, intent: Any, chat_state: dict[str, Any]) -> bool:
    """Apply a parsed ConfigIntent: set the env var(s), persist via knob_store
    (so a running daemon reads the switch immediately), confirm, and ground the
    Manager with a note. Returns True iff a change was applied."""
    from ..core.knob_store import write_persisted_knob

    theme = chat_state.get("theme")

    def _confirm(line: str) -> None:
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
    iterate, max_cycles, budget, body = _parse_add_flags(
        text,
        default_iterate=cfg.get("iterate", True),
        default_cycles=cfg.get("cycles", 6),
        default_budget=cfg.get("budget", 30.0),
    )
    body = body or text.strip()
    theme = chat_state.get("theme")

    # Natural-language change to one of Argus's own runtime knobs (backend /
    # model / effort / budget cap / a toggle)? One LLM intent call decides —
    # no keyword/regex matching — before the text becomes research work.
    if _maybe_handle_config_intent(mem, body, chat_state):
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

                reply = manager_triage(mem, body, chat_state, on_phase=_on_phase)
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
            continuous = _maybe_auto_promote_to_continuous(mem, body, chat_state, theme)

    item, daemon_alive, daemon_pid = enqueue_mission(
        mem, body, chat_state, iterate=iterate, max_cycles=max_cycles,
        budget=budget, theme=theme)
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
        rc = _run_manager_repl_locked(args, mem, created, chat_state=chat_state)
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
    created: list[str],
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
    print("  " + theme.gray("session ") + theme.cyan(str(_sid or "-")))
    try:
        from ..daemon.life_worker import read_daemon_status as _read_daemon_status
        _ds = _read_daemon_status(mem.project.root)
        _pid = str(_ds.pid) if getattr(_ds, "alive", False) and getattr(_ds, "pid", None) else "-"
    except Exception:  # noqa: BLE001
        _pid = "-"
    print("  " + theme.gray("daemon  ") + theme.cyan(_pid))
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
        banner_row = (
            theme.cyan("╭─ ") + base_prompt
            + (resume_marker if chat_state.get("last_thread_id") else "")
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
        if cmd == "/resume":
            _resume_cmd(mem, chat_state, global_root, rest_text)
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
            _manager_divide_user_task(mem, body, chat_state, theme=theme)
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
