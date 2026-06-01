"""High-signal human-readable activity log.

The daemon already emits a rich structured event stream to
``events.jsonl`` (machine replay) and a verbose ``daemon.log`` (raw codex
stdout + Python logging). Neither is pleasant to read when you just want
to answer "what is the agent doing right now / what did it do" while
debugging a run.

``ActivityLogSink`` is a decorator sink (same pattern as
:class:`argus_skill.life.event_log.JsonlEventSink`) that passes every
event through to its downstream unchanged, and *additionally* renders a
small allow-list of milestone events as one concise line each to
``<life_dir>/activity.log``.

Design goals:

* High signal only. We deliberately render a curated allow-list of
  milestones (mission start/end, phase changes, planner decisions and
  enqueued tasks, errors, inbox, bootstrap, stop). Everything else —
  raw stream lines, token-accounting events, idle polling, periodic
  telemetry — is dropped from this log. ``events.jsonl`` remains the
  exhaustive surface.
* One line per milestone: ``<iso-ts>  <CATEGORY> <detail>``. Stable,
  greppable, column-aligned categories.
* Never crash the supervisor. Downstream is called first; disk failures
  are swallowed.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

ACTIVITY_FILE = "activity.log"
ROLL_FILE = "activity.log.1"
ROLL_BYTES = 20 * 1024 * 1024  # 20 MiB — a cockpit log, not an audit trail


class _Sink(Protocol):
    def handle_event(self, event: dict[str, Any]) -> None: ...


def _txt(event: dict[str, Any], *keys: str, limit: int = 160) -> str:
    """First non-empty string field among ``keys`` (truncated)."""
    for key in keys:
        val = event.get(key)
        if val:
            return str(val)[:limit]
    return ""


def _money(event: dict[str, Any], key: str = "cost_usd") -> str:
    try:
        return f"${float(event.get(key, 0.0)):.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _mission_started(e: dict[str, Any]) -> str:
    n = e.get("missions_started")
    head = f"start   #{n}" if n is not None else "start"
    return f"MISSION  {head}  {_txt(e, 'title', 'item_id')}"


def _mission_completed(e: dict[str, Any]) -> str:
    status = _txt(e, "status") or ("done" if e.get("success") else "failed")
    parts = [f"done    {status}", f"success={bool(e.get('success'))}"]
    parts.append(f"rounds={e.get('rounds', 0)}")
    parts.append(f"cost={_money(e)}")
    return "MISSION  " + "  ".join(parts)


def _mission_orphaned(e: dict[str, Any]) -> str:
    detail = _txt(e, "title", "item_id")
    err = _txt(e, "error")
    line = f"MISSION  orphan  {detail}"
    return f"{line} :: {err}" if err else line


def _phase_started(e: dict[str, Any]) -> str:
    layer = _txt(e, "agent_layer") or "?"
    return f"PHASE    {layer:<9} round={e.get('round_index', 0)}"


def _round_main_completed(e: dict[str, Any]) -> str:
    return f"ROUND    engineer  round={e.get('round_index', '?')}  built"


def _round_review_completed(e: dict[str, Any]) -> str:
    status = _txt(e, "status") or "?"
    conf = e.get("confidence")
    conf_part = f"  conf={conf:.2f}" if isinstance(conf, (int, float)) else ""
    reason = _txt(e, "reason", limit=120)
    line = f"ROUND    reviewer  round={e.get('round_index', '?')}  verdict={status}{conf_part}"
    return f"{line} :: {reason}" if reason else line


def _failure_nudge(e: dict[str, Any]) -> str:
    detail = _txt(e, "text", "reason") or "repeated tool failures"
    return f"WARN     engineer  round={e.get('round', '?')}  {detail}"


def _planner_start(_e: dict[str, Any]) -> str:
    return "PLANNER  start"


def _planner_verdict(e: dict[str, Any]) -> str:
    parts = [
        "verdict",
        f"project_done={bool(e.get('project_done'))}",
        f"proposed={e.get('task_count', 0)}",
        f"enqueued={e.get('enqueued_tasks', 0)}",
    ]
    skipped = e.get("skipped_duplicate_tasks", 0)
    if skipped:
        parts.append(f"skipped_dup={skipped}")
    reason = _txt(e, "reason")
    line = "PLANNER  " + "  ".join(parts)
    return f"{line} :: {reason}" if reason else line


def _planner_task_added(e: dict[str, Any]) -> str:
    impact = e.get("impact_score")
    head = f"+task   impact={impact}" if impact is not None else "+task"
    return f"PLANNER  {head}  {_txt(e, 'title')}"


def _planner_error(e: dict[str, Any]) -> str:
    return f"PLANNER  error   {_txt(e, 'text', 'reason', 'error')}"


def _planner_deferred(e: dict[str, Any]) -> str:
    return f"PLANNER  defer   {_txt(e, 'text', 'reason')}"


def _supervisor_error(e: dict[str, Any]) -> str:
    return f"ERROR    supervisor  {_txt(e, 'text', 'error', 'status')}"


def _auth_failure(e: dict[str, Any]) -> str:
    return f"ERROR    auth        {_txt(e, 'text', 'error')}"


def _inbox_queued(e: dict[str, Any]) -> str:
    return f"INBOX    queued   {_txt(e, 'text')}"


def _inbox_drained(e: dict[str, Any]) -> str:
    return f"INBOX    drained  {_txt(e, 'text')}"


def _bootstrap_required(e: dict[str, Any]) -> str:
    return f"BOOTSTRAP required  {_txt(e, 'text', 'reason')}"


def _post_mission_stop(e: dict[str, Any]) -> str:
    return f"STOP     post-mission  {_txt(e, 'text', 'reason')}"


def _life_status(e: dict[str, Any]) -> str:
    return f"STATUS   {_txt(e, 'text')}"


# Curated allow-list: event ``type`` -> one-line renderer. Anything not
# listed here is intentionally omitted from activity.log.
RENDERERS: dict[str, Callable[[dict[str, Any]], str]] = {
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


def render_line(event: dict[str, Any]) -> str | None:
    """Render a single milestone event, or ``None`` to drop it."""
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


def _iso(ts: Any) -> str:
    try:
        seconds = float(ts)
    except (TypeError, ValueError):
        seconds = time.time()
    return (
        datetime.fromtimestamp(seconds, tz=timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


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
        line = render_line(event)
        if line is None:
            return
        ts = event.get("ts") if isinstance(event, dict) else None
        self._append(f"{_iso(ts)}  {line}")

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

    def _append(self, line: str) -> None:
        with self._lock:
            try:
                self._maybe_roll()
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
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
    "RENDERERS",
    "ACTIVITY_FILE",
    "ROLL_FILE",
    "ROLL_BYTES",
]
