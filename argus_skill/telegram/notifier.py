"""Telegram bot: outbound notifier (sendMessage + sendDocument).

Provenance: trimmed from ``ArgusBot/codex_autoloop/telegram_notifier.py``.
Kept: text + document send, chunking long messages, typing pulse during
long operations. Dropped: live-message editing, photo/video special
casing — we use ``sendDocument`` for everything non-text.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

ErrorCallback = Callable[[str], None]


# Events the average user wants to see (default).
_USER_FACING_EVENTS: set[str] = {
    "task.queued",
    "task.started",
    "task.completed",
    "task.skipped",
    "task.error",
    "command.ack",
    "command.error",
    "command.unknown",
    "daemon.started",
    "daemon.stopping",
    "help",
    "status.report",
    # Mission-mode high-signal lifecycle (always shown, even in quiet mode).
    "mission.started",
    "mission.completed",
    "mission.error",
    "mission.idle",
    "round.review.completed",
    "plan.completed",
    "round.control.injected",
    "engineer.failure_nudge",
    "loop.completed",
    "pptx.report.ready",
    "final.report.ready",
}

# Internal lifecycle events — visible only when verbose mode is on.
_INTERNAL_EVENTS: set[str] = {
    "loop.start",
    "loop.started",
    "loop.done",
    "match.info",
    "scientist.start",
    "scientist.error",
    "round.start",
    "round.started",
    "round.main.completed",
    "round.checks.completed",
    "round.watchdog.checked",
    "round.watchdog.restart_requested",
    "review.done",
    "checks.done",
    "skill.writeback",
    "distill.start",
    "distill.done",
    "engineer.progress",
    "life.mission.started",
    "life.mission.completed",
    "life.status",
}

_VERBOSE_EVENTS: set[str] = _USER_FACING_EVENTS | _INTERNAL_EVENTS

# Per-event presentation: an icon (or short prefix). Events not in this
# map fall back to ``[event.type]`` so internal/dev events stay grep-able
# in verbose mode.
_EVENT_ICONS: dict[str, str] = {
    "task.queued":      "📥",
    "task.started":     "🏃",
    "task.completed":   "✅",
    "task.skipped":     "⏭",
    "task.error":       "❌",
    "command.ack":      "✓",
    "command.error":    "⚠️",
    "command.unknown":  "❓",
    "daemon.started":   "🟢",
    "daemon.stopping":  "🛑",
    "help":             "ℹ️",
    "status.report":    "📊",
    "mission.started":  "🎯",
    "mission.completed": "🎉",
    "mission.error":    "💥",
    "mission.idle":     "🟦",
    "loop.started":     "🚀",
    "loop.completed":   "🏁",
    "round.started":    "🔁",
    "round.main.completed":   "🔧",
    "round.checks.completed": "🔍",
    "round.review.completed": "🧑‍⚖️",
    "round.control.injected": "💉",
    "round.watchdog.checked":          "🐶",
    "round.watchdog.restart_requested": "🔄",
    "plan.completed":   "📋",
    "match.info":       "🎯",
    "scientist.start":  "🧪",
    "scientist.error":  "🧪❌",
    "skill.writeback":  "💾",
    "pptx.report.ready": "📊",
    "final.report.ready": "📄",
    "distill.start":    "🧬",
    "distill.done":     "🧬",
    # Life-mode lifecycle (in-process REPL surfaces these).
    "life.mission.started":   "▶",
    "life.mission.completed": "■",
    "life.status":            "ℹ️",
    # SkillLoop legacy lifecycle (used by life mode's runner).
    "loop.start":  "🚀",
    "loop.done":   "🏁",
    "round.start": "🔁",
    "review.done": "🧑‍⚖️",
    "checks.done": "🔍",
    # Live codex/claude/copilot stream progress (one beat per
    # ``item.completed`` JSON event the backend emits).
    "engineer.progress": "◆",
    # Repeated-tool-failure interrupt: the failed-tool ledger fires this
    # at most once per tool per mission when the agent is detected to be
    # blind-retrying a failing operation. High-signal — user should see it.
    "engineer.failure_nudge": "⚠",
}


@dataclass
class TelegramConfig:
    bot_token: str
    chat_id: str
    timeout_seconds: int = 10
    typing_enabled: bool = True
    typing_interval_seconds: int = 4
    # Default to user-facing events only. Internal events still go to the
    # JSONL event sink (for diagnostics) but stop polluting the chat.
    # Toggle with /verbose or /quiet at runtime.
    notify_event_types: set[str] = field(
        default_factory=lambda: set(_USER_FACING_EVENTS)
    )
    verbose: bool = False


class TelegramNotifier:
    def __init__(self, config: TelegramConfig, on_error: ErrorCallback | None = None) -> None:
        self.config = config
        self.on_error = on_error
        base = f"https://api.telegram.org/bot{config.bot_token}"
        self.send_message_url = f"{base}/sendMessage"
        self.send_chat_action_url = f"{base}/sendChatAction"
        self.send_document_url = f"{base}/sendDocument"
        self._typing_stop = threading.Event()
        self._typing_thread: threading.Thread | None = None

    # --- public surface ---------------------------------------------------

    def set_verbose(self, verbose: bool) -> None:
        """Toggle the chat-side event filter.

        Verbose mode forwards internal lifecycle events (round.start,
        match.info, …) to Telegram. Quiet mode (the default) shows only
        the user-facing milestones.
        """
        self.config.verbose = bool(verbose)
        self.config.notify_event_types = (
            set(_VERBOSE_EVENTS) if self.config.verbose else set(_USER_FACING_EVENTS)
        )

    def send_message(self, message: str) -> bool:
        chunks = _split_telegram_text(message, limit=3900)
        if not chunks:
            return True
        sent_all = True
        for chunk in chunks:
            payload = {
                "chat_id": self.config.chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if not self._post_form(self.send_message_url, payload):
                sent_all = False
        return sent_all

    def send_local_file(self, path: str | Path, *, caption: str = "") -> bool:
        file_path = Path(path)
        if not file_path.exists():
            self._emit_error(f"Telegram local file missing: {file_path}")
            return False
        try:
            file_bytes = file_path.read_bytes()
        except OSError as exc:
            self._emit_error(f"Telegram local file read failed: {exc}")
            return False
        boundary = f"----argusskill{uuid4().hex}"
        body = bytearray()
        body.extend(_multipart_text_part(boundary, "chat_id", self.config.chat_id))
        if caption:
            body.extend(_multipart_text_part(boundary, "caption", caption[:1024]))
        body.extend(
            _multipart_file_part(boundary, "document", file_path.name, file_bytes)
        )
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))
        req = urllib.request.Request(
            self.send_document_url,
            data=bytes(body),
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=max(30, self.config.timeout_seconds)):
                return True
        except urllib.error.URLError as exc:
            self._emit_error(f"Telegram sendDocument network error: {exc}")
            return False

    def notify_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type", ""))
        if event_type == "task.started":
            self._start_typing()
        elif event_type in {"loop.done", "task.error", "daemon.stopping"}:
            self._stop_typing()
        if event_type not in self.config.notify_event_types:
            return
        message = format_event_message(event)
        if message:
            self.send_message(message)

    def send_typing(self) -> None:
        payload = {"chat_id": self.config.chat_id, "action": "typing"}
        self._post_form(self.send_chat_action_url, payload)

    def close(self) -> None:
        self._stop_typing()

    # --- internals --------------------------------------------------------

    def _start_typing(self) -> None:
        if not self.config.typing_enabled:
            return
        if self._typing_thread is not None and self._typing_thread.is_alive():
            return
        self._typing_stop.clear()
        self._typing_thread = threading.Thread(target=self._typing_loop, daemon=True)
        self._typing_thread.start()

    def _stop_typing(self) -> None:
        self._typing_stop.set()
        thread = self._typing_thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._typing_thread = None

    def _typing_loop(self) -> None:
        while not self._typing_stop.is_set():
            self.send_typing()
            self._typing_stop.wait(self.config.typing_interval_seconds)

    def _post_form(self, url: str, payload: dict[str, Any]) -> bool:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8")
            except Exception:
                pass
            self._emit_error(f"Telegram HTTP {exc.code}: {body[:300]}")
            return False
        except urllib.error.URLError as exc:
            self._emit_error(f"Telegram network error: {exc}")
            return False
        try:
            response_payload = json.loads(raw)
        except json.JSONDecodeError:
            self._emit_error("Telegram non-JSON response")
            return False
        if not response_payload.get("ok"):
            self._emit_error(
                f"Telegram api error: {response_payload.get('description', 'unknown')}"
            )
            return False
        return True

    def _emit_error(self, message: str) -> None:
        if self.on_error is not None:
            self.on_error(message)


def format_event_message(event: dict[str, Any]) -> str:
    """Render a structured event as a short human-friendly string.

    LoopEngine and SkillLoopRunner emit events with rich structured payloads
    (``round_index``, ``status``, ``reason``, ``last_message``, …) instead of
    a single ``text`` field, so we dispatch to per-type renderers that pick
    out the most useful pieces.  Events without a custom renderer fall back
    to the legacy ``icon + text`` shape, and totally-unknown events keep the
    bracketed form so they remain grep-able in verbose mode.
    """
    kind = str(event.get("type", "?"))
    icon = _EVENT_ICONS.get(kind, "")

    # Per-event renderer: receives the full event dict, returns body text
    # (without the leading icon).
    renderer = _RICH_RENDERERS.get(kind)
    if renderer is not None:
        body = renderer(event)
        if not body:
            return icon or f"[{kind}]"
        return f"{icon} {body}".lstrip()

    # Legacy / simple events with a free-form ``text`` field.
    text = str(event.get("text", "")).strip()
    if icon == "":
        # Internal / unknown event — keep bracketed form for grep-ability.
        if not text:
            return f"[{kind}]"
        if len(text) > 200:
            text = text[:200].rstrip() + "…"
        return f"[{kind}] {text}"
    if not text:
        return icon
    cap = 1500 if kind == "task.completed" else 300
    if len(text) > cap:
        text = text[:cap].rstrip() + "…"
    return f"{icon} {text}"


# ---------------------------------------------------------------------------
# Per-event-type rich renderers (LoopEngine + SkillLoopRunner payloads).
# ---------------------------------------------------------------------------

def _trunc(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[:n].rstrip() + "…"


def _round_label(event: dict[str, Any]) -> str:
    idx = event.get("round_index")
    return f"round {idx}" if idx is not None else "round ?"


def _render_loop_started(event: dict[str, Any]) -> str:
    obj = _trunc(str(event.get("objective", "")), 120)
    parts = [f"loop started — max_rounds={event.get('max_rounds', '?')}"]
    plan_mode = event.get("plan_mode")
    if plan_mode:
        parts.append(f"plan_mode={plan_mode}")
    out = ", ".join(parts)
    if obj:
        out += f"\n   objective: {obj}"
    return out


def _render_round_started(event: dict[str, Any]) -> str:
    return _round_label(event) + " starting…"


def _render_round_main_completed(event: dict[str, Any]) -> str:
    label = _round_label(event)
    last = _trunc(str(event.get("last_message") or ""), 800)
    fatal = (event.get("fatal_error") or "").strip()
    turn_completed = event.get("turn_completed")
    turn_failed = event.get("turn_failed")
    flags = []
    if turn_failed:
        flags.append("turn_failed")
    elif turn_completed is False:
        flags.append("incomplete")
    head = f"{label}: main agent finished"
    if flags:
        head += f" ({', '.join(flags)})"
    body = ""
    if last:
        body = f"\n   ↳ {last}"
    elif fatal:
        body = f"\n   ↳ ⚠ {_trunc(fatal, 300)}"
    return head + body


def _render_round_checks_completed(event: dict[str, Any]) -> str:
    label = _round_label(event)
    checks = event.get("checks") or []
    if not checks:
        return f"{label}: (no acceptance checks configured)"
    passed = sum(1 for c in checks if c.get("passed"))
    failed = len(checks) - passed
    head = f"{label}: checks {passed} ✓ / {failed} ✗"
    if failed:
        # Show first failing command for quick diagnosis.
        for c in checks:
            if not c.get("passed"):
                cmd = _trunc(str(c.get("command") or ""), 80)
                head += f" — failed: {cmd} (exit {c.get('exit_code')})"
                break
    return head


def _render_round_review_completed(event: dict[str, Any]) -> str:
    label = _round_label(event)
    status = str(event.get("status", "?"))
    reason = _trunc(str(event.get("reason") or ""), 400)
    next_action = _trunc(str(event.get("next_action") or ""), 200)
    status_icon = {
        "done": "✅",
        "continue": "↻",
        "blocked": "⛔",
        "no_progress": "🚫",
    }.get(status, "•")
    head = f"{label}: review {status_icon} {status}"
    parts = [head]
    if reason:
        parts.append(f"   ↳ reason: {reason}")
    if next_action and status != "done":
        parts.append(f"   ↳ next: {next_action}")
    return "\n".join(parts)


def _render_plan_completed(event: dict[str, Any]) -> str:
    label = _round_label(event)
    plan_mode = event.get("plan_mode") or "?"
    follow_up = event.get("follow_up_required")
    main_inst = _trunc(str(event.get("main_instruction") or ""), 400)
    review_inst = _trunc(str(event.get("review_instruction") or ""), 200)
    next_explore = _trunc(str(event.get("next_explore") or ""), 200)
    flag = "" if follow_up is None else (
        " (follow-up needed)" if follow_up else " (no more follow-up)"
    )
    parts = [f"{label} plan ({plan_mode}){flag}"]
    if main_inst:
        parts.append(f"   ↳ main: {main_inst}")
    if next_explore:
        parts.append(f"   ↳ explore: {next_explore}")
    if review_inst:
        parts.append(f"   ↳ review: {review_inst}")
    return "\n".join(parts)


def _render_round_control_injected(event: dict[str, Any]) -> str:
    label = _round_label(event)
    instruction = _trunc(str(event.get("instruction") or ""), 400)
    return f"{label}: operator instruction injected\n   ↳ {instruction}"


def _render_round_watchdog_checked(event: dict[str, Any]) -> str:
    label = _round_label(event)
    idle = event.get("idle_seconds")
    should_restart = event.get("should_restart")
    reason = _trunc(str(event.get("reason") or ""), 200)
    matched = event.get("matched_pattern")
    suffix = f" idle={idle}s"
    if should_restart:
        suffix += " — RESTART"
    if matched:
        suffix += f" ({matched})"
    if reason and should_restart:
        suffix += f" — {reason}"
    return f"{label} watchdog:{suffix}"


def _render_round_watchdog_restart_requested(event: dict[str, Any]) -> str:
    label = _round_label(event)
    reason = _trunc(str(event.get("reason") or ""), 300)
    return f"{label}: watchdog → restart — {reason}"


def _render_loop_completed(event: dict[str, Any]) -> str:
    success = event.get("success")
    reason = _trunc(str(event.get("stop_reason") or ""), 400)
    head = "loop done — success" if success else "loop done — FAILED"
    if reason:
        head += f"\n   ↳ {reason}"
    return head


def _render_pptx_report_ready(event: dict[str, Any]) -> str:
    return f"pptx report ready: {event.get('path', '')} (via {event.get('generated_by', '?')})"


def _render_final_report_ready(event: dict[str, Any]) -> str:
    return f"final report ready: {event.get('path', '')} (via {event.get('generated_by', '?')})"


def _render_command_ack(event: dict[str, Any]) -> str:
    """Render command acknowledgements.

    For most acks we just show the short text. For ``/show`` responses
    (``show_kind`` present) we render the full body in a fenced block so
    the operator can read the prompt/plan/review verbatim.
    """
    text = str(event.get("text", "")).strip()
    show_kind = event.get("show_kind")
    if show_kind:
        # The body may be multi-line — wrap in a code fence and let the
        # caller decide whether to truncate further.
        if not text:
            return f"/show {show_kind}: (empty)"
        return f"/show {show_kind}:\n```\n{text}\n```"
    return text


def _render_status_report(event: dict[str, Any]) -> str:
    """Pass /status text through verbatim (multi-line, no truncation).

    The MissionDaemon now emits a multi-line snapshot with round/phase/
    last-verdict/recent events; chopping it at 300 chars (the legacy
    cap) would defeat the whole point.
    """
    return str(event.get("text", "")).rstrip()


_PROGRESS_KIND_BADGE = {
    "agent_message":      "💬",
    "assistant_message":  "💬",
    "reasoning":          "🤔",
    "command_execution":  "$",
    "tool_use":           "🔧",
    "file_change":        "📝",
}


def _render_engineer_progress(event: dict[str, Any]) -> str:
    """Live codex/claude/copilot stream beat — one item per call."""
    kind = str(event.get("kind") or "message").strip()
    text = str(event.get("text") or "").strip()
    badge = _PROGRESS_KIND_BADGE.get(kind, "•")
    if not text:
        return f"{badge} {kind}"
    # Already truncated upstream to 600 chars; trim further for chat scroll.
    text = _trunc(text, 240)
    if "\n" in text:
        # Keep the first non-blank line as the headline; show line count.
        lines = [ln for ln in text.splitlines() if ln.strip()]
        head = lines[0] if lines else text
        more = len(lines) - 1
        head = _trunc(head, 200)
        if more > 0:
            return f"{badge} {head}  (+{more} line{'s' if more != 1 else ''})"
        return f"{badge} {head}"
    return f"{badge} {text}"


def _render_life_mission_started(event: dict[str, Any]) -> str:
    title = (event.get("title") or event.get("objective") or "").strip()
    if title:
        return f"mission start — {_trunc(title, 100)}"
    return "mission start"


def _render_life_mission_completed(event: dict[str, Any]) -> str:
    parts: list[str] = []
    status = event.get("status")
    if status:
        parts.append(f"status={status}")
    rounds = event.get("rounds")
    if rounds is not None:
        parts.append(f"rounds={rounds}")
    elapsed = event.get("elapsed_seconds") or event.get("elapsed_s")
    if elapsed is not None:
        parts.append(f"elapsed={float(elapsed):.1f}s")
    cost = event.get("cost_usd")
    if cost is not None:
        parts.append(f"cost=${float(cost):.4f}")
    if not parts:
        return "mission complete"
    return "mission complete  ·  " + "  ·  ".join(parts)


def _render_loop_start(event: dict[str, Any]) -> str:
    """Hide the giant memory-context dump; show only the live objective."""
    text = str(event.get("text") or "")
    # The supervisor prepends "### Memory context (non-authoritative) … ---
    # ## Live objective\n<objective>". Skip the prelude entirely.
    marker = "## Live objective"
    if marker in text:
        live = text.split(marker, 1)[1].strip()
        live = live.lstrip(":").strip()
        return f"task: {_trunc(live, 200)}"
    # Fallback: show the first non-blank line.
    line = next((ln for ln in text.splitlines() if ln.strip()), "")
    return f"task: {_trunc(line, 200)}" if line else "task started"


def _render_loop_done(event: dict[str, Any]) -> str:
    text = str(event.get("text") or "").strip()
    return _trunc(text, 200) if text else "loop done"


def _render_round_start(event: dict[str, Any]) -> str:
    text = str(event.get("text") or "").strip()
    return _trunc(text, 160) if text else "engineer round"


def _render_review_done(event: dict[str, Any]) -> str:
    text = str(event.get("text") or "").strip()
    return _trunc(text, 200) if text else "review done"


_RICH_RENDERERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "loop.started": _render_loop_started,
    "round.started": _render_round_started,
    "round.main.completed": _render_round_main_completed,
    "round.checks.completed": _render_round_checks_completed,
    "round.review.completed": _render_round_review_completed,
    "round.control.injected": _render_round_control_injected,
    "round.watchdog.checked": _render_round_watchdog_checked,
    "round.watchdog.restart_requested": _render_round_watchdog_restart_requested,
    "plan.completed": _render_plan_completed,
    "loop.completed": _render_loop_completed,
    "pptx.report.ready": _render_pptx_report_ready,
    "final.report.ready": _render_final_report_ready,
    "command.ack": _render_command_ack,
    "status.report": _render_status_report,
    # Life mode + legacy SkillLoop:
    "engineer.progress": _render_engineer_progress,
    "life.mission.started": _render_life_mission_started,
    "life.mission.completed": _render_life_mission_completed,
    "loop.start": _render_loop_start,
    "loop.done": _render_loop_done,
    "round.start": _render_round_start,
    "review.done": _render_review_done,
}


def _split_telegram_text(text: str, *, limit: int) -> list[str]:
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        # Try to split on a newline to keep messages readable.
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks


def _multipart_text_part(boundary: str, field: str, value: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"\r\n\r\n'
        f"{value}\r\n"
    ).encode("utf-8")


def _multipart_file_part(
    boundary: str, field: str, filename: str, content: bytes
) -> bytes:
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    tail = b"\r\n"
    return head + content + tail
