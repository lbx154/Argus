"""Telegram Bot poller — inbound command interface for the daemon.

Runs as a daemon thread inside :class:`~argus_skill.daemon.life_worker.LifeWorker`.
Polls ``getUpdates`` with long-polling and dispatches commands:

* ``/add <title>: <objective>`` — add a task to the backlog
* ``/status`` — reply with daemon / active queue / history / cost summary
* ``/config [key=val ...]`` — view/change session defaults
* ``/identity`` / ``/identity set <text>`` — view or update the identity card
* ``/backend [codex|memory]`` — show or change the active backend
* ``/reset`` — drop the current codex session id
* ``/skills [ls|promote <name>]`` — inspect or promote skills
* ``/backlog [all]`` — list pending tasks or full backlog
* ``/done`` / ``/skip`` / ``/rm`` / ``/stop`` — backlog item lifecycle controls
* ``/start [objective]`` or ``/continuous start`` — enable continuous mode
* ``/continuous stop`` — disable continuous mode
* ``/run [opts]`` — run the shared foreground supervisor helper
* ``/nudge <text>`` — inject operator guidance into the next mission round
* ``/help`` — show available commands

Only messages from the configured ``chat_id`` (and optionally
``user_id``) are processed. Everything else is silently dropped.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..apps._inbox import count_pending_inbox_messages
from ..apps._life_actions import (
    _CONFIG_DEFAULTS as _LIFE_CONFIG_DEFAULTS,
)
from ..apps._life_actions import (
    add_backlog_item,
    append_note,
    format_backlog_list,
    format_journal_tail,
    format_status_change,
    parse_add_flags,
    render_backend_cmd,
    render_config_cmd,
    render_identity_cmd,
    render_reset_cmd,
    render_run_command,
    render_skills_cmd,
    stop_iteration,
)
from .status import count_backlog_statuses, describe_continuous_state, select_current_running_item

log = logging.getLogger(__name__)

__all__ = ["TelegramPoller", "telegram_enabled"]


def telegram_enabled() -> bool:
    """Whether the optional Telegram command interface is explicitly enabled."""
    return (os.environ.get("ARGUS_SKILL_ENABLE_TELEGRAM") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


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

/add <code>&lt;text&gt;</code> [--once] [--cycles=N] [--budget=$X] — 添加任务
/status — 查看守护进程、持续模式、当前任务、backlog/history、收件箱和预算/花费
/config [key=val ...] — 调整会话默认值
/identity — 查看身份卡
/identity set <text> — 单条消息更新身份卡
/backend [codex|memory] — 查看或切换后端
/reset — 清除当前 codex 会话
/skills [ls|promote <name>] — 查看或提升技能
/backlog [all] — 查看待办任务
/done <id> /skip <id> /rm <id> — 更新任务状态
/journal [N] — 查看最近日志
/note <text> — 追加手动笔记
/run [opts] — 运行一次任务调度
/stop <id> — 关闭任务迭代；必要时会把待办项标记为已完成
/start [目标] — 开启持续模式（/continuous start 的别名）
/continuous start|stop [目标] — 持续模式控制
/nudge <code>文本</code> — 向当前任务注入指令
/help — 显示此帮助

直接发文字 → 运行中注入当前任务；空闲时自动添加为任务。显式排新任务请用 /add

<b>🏗️ 四层 Agent 架构</b>
L1 👷 工程师 — 编码执行任务
L2 👨‍🏫 审查员 — 代码审查与修复
L3 👔 评审员 — 评估质量并决定迭代
L4 🧠 规划师 — 分析项目并规划新任务"""


@dataclass(frozen=True)
class _QueuedTask:
    id: str
    title: str
    objective: str


class _CommandRouter:
    """Stateless router: parses a message and executes the matching command."""

    def __init__(self, *, life_dir: Path, token: str, chat_id: str) -> None:
        self.life_dir = life_dir
        self.token = token
        self.chat_id = chat_id
        self._state: dict[str, Any] = {
            "backend": os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex"),
            "config": dict(_LIFE_CONFIG_DEFAULTS),
            "continuous_objective": "",
            "last_thread_id": None,
        }

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
            "/config": self._cmd_config,
            "/identity": self._cmd_identity,
            "/backend": self._cmd_backend,
            "/reset": self._cmd_reset,
            "/skills": self._cmd_skills,
            "/backlog": self._cmd_backlog,
            "/start": self._cmd_start,
            "/stop": self._cmd_stop,
            "/continuous": self._cmd_continuous,
            "/done": self._cmd_done,
            "/skip": self._cmd_skip,
            "/rm": self._cmd_rm,
            "/journal": self._cmd_journal,
            "/note": self._cmd_note,
            "/run": self._cmd_run,
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
            self._cmd_free_text(text)

    # -- individual commands -----------------------------------------------

    _LAYER_LABELS = {
        "engineer": "👷 工程师 (L1)",
        "reviewer": "👨‍🏫 审查员 (L2)",
        # critic layer removed,
        "planner":  "🧠 规划师 (L4)",
    }

    def _detect_active_layer(self, mem: Any) -> str:
        """Read the explicit active agent layer from the most recent journal entry."""
        try:
            entries = mem.journal.tail(3)
            for e in reversed(entries):
                extra = getattr(e, "extra", None) or {}
                if isinstance(extra, dict):
                    layer = extra.get("agent_layer", "")
                    label = self._LAYER_LABELS.get(layer, "")
                    if label:
                        return label
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _queue_task(self, arg: str) -> _QueuedTask | None:
        cfg = self._state.setdefault("config", dict(_LIFE_CONFIG_DEFAULTS))
        iterate, cycles, budget, body = parse_add_flags(
            arg,
            defaults=cfg,
        )
        body = body.strip()
        if not body:
            return None
        if ":" in body and body.index(":") < 60:
            title, objective = body.split(":", 1)
            title = title.strip()
            objective = objective.strip() or title
        else:
            title = body[:60].strip()
            objective = body.strip()

        from .memory import LifeMemory

        mem = LifeMemory.open(self.life_dir)
        item = add_backlog_item(
            mem,
            objective,
            iterate=iterate,
            iteration_max_cycles=cycles,
            iteration_budget_usd=budget,
        )
        if title != item.title or objective != item.objective:
            mem.backlog.update(item.id, title=title, objective=objective)
        return _QueuedTask(id=item.id, title=title, objective=objective)

    def _cmd_add(self, arg: str) -> None:
        if not arg:
            self._reply("用法: /add 任务标题: 详细目标\n或直接发送任务描述")
            return
        queued = self._queue_task(arg)
        if queued is None:
            self._reply("用法: /add 任务标题: 详细目标\n或直接发送任务描述")
            return
        self._reply(
            f"✅ 任务已添加\n\n"
            f"📌 <b>{_esc(queued.title)}</b>\n"
            f"🎯 {_esc(queued.objective[:200])}\n"
            f"🔖 ID: <code>{queued.id}</code>"
        )

    def _cmd_free_text(self, text: str) -> None:
        """Route natural Telegram text to the most timely useful action."""
        from ..apps._inbox import queue_inbox_message
        from ..daemon.life_worker import read_daemon_status
        from .memory import LifeMemory

        mem = LifeMemory.open(self.life_dir)
        current_task = select_current_running_item(mem.backlog.all())
        daemon_status = read_daemon_status(self.life_dir)
        if daemon_status.alive and current_task is not None:
            text = text.strip()
            queue_inbox_message(self.life_dir, text, source="telegram.free_text")
            title = _esc(str(getattr(current_task, "title", ""))[:80])
            self._reply(
                "收到，我把这句话交给当前任务了。\n"
                f"🔧 现在处理：{title}\n"
                "它不会打断正在进行的 LLM 调用；下一轮会看到。\n"
                "如果想另外开一个任务，请用 /add；查进度用 /status。"
            )
            return

        queued = self._queue_task(text)
        if queued is None:
            self._reply("我没收到有效内容；可以直接发任务描述，或用 /help 查看命令。")
            return
        if daemon_status.alive:
            status_line = "我会开始处理，并把关键进展继续发在这里。"
        else:
            status_line = "我先记下来了；守护进程现在没运行，启动后会处理。"
        self._reply(
            "收到，我会把这当作一个新任务来做。\n"
            f"📌 <b>{_esc(queued.title)}</b>\n"
            f"🎯 {_esc(queued.objective[:200])}\n"
            f"🔖 ID: <code>{queued.id}</code>\n"
            f"{status_line}\n"
            "中间如果在匹配技能、读代码或跑测试，我也会发进展；/status 可以随时查看。"
        )

    def _cmd_status(self, _arg: str) -> None:
        from ..daemon.life_worker import (
            format_budget_status,
            read_continuous_state,
            read_daemon_status,
        )
        from .memory import LifeMemory

        mem = LifeMemory.open(self.life_dir)
        ds = read_daemon_status(self.life_dir)
        cs = read_continuous_state(self.life_dir)

        all_items = mem.backlog.all()
        pending, running, done, failed, skipped = count_backlog_statuses(all_items)
        current_task = select_current_running_item(all_items)
        inbox_pending = count_pending_inbox_messages(self.life_dir)

        try:
            total_cost = mem.journal.total_cost_since(0)
        except Exception:  # noqa: BLE001
            total_cost = 0.0
        cont = describe_continuous_state(cs)

        lines = ["📊 <b>argus-skill 状态</b>", ""]

        # Daemon
        if ds.alive:
            uptime_str = _fmt_duration(ds.uptime_seconds) if ds.uptime_seconds else "?"
            lines.append(f"🟢 守护进程运行中 (PID {ds.pid}, 已运行 {uptime_str})")
        else:
            lines.append("🔴 守护进程未运行")

        # Continuous mode
        if cont.enabled:
            obj_text = cont.objective[:80] if cont.objective else "无"
            lines.append(f"♾️ 持续模式: <b>开启</b> — {_esc(obj_text)}")
        elif cont.is_completed:
            lines.append(f"🏁 持续模式: 已完成 — {_esc(cont.done_reason[:80])}")
            if cont.done_at:
                lines.append(f"🕒 完成于: {_esc(cont.done_at)}")
        else:
            lines.append("⏸️ 持续模式: 关闭")

        # Current task + active layer
        if current_task:
            current_id = _esc(str(getattr(current_task, "id", "")))
            current_title = _esc(str(getattr(current_task, "title", ""))[:60])
            current_objective = _esc(str(getattr(current_task, "objective", ""))[:150])
            lines.append(f"\n🔧 <b>当前任务:</b> {current_title}")
            lines.append(f"🔖 ID: <code>{current_id}</code>")
            lines.append(f"🎯 {current_objective}")
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
        lines.append(f"\n📋 active: {pending} pending · {running} running")
        history_parts = [part for part in (
            f"{done} done" if done else "",
            f"{failed} failed" if failed else "",
            f"{skipped} skipped" if skipped else "",
        ) if part]
        if history_parts:
            lines.append(f"🕰️ history: {' · '.join(history_parts)}")

        lines.append(f"📬 收件箱: {inbox_pending} 条待处理")
        lines.append(f"💵 {format_budget_status(mem.journal, status=ds)}")

        # Cost
        lines.append(f"💵 累计花费: <b>${total_cost:.2f}</b>")

        self._reply("\n".join(lines))

    def _cmd_config(self, arg: str) -> None:
        from ..daemon.life_worker import read_continuous_state

        self._state["continuous_state"] = read_continuous_state(self.life_dir)
        self._state["continuous_objective"] = self._state["continuous_state"].objective
        tokens = shlex.split(arg) if arg.strip() else []
        body = render_config_cmd(tokens, self._state, life_dir=self.life_dir)
        self._reply(f"<pre>{_esc(body)}</pre>")

    def _cmd_identity(self, arg: str) -> None:
        from .memory import LifeMemory

        if arg.strip().lower().startswith("edit"):
            self._reply("Telegram 不支持 /identity edit；请使用 /identity 或 /identity set <text>")
            return
        mem = LifeMemory.open(self.life_dir)
        tokens = shlex.split(arg) if arg.strip() else []
        body = render_identity_cmd(mem, tokens, arg)
        self._reply(f"<pre>{_esc(body)}</pre>")

    def _cmd_backend(self, arg: str) -> None:
        from ..daemon.life_worker import read_continuous_state

        self._state["continuous_state"] = read_continuous_state(self.life_dir)
        self._state["continuous_objective"] = self._state["continuous_state"].objective
        tokens = shlex.split(arg) if arg.strip() else []
        self._reply(f"<pre>{_esc(render_backend_cmd(tokens, self._state))}</pre>")

    def _cmd_reset(self, _arg: str) -> None:
        self._reply(f"<pre>{_esc(render_reset_cmd(self._state))}</pre>")

    def _cmd_skills(self, arg: str) -> None:
        tokens = shlex.split(arg) if arg.strip() else []
        self._reply(f"<pre>{_esc(render_skills_cmd(tokens))}</pre>")

    def _cmd_backlog(self, arg: str) -> None:
        from .memory import LifeMemory

        mem = LifeMemory.open(self.life_dir)
        include_all = arg.strip().lower() == "all"
        body = format_backlog_list(mem, include_all=include_all)
        if body == "(backlog is empty)":
            self._reply("📋 待办列表为空" if not include_all else "📋 全部任务为空")
            return
        title = "📋 <b>全部任务</b>" if include_all else "📋 <b>待办任务</b>"
        self._reply(f"{title}\n\n<pre>{_esc(body)}</pre>" if body else title)

    def _cmd_start(self, arg: str) -> None:
        self._cmd_continuous(f"start {arg}".strip())

    def _cmd_continuous(self, arg: str) -> None:
        from ..daemon.life_worker import (
            continuous_mode_error,
            read_continuous_config,
            read_daemon_status,
            write_continuous_config,
        )

        tokens = shlex.split(arg) if arg else []
        sub = tokens[0].lower() if tokens else "status"
        if sub in {"start", "on", "enable"}:
            _, current_obj = read_continuous_config(self.life_dir)
            objective = " ".join(tokens[1:]).strip() or current_obj
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
            return
        if sub in {"stop", "off", "pause"}:
            _, objective = read_continuous_config(self.life_dir)
            write_continuous_config(self.life_dir, enabled=False, objective=objective)
            self._reply("⏸️ 持续模式已暂停\n当前任务执行完毕后将停止")
            return
        enabled, objective = read_continuous_config(self.life_dir)
        state = "开启" if enabled else "关闭"
        self._reply(
            f"♾️ 持续模式: {state}\n"
            f"🎯 目标: {_esc(objective[:200]) if objective else '（无）'}"
        )

    def _cmd_stop(self, arg: str) -> None:
        item_id = arg.strip()
        if not item_id:
            self._reply("用法: /stop <id>")
            return
        from .memory import LifeMemory

        mem = LifeMemory.open(self.life_dir)
        result = stop_iteration(mem, item_id)
        if "(not found)" in result:
            self._reply(f"❓ 未找到任务: <code>{_esc(item_id)}</code>")
            return
        self._reply(f"⏸️ {result}")

    def _cmd_done(self, arg: str) -> None:
        self._cmd_status_change("/done", arg)

    def _cmd_skip(self, arg: str) -> None:
        self._cmd_status_change("/skip", arg)

    def _cmd_rm(self, arg: str) -> None:
        self._cmd_status_change("/rm", arg)

    def _cmd_status_change(self, cmd: str, arg: str) -> None:
        item_id = arg.strip()
        if not item_id:
            self._reply(f"用法: {cmd} <id>")
            return
        from .memory import LifeMemory

        mem = LifeMemory.open(self.life_dir)
        result = format_status_change(mem, cmd, item_id)
        if "(not found)" in result:
            self._reply(f"❓ 未找到任务: <code>{_esc(item_id)}</code>")
            return
        labels = {
            "/done": "已完成",
            "/skip": "已跳过",
            "/rm": "已删除",
        }
        self._reply(f"✅ {labels.get(cmd, cmd)}: <code>{_esc(item_id)}</code>")

    def _cmd_journal(self, arg: str) -> None:
        n = 10
        if arg.strip():
            try:
                n = int(arg.strip())
            except ValueError:
                self._reply(f"用法: /journal [N]  (got: {arg!r})")
                return
        from .memory import LifeMemory

        mem = LifeMemory.open(self.life_dir)
        body = format_journal_tail(mem, n)
        self._reply(f"📓 <b>最近日志</b>\n\n<pre>{_esc(body)}</pre>" if body else "📓 <b>最近日志</b>")

    def _cmd_note(self, arg: str) -> None:
        if not arg.strip():
            self._reply("用法: /note <text>")
            return
        from .memory import LifeMemory

        mem = LifeMemory.open(self.life_dir)
        result = append_note(mem, arg)
        self._reply(f"📝 {result}")

    def _cmd_run(self, arg: str) -> None:
        from types import SimpleNamespace

        from .memory import LifeMemory

        opts = shlex.split(arg) if arg.strip() else []
        mem = LifeMemory.open(self.life_dir)
        global_root = (
            self.life_dir.parent.parent
            if self.life_dir.parent.name == "projects"
            and self.life_dir.parent.parent != self.life_dir.parent
            else self.life_dir
        )
        run_mem = SimpleNamespace(
            root=self.life_dir,
            global_root=global_root,
            project=SimpleNamespace(root=self.life_dir),
            identity=mem.identity,
            journal=mem.journal,
            backlog=mem.backlog,
        )
        output = render_run_command(run_mem, opts, self._state)
        if not output:
            self._reply("用法: /run [opts]")
            return
        self._reply(f"<pre>{_esc(output)}</pre>")

    def _cmd_nudge(self, arg: str) -> None:
        if not arg:
            self._reply("用法: /nudge <指令文本>\n会注入到当前任务的下一轮执行中")
            return
        from ..apps._inbox import queue_inbox_message

        text = arg.strip()
        queue_inbox_message(self.life_dir, text, source="telegram.nudge")
        self._reply(
            f"💬 指令已注入 ({len(text)} 字)\n"
            "当前 LLM 调用无法中断；下一次工程师 round / 下一 mission prompt 会看到。"
        )

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
        self._state: dict[str, Any] = {
            "backend": os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex"),
            "config": dict(_LIFE_CONFIG_DEFAULTS),
            "continuous_objective": "",
            "last_thread_id": None,
        }

    @property
    def enabled(self) -> bool:
        return telegram_enabled() and bool(self.token and self.chat_id)

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
