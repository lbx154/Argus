"""Shared human-log rendering primitives (domain-agnostic plumbing).

The live ``--follow`` terminal view (:mod:`argus_skill.apps.cli._follow`) needs
consistent timestamping, wrapping and "what is the current mission" state. This
module owns those cross-cutting concerns:

* **Grouping** — a tiny streaming state machine (:class:`LogState` +
  :func:`advance`) that tracks the current mission / planner cycle so events
  can be drawn as an indented tree (``┌─`` open, ``│`` interior, ``└─`` close,
  ``·`` standalone). Mission/round events do not all carry an ``item_id`` and
  missions run sequentially in the daemon, so grouping must be positional.
* **Time** — :func:`format_timestamp` renders LOCAL ``HH:MM:SS`` plus a relative
  ``(+Δ)`` gap since the previous rendered line (:func:`gap_str`).
* **Full text** — :func:`wrap_body` word-wraps long reason/detail text onto
  continuation lines instead of truncating it (CJK width-aware, no deps).
* **Assembly** — :func:`block` composes a head line (+ wrapped detail) with the
  right glyph/indentation; an optional paint callback adds ANSI color for TTY
  callers.

Deliberately stdlib-only: it must NOT import from ``argus_skill.life`` or
``argus_skill.cli`` (the former would invert the layering, the latter would
drag the whole CLI render stack into the daemon).  This is presentation
plumbing — it makes no research/quality judgement of any kind.
"""
from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .event_catalog import EventType, canonical_event_type

# ── connectors (returned by ``advance``) ──────────────────────────────────
OPEN = "open"    # first line of a group (mission / planner cycle)
MID = "mid"      # interior line of an open group
CLOSE = "close"  # last line of a group
FLAT = "flat"    # standalone line, not part of any group

_GLYPH = {
    OPEN: "┌─ ",
    MID: "│  ",
    CLOSE: "└─ ",
    FLAT: "·  ",
}

CAT_W = 8         # category column width (keeps grep-friendly alignment)
TS_W = 18         # timestamp column width ("HH:MM:SS (+1h3m)" + slack)
DEFAULT_WIDTH = 100
MARK = "↳"        # continuation marker for wrapped detail


# ── grouping state machine ────────────────────────────────────────────────

@dataclass
class LogState:
    """Streaming context for tree grouping. One per sink / follow loop."""

    mission_open: bool = False
    mission_id: str = ""
    mission_seq: int | None = None
    mission_title: str = ""
    round_index: int | None = None
    planner_open: bool = False
    prev_ts: float | None = None


def _round_of(event: dict[str, Any]) -> int | None:
    for key in ("round_index", "round"):
        val = event.get(key)
        if isinstance(val, int):
            return val
        if isinstance(val, str) and val.isdigit():
            return int(val)
    return None


def advance(state: LogState, etype: str, event: dict[str, Any]) -> str:
    """Update ``state`` for one event and return its connector.

    Positional, not id-joined: interior round events carry only
    ``round_index`` (no ``item_id``) and missions are sequential, so a single
    ``mission_open`` flag suffices.  A mid-group event seen with nothing open
    (fresh sink / daemon restart) degrades to ``FLAT`` rather than faking a
    header — safe for a streaming log.
    """
    etype = canonical_event_type(etype)
    if etype == EventType.LIFE_MISSION_STARTED:
        state.mission_open = True
        state.planner_open = False
        state.mission_id = str(event.get("item_id") or "")
        seq = event.get("missions_started")
        state.mission_seq = seq if isinstance(seq, int) else None
        state.mission_title = str(event.get("title") or "")
        state.round_index = None
        return OPEN
    if etype in (
        EventType.LIFE_MISSION_COMPLETED,
        EventType.LIFE_MISSION_ORPHANED,
    ):
        was = state.mission_open
        state.mission_open = False
        state.round_index = None
        return CLOSE if was else FLAT
    if etype == EventType.LIFE_PLANNER_START:
        state.mission_open = False
        state.planner_open = True
        return OPEN
    if etype in (EventType.LIFE_PLANNER_VERDICT, EventType.LIFE_PLANNER_ERROR):
        was = state.planner_open
        state.planner_open = False
        return CLOSE if was else FLAT
    if etype in (
        EventType.LIFE_PHASE_STARTED,
        EventType.ROUND_START,
        EventType.ROUND_MAIN_COMPLETED,
        EventType.ROUND_REVIEW_COMPLETED,
        "engineer.failure_nudge",
        EventType.LIFE_MANAGER_STAGE_DECISION,
    ):
        r = _round_of(event)
        if r is not None:
            state.round_index = r
        return MID if state.mission_open else FLAT
    if etype in (EventType.LIFE_PLANNER_TASK_ADDED, "life.planner.deferred"):
        return MID if state.planner_open else FLAT
    if etype in ("life.supervisor.error", "life.auth_failure"):
        return MID if (state.mission_open or state.planner_open) else FLAT
    return FLAT


# ── timestamps ────────────────────────────────────────────────────────────

def gap_str(delta: float) -> str:
    """Compact relative gap, e.g. ``+0s`` / ``+37s`` / ``+2m5s`` / ``+1h3m``."""
    d = max(0, int(delta))
    if d < 60:
        return f"+{d}s"
    if d < 3600:
        m, s = divmod(d, 60)
        return f"+{m}m{s}s" if s else f"+{m}m"
    if d < 86400:
        h, rem = divmod(d, 3600)
        m = rem // 60
        return f"+{h}h{m}m" if m else f"+{h}h"
    days, rem = divmod(d, 86400)
    h = rem // 3600
    return f"+{days}d{h}h" if h else f"+{days}d"


def local_hms(ts: float | None) -> str:
    try:
        seconds = float(ts)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        seconds = time.time()
    return datetime.fromtimestamp(seconds).strftime("%H:%M:%S")


def format_timestamp(ts: float | None, prev_ts: float | None) -> str:
    """Local ``HH:MM:SS (+Δ)`` left-justified to :data:`TS_W`.

    ``ts`` may be ``None`` (the activity-log sink sees pre-``ts`` events) — we
    fall back to wall-clock now so the gap stays meaningful.
    """
    try:
        seconds = float(ts)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        seconds = time.time()
    gap = gap_str(seconds - prev_ts) if prev_ts is not None else "+0s"
    return f"{local_hms(seconds)} ({gap})".ljust(TS_W)


# ── width-aware wrapping (no truncation) ──────────────────────────────────

def _char_w(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _disp_width(s: str) -> int:
    return sum(_char_w(c) for c in s)


def _hard_split(token: str, width: int) -> list[str]:
    """Break a single token wider than ``width`` into width-bounded pieces."""
    out: list[str] = []
    cur = ""
    cur_w = 0
    for ch in token:
        cw = _char_w(ch)
        if cur and cur_w + cw > width:
            out.append(cur)
            cur, cur_w = "", 0
        cur += ch
        cur_w += cw
    if cur:
        out.append(cur)
    return out


def wrap_body(text: str, width: int) -> list[str]:
    """Word-wrap ``text`` to ``width`` display cells. Never truncates.

    Whitespace-collapsed; overlong unbreakable tokens are hard-split. Returns
    ``[]`` for empty input.  ``" ".join(wrap_body(t, w))`` round-trips the
    collapsed text.
    """
    width = max(2, int(width))
    words = str(text).split()
    if not words:
        return []
    lines: list[str] = []
    cur = ""
    cur_w = 0
    for word in words:
        for piece in (_hard_split(word, width) if _disp_width(word) > width else [word]):
            pw = _disp_width(piece)
            if not cur:
                cur, cur_w = piece, pw
            elif cur_w + 1 + pw <= width:
                cur += " " + piece
                cur_w += 1 + pw
            else:
                lines.append(cur)
                cur, cur_w = piece, pw
    if cur:
        lines.append(cur)
    return lines


# ── assembly ──────────────────────────────────────────────────────────────

_Paint = Callable[[str], str]


def _ident(s: str) -> str:
    return s


def _cat_pad(category: str) -> int:
    return max(0, CAT_W - len(category)) + 1


def block(
    ts_field: str,
    connector: str,
    category: str,
    primary: str,
    detail: str = "",
    *,
    width: int = DEFAULT_WIDTH,
    paint_connector: _Paint | None = None,
    paint_category: _Paint | None = None,
) -> str:
    """Assemble one event into a head line plus wrapped continuation lines.

    ``ts_field`` should come from :func:`format_timestamp` (already padded).
    Two overflow shapes, chosen by whether ``primary`` is set:

    * ``primary`` present — the structured tokens sit on the head; a separate
      ``detail`` (reason/explanation) wraps below, each block led by ``↳``.
    * ``primary`` empty — a pure free-form event (status / error / inbox): the
      ``detail`` text flows from the head line downward as plain continuation.

    ``paint_*`` add ANSI color (TTY caller); omit them for a plain file. Paint
    callbacks only decorate (no display width), so column maths use plain text.
    """
    pc = paint_connector or _ident
    pk = paint_category or _ident
    glyph = _GLYPH.get(connector, _GLYPH[FLAT])
    pad = _cat_pad(category)
    cat_painted = pk(category) + " " * pad
    prefix_w = TS_W + 2 + _disp_width(glyph) + len(category) + pad
    head_prefix = f"{ts_field}  {pc(glyph)}{cat_painted}"

    bar = pc("│") if connector in (OPEN, MID) else " "
    cont_lead = f"{' ' * TS_W}  {bar}  "
    cont_w = max(8, width - (TS_W + 2 + 1 + 2))

    if primary:
        out = [f"{head_prefix}{primary}".rstrip()]
        for i, ln in enumerate(wrap_body(detail, cont_w)):
            marker = f"{MARK} " if i == 0 else "  "
            out.append(f"{cont_lead}{marker}{ln}")
        return "\n".join(out)

    # Pure free-form event: flow detail from the head downward (no ↳ marker).
    chunks = wrap_body(detail, max(8, width - prefix_w)) or [""]
    out = [f"{head_prefix}{chunks[0]}".rstrip()]
    out.extend(f"{cont_lead}{ln}" for ln in chunks[1:])
    return "\n".join(out)


def glyph_for(connector: str) -> str:
    """Public accessor for the raw 3-char connector glyph."""
    return _GLYPH.get(connector, _GLYPH[FLAT])


def interior(state: LogState, connector: str) -> str:
    """Reclassify a ``FLAT`` line to ``MID`` while a group is open.

    The live ``--follow`` view shows many non-milestone events (agent
    progress, telemetry, round starts) that :func:`advance` leaves ``FLAT``;
    inside an open mission/planner group they should nest as ``│`` interior
    lines so the tree reads continuously.
    """
    if connector == FLAT and (state.mission_open or state.planner_open):
        return MID
    return connector


def follow_line(
    ts_field: str,
    connector: str,
    body: str,
    *,
    width: int = DEFAULT_WIDTH,
    paint_connector: _Paint | None = None,
) -> str:
    """Prefix a pre-formatted live-view ``body`` with timestamp + tree glyph.

    Unlike :func:`block`, ``body`` is an already-rendered string (emoji +
    layer label + text) owned by the follow renderer; this only adds the
    shared timestamp/glyph column and wraps the whole thing (no truncation)
    onto aligned continuation lines.
    """
    pc = paint_connector or _ident
    glyph = _GLYPH.get(connector, _GLYPH[FLAT])
    prefix_w = TS_W + 2 + _disp_width(glyph)
    chunks = wrap_body(" ".join(str(body).split()), max(8, width - prefix_w)) or [""]
    bar = pc("│") if connector in (OPEN, MID) else " "
    cont_lead = f"{' ' * TS_W}  {bar}  "
    out = [f"{ts_field}  {pc(glyph)}{chunks[0]}".rstrip()]
    out.extend(f"{cont_lead}{c}" for c in chunks[1:])
    return "\n".join(out)


__all__ = [
    "OPEN", "MID", "CLOSE", "FLAT",
    "CAT_W", "TS_W", "DEFAULT_WIDTH", "MARK",
    "LogState", "advance", "interior",
    "gap_str", "local_hms", "format_timestamp",
    "wrap_body", "block", "follow_line", "glyph_for",
]
