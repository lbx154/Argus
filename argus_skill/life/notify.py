"""Notification dispatcher for terminal life events.

When the supervisor finalises a mission (``mission_complete`` /
``mission_failed`` / ``mission_orphaned`` / ``budget_pause`` /
``auth_failure``) the operator should hear about it without having to
stare at the terminal. This module hands such journal entries off to
three optional channels:

* ``ARGUS_SKILL_NOTIFY_WEBHOOK``: HTTP POST the event JSON to a URL.
  Slack / Discord / generic webhooks all work — the payload is
  intentionally simple ``{kind, title, summary, ts, cost_usd}``. We
  use ``urllib`` so there's no extra dependency.
* ``ARGUS_SKILL_NOTIFY_CMD``: shell command that gets the same JSON on
  stdin. Operators can wire ``mail``, ``notify-send``, ``pagerduty``,
  custom scripts, etc.
* ``ARGUS_SKILL_TELEGRAM_BOT_TOKEN`` + ``ARGUS_SKILL_TELEGRAM_CHAT_ID``:
  sends formatted messages to a Telegram chat via the Bot API.

All are best-effort: any failure is logged at WARNING and dropped on
the floor so the supervisor never crashes because of a flaky pager.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time
from typing import Any

log = logging.getLogger(__name__)

# Journal entry kinds that warrant operator escalation.
DEFAULT_NOTIFY_KINDS = frozenset({
    "mission_started",
    "mission_complete",
    "mission_failed",
    "mission_iterated",
    "mission_orphaned",
    "mission_requeued",
    "budget_pause",
    "auth_failure",
    "planner_cycle",
    "planner_done",
    "phase_change",
})


def dispatch_journal_entry(entry: Any, *, kinds: frozenset[str] = DEFAULT_NOTIFY_KINDS) -> None:
    """Send ``entry`` to the configured notification channels.

    ``entry`` may be a ``JournalEntry`` dataclass or a plain dict (the
    in-memory journal returns the dataclass; tests pass dicts).
    """
    payload = _payload_from_entry(entry)
    kind = str(payload.get("kind") or "")
    if kind not in kinds:
        return
    _post_webhook(payload)
    _run_cmd(payload)
    _post_telegram(payload)


def _payload_from_entry(entry: Any) -> dict[str, Any]:
    # Dataclass with .to_jsonable()?
    if hasattr(entry, "to_jsonable"):
        try:
            d = entry.to_jsonable()
            if isinstance(d, dict):
                return _shape(d)
        except Exception:  # noqa: BLE001
            pass
    # Already a dict?
    if isinstance(entry, dict):
        return _shape(entry)
    # Last-ditch: try to read attributes by name.
    out: dict[str, Any] = {}
    for k in ("id", "kind", "title", "summary", "ts", "cost_usd", "tags"):
        if hasattr(entry, k):
            out[k] = getattr(entry, k)
    return _shape(out)


def _shape(d: dict[str, Any]) -> dict[str, Any]:
    keep = ("id", "kind", "title", "summary", "ts", "cost_usd", "tags", "extra")
    return {k: d.get(k) for k in keep if k in d}


def _post_webhook(payload: dict[str, Any]) -> None:
    url = (os.environ.get("ARGUS_SKILL_NOTIFY_WEBHOOK") or "").strip()
    if not url:
        return
    try:
        import urllib.request

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "argus-skill"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            if resp.status >= 400:
                log.warning(
                    "notify webhook returned status=%d for kind=%s",
                    resp.status, payload.get("kind"),
                )
    except Exception as exc:  # noqa: BLE001
        log.warning("notify webhook failed (%s: %s)", type(exc).__name__, exc)


def _run_cmd(payload: dict[str, Any]) -> None:
    cmd = (os.environ.get("ARGUS_SKILL_NOTIFY_CMD") or "").strip()
    if not cmd:
        return
    try:
        body = json.dumps(payload, ensure_ascii=False)
        subprocess.run(
            shlex.split(cmd),
            input=body.encode("utf-8"),
            timeout=10.0,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("notify cmd failed (%s: %s)", type(exc).__name__, exc)


# ---------------------------------------------------------------------------
# Telegram Bot API
# ---------------------------------------------------------------------------

_KIND_LABELS: dict[str, tuple[str, str]] = {
    "mission_started":   ("🚀", "开始任务"),
    "mission_complete":  ("✅", "任务完成"),
    "mission_failed":    ("❌", "任务失败"),
    "mission_iterated":  ("🔁", "任务迭代中"),
    "mission_orphaned":  ("⚠️", "任务被回收"),
    "mission_requeued":  ("🔃", "任务已恢复"),
    "budget_pause":      ("💰", "预算暂停"),
    "auth_failure":      ("🔐", "认证失败"),
    "planner_cycle":     ("📋", "规划完成"),
    "planner_done":      ("🏁", "项目完成"),
    "phase_change":      ("🔄", "层级切换"),
}

_LAYER_LABELS: dict[str, str] = {
    "engineer": "👷 工程师 (L1)",
    "reviewer": "👨‍🏫 审查员 (L2)",
    "critic":   "👔 评审员 (L3)",
    "planner":  "🧠 规划师 (L4)",
}

# HTML-escape helper
def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_telegram_message(payload: dict[str, Any]) -> str:
    """Build a human-readable Telegram message from a journal payload."""
    kind = payload.get("kind", "unknown")
    emoji, label = _KIND_LABELS.get(kind, ("🔔", kind))
    title = payload.get("title", "")
    summary = payload.get("summary", "")
    cost = payload.get("cost_usd")
    extra = payload.get("extra") or {}
    if not isinstance(extra, dict):
        extra = {}

    # Timestamp
    ts = payload.get("ts")
    if isinstance(ts, (int, float)):
        import datetime
        ts_str = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
    else:
        ts_str = time.strftime("%H:%M:%S")

    # Header
    lines = [f"{emoji} <b>{label}</b>  {ts_str}", "━━━━━━━━━━━━━━━━"]

    # Agent layer
    layer = extra.get("agent_layer", "")
    layer_label = _LAYER_LABELS.get(layer, "")
    if layer_label:
        lines.append(layer_label)

    # Task title
    if title:
        t = _esc(title if len(title) <= 80 else title[:77] + "…")
        lines.append(f"📌 {t}")

    # Objective (from extra, if available)
    objective = extra.get("objective", "")
    if objective:
        obj_text = _esc(objective if len(objective) <= 200 else objective[:197] + "…")
        lines.append(f"🎯 {obj_text}")

    # Kind-specific details
    phase_status = extra.get("phase_status", "")
    if kind == "mission_started":
        pass  # objective line above is sufficient
    elif kind == "phase_change":
        if phase_status == "completed":
            # Show completion details
            details: list[str] = []
            tokens_in = extra.get("input_tokens", 0)
            tokens_out = extra.get("output_tokens", 0)
            if tokens_in or tokens_out:
                details.append(f"tokens: {tokens_in}→{tokens_out}")
            rounds = extra.get("rounds")
            if rounds:
                details.append(f"轮次: {rounds}")
            imp_count = extra.get("improvement_count")
            if imp_count is not None:
                details.append(f"改进: {imp_count}项")
            reason = extra.get("reason", "")
            if reason:
                details.append(_esc(reason[:100]))
            if details:
                lines.append(f"📊 {' · '.join(details)}")
    elif kind in ("mission_complete", "mission_failed", "mission_iterated"):
        _format_mission_details(lines, extra, summary)
    elif kind == "planner_cycle":
        _format_planner_details(lines, summary)
    elif kind == "planner_done":
        if summary:
            lines.append(f"\n{_esc(summary[:300])}")
    elif kind == "budget_pause":
        if summary:
            lines.append(f"\n⏸️ {_esc(summary[:200])}")
    elif summary:
        s = _esc(summary if len(summary) <= 300 else summary[:297] + "…")
        lines.append(f"\n{s}")

    # Cost line
    # Show "本次" for phase_change only when completed (has real cost)
    cumul = extra.get("cumulative_cost_usd")
    cost_parts: list[str] = []
    show_per_item = kind not in ("mission_started",)
    if kind == "phase_change" and phase_status != "completed":
        show_per_item = False
    if cost is not None and show_per_item and float(cost) > 0:
        cost_parts.append(f"本次 ${float(cost):.4f}")
    if cumul is not None:
        cost_parts.append(f"累计 <b>${float(cumul):.2f}</b>")
    if cost_parts:
        lines.append(f"\n💵 {' · '.join(cost_parts)}")

    return "\n".join(lines)


def _format_mission_details(lines: list[str], extra: dict[str, Any], summary: str) -> None:
    """Add mission-specific progress info."""
    details: list[str] = []

    # Rounds
    # Parse from summary (format: "status=done; rounds=5; ...")
    for part in summary.split(";"):
        part = part.strip()
        if part.startswith("rounds="):
            try:
                rounds = int(part.split("=")[1])
                details.append(f"执行 {rounds} 轮")
            except (ValueError, IndexError):
                pass
        elif part.startswith("elapsed="):
            try:
                elapsed = part.split("=")[1]
                details.append(f"耗时 {elapsed}")
            except IndexError:
                pass

    # Iteration info
    iteration = extra.get("iteration") or {}
    if isinstance(iteration, dict):
        cycle = iteration.get("cycle") or iteration.get("cycles_done")
        max_c = iteration.get("max_cycles")
        if cycle:
            details.append(f"迭代 {cycle}/{max_c or '?'}")
        if iteration.get("requeued"):
            details.append("已重排队")

    if details:
        lines.append(f"\n📊 {' · '.join(details)}")


def _format_planner_details(lines: list[str], summary: str) -> None:
    """Add planner-specific task list."""
    # Summary format: "generated N task(s): title1, title2, ..."
    if ":" in summary:
        prefix, tasks_str = summary.split(":", 1)
        lines.append(f"\n{_esc(prefix.strip())}")
        tasks = [t.strip() for t in tasks_str.split(",") if t.strip()]
        for i, t in enumerate(tasks[:5], 1):
            lines.append(f"  {i}. {_esc(t[:60])}")
        if len(tasks) > 5:
            lines.append(f"  … 还有 {len(tasks) - 5} 个")
    elif summary:
        lines.append(f"\n{_esc(summary[:300])}")


def _post_telegram(payload: dict[str, Any]) -> None:
    token = (os.environ.get("ARGUS_SKILL_TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("ARGUS_SKILL_TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        return
    text = _format_telegram_message(payload)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, ensure_ascii=False).encode("utf-8")
    try:
        import urllib.request
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            if resp.status >= 400:
                log.warning(
                    "telegram notify returned status=%d for kind=%s",
                    resp.status, payload.get("kind"),
                )
    except Exception as exc:  # noqa: BLE001
        log.warning("telegram notify failed (%s: %s)", type(exc).__name__, exc)


# ---------------------------------------------------------------------------
# Telegram live-streaming reporter
# ---------------------------------------------------------------------------

_PROGRESS_KIND_EMOJI: dict[str, str] = {
    "agent_message": "💭",
    "command_execution": "🔧",
    "reasoning": "🧠",
}


def _parse_command(raw: str) -> str:
    """Turn a raw shell command into a short, readable description."""
    import re

    # Strip /bin/bash -lc wrapper
    m = re.search(r'/bin/(?:ba)?sh\s+-\w*c\s+["\'](.+)', raw, re.DOTALL)
    cmd = m.group(1).rstrip("'\"") if m else raw

    # sed -n 'N,Mp' FILE → 📖 reading FILE:N-M
    m = re.match(r"sed\s+-n\s+'?(\d+),(\d+)p'?\s+(.+)", cmd)
    if m:
        path = _short_path(m.group(3).strip().strip("'\""))
        return f"📖 读取 {path}:{m.group(1)}-{m.group(2)}"

    # cat FILE → 📖 reading FILE
    m = re.match(r"cat\s+(.+)", cmd)
    if m:
        return f"📖 读取 {_short_path(m.group(1).strip())}"

    # rg / grep → 🔍 searching PATTERN
    if cmd.startswith(("rg ", "grep ")):
        # Extract quoted pattern if possible
        m2 = re.search(r'''["']([^"']+)["']''', cmd)
        if m2:
            pat = m2.group(1)[:50]
        else:
            # Take first non-flag argument
            parts = cmd.split()
            pat = next((p for p in parts[1:] if not p.startswith("-")), "…")[:50]
        return f"🔍 搜索 {pat}"

    # python / python3 → 🐍 running python script
    if cmd.startswith(("python", "python3")):
        # Try to extract the module/script or first meaningful line
        if "<<" in cmd:
            return "🐍 执行 Python 脚本"
        m2 = re.match(r"python3?\s+(?:-\w+\s+)*(.+)", cmd)
        return f"🐍 执行 {_short_path(m2.group(1)[:60])}" if m2 else "🐍 执行 Python"

    # git commands
    if cmd.startswith("git "):
        return f"📦 {cmd[:60]}"

    # npm/pip/make/pytest
    for tool in ("npm", "pip", "make", "pytest", "ruff", "mypy"):
        if cmd.startswith(tool):
            return f"🔧 {cmd[:60]}"

    # Generic: just truncate
    short = cmd if len(cmd) <= 60 else cmd[:57] + "…"
    return f"▸ {short}"


def _short_path(path: str) -> str:
    """Strip common prefixes to make paths readable."""
    import re
    # Remove /home/<user>/<project>/ prefix
    path = re.sub(r'^/home/[^/]+/[^/]+/', '', path.strip().strip("'\""))
    # Remove leading ./ or ./
    path = re.sub(r'^\./', '', path)
    return path


def _summarize_progress(items: list[dict[str, Any]]) -> list[str]:
    """Turn a list of progress events into readable action lines.

    Consecutive file reads are batched into a single summary line.
    Agent messages are always shown individually (they're the most valuable).
    """
    actions: list[str] = []
    pending_reads: list[str] = []

    def _flush_reads() -> None:
        if not pending_reads:
            return
        if len(pending_reads) == 1:
            actions.append(pending_reads[0])
        else:
            # Collapse N file reads into one line
            actions.append(f"📖 读取了 {len(pending_reads)} 个文件")
        pending_reads.clear()

    for ev in items:
        kind = ev.get("kind", "")
        text = ev.get("text", "")
        if not text:
            continue

        if kind == "agent_message":
            _flush_reads()
            # Agent thinking — the most valuable content
            msg = text if len(text) <= 150 else text[:147] + "…"
            actions.append(f"💭 {msg}")
        elif kind == "reasoning":
            _flush_reads()
            msg = text if len(text) <= 100 else text[:97] + "…"
            actions.append(f"🧠 {msg}")
        elif kind == "command_execution":
            parsed = _parse_command(text)
            if parsed.startswith("📖"):
                pending_reads.append(parsed)
            else:
                _flush_reads()
                actions.append(parsed)
        else:
            _flush_reads()
            short = text if len(text) <= 80 else text[:77] + "…"
            actions.append(f"▸ {short}")

    _flush_reads()
    return actions


class TelegramStreamReporter:
    """Buffers ``engineer.progress`` events and periodically edits a
    single live-status Telegram message.

    All network I/O happens in a dedicated daemon thread to avoid
    blocking the event pipeline.  The public API (``on_event``,
    ``start_mission``, ``end_mission``) is thread-safe — callers just
    enqueue lightweight objects into a ``collections.deque``.

    Usage::

        reporter = TelegramStreamReporter()
        reporter.start()          # spawns the flush thread
        reporter.on_event(event)  # call from any thread
        reporter.stop()           # graceful shutdown
    """

    FLUSH_INTERVAL = 12  # seconds between Telegram edits
    MAX_LINES = 6        # recent progress lines shown
    MAX_MSG_LEN = 3800   # leave room for markup overhead

    def __init__(self, *, stop_event: Any = None) -> None:
        import collections
        import threading as _threading

        self._token = (os.environ.get("ARGUS_SKILL_TELEGRAM_BOT_TOKEN") or "").strip()
        self._chat_id = (os.environ.get("ARGUS_SKILL_TELEGRAM_CHAT_ID") or "").strip()
        self._stop = stop_event or _threading.Event()
        self._buf: collections.deque[dict[str, Any]] = collections.deque(maxlen=50)
        self._lock = _threading.Lock()
        self._live_msg_id: int | None = None
        self._mission_title: str = ""
        self._mission_layer: str = ""
        self._mission_start: float = 0.0
        self._last_flush_text: str = ""
        self._thread: _threading.Thread | None = None
        self._enabled = bool(self._token and self._chat_id)

    # -- public API (called from event thread) ----------------------------

    def start(self) -> None:
        if not self._enabled:
            return
        import threading as _threading
        t = _threading.Thread(target=self._run, name="tg-stream", daemon=True)
        self._thread = t
        t.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def on_event(self, event: dict[str, Any]) -> None:
        """Accept any event dict from the sink.  Only progress events
        are buffered; lifecycle events update mission state."""
        if not self._enabled:
            return
        etype = event.get("type", "")
        if etype == "engineer.progress":
            with self._lock:
                self._buf.append(event)
        elif etype == "life.mission.started":
            self.start_mission(
                title=event.get("title", ""),
                layer="engineer",
            )
        elif etype in ("life.mission.completed", "life.mission.failed"):
            self.end_mission()
        elif etype == "life.planner.start":
            self.start_mission(
                title=event.get("objective", "规划中…")[:80],
                layer="planner",
            )
        elif etype in ("life.planner.verdict", "life.planner.error"):
            self.end_mission()

    def start_mission(self, *, title: str, layer: str = "engineer") -> None:
        with self._lock:
            self._mission_title = title
            self._mission_layer = layer
            self._mission_start = time.time()
            self._buf.clear()
            self._live_msg_id = None
            self._last_flush_text = ""

    def end_mission(self) -> None:
        msg_id = self._live_msg_id
        with self._lock:
            self._buf.clear()
            self._live_msg_id = None
            self._mission_title = ""
            self._last_flush_text = ""
        if msg_id:
            self._delete_message(msg_id)

    # -- background thread ------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._flush()
            except Exception:  # noqa: BLE001
                log.debug("tg-stream flush error", exc_info=True)
            self._stop.wait(timeout=self.FLUSH_INTERVAL)

    def _flush(self) -> None:
        with self._lock:
            if not self._buf or not self._mission_title:
                return
            items = list(self._buf)
            title = self._mission_title
            layer = self._mission_layer
            start = self._mission_start

        text = self._render(items, title, layer, start)
        if text == self._last_flush_text:
            return  # no change — skip edit

        if self._live_msg_id:
            ok = self._edit_message(self._live_msg_id, text)
            if not ok:
                # Message gone / error — send a new one
                self._live_msg_id = self._send_message(text)
        else:
            self._live_msg_id = self._send_message(text)
        self._last_flush_text = text

    def _render(
        self,
        items: list[dict[str, Any]],
        title: str,
        layer: str,
        start: float,
    ) -> str:
        elapsed = time.time() - start if start else 0
        mins, secs = divmod(int(elapsed), 60)

        layer_label = _LAYER_LABELS.get(layer, layer)
        header = f"⚡ <b>实时进展</b>  {mins}m{secs:02d}s"
        lines = [header, "━━━━━━━━━━━━━━━━"]
        lines.append(layer_label)
        lines.append(f"📌 {_esc(title[:80])}")
        lines.append("")

        # Intelligently summarize recent activity
        actions = _summarize_progress(items)
        for a in actions[-self.MAX_LINES:]:
            lines.append(_esc(a))

        body = "\n".join(lines)
        if len(body) > self.MAX_MSG_LEN:
            body = body[:self.MAX_MSG_LEN] + "\n…"
        return body

    # -- Telegram API helpers ---------------------------------------------

    def _send_message(self, text: str) -> int | None:
        try:
            import urllib.request
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            body = json.dumps({
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "disable_notification": True,
            }, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data.get("result", {}).get("message_id")
        except Exception:  # noqa: BLE001
            log.debug("tg-stream send failed", exc_info=True)
            return None

    def _edit_message(self, msg_id: int, text: str) -> bool:
        try:
            import urllib.request
            url = f"https://api.telegram.org/bot{self._token}/editMessageText"
            body = json.dumps({
                "chat_id": self._chat_id,
                "message_id": msg_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status < 400
        except Exception:  # noqa: BLE001
            log.debug("tg-stream edit failed", exc_info=True)
            return False

    def _delete_message(self, msg_id: int) -> None:
        try:
            import urllib.request
            url = f"https://api.telegram.org/bot{self._token}/deleteMessage"
            body = json.dumps({
                "chat_id": self._chat_id,
                "message_id": msg_id,
            }).encode("utf-8")
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:  # noqa: BLE001
            pass


__all__ = ["dispatch_journal_entry", "DEFAULT_NOTIFY_KINDS", "TelegramStreamReporter"]
