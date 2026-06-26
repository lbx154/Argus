"""High-signal human-readable activity log.

The daemon already emits a rich structured event stream to
``events.jsonl`` (machine replay) and a verbose ``daemon.log`` (raw codex
stdout + Python logging). Neither is pleasant to read when you just want
to answer "what is the agent doing right now / what did it do" while
debugging a run.

``ActivityLogSink`` is a decorator sink (same pattern as
:class:`argus_skill.life.event_log.JsonlEventSink`) that passes every
event through to its downstream unchanged, and *additionally* renders a
small allow-list of milestone events to ``<life_dir>/activity.log``.

Design goals:

* High signal only. We deliberately render a curated allow-list of
  milestones (mission start/end, phase changes, planner decisions and
  enqueued tasks, errors, inbox, bootstrap, stop). Everything else —
  raw stream lines, token-accounting events, idle polling, periodic
  telemetry — is dropped from this log. ``events.jsonl`` remains the
  exhaustive surface.
* Human-readable structure, via the shared
  :mod:`argus_skill.core.log_view` primitives (also used by the live
  ``--follow`` view, so both surfaces stay consistent):

  - events are grouped into an indented tree by mission / planner cycle
    (``┌─`` open, ``│`` interior, ``└─`` close, ``·`` standalone);
  - timestamps are LOCAL ``HH:MM:SS`` plus a relative ``(+Δ)`` gap;
  - long reason/status text is wrapped onto continuation lines, never
    truncated.
* Never crash the supervisor. Downstream is called first; disk failures
  are swallowed.

Each milestone is decomposed into ``(category, primary, detail)`` —
``category`` is the grep-friendly column (MISSION/ROUND/PLANNER/…),
``primary`` the short structured tokens, ``detail`` the free-form text
that wraps. :func:`render_line` keeps the legacy one-line form for
external callers / tests.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from ..core import log_view as lv

ACTIVITY_FILE = "activity.log"
ROLL_FILE = "activity.log.1"
ROLL_BYTES = 20 * 1024 * 1024  # 20 MiB — a cockpit log, not an audit trail


class _Sink(Protocol):
    def handle_event(self, event: dict[str, Any]) -> None: ...


def _txt(event: dict[str, Any], *keys: str, limit: int = 160) -> str:
    """First non-empty string field among ``keys`` (truncated).

    Used for SHORT identifying fields (title, layer) on the head line.
    """
    for key in keys:
        val = event.get(key)
        if val:
            return str(val)[:limit]
    return ""


def _full(event: dict[str, Any], *keys: str) -> str:
    """First non-empty field among ``keys``, full text (no truncation).

    Used for the free-form ``detail`` that :func:`log_view.block` wraps.
    """
    for key in keys:
        val = event.get(key)
        if val:
            return str(val).strip()
    return ""


def _money(event: dict[str, Any], key: str = "cost_usd") -> str:
    try:
        return f"${float(event.get(key, 0.0)):.2f}"
    except (TypeError, ValueError):
        return "$0.00"


# ── per-type renderers: event -> (category, primary, detail) ──────────────
#
# ``primary`` holds short, grep-friendly tokens that sit on the head line;
# ``detail`` holds free-form text (reason / message) that wraps below.

_Parts = tuple[str, str, str]


def _mission_started(e: dict[str, Any]) -> _Parts:
    n = e.get("missions_started")
    head = f"start   #{n}" if n is not None else "start"
    return ("MISSION", f"{head}  {_txt(e, 'title', 'item_id')}".rstrip(), "")


def _mission_completed(e: dict[str, Any]) -> _Parts:
    status = _txt(e, "status") or ("done" if e.get("success") else "failed")
    parts = [
        f"done    {status}",
        f"success={bool(e.get('success'))}",
        f"rounds={e.get('rounds', 0)}",
        f"cost={_money(e)}",
    ]
    return ("MISSION", "  ".join(parts), "")


def _mission_orphaned(e: dict[str, Any]) -> _Parts:
    detail = _txt(e, "title", "item_id")
    return ("MISSION", f"orphan  {detail}".rstrip(), _full(e, "error"))


def _phase_started(e: dict[str, Any]) -> _Parts:
    layer = _txt(e, "agent_layer") or "?"
    return ("PHASE", f"{layer:<9} round={e.get('round_index', 0)}", "")


def _round_main_completed(e: dict[str, Any]) -> _Parts:
    return ("ROUND", f"engineer  round={e.get('round_index', '?')}  built", "")


def _round_review_completed(e: dict[str, Any]) -> _Parts:
    status = _txt(e, "status") or "?"
    conf = e.get("confidence")
    conf_part = f"  conf={conf:.2f}" if isinstance(conf, (int, float)) else ""
    primary = f"reviewer  round={e.get('round_index', '?')}  verdict={status}{conf_part}"
    return ("ROUND", primary, _full(e, "reason"))


def _failure_nudge(e: dict[str, Any]) -> _Parts:
    detail = _full(e, "text", "reason") or "repeated tool failures"
    return ("WARN", f"engineer  round={e.get('round', '?')}", detail)


def _planner_start(_e: dict[str, Any]) -> _Parts:
    return ("PLANNER", "start", "")


def _planner_verdict(e: dict[str, Any]) -> _Parts:
    parts = [
        "verdict",
        f"project_done={bool(e.get('project_done'))}",
        f"proposed={e.get('task_count', 0)}",
        f"enqueued={e.get('enqueued_tasks', 0)}",
    ]
    skipped = e.get("skipped_duplicate_tasks", 0)
    if skipped:
        parts.append(f"skipped_dup={skipped}")
    return ("PLANNER", "  ".join(parts), _full(e, "reason"))


def _planner_task_added(e: dict[str, Any]) -> _Parts:
    impact = e.get("impact_score")
    head = f"+task   impact={impact}" if impact is not None else "+task"
    return ("PLANNER", f"{head}  {_txt(e, 'title')}".rstrip(), "")


def _planner_error(e: dict[str, Any]) -> _Parts:
    return ("PLANNER", "error", _full(e, "text", "reason", "error"))


def _planner_deferred(e: dict[str, Any]) -> _Parts:
    return ("PLANNER", "defer", _full(e, "text", "reason"))


def _supervisor_error(e: dict[str, Any]) -> _Parts:
    return ("ERROR", "supervisor", _full(e, "text", "error", "status"))


def _auth_failure(e: dict[str, Any]) -> _Parts:
    return ("ERROR", "auth", _full(e, "text", "error"))


def _inbox_queued(e: dict[str, Any]) -> _Parts:
    return ("INBOX", "queued", _full(e, "text"))


def _inbox_drained(e: dict[str, Any]) -> _Parts:
    return ("INBOX", "drained", _full(e, "text"))


def _bootstrap_required(e: dict[str, Any]) -> _Parts:
    return ("BOOTSTRAP", "required", _full(e, "text", "reason"))


def _post_mission_stop(e: dict[str, Any]) -> _Parts:
    return ("STOP", "post-mission", _full(e, "text", "reason"))


def _life_status(e: dict[str, Any]) -> _Parts:
    return ("STATUS", "", _full(e, "text"))


# Curated allow-list: event ``type`` -> (category, primary, detail) renderer.
# Anything not listed here is intentionally omitted from activity.log.
RENDERERS: dict[str, Callable[[dict[str, Any]], _Parts]] = {
    "life.mission.started": _mission_started,
    "life.mission.completed": _mission_completed,
    "life.mission.orphaned": _mission_orphaned,
    "life.phase.started": _phase_started,
    "round.main.completed": _round_main_completed,
    "round.review.completed": _round_review_completed,
    "engineer.failure_nudge": _failure_nudge,
    "life.planner.start": _planner_start,
    "life.planner.verdict": _planner_verdict,
    "life.planner.task_added": _planner_task_added,
    "life.planner.error": _planner_error,
    "life.planner.deferred": _planner_deferred,
    "life.supervisor.error": _supervisor_error,
    "life.auth_failure": _auth_failure,
    "life.inbox.queued": _inbox_queued,
    "life.inbox.drained": _inbox_drained,
    "life.project.bootstrap_required": _bootstrap_required,
    "life.post_mission.stop": _post_mission_stop,
}

# ``life.status`` carries free-form supervisor status lines. Most are
# actionable (gate blocks, project done, budget blocks); a couple are
# pure idle chatter we never want in a high-signal log.
_DROP_STATUS_TEXT: frozenset[str] = frozenset({
    "backlog empty; exiting",
    "stop requested while idle",
})


def milestone_parts(event: dict[str, Any]) -> _Parts | None:
    """Resolve an event to ``(category, primary, detail)`` or ``None`` to drop."""
    if not isinstance(event, dict):
        return None
    etype = str(event.get("type", ""))
    renderer = RENDERERS.get(etype)
    if renderer is not None:
        try:
            return renderer(event)
        except Exception:  # noqa: BLE001
            return None
    if etype == "life.status":
        text = str(event.get("text", "")).strip()
        if not text or text in _DROP_STATUS_TEXT:
            return None
        return _life_status(event)
    return None


def render_line(event: dict[str, Any]) -> str | None:
    """Legacy one-line form of a milestone event, or ``None`` to drop it.

    Kept for external callers / tests. The sink itself uses the richer
    grouped/timestamped block; this collapses ``(category, primary, detail)``
    back to ``CATEGORY  primary[ :: detail]`` without timestamp or tree glyph.
    """
    parts = milestone_parts(event)
    if parts is None:
        return None
    category, primary, detail = parts
    if primary and detail:
        body = f"{primary} :: {detail}"
    else:
        body = primary or detail
    return f"{category:<8} {body}".rstrip() if body else category


class ActivityLogSink:
    """Tee milestone events to a concise human-readable ``activity.log``."""

    def __init__(
        self,
        downstream: _Sink | None,
        *,
        life_dir: Path | str,
        roll_bytes: int = ROLL_BYTES,
    ) -> None:
        self._downstream = downstream
        self._dir = Path(life_dir)
        self._path = self._dir / ACTIVITY_FILE
        self._roll_path = self._dir / ROLL_FILE
        self._roll_bytes = max(256 * 1024, int(roll_bytes))
        self._lock = threading.Lock()
        self._state = lv.LogState()
        self._width = lv.DEFAULT_WIDTH
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass

    # --- Sink protocol -----------------------------------------------

    def handle_event(self, event: dict[str, Any]) -> None:
        if self._downstream is not None:
            try:
                self._downstream.handle_event(event)
            except Exception:  # noqa: BLE001
                pass
        block = self._render_block(event)
        if block is None:
            return
        self._append(block)

    def _render_block(self, event: dict[str, Any]) -> str | None:
        parts = milestone_parts(event)
        if parts is None:
            return None
        category, primary, detail = parts
        etype = str(event.get("type", "")) if isinstance(event, dict) else ""
        connector = lv.advance(self._state, etype, event)
        raw = event.get("ts") if isinstance(event, dict) else None
        try:
            secs = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            secs = time.time()
        ts_field = lv.format_timestamp(secs, self._state.prev_ts)
        self._state.prev_ts = secs
        return lv.block(
            ts_field, connector, category, primary, detail, width=self._width
        )

    def handle_stream_line(self, stream: str, line: str) -> None:
        if self._downstream is not None:
            try:
                handler = getattr(self._downstream, "handle_stream_line", None)
                if handler is not None:
                    handler(stream, line)
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        if self._downstream is None:
            return
        try:
            closer = getattr(self._downstream, "close", None)
            if closer is not None:
                closer()
        except Exception:  # noqa: BLE001
            pass

    # --- helpers -----------------------------------------------------

    def _append(self, block: str) -> None:
        with self._lock:
            try:
                self._maybe_roll()
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(block + "\n")
            except Exception:  # noqa: BLE001
                pass

    def _maybe_roll(self) -> None:
        try:
            size = self._path.stat().st_size
        except FileNotFoundError:
            return
        except Exception:  # noqa: BLE001
            return
        if size < self._roll_bytes:
            return
        try:
            if self._roll_path.exists():
                self._roll_path.unlink()
            os.replace(self._path, self._roll_path)
        except Exception:  # noqa: BLE001
            pass


def wrap(
    downstream: _Sink | None,
    *,
    life_dir: Path | str,
    roll_bytes: int = ROLL_BYTES,
) -> ActivityLogSink:
    """Convenience factory mirroring ``event_log.wrap``."""
    return ActivityLogSink(downstream, life_dir=Path(life_dir), roll_bytes=roll_bytes)


__all__ = [
    "ActivityLogSink",
    "wrap",
    "render_line",
    "milestone_parts",
    "RENDERERS",
    "ACTIVITY_FILE",
    "ROLL_FILE",
    "ROLL_BYTES",
]
