"""Live mission-follow rendering for the Manager REPL."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from ..apps._env import env_flag as _env_flag

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

    def set_activity(self, layer: str | None, _note: str = "") -> None:
        """Record the REAL current role so the idle label shows role-appropriate
        motion. ``_note`` is accepted for call-site compatibility but ignored —
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
    import select as _select
    import shutil
    from collections import deque as _deque

    from ..apps.cli._follow import (
        _follow_layer_from_event,
        _format_follow_event,
    )
    from ..cli.roles_status import (
        _clip_ansi_line,
        _disp_width,
        render_roles_snapshot,
        role_activity,
        role_paint,
    )

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

__all__ = [
    "_PANE_IDLE_VERBS",
    "_TAIL_ROLE_TITLES",
    "_TAIL_ROLE_VERBS",
    "_TAIL_SPIN_INTERVAL",
    "_TAIL_WAIT_VERBS",
    "_TailPrinter",
    "_TailWaitSpinner",
    "_follow_events_stream",
    "_sleep_until",
    "_tail_spin_interval",
    "_type_out_line",
    "_wrap_plain",
    "follow_mission_live_roles",
    "tail_mission_events",
]
