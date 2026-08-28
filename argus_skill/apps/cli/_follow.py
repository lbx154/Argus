"""Display formatting for the cli --follow / status views."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlencode

from ...core import paths as core_paths
from ...core.operator_messages import uses_cjk
from ...core.role_reply import strip_named_lines
from ...core.secret_guard import known_secret_values, redact_secrets_text
from .._inbox import format_inbox_event
from ..tui_launcher import _bundle_path
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
    title = str(event.get("title") or context.get("title") or "")
    objective = str(event.get("objective") or context.get("objective") or "")
    subject = title or objective or "current task"
    bits = [_clean_follow_text(subject, limit=None)]
    if objective and objective != title:
        bits.append(_clean_follow_text(objective, limit=None))
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


class _FollowEventRenderer:
    """Render a follow session through one long-lived bundled TS process."""

    def __init__(self, *, theme: Any = None) -> None:
        self._theme = theme
        self._process: subprocess.Popen[str] | None = None
        bundle = _bundle_path()
        node = shutil.which("node")
        if bundle is None:
            self._degrade("TUI bundle not found")
            return
        if node is None:
            self._degrade("Node.js not found")
            return
        command = [
            node,
            str(bundle),
            "render-events",
            "--locale",
            "zh-CN",
            "--unknown-event-policy",
            "greppable",
            "--density",
            "compact",
        ]
        if os.environ.get("ARGUS_SKILL_SHOW_REASONING", "0").lower() in (
            "1", "true", "yes", "on",
        ):
            command.append("--show-reasoning")
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            self._degrade(str(exc))

    def _degrade(self, reason: str) -> None:
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait()
        detail = " ".join(str(reason).split())
        sys.stderr.write(
            "argus-skill: semantic event renderer unavailable"
            f" ({detail}); using Python fallback for this follow session\n"
        )
        sys.stderr.flush()

    @staticmethod
    def _failure_reason(process: subprocess.Popen[str]) -> str:
        code = process.poll()
        if code is None:
            return "renderer closed its output stream"
        stderr = process.stderr.read().strip() if process.stderr is not None else ""
        reason = f"renderer exited with status {code}"
        return f"{reason}: {stderr}" if stderr else reason

    def render(
        self,
        event: dict,
        current_layer: str,
        *,
        mission_context: dict[str, str] | None = None,
        full: bool = False,
    ) -> str | None:
        process = self._process
        if process is None:
            return _format_follow_event(
                event,
                current_layer,
                mission_context=mission_context,
                theme=self._theme,
                full=full,
            )
        assert process.stdin is not None and process.stdout is not None
        render_event = event
        if mission_context and event.get("type") in {
            "life.mission.started", "life.mission.completed",
        }:
            render_event = {**mission_context, **event}
        try:
            process.stdin.write(json.dumps(render_event, ensure_ascii=False) + "\n")
            process.stdin.flush()
            rendered = process.stdout.readline()
        except (BrokenPipeError, OSError, ValueError) as exc:
            reason = self._failure_reason(process)
            self._degrade(f"{reason}: {exc}" if process.poll() is None else reason)
            return self.render(
                event,
                current_layer,
                mission_context=mission_context,
                full=full,
            )
        if rendered == "":
            self._degrade(self._failure_reason(process))
            return self.render(
                event,
                current_layer,
                mission_context=mission_context,
                full=full,
            )
        line = rendered.rstrip("\r\n")
        return _colorize_role_tags(self._theme, line) if line else None

    def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        assert process.stdin is not None
        try:
            process.stdin.close()
        except BrokenPipeError:
            pass
        process.wait()


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
    title = _clean_follow_text(str(event.get("title") or "new task"), limit=90)
    objective = _clean_follow_text(str(event.get("objective") or ""), limit=120)
    text = f"Planner added “{title}”"
    if objective:
        text += f": {objective}"
    return f"📋 [{_follow_layer_label('planner')}] {text}"


def _format_follow_planner_task_skipped(event: dict) -> str:
    skip_category = str(event.get("skip_category") or "")
    title = _clean_follow_text(str(event.get("title") or "proposed task"), limit=90)
    reason = _clean_follow_text(str(event.get("reason") or ""), limit=140)
    if skip_category == "recent_no_progress_failure":
        text = (
            f"Planner held “{title}” because the same approach recently made no progress."
        )
    else:
        matched_title = _clean_follow_text(
            str(event.get("matched_title") or "an existing task"),
            limit=90,
        )
        text = f"Planner skipped “{title}” because “{matched_title}” already covers it."
    if reason:
        text += f" {reason}"
    return f"⏭️ [{_follow_layer_label('planner')}] {text}"


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
        objective = str(event.get("objective") or "")
        text = "正在理解这项任务…" if uses_cjk(objective) else "Understanding the task…"
        return f"🧭 [{_follow_layer_label('manager')}] {text}"

    if etype == "life.manager.intent.completed":
        vertical = str(event.get("vertical") or "")
        label = vertical.replace("_", " ")
        if uses_cjk(str(event.get("objective") or "")):
            text = f"已将这项任务交给 {label} 工作流。" if label else "已为团队准备好这项任务。"
        else:
            text = f"Prepared this request for {label} work." if label else "Prepared this request for the team."
        return f"🧭 [{_follow_layer_label('manager')}] {text}"

    if etype == "life.manager.intent.failed":
        phase = str(event.get("phase") or "").strip()
        chinese = uses_cjk(str(event.get("objective") or ""))
        reasons = {
            "backend": "the routing service did not return a usable answer",
            "parse": "the routing answer could not be read",
            "contract": "the routing answer was incomplete",
            "timeout": "routing took too long",
        }
        zh_reasons = {
            "backend": "分流服务没有返回可用答案",
            "parse": "无法读取分流结果",
            "contract": "分流结果缺少必要信息",
            "timeout": "分流耗时过长",
        }
        concise_reason = (
            zh_reasons.get(phase, "暂时没有可用的分流结果")
            if chinese
            else reasons.get(phase, "no usable routing decision was available")
        )
        if chinese:
            text = f"暂时无法判断该如何处理这项请求：{concise_reason}。尚未创建任务。"
        else:
            text = (
                "I couldn’t determine how to handle this request because "
                f"{concise_reason}. Nothing was queued."
            )
        return f"⚠️ [{_follow_layer_label('manager')}] {text}"

    if etype == "life.manager.stage_decision":
        action = str(event.get("action") or "hold").strip().lower()
        stage = str(event.get("target_stage") or event.get("current_stage") or "")
        reason = _clean_follow_text(str(event.get("reason") or ""), limit=120)
        stage_name = stage.replace("_", " ") or "this stage"
        if uses_cjk(reason):
            verdict = {
                "advance": f"已推进到 {stage_name}",
                "hold": f"保持在 {stage_name}",
                "rollback": f"已返回 {stage_name}",
                "complete": f"已完成 {stage_name}",
            }.get(action, f"已审阅 {stage_name}")
        else:
            verdict = {
                "advance": f"Advanced to {stage_name}",
                "hold": f"Staying in {stage_name}",
                "rollback": f"Returning to {stage_name}",
                "complete": f"Completed {stage_name}",
            }.get(action, f"Reviewed {stage_name}")
        return f"🧭 [{_follow_layer_label('manager')}] {verdict}" + (f": {reason}" if reason else ".")

    if etype == "life.mission.started":
        bits = _format_follow_mission_context(event, mission_context=mission_context)
        text = f"Started: {bits[0]}."
        if len(bits) > 1:
            text += f" {bits[1]}"
        return f"\n🚀 [{_follow_layer_label('engineer')}] {text}"

    if etype == "life.phase.started":
        text = f"[{label}] Starting"
        if event.get("round_index"):
            text += f" round {event['round_index']}"
        if event.get("iteration_cycle"):
            text += (
                f" (iteration {event['iteration_cycle']} of "
                f"{event.get('iteration_max', '?')})"
            )
        return f"🔄 {text}."

    if etype == "round.review.started":
        return f"🔄 [{_follow_layer_label('reviewer')}] Checking round {event.get('round_index', '?')}."

    if etype == "round.main.completed":
        return f"✅ [{_follow_layer_label('engineer')}] Finished round {event.get('round_index', '?')}."

    if etype == "round.review.completed":
        status = str(event.get("status") or "").strip().lower()
        round_index = event.get("round_index", "?")
        reason = _clean_follow_text(str(event.get("reason") or ""), limit=None)
        verdict = {
            "done": f"Accepted round {round_index}.",
            "continue": f"Requested another pass after round {round_index}.",
            "blocked": f"Needs an external decision after round {round_index}.",
        }.get(status, f"Finished checking round {round_index}.")
        return f"✅ [{_follow_layer_label('reviewer')}] {verdict}" + (
            f" {reason}" if reason else ""
        )

    if etype == "life.iteration.critic":
        stop = bool(event.get("stop"))
        count = int(event.get("improvement_count") or 0)
        reason = _clean_follow_text(str(event.get("reason") or ""), limit=None)
        verdict = (
            "The iteration review found no further changes."
            if stop
            else f"The iteration review queued {count} improvement(s)."
        )
        return f"👔 [{_follow_layer_label('critic')}] {verdict}" + (
            f" {reason}" if reason else ""
        )

    if etype == "life.iteration.continued":
        return (
            f"🔁 [{_follow_layer_label('critic')}] Another iteration is queued "
            f"after cycle {event.get('cycles_done', '?')} of {event.get('cycles_max', '?')}."
        )

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
        raw_iteration = event.get("iteration")
        iter_info = raw_iteration if isinstance(raw_iteration, dict) else {}
        context = _format_follow_mission_context(event, mission_context=mission_context)
        title = context[0]
        summary = _clean_follow_text(
            str(
                event.get("summary")
                or event.get("stop_reason")
                or event.get("failure_reason")
                or event.get("reason")
                or ""
            ),
            limit=None,
        )
        chinese = uses_cjk(f"{title}\n{summary}")
        if iter_info.get("requeued"):
            headline = (
                f"{title} 的本轮已完成；下一轮已加入队列。"
                if chinese
                else f"Round complete for {title}; another iteration is queued."
            )
            icon = "🔁"
        else:
            status = str(event.get("status") or "").strip().lower()
            paused = (
                status.startswith("paused_")
                or event.get("resumable") is True
                or event.get("recoverable") is True
            )
            if event.get("success") is True or status in {"done", "success", "completed"}:
                headline = f"已完成：{title}。" if chinese else f"Completed: {title}."
                icon = "✅"
            elif paused:
                headline = f"已暂停：{title}。" if chinese else f"Paused: {title}."
                icon = "⏸️"
            else:
                headline = f"未能完成 {title}。" if chinese else f"Could not complete {title}."
                icon = "❌"
        return f"{icon} {headline}" + (f" {summary}" if summary else "")

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
