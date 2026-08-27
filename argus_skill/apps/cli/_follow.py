"""Display formatting for the cli --follow / status views."""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlencode

from ...core import paths as core_paths
from ...core.role_reply import strip_named_lines
from ...core.secret_guard import known_secret_values, redact_secrets_text
from .._inbox import format_inbox_event
from . import _core

_FOLLOW_LAYER_LABELS = {
    "manager": "Manager",
    "engineer": "Engineer",
    "reviewer": "Reviewer",
    # critic layer removed,
    "planner": "Planner",
}


def _resolve_follow_events_path(args: argparse.Namespace) -> Path:
    if args.life_dir:
        explicit = core_paths.resolve_runtime_path(args.life_dir, context="--life-dir")
        if explicit.name == "events.jsonl":
            return explicit
    bundle = _core._resolve_project_bundle(args)
    return bundle.project.root / "events.jsonl"


def _follow_websocket_url(args: argparse.Namespace) -> str:
    explicit = str(getattr(args, "life_dir", "") or "").strip()
    if explicit:
        path = core_paths.resolve_runtime_path(explicit, context="--life-dir")
        if path.name == "events.jsonl":
            return ""
    bundle = _core._resolve_project_bundle(args)
    host = str(getattr(args, "web_host", "127.0.0.1") or "127.0.0.1")
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    elif ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = int(getattr(args, "web_port", 8799) or 8799)
    query = {"replay": "40", "view": "full"}
    token = str(os.environ.get("ARGUS_SKILL_WEB_TOKEN", "") or "").strip()
    if token:
        query["token"] = token
    return (
        f"ws://{host}:{port}/api/projects/{bundle.project.root.name}/stream?"
        f"{urlencode(query)}"
    )


def _stream_follow_websocket(
    args: argparse.Namespace,
    on_event: Callable[[dict[str, Any]], None],
    *,
    on_idle: Callable[[], None] | None = None,
    connect_factory: Callable[..., Any] | None = None,
) -> bool:
    """Consume the WebAPI's existing event stream until it closes.

    Returns ``False`` when the live endpoint is unavailable or disconnects so
    the caller can continue with the durable ``events.jsonl`` tail.
    """
    if connect_factory is None:
        try:
            from websockets.sync.client import connect as connect_factory
        except ImportError:
            return False
    url = _follow_websocket_url(args)
    if not url:
        return False
    try:
        with connect_factory(
            url,
            open_timeout=1,
            close_timeout=1,
        ) as websocket:
            while True:
                try:
                    raw = websocket.recv(timeout=0.5)
                except TimeoutError:
                    if on_idle is not None:
                        on_idle()
                    continue
                if raw is None:
                    return False
                try:
                    event = json.loads(str(raw))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(event, dict):
                    on_event(event)
    except Exception:  # noqa: BLE001 — file-tail fallback remains available
        return False


def _follow_layer_label(layer: str | None) -> str:
    return _FOLLOW_LAYER_LABELS.get(layer or "", layer or "agent")


def _follow_layer_from_event(event: dict, current: str) -> str:
    layer = event.get("agent_layer")
    if isinstance(layer, str) and layer:
        return layer
    etype = str(event.get("type") or "")
    if etype in {
        "life.mission.started",
        "loop.start",
        "round.start",
        "round.main.completed",
        "round.review.deferred",
    }:
        return "engineer"
    if etype.startswith("life.manager.") or etype.startswith("manager."):
        return "manager"
    if etype in {"round.review.started", "round.review.completed"}:
        return "reviewer"
    if etype in {"life.iteration.critic", "life.iteration.continued"}:
        return "critic"
    if etype.startswith("life.planner."):
        return "planner"
    return current


def _clean_follow_text(text: str, *, limit: int | None = 220) -> str:

    text = redact_secrets_text(
        str(text or ""),
        known_values=known_secret_values(),
    )
    text = re.sub(r"```[a-zA-Z0-9_-]*", " ", text)
    text = text.replace("```", " ")
    text = re.sub(r"\[([^\]]+)\]\(\(?[^)\n]+\)?\)", r"\1", text)
    text = " ".join(text.split())
    # Full-output mode (the TUI sets ARGUS_SKILL_FOLLOW_FULL): never truncate, so
    # the activity pane shows the whole reasoning/command instead of a clipped
    # one-liner. The CLI single-line follow keeps the default cap.
    if os.environ.get("ARGUS_SKILL_FOLLOW_FULL", "").strip() in ("1", "true", "yes", "on"):
        limit = None
    if limit is None or len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _verification_summary(text: str) -> str | None:
    lowered = text.lower()
    if "verification" not in lowered and "verbatim" not in lowered:
        return None
    parts: list[str] = []
    if "[100%]" in text or " passed" in lowered:
        parts.append("tests passed")
    if "All checks passed!" in text:
        parts.append("ruff passed")
    if "Success: no issues found" in text:
        parts.append("mypy passed")
    elif "python -m mypy" in text or "note:" in text:
        parts.append("mypy completed")
    if not parts:
        return None
    return "✅ 验证：" + " · ".join(dict.fromkeys(parts))


def _json_object_from_text(text: str) -> dict | None:
    import json

    stripped = str(text or "").strip()
    if not stripped:
        return None
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(stripped[start:end + 1])
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _select_backlog_row_by_id(
    rows: Sequence[dict[str, Any]],
    item_id: str,
) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("id") or "") == item_id:
            return row
    return None


def _read_backlog_rows(backlog_path: Path) -> list[dict[str, Any]]:
    import json

    rows: list[dict[str, Any]] = []
    try:
        with backlog_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows


def _format_follow_mission_context(
    event: dict,
    *,
    mission_context: dict[str, str] | None = None,
) -> list[str]:
    context = mission_context or {}
    item_id = str(event.get("item_id") or context.get("item_id") or "")
    title = str(event.get("title") or context.get("title") or "")
    objective = str(event.get("objective") or context.get("objective") or "")
    bits = [f"item_id={item_id or '-'}"]
    bits.append(
        f"title={_clean_follow_text(title, limit=None) if title else '-'}"
    )
    bits.append(
        f"objective={_clean_follow_text(objective, limit=None) if objective else '-'}"
    )
    return bits


class _FollowCoalescer:
    """Collapse streamed ``replace``+``message_id`` agent_message beats into a
    single committed render — the standalone-``--follow`` counterpart of the
    cockpit tail's ``_TailPrinter``. Driven by an ``emit(event)`` callback so
    the caller keeps its own timestamp / connector formatting.

    Commits the held message on: a new ``message_id``, any non-``replace``
    event, an idle gap (``>= idle_commit_after`` seconds of stream silence), or
    :meth:`flush`. Within one message the latest snapshot is authoritative, so
    corrections that shorten the final copy do not leave stale text behind.
    """

    def __init__(self, emit: "Callable[[dict], None]", *,
                 idle_commit_after: float = 0.5) -> None:
        self._emit = emit
        self._mid: str | None = None
        self._ev: dict | None = None
        self._at: float = 0.0
        self._idle_after = idle_commit_after

    def _commit(self) -> None:
        if self._ev is not None:
            ev, self._ev, self._mid = self._ev, None, None
            self._emit(ev)

    def feed(self, event: dict) -> None:
        mid = str(event.get("message_id") or "")
        if bool(event.get("replace")) and mid:
            if self._mid is not None and mid != self._mid:
                self._commit()
            self._ev = event
            self._mid = mid
            self._at = time.monotonic()
            return
        self._commit()
        self._emit(event)

    def flush_idle(self) -> None:
        if (
            self._ev is not None
            and time.monotonic() - self._at >= self._idle_after
        ):
            self._commit()

    def flush(self) -> None:
        self._commit()


def _format_follow_agent_message(layer: str, text: str, *, full: bool = False) -> str:
    summary = _verification_summary(text)
    if summary:
        return summary
    data = _json_object_from_text(text)
    if data:
        if layer == "reviewer":
            status = data.get("status", "?")
            reason = _clean_follow_text(str(data.get("reason") or ""), limit=None)
            return f"💭 reviewer verdict: {status}" + (
                f" · {reason}" if reason else ""
            )
        if layer == "critic":
            stop = bool(data.get("stop"))
            improvements = data.get("improvements") or []
            count = len(improvements) if isinstance(improvements, list) else 0
            reason = _clean_follow_text(str(data.get("reason") or ""), limit=None)
            verdict = "stop" if stop else f"continue · {count} improvement(s)"
            return f"💭 critic verdict: {verdict}" + (f" · {reason}" if reason else "")
        if layer == "planner":
            done = bool(data.get("project_done"))
            tasks = data.get("new_tasks") or []
            count = len(tasks) if isinstance(tasks, list) else 0
            reason = _clean_follow_text(str(data.get("reason") or ""), limit=None)
            verdict = "project done" if done else f"queue {count} task(s)"
            return f"💭 planner verdict: {verdict}" + (f" · {reason}" if reason else "")
    body = _clean_follow_text(
        strip_named_lines(
            text,
            ("MILESTONE_STATUS", "NEXT_OWNER", "OPERATOR_QUESTION", "OPERATOR_OPTIONS"),
        ),
        limit=None,
    )
    return "💭 " + body


def _format_follow_command(event: dict) -> str:
    from ...cli.event_format import annotate_progress_result, format_progress_command

    event_for_render = dict(event)
    cmd = redact_secrets_text(
        str(event.get("text") or ""),
        known_values=known_secret_values(),
    )
    event_for_render["text"] = cmd
    parsed = format_progress_command(cmd)
    excerpt = redact_secrets_text(
        str(event.get("output_excerpt") or ""),
        known_values=known_secret_values(),
    )
    compact = excerpt
    if "pytest" in cmd and "[100%]" in excerpt:
        compact = "pytest passed [100%]"
    elif "ruff check" in cmd and "All checks passed!" in excerpt:
        compact = "All checks passed!"
    elif "mypy" in cmd and "Success: no issues found" in excerpt:
        compact = "mypy passed"
    elif "mypy" in cmd and "note:" in excerpt:
        compact = "mypy completed (notes omitted)"
    elif parsed.startswith(("📖", "🔍", "📁", "📂", "🔎")) and not _command_failed(event):
        compact = ""
    if compact:
        event_for_render["output_excerpt"] = compact
    else:
        event_for_render.pop("output_excerpt", None)
    return annotate_progress_result(parsed, event_for_render)


def _read_recent_jsonl_events(
    path: Path,
    *,
    limit: int = 80,
    max_bytes: int = 256 * 1024,
) -> list[dict[str, Any]]:
    """Read a bounded JSONL tail without scanning the whole event log."""
    if limit <= 0:
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=limit)
    try:
        with path.open("rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            start = max(0, size - max(1, int(max_bytes)))
            fh.seek(start)
            raw = fh.read()
    except OSError:
        return []
    if start:
        _, sep, raw = raw.partition(b"\n")
        if not sep:
            return []
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(event, dict):
            rows.append(event)
    return list(rows)


def _read_recent_project_events(
    life_dir: Path,
    *,
    limit: int = 80,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    current = _read_recent_jsonl_events(life_dir / "events.jsonl", limit=limit)
    previous = _read_recent_jsonl_events(
        life_dir / "events.jsonl.1",
        limit=limit,
    )
    return _merge_recent_event_rows(previous, current, limit=limit)


def _merge_recent_event_rows(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Join rollover/live tails while removing only their exact boundary overlap."""
    if limit <= 0:
        return []
    overlap = 0
    for size in range(min(len(previous), len(current)), 0, -1):
        if previous[-size:] == current[:size]:
            overlap = size
            break
    return [*previous, *current[overlap:]][-limit:]


def _format_follow_planner_task_added(event: dict) -> str:
    bits = ["added"]
    if event.get("item_id"):
        bits.append(f"item_id={event['item_id']}")
    if event.get("title"):
        bits.append(f"title={_clean_follow_text(str(event['title']), limit=90)}")
    if event.get("objective"):
        bits.append(f"objective={_clean_follow_text(str(event['objective']), limit=120)}")
    return f"📋 [{_follow_layer_label('planner')}] " + " · ".join(bits)


def _format_follow_planner_task_skipped(event: dict) -> str:
    skip_category = str(event.get("skip_category") or "")
    if skip_category == "recent_no_progress_failure":
        bits = ["quarantined recent no-progress failure"]
    else:
        bits = ["skipped duplicate"]
    if event.get("title"):
        bits.append(f"title={_clean_follow_text(str(event['title']), limit=90)}")
    if event.get("objective"):
        bits.append(f"objective={_clean_follow_text(str(event['objective']), limit=120)}")
    if event.get("matched_item_id"):
        bits.append(f"matched_item_id={event['matched_item_id']}")
    if event.get("matched_title"):
        bits.append(f"matched_title={_clean_follow_text(str(event['matched_title']), limit=90)}")
    if event.get("matched_status"):
        bits.append(f"matched_status={event['matched_status']}")
    if event.get("matched_stop_reason"):
        bits.append(
            f"matched_stop_reason={_clean_follow_text(str(event['matched_stop_reason']), limit=120)}"
        )
    if event.get("skip_category"):
        bits.append(f"skip_category={event['skip_category']}")
    reason = _clean_follow_text(str(event.get("reason") or ""), limit=140)
    if reason:
        bits.append(f"reason={reason}")
    return f"⏭️ [{_follow_layer_label('planner')}] " + " · ".join(bits)


def _command_failed(event: dict) -> bool:
    status = str(event.get("status") or "").lower()
    exit_code = event.get("exit_code")
    return status == "failed" or (
        isinstance(exit_code, int) and exit_code not in (0, None)
    )


_ROLE_TAG_RE = re.compile(r"\[(Manager|Planner|Engineer|Reviewer)\]")


def _colorize_role_tags(theme: Any, text: str) -> str:
    """Recolour every ``[Role]`` tag in ``text`` with that role's signature hue
    (see ``cli.role_colors.ROLE_COLOR``) — a pure text touch-up applied to an
    already-rendered, append-only line. No cursor math, no redraw risk: this
    is the same append-only scrolling feed as before, just with the same
    colour-per-role language used everywhere else in the cockpit."""
    if theme is None:
        return text
    from ...cli.role_colors import role_paint

    def _sub(m: "re.Match[str]") -> str:
        name = m.group(1)
        return role_paint(theme, name, f"[{name}]")

    return _ROLE_TAG_RE.sub(_sub, text)


def _format_follow_event(
    event: dict,
    current_layer: str,
    *,
    mission_context: dict[str, str] | None = None,
    theme: Any = None,
    full: bool = False,
) -> str | None:
    """Render one ``events.jsonl`` line for the scrolling follow view.

    ``theme`` is optional and additive: when given, the ``[Role]`` tag is
    recoloured in that role's signature hue (see ``_colorize_role_tags``);
    omitted entirely (``None``, the default), this is byte-for-byte the
    historical plain-text output every existing caller (the TUI's styled
    feed pane, tests) already relies on. ``full=True`` (the Ctrl+O reasoning
    pane) shows the WHOLE thought with no ``(+N chars)`` clip — the caller
    word-wraps it.
    """
    rendered = _format_follow_event_body(
        event, current_layer, mission_context=mission_context, full=full,
    )
    if rendered and theme is not None:
        return _colorize_role_tags(theme, rendered)
    return rendered


def _format_follow_event_body(
    event: dict,
    current_layer: str,
    *,
    mission_context: dict[str, str] | None = None,
    full: bool = False,
) -> str | None:
    inbox_line = format_inbox_event(event) if isinstance(event, dict) else None
    if inbox_line is not None:
        return f"  {inbox_line}"

    etype = str(event.get("type") or "")
    layer = _follow_layer_from_event(event, current_layer)
    label = _follow_layer_label(layer)

    if etype == "engineer.progress":
        kind = str(event.get("kind") or "")
        text = str(event.get("text") or "")
        if not text:
            return None
        if kind == "agent_message":
            return f"  [{label}] {_format_follow_agent_message(layer, text, full=full)}"
        if kind == "command_execution":
            action = str(event.get("action_summary") or "").strip()
            if action:
                return f"  [{label}] ▸ {action}"
            return f"  [{label}] {_format_follow_command(event)}"
        if kind == "reasoning":
            if os.environ.get("ARGUS_SKILL_SHOW_REASONING", "0").lower() not in (
                "1", "true", "yes", "on",
            ):
                return None
            limit = None if full else 180
            return f"  [{label}] 🧠 {_clean_follow_text(text, limit=limit)}"
        return f"  [{label}] ▸ {_clean_follow_text(text, limit=(None if full else 160))}"

    # Manager events (front-door SELF/TEAM route, vertical division, stage
    # advance/hold/rollback) previously had NO branch here and silently
    # vanished from the follow feed — the operator could watch Engineer /
    # Reviewer / Planner but never see what the Manager itself decided. All
    # four roles now show up in the same scrolling transcript.
    if etype == "life.manager.intent.started":
        return f"🧭 [{_follow_layer_label('manager')}] 判断任务归属…"

    if etype == "life.manager.intent.completed":
        vertical = str(event.get("vertical") or "")
        domain = str(event.get("domain") or "")
        kind = str(event.get("kind") or "")
        bits = [f"→ {vertical}" if vertical else "分流完成"]
        if domain:
            bits.append(f"domain={domain}")
        if kind:
            bits.append(f"kind={kind}")
        return f"🧭 [{_follow_layer_label('manager')}] " + " · ".join(bits)

    if etype == "life.manager.intent.failed":
        phase = str(event.get("phase") or "").strip()
        cause = _clean_follow_text(
            str(event.get("cause") or event.get("backend_error") or ""),
            limit=None,
        )
        err = _clean_follow_text(str(event.get("error") or ""), limit=None)
        if not phase or not cause:
            return f"⚠️ [{_follow_layer_label('manager')}] 分流失败" + (
                f" · {err}" if err else ""
            )
        phase_label = {
            "backend": "后端",
            "parse": "解析",
            "contract": "契约：",
            "timeout": "超时",
        }.get(phase, phase)
        attempts = int(event.get("attempts") or 0)
        attempt = f" (第{attempts}次尝试)" if attempts > 1 else ""
        rendered = (
            f"⚠️ [{_follow_layer_label('manager')}] 分流失败 · "
            f"{phase_label} {cause}{attempt}"
        )
        return rendered + (f" · 原始错误: {err}" if err else "")

    if etype == "life.manager.stage_decision":
        action = str(event.get("action") or "hold")
        stage = str(event.get("target_stage") or event.get("current_stage") or "")
        reason = _clean_follow_text(str(event.get("reason") or ""), limit=120)
        if stage and action != "hold":
            verdict = f"{action} → {stage}"
        elif stage:
            verdict = f"{action} @ {stage}"
        else:
            verdict = action
        return f"🧭 [{_follow_layer_label('manager')}] {verdict}" + (f" · {reason}" if reason else "")

    if etype == "life.mission.started":
        bits = ["started", *_format_follow_mission_context(event, mission_context=mission_context)]
        return f"\n🚀 [{_follow_layer_label('engineer')}] " + " · ".join(bits)

    if etype == "life.phase.started":
        bits = [f"进入 [{label}]"]
        if event.get("round_index"):
            bits.append(f"round={event['round_index']}")
        if event.get("iteration_cycle"):
            bits.append(
                f"iteration={event['iteration_cycle']}/{event.get('iteration_max', '?')}"
            )
        return "🔄 " + " · ".join(bits)

    if etype == "round.review.started":
        return f"🔄 进入 [{_follow_layer_label('reviewer')}] · round={event.get('round_index', '?')}"

    if etype == "round.main.completed":
        return f"✅ [{_follow_layer_label('engineer')}] completed · round={event.get('round_index', '?')}"

    if etype == "round.review.completed":
        status = event.get("status", "?")
        reason = _clean_follow_text(str(event.get("reason") or ""), limit=None)
        return f"✅ [{_follow_layer_label('reviewer')}] completed · status={status}" + (
            f" · {reason}" if reason else ""
        )

    if etype == "life.iteration.critic":
        stop = bool(event.get("stop"))
        count = int(event.get("improvement_count") or 0)
        reason = _clean_follow_text(str(event.get("reason") or ""), limit=None)
        verdict = "stop" if stop else f"continue · {count} improvement(s)"
        return f"👔 [{_follow_layer_label('critic')}] {verdict}" + (
            f" · {reason}" if reason else ""
        )

    if etype == "life.iteration.continued":
        return f"🔁 [{_follow_layer_label('critic')}] queued next iteration · cycle={event.get('cycles_done', '?')}/{event.get('cycles_max', '?')}"

    if etype == "life.planner.start":
        obj = _clean_follow_text(str(event.get("objective") or ""), limit=None)
        return f"\n📋 [{_follow_layer_label('planner')}] planning" + (
            f" · {obj}" if obj else ""
        )

    if etype == "life.planner.verdict":
        if event.get("project_done"):
            return f"🏁 [{_follow_layer_label('planner')}] project done"
        return f"📋 [{_follow_layer_label('planner')}] queued {event.get('enqueued_tasks', event.get('task_count', '?'))} task(s)"

    if etype == "life.planner.task_added":
        return _format_follow_planner_task_added(event)

    if etype == "life.planner.task_skipped":
        return _format_follow_planner_task_skipped(event)

    if etype == "life.planner.error":
        return f"⚠️ [{_follow_layer_label('planner')}] planner error · {_clean_follow_text(str(event.get('error') or event.get('text') or ''), limit=None)}"

    if etype == "life.mission.completed":
        status = event.get("status", "?")
        raw_iteration = event.get("iteration")
        iter_info = raw_iteration if isinstance(raw_iteration, dict) else {}
        if iter_info.get("requeued"):
            bits = ["mission round complete", "requeued by critic", f"status={status}"]
        else:
            bits = [
                "mission complete",
                f"status={status}",
                f"success={event.get('success')}",
            ]
        bits.extend(_format_follow_mission_context(event, mission_context=mission_context))
        return "✅ " + " · ".join(bits)

    if etype == "life.mission.failed":
        return f"❌ mission failed · {_clean_follow_text(str(event.get('reason') or event.get('error') or ''), limit=None)}"

    if etype == "loop.start":
        return f"▶️ [{_follow_layer_label('engineer')}] {_clean_follow_text(str(event.get('text') or ''), limit=180)}"

    if etype == "round.start":
        return f"▶️ [{_follow_layer_label('engineer')}] {event.get('text', 'round started')}"

    if etype == "loop.done":
        return f"🏁 loop done · {_clean_follow_text(str(event.get('text') or ''), limit=None)}"

    return None


def _daemon_alive_for_events_path(events_path: Path) -> bool | None:
    pid_path = events_path.parent / "daemon.pid"
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _format_follow_heartbeat(events_path: Path, current_layer: str, idle_seconds: float) -> str:
    alive = _daemon_alive_for_events_path(events_path)
    if alive is True:
        state = "daemon alive"
    elif alive is False:
        state = "daemon not running"
    else:
        state = "daemon state unknown"
    return (
        f"  ⏳ [{_follow_layer_label(current_layer)}] waiting "
        f"{_core._format_short_duration(idle_seconds)} without new events · {state} · "
        "normal during LLM calls"
    )
