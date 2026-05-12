"""Telegram Bot poller — inbound command interface for the daemon.

Runs as a daemon thread inside :class:`~argus_skill.daemon.life_worker.LifeWorker`.
Polls ``getUpdates`` with long-polling and dispatches commands:

* ``/add <title>: <objective>`` — add a task to the backlog
* ``/status`` — reply with daemon / backlog / cost summary
* ``/backlog`` — list pending tasks
* ``/start [objective]`` — enable continuous mode
* ``/stop`` — disable continuous mode
* ``/nudge <text>`` — inject operator guidance into the next mission round
* ``/help`` — show available commands

Only messages from the configured ``chat_id`` (and optionally
``user_id``) are processed. Everything else is silently dropped.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

__all__ = ["TelegramPoller"]


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

def _api_call(token: str, method: str, payload: dict[str, Any] | None = None, *, timeout: float = 35) -> dict[str, Any] | None:
    """Call a Telegram Bot API method. Returns the parsed JSON or None on error."""
    import urllib.request
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    try:
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.debug("telegram api %s failed: %s", method, exc)
        return None


def _send_message(token: str, chat_id: str, text: str, *, parse_mode: str = "HTML") -> None:
    """Send a message to the configured chat. Truncates to 4096 chars."""
    if len(text) > 4090:
        text = text[:4087] + "…"
    _api_call(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }, timeout=10)


# ---------------------------------------------------------------------------
# Offset persistence
# ---------------------------------------------------------------------------

def _offset_path(life_dir: Path) -> Path:
    return life_dir / "telegram.offset"


def _read_offset(life_dir: Path) -> int | None:
    p = _offset_path(life_dir)
    try:
        return int(p.read_text().strip())
    except (OSError, ValueError):
        return None


def _write_offset(life_dir: Path, offset: int) -> None:
    try:
        _offset_path(life_dir).write_text(str(offset), encoding="utf-8")
    except OSError:
        log.warning("failed to persist telegram offset")


def _fast_forward(token: str, life_dir: Path) -> int:
    """Skip all pending updates and return the next offset."""
    resp = _api_call(token, "getUpdates", {"offset": -1, "limit": 1, "timeout": 0}, timeout=10)
    if resp and resp.get("ok") and resp.get("result"):
        offset = resp["result"][-1]["update_id"] + 1
        _write_offset(life_dir, offset)
        return offset
    return 0


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

_HELP_TEXT = """🤖 <b>argus-skill 命令列表</b>

/add <code>标题: 详细目标</code> — 添加任务
/status — 查看当前状态
/backlog — 查看待办任务
/start [目标] — 开启持续模式
/stop — 暂停持续模式
/nudge <code>文本</code> — 向当前任务注入指令
/help — 显示此帮助

直接发文字 → 自动添加为任务

<b>🏗️ 四层 Agent 架构</b>
L1 👷 工程师 — 编码执行任务
L2 👨‍🏫 审查员 — 代码审查与修复
L3 👔 评审员 — 评估质量并决定迭代
L4 🧠 规划师 — 分析项目并规划新任务"""


class _CommandRouter:
    """Stateless router: parses a message and executes the matching command."""

    def __init__(self, *, life_dir: Path, token: str, chat_id: str) -> None:
        self.life_dir = life_dir
        self.token = token
        self.chat_id = chat_id

    def _reply(self, text: str) -> None:
        _send_message(self.token, self.chat_id, text)

    # -- routing -----------------------------------------------------------

    def dispatch(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        # Strip bot mention suffix (e.g. /status@mybot)
        parts = text.split(None, 1)
        cmd_raw = parts[0].lower()
        if "@" in cmd_raw:
            cmd_raw = cmd_raw.split("@")[0]
        arg = parts[1].strip() if len(parts) > 1 else ""

        handlers = {
            "/add": self._cmd_add,
            "/status": self._cmd_status,
            "/backlog": self._cmd_backlog,
            "/start": self._cmd_start,
            "/stop": self._cmd_stop,
            "/nudge": self._cmd_nudge,
            "/help": self._cmd_help,
        }
        handler = handlers.get(cmd_raw)
        if handler:
            try:
                handler(arg)
            except Exception as exc:  # noqa: BLE001
                log.exception("telegram command %s failed", cmd_raw)
                self._reply(f"❌ 命令执行失败: {exc}")
        elif text.startswith("/"):
            self._reply(f"❓ 未知命令: {cmd_raw}\n使用 /help 查看可用命令")
        else:
            # Free text → treat as task
            self._cmd_add(text)

    # -- individual commands -----------------------------------------------

    _LAYER_LABELS = {
        "engineer": "👷 工程师 (L1)",
        "reviewer": "👨‍🏫 审查员 (L2)",
        "critic":   "👔 评审员 (L3)",
        "planner":  "🧠 规划师 (L4)",
    }

    def _detect_active_layer(self, mem: Any) -> str:
        """Infer the active agent layer from the most recent journal entry."""
        try:
            entries = mem.journal.tail(3)
            for e in reversed(entries):
                extra = getattr(e, "extra", None) or {}
                if isinstance(extra, dict):
                    layer = extra.get("agent_layer", "")
                    label = self._LAYER_LABELS.get(layer, "")
                    if label:
                        return label
                # Fallback: infer from kind
                kind = getattr(e, "kind", "")
                if kind in ("mission_started",):
                    return self._LAYER_LABELS["engineer"]
                if kind in ("mission_iterated",):
                    return self._LAYER_LABELS["critic"]
                if kind in ("planner_cycle", "planner_done"):
                    return self._LAYER_LABELS["planner"]
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _cmd_add(self, arg: str) -> None:
        if not arg:
            self._reply("用法: /add 任务标题: 详细目标\n或直接发送任务描述")
            return
        # Parse "title: objective" or use whole text as both
        if ":" in arg and arg.index(":") < 60:
            title, objective = arg.split(":", 1)
            title = title.strip()
            objective = objective.strip() or title
        else:
            title = arg[:60].strip()
            objective = arg.strip()

        from .memory import BacklogItem, LifeMemory
        mem = LifeMemory.open(self.life_dir)
        item = BacklogItem.new(title=title, objective=objective)
        mem.backlog.add(item)
        self._reply(
            f"✅ 任务已添加\n\n"
            f"📌 <b>{_esc(title)}</b>\n"
            f"🎯 {_esc(objective[:200])}\n"
            f"🔖 ID: <code>{item.id}</code>"
        )

    def _cmd_status(self, _arg: str) -> None:
        from ..daemon.life_worker import read_continuous_state, read_daemon_status
        from .memory import LifeMemory

        mem = LifeMemory.open(self.life_dir)
        ds = read_daemon_status(self.life_dir)
        cs = read_continuous_state(self.life_dir)

        all_items = mem.backlog.all()
        pending = sum(1 for it in all_items if it.status == "pending")
        running = sum(1 for it in all_items if it.status == "running")
        done = sum(1 for it in all_items if it.status == "done")
        failed = sum(1 for it in all_items if it.status == "failed")

        try:
            total_cost = mem.journal.total_cost_since(0)
        except Exception:  # noqa: BLE001
            total_cost = 0.0

        # Current running task
        running_items = [it for it in all_items if it.status == "running"]
        current_task = running_items[0] if running_items else None

        lines = ["📊 <b>argus-skill 状态</b>", ""]

        # Daemon
        if ds.alive:
            uptime_str = _fmt_duration(ds.uptime_seconds) if ds.uptime_seconds else "?"
            lines.append(f"🟢 守护进程运行中 (PID {ds.pid}, 已运行 {uptime_str})")
        else:
            lines.append("🔴 守护进程未运行")

        # Continuous mode
        if cs.enabled:
            obj_text = cs.objective[:80] if cs.objective else "无"
            lines.append(f"♾️ 持续模式: <b>开启</b> — {_esc(obj_text)}")
        elif cs.done_reason:
            lines.append(f"🏁 持续模式: 已完成 — {_esc(cs.done_reason[:80])}")
        else:
            lines.append("⏸️ 持续模式: 关闭")

        # Current task + active layer
        if current_task:
            lines.append(f"\n🔧 <b>当前任务:</b> {_esc(current_task.title[:60])}")
            lines.append(f"🎯 {_esc(current_task.objective[:150])}")
            # Determine active layer from most recent journal entry
            active_layer = self._detect_active_layer(mem)
            if active_layer:
                lines.append(f"🏗️ 当前层级: {active_layer}")
        else:
            # No running task — check if planner is active
            active_layer = self._detect_active_layer(mem)
            if active_layer:
                lines.append(f"\n🏗️ 当前层级: {active_layer}")
            else:
                lines.append("\n💤 空闲中")

        # Backlog
        lines.append(f"\n📋 待办 {pending} · 运行中 {running} · 完成 {done} · 失败 {failed}")

        # Cost
        lines.append(f"💵 累计花费: <b>${total_cost:.2f}</b>")

        self._reply("\n".join(lines))

    def _cmd_backlog(self, _arg: str) -> None:
        from .memory import LifeMemory
        mem = LifeMemory.open(self.life_dir)
        pending = mem.backlog.pending()
        if not pending:
            self._reply("📋 待办列表为空")
            return
        lines = [f"📋 <b>待办任务 ({len(pending)})</b>", ""]
        for i, it in enumerate(pending[:15], 1):
            lines.append(f"{i}. <b>{_esc(it.title[:50])}</b>")
            lines.append(f"   🎯 {_esc(it.objective[:100])}")
        if len(pending) > 15:
            lines.append(f"\n… 还有 {len(pending) - 15} 个任务")
        self._reply("\n".join(lines))

    def _cmd_start(self, arg: str) -> None:
        from ..daemon.life_worker import (
            continuous_mode_error,
            read_continuous_config,
            read_daemon_status,
            write_continuous_config,
        )
        _, current_obj = read_continuous_config(self.life_dir)
        objective = arg.strip() or current_obj
        backend = (
            read_daemon_status(self.life_dir).backend
            or os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex")
        )
        error = continuous_mode_error(backend, True, objective)
        if error:
            self._reply(f"❌ {error}")
            return
        write_continuous_config(self.life_dir, enabled=True, objective=objective)
        self._reply(
            f"▶️ 持续模式已开启\n"
            f"🎯 目标: {_esc(objective[:200]) if objective else '(沿用上次目标)'}"
        )

    def _cmd_stop(self, _arg: str) -> None:
        from ..daemon.life_worker import read_continuous_config, write_continuous_config
        _, objective = read_continuous_config(self.life_dir)
        write_continuous_config(self.life_dir, enabled=False, objective=objective)
        self._reply("⏸️ 持续模式已暂停\n当前任务执行完毕后将停止")

    def _cmd_nudge(self, arg: str) -> None:
        if not arg:
            self._reply("用法: /nudge <指令文本>\n会注入到当前任务的下一轮执行中")
            return
        inbox = self.life_dir / "inbox.jsonl"
        inbox.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": time.time(), "text": arg, "source": "telegram"}
        with inbox.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._reply(f"💬 指令已注入 ({len(arg)} 字)\n下一轮任务执行时工程师将看到此指令")

    def _cmd_help(self, _arg: str) -> None:
        self._reply(_HELP_TEXT)


# ---------------------------------------------------------------------------
# Poller
# ---------------------------------------------------------------------------

class TelegramPoller:
    """Long-polling thread that listens for inbound Telegram commands.

    Start with :meth:`start` (spawns a daemon thread). Stops when the
    ``stop_event`` fires or the parent process exits.
    """

    def __init__(
        self,
        *,
        life_dir: Path,
        token: str | None = None,
        chat_id: str | None = None,
        user_id: str | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.life_dir = life_dir
        self.token = (token or os.environ.get("ARGUS_SKILL_TELEGRAM_BOT_TOKEN") or "").strip()
        self.chat_id = (chat_id or os.environ.get("ARGUS_SKILL_TELEGRAM_CHAT_ID") or "").strip()
        self.user_id = (user_id or os.environ.get("ARGUS_SKILL_TELEGRAM_USER_ID") or "").strip()
        self._stop = stop_event or threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _message_allowed(self, msg: dict[str, Any]) -> bool:
        chat = msg.get("chat")
        if not isinstance(chat, dict):
            return False
        msg_chat_id = str(chat.get("id", ""))
        if msg_chat_id != self.chat_id:
            return False
        if not self.user_id:
            return True
        sender = msg.get("from")
        if not isinstance(sender, dict):
            return False
        sender_id = str(sender.get("id", ""))
        return sender_id == self.user_id

    def start(self) -> None:
        if not self.enabled:
            log.info("telegram poller disabled (missing token or chat_id)")
            return
        self._thread = threading.Thread(
            target=self._poll_loop, name="telegram-poller", daemon=True
        )
        self._thread.start()
        log.info("telegram poller started")

    # -- main loop ---------------------------------------------------------

    def _poll_loop(self) -> None:
        router = _CommandRouter(
            life_dir=self.life_dir, token=self.token, chat_id=self.chat_id,
        )

        # Recover offset or fast-forward to skip stale messages
        offset = _read_offset(self.life_dir)
        if offset is None:
            log.info("telegram poller: first boot, fast-forwarding updates")
            offset = _fast_forward(self.token, self.life_dir)

        backoff = 1.0

        while not self._stop.is_set():
            try:
                resp = _api_call(self.token, "getUpdates", {
                    "offset": offset,
                    "limit": 20,
                    "timeout": 30,
                    "allowed_updates": ["message"],
                }, timeout=35)

                if not resp or not resp.get("ok"):
                    self._stop.wait(timeout=min(backoff, 30))
                    backoff = min(backoff * 2, 60)
                    continue

                backoff = 1.0
                updates = resp.get("result") or []

                for update in updates:
                    uid = update.get("update_id", 0)
                    offset = uid + 1
                    _write_offset(self.life_dir, offset)

                    msg = update.get("message") or {}
                    text = (msg.get("text") or "").strip()

                    if not self._message_allowed(msg):
                        log.debug("telegram: ignoring unauthorized message")
                        continue
                    if not text:
                        continue

                    log.info("telegram command: %s", text[:80])
                    router.dispatch(text)

            except Exception:  # noqa: BLE001
                log.exception("telegram poller error; retrying in %.0fs", backoff)
                self._stop.wait(timeout=min(backoff, 60))
                backoff = min(backoff * 2, 60)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    """Escape HTML special chars for Telegram."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "?"
    s = int(seconds)
    if s < 60:
        return f"{s}秒"
    if s < 3600:
        return f"{s // 60}分{s % 60}秒"
    h = s // 3600
    m = (s % 3600) // 60
    if h < 24:
        return f"{h}时{m}分"
    d = h // 24
    h = h % 24
    return f"{d}天{h}时{m}分"
