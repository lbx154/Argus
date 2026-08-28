"""The operator command surface, independent of any messaging platform.

Lifted out of ``argus_skill.life.telegram_bot`` so Telegram and Feishu run the
same commands instead of drifting apart. Command bodies are unchanged: they
compose replies in Telegram-flavoured HTML and hand them to a
:class:`~argus_skill.life.chat.transport.ChatTransport`, which renders that
canonical markup for its own platform.

Commands:

* ``/add <title>: <objective>`` — add a task to the backlog
* ``/status`` — daemon / active queue / history / cost summary
* ``/config [key=val ...]`` — view/change session defaults
* ``/identity`` / ``/identity set <text>`` — view or update the identity card
* ``/backend [codex|claude|copilot|opencode|pi|grok|qoder|dsh|memory]`` — show or change backend
* ``/reset`` — drop the current codex session id
* ``/skills [ls|promote <name>]`` — inspect or promote skills
* ``/backlog [all]`` — list pending tasks or the full backlog
* ``/done`` / ``/skip`` / ``/rm`` / ``/stop`` — backlog item lifecycle
* ``/start [objective]`` / ``/continuous start|stop`` — continuous mode
* ``/run [opts]`` — run the shared foreground supervisor helper
* ``/nudge <text>`` — inject operator guidance into the next mission round
* ``/journal [N]`` / ``/note <text>`` — read or append the journal
* ``/help`` — show available commands
"""
from __future__ import annotations

import logging
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...apps._inbox import count_pending_inbox_messages
from ...apps._life_actions import (
    DEFAULT_LIFE_CONFIG,
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
from ..status import (
    count_backlog_statuses,
    describe_continuous_state,
    select_current_running_item,
)
from .transport import ChatTransport

log = logging.getLogger(__name__)

__all__ = ["CommandRouter", "COMMAND_MENU", "help_text"]


#: Command name → one-line description, in the order operators meet them.
#: Doubles as the source for Telegram's ``setMyCommands`` menu, so a phone
#: shows the whole surface without anyone having to remember ``/help``.
COMMAND_MENU: tuple[tuple[str, str], ...] = (
    ("status", "查看守护进程 / 当前任务 / backlog / 花费"),
    ("add", "添加任务：/add 标题: 目标"),
    ("ask", "直接回答，不排任务、只走 Manager"),
    ("nudge", "向当前任务注入指令"),
    ("backlog", "查看待办任务（/backlog all 看全部）"),
    ("journal", "查看最近日志"),
    ("start", "开启持续模式"),
    ("stop", "结束某个任务的迭代"),
    ("continuous", "持续模式开关"),
    ("done", "标记任务完成"),
    ("skip", "跳过任务"),
    ("rm", "删除任务"),
    ("run", "运行一次任务调度"),
    ("config", "查看/调整会话默认值"),
    ("backend", "查看或切换后端"),
    ("identity", "查看或更新身份卡"),
    ("skills", "查看或提升技能"),
    ("note", "追加一条手动笔记"),
    ("reset", "清除当前会话 id"),
    ("help", "显示命令列表"),
)


def help_text(channel_name: str = "") -> str:
    """Render the ``/help`` body, naming the channel the operator is on."""
    where = channel_name or "这里"
    architecture = """<b>🏗️ 四角色运行时</b>
01 👔 Manager · 控制 — 理解意图、选择工作流
02 🧠 Planner · 方向 — 规划后续任务
03 👷 Engineer · 执行 — 实现、调研、实验
04 👨‍🏫 Reviewer · 验证 — 检查正确性与完成状态"""
    return f"""🤖 <b>argus-skill 命令列表</b>

/add <code>&lt;text&gt;</code> [--once] [--cycles=N] — 添加任务
/status — 查看守护进程、持续模式、当前任务、backlog/history、收件箱和预算/花费
/config [key=val ...] — 调整会话默认值
/identity — 查看身份卡
/identity set <text> — 单条消息更新身份卡
/backend [codex|claude|copilot|opencode|pi|grok|qoder|dsh|memory] — 查看或切换后端
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
/ask <code>问题</code> — 直接回答，不排任务、只走 Manager
/nudge <code>文本</code> — 向当前任务注入指令
/help — 显示此帮助

直接发文字 → 运行中注入当前任务；空闲时由 Manager 判断直接回复或添加任务。显式排新任务请用 /add
当前频道：{where}

{architecture}"""


@dataclass(frozen=True)
class QueuedTask:
    id: str
    title: str
    objective: str


class CommandRouter:
    """Stateless router: parses a message and executes the matching command."""

    def __init__(self, *, life_dir: Path, transport: ChatTransport) -> None:
        self.life_dir = life_dir
        self.transport = transport
        self.channel = transport.channel
        self._state: dict[str, Any] = {
            "backend": os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex"),
            "config": dict(DEFAULT_LIFE_CONFIG),
            "continuous_objective": "",
            "last_thread_id": None,
            "session_id": self.life_dir.name,
            "global_root": str(self.life_dir.parent.parent),
        }

    def _reply(self, text: str) -> None:
        self.transport.send(text)

    def _source(self, kind: str) -> str:
        """Inbox provenance tag, e.g. ``telegram.nudge`` / ``feishu.nudge``."""
        return f"{self.channel}.{kind}"

    def _intake_operator_text(self, text: str) -> tuple[str, Any, str | None, str]:
        """Redact credentials, then run the shared Manager intake classifier."""
        from ...core.operator_context import import_deterministic_credential
        from ...manager.config_intent import _front_door_classify
        from ..memory import MemoryBundle

        safe_text, credential = import_deterministic_credential(
            self.life_dir,
            text,
            global_root=self.life_dir.parent.parent,
        )
        mem = MemoryBundle.for_cwd(
            fingerprint=self.life_dir.name,
            global_root=self.life_dir.parent.parent,
        )
        self._state["_frontdoor_credential_imported"] = credential is not None
        intent, control, route = _front_door_classify(
            mem,
            safe_text,
            self._state,
            active_mission=bool(select_current_running_item(mem.backlog.active())),
        )
        return safe_text, intent, control, route

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
            "/ask": self._cmd_ask,
            "/chat": self._cmd_ask,
            "/nudge": self._cmd_nudge,
            "/help": self._cmd_help,
        }
        handler = handlers.get(cmd_raw)
        if handler:
            try:
                handler(arg)
            except Exception:  # noqa: BLE001
                log.exception("%s command %s failed", self.channel, cmd_raw)
                self._reply("❌ 这条命令暂时无法完成。详细错误已记录，请稍后重试。")
        elif text.startswith("/"):
            self._reply(f"❓ 未知命令: {cmd_raw}\n使用 /help 查看可用命令")
        else:
            try:
                self._cmd_free_text(text)
            except Exception:  # noqa: BLE001
                log.exception("%s free-text dispatch failed", self.channel)
                self._reply("❌ 暂时无法安排这项任务。详细错误已记录，请稍后重试。")

    # -- individual commands -----------------------------------------------

    _LAYER_LABELS = {
        "manager": "👔 Manager · 控制",
        "planner": "🧠 Planner · 方向",
        "engineer": "👷 Engineer · 执行",
        "reviewer": "👨‍🏫 Reviewer · 验证",
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

    def _queue_task(self, arg: str, *, intake_done: bool = False) -> QueuedTask | None:
        if not intake_done:
            arg, _intent, _control, _route = self._intake_operator_text(arg)
        cfg = self._state.setdefault("config", dict(DEFAULT_LIFE_CONFIG))
        iterate, cycles, body = parse_add_flags(
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

        from ...manager.front_door import manager_bounded_handoff
        from ..memory import BacklogItem, MemoryBundle
        from ..supervisor.backlog_guard import decision_evidence

        mem = MemoryBundle.for_cwd(
            fingerprint=self.life_dir.name,
            global_root=self.life_dir.parent.parent,
        )
        item_id = BacklogItem.new_id()

        def _persist(execution_task: str, division: Any) -> Any:
            return add_backlog_item(
                mem,
                execution_task,
                item_id=item_id,
                iterate=iterate,
                iteration_max_cycles=cycles,
                manager_decision=decision_evidence(division) or {"routed": True},
            )

        item = manager_bounded_handoff(
            mem,
            objective,
            self._state,
            _persist,
            root_task_id=item_id,
        )
        execution_task = item.objective
        clean_title = execution_task.splitlines()[0][:60].strip()
        if clean_title and clean_title != item.title:
            mem.backlog.update(item.id, title=clean_title)
        return QueuedTask(
            id=item.id,
            title=clean_title or item.title,
            objective=execution_task,
        )

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
            f"🎯 {_esc(queued.objective[:200])}"
        )

    def _cmd_free_text(self, text: str) -> None:
        """Route natural chat text to the most timely useful action."""
        from ...apps._inbox import queue_inbox_message
        from ...daemon.life_worker import read_daemon_status
        from ...manager.config_intent import _apply_config_intent
        from ...manager.front_door import manager_triage
        from ..memory import LifeMemory, MemoryBundle

        mem = LifeMemory.open(self.life_dir)
        current_task = select_current_running_item(mem.backlog.active())
        daemon_status = read_daemon_status(self.life_dir)
        if daemon_status.alive and current_task is not None:
            from ...core.operator_context import import_deterministic_credential

            text, _credential = import_deterministic_credential(
                self.life_dir,
                text.strip(),
                global_root=self.life_dir.parent.parent,
            )
            queue_inbox_message(self.life_dir, text, source=self._source("free_text"))
            title = _esc(str(getattr(current_task, "title", ""))[:80])
            self._reply(
                "收到，我把这句话交给当前任务了。\n"
                f"🔧 现在处理：{title}\n"
                "它不会打断正在进行的 LLM 调用；下一轮会看到。\n"
                "如果想另外开一个任务，请用 /add；查进度用 /status。"
            )
            return

        text, intent, _control, route = self._intake_operator_text(text.strip())
        if intent is not None:
            confirmations: list[str] = []
            manager_mem = MemoryBundle.for_cwd(
                fingerprint=self.life_dir.name,
                global_root=self.life_dir.parent.parent,
            )
            if _apply_config_intent(
                manager_mem,
                intent,
                self._state,
                on_confirm=confirmations.append,
            ):
                self._reply("\n".join(confirmations))
                return

        manager_mem = MemoryBundle.for_cwd(
            fingerprint=self.life_dir.name,
            global_root=self.life_dir.parent.parent,
        )
        from ...manager.front_door import _accepts_parameter

        triage_kwargs: dict[str, Any] = {}
        if _accepts_parameter(manager_triage, "route"):
            triage_kwargs["route"] = route
        if _accepts_parameter(manager_triage, "self_mode"):
            triage_kwargs["self_mode"] = str(
                self._state.get("_frontdoor_self_mode", "inspect")
            )
        reply = manager_triage(manager_mem, text, self._state, **triage_kwargs)
        if reply is not None:
            self._reply(_esc(reply))
            return

        queued = self._queue_task(text, intake_done=True)
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
            f"{status_line}\n"
            "中间如果在匹配技能、读代码或跑测试，我也会发进展；/status 可以随时查看。"
        )

    def _cmd_status(self, _arg: str) -> None:
        from ...daemon.life_worker import (
            format_budget_status,
            read_continuous_state,
            read_daemon_status,
        )
        from ..memory import LifeMemory

        mem = LifeMemory.open(self.life_dir)
        ds = read_daemon_status(self.life_dir)
        cs = read_continuous_state(self.life_dir)

        # /status reports cumulative done/failed/skipped counts, so it is an
        # explicit history consumer rather than a live scheduling read.
        all_items = mem.backlog.history()
        pending, running, paused, done, failed, skipped = count_backlog_statuses(
            all_items
        )
        current_task = select_current_running_item(all_items)
        inbox_pending = count_pending_inbox_messages(self.life_dir)

        try:
            from ...core.usage import format_usage_cost, project_usage_summary

            usage_summary = project_usage_summary(self.life_dir)
            total_cost_text = format_usage_cost(usage_summary)
        except Exception:  # noqa: BLE001
            total_cost_text = "partial"
        cont = describe_continuous_state(cs)

        lines = ["📊 <b>argus-skill 状态</b>", ""]

        # Daemon
        if ds.alive:
            uptime_str = _fmt_duration(ds.uptime_seconds) if ds.uptime_seconds else "?"
            lines.append(f"🟢 后台工作进程运行中（已运行 {uptime_str}）")
        else:
            lines.append("🔴 后台工作进程未运行")

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
            current_title = _esc(str(getattr(current_task, "title", ""))[:60])
            current_objective = _esc(str(getattr(current_task, "objective", ""))[:150])
            lines.append(f"\n🔧 <b>当前任务:</b> {current_title}")
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
        lines.append(
            f"\n📋 进行中：{pending} 项待办 · {running} 项执行中 · {paused} 项已暂停"
        )
        history_parts = [part for part in (
            f"{done} 项完成" if done else "",
            f"{failed} 项未完成" if failed else "",
            f"{skipped} 项已跳过" if skipped else "",
        ) if part]
        if history_parts:
            lines.append(f"🕰️ 历史：{' · '.join(history_parts)}")

        lines.append(f"📬 收件箱: {inbox_pending} 条待处理")
        lines.append(f"💵 {format_budget_status(mem.journal, status=ds)}")

        # Cost
        lines.append(f"💵 累计成本: <b>{_esc(total_cost_text)}</b>")

        self._reply("\n".join(lines))

    def _cmd_config(self, arg: str) -> None:
        from ...daemon.life_worker import read_continuous_state

        self._state["continuous_state"] = read_continuous_state(self.life_dir)
        self._state["continuous_objective"] = self._state["continuous_state"].objective
        tokens = shlex.split(arg) if arg.strip() else []
        body = render_config_cmd(tokens, self._state, life_dir=self.life_dir)
        self._reply(f"<pre>{_esc(body)}</pre>")

    def _cmd_identity(self, arg: str) -> None:
        from ..memory import LifeMemory

        if arg.strip().lower().startswith("edit"):
            self._reply(
                f"{self.transport.display_name} 不支持 /identity edit；"
                "请使用 /identity 或 /identity set <text>"
            )
            return
        mem = LifeMemory.open(self.life_dir)
        tokens = shlex.split(arg) if arg.strip() else []
        body = render_identity_cmd(mem, tokens, arg)
        self._reply(f"<pre>{_esc(body)}</pre>")

    def _cmd_backend(self, arg: str) -> None:
        from ...daemon.life_worker import read_continuous_state

        self._state["continuous_state"] = read_continuous_state(self.life_dir)
        self._state["continuous_objective"] = self._state["continuous_state"].objective
        tokens = shlex.split(arg) if arg.strip() else []
        self._reply(
            f"<pre>{_esc(render_backend_cmd(tokens, self._state, life_dir=self.life_dir))}</pre>"
        )

    def _cmd_reset(self, _arg: str) -> None:
        self._reply(f"<pre>{_esc(render_reset_cmd(self._state))}</pre>")

    def _cmd_skills(self, arg: str) -> None:
        tokens = shlex.split(arg) if arg.strip() else []
        self._reply(f"<pre>{_esc(render_skills_cmd(tokens))}</pre>")

    def _cmd_backlog(self, arg: str) -> None:
        from ..memory import LifeMemory

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
        from ...daemon.life_worker import (
            continuous_mode_error,
            disable_continuous_config,
            read_continuous_config,
            read_daemon_status,
        )

        tokens = shlex.split(arg) if arg else []
        sub = tokens[0].lower() if tokens else "status"
        if sub in {"start", "on", "enable"}:
            _, current_obj = read_continuous_config(self.life_dir)
            requested_objective = " ".join(tokens[1:]).strip()
            objective = requested_objective or current_obj
            backend = (
                read_daemon_status(self.life_dir).backend
                or os.environ.get("ARGUS_SKILL_LIFE_BACKEND", "codex")
            )
            error = continuous_mode_error(backend, True, objective)
            if error:
                self._reply(f"❌ {error}")
                return
            from ...manager.front_door import manager_continuous_handoff
            from ..memory import MemoryBundle

            mem = MemoryBundle.for_cwd(
                fingerprint=self.life_dir.name,
                global_root=self.life_dir.parent.parent,
            )
            objective = manager_continuous_handoff(
                mem,
                requested_objective,
                self._state,
            )
            self._state["continuous_objective"] = objective
            self._reply(
                f"▶️ 持续模式已开启\n"
                f"🎯 目标: {_esc(objective[:200]) if objective else '(沿用上次目标)'}"
            )
            return
        if sub in {"stop", "off", "pause"}:
            disable_continuous_config(self.life_dir)
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
        from ..memory import LifeMemory

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
        from ..memory import LifeMemory

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
        from ..memory import LifeMemory

        mem = LifeMemory.open(self.life_dir)
        body = format_journal_tail(mem, n)
        self._reply(f"📓 <b>最近日志</b>\n\n<pre>{_esc(body)}</pre>" if body else "📓 <b>最近日志</b>")

    def _cmd_note(self, arg: str) -> None:
        if not arg.strip():
            self._reply("用法: /note <text>")
            return
        from ..memory import LifeMemory

        mem = LifeMemory.open(self.life_dir)
        result = append_note(mem, arg)
        self._reply(f"📝 {result}")

    def _cmd_run(self, arg: str) -> None:
        from types import SimpleNamespace

        from ..memory import LifeMemory

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
        from ...apps._inbox import queue_inbox_message

        from ...core.operator_context import import_deterministic_credential

        text, _credential = import_deterministic_credential(
            self.life_dir,
            arg.strip(),
            global_root=self.life_dir.parent.parent,
        )
        queue_inbox_message(self.life_dir, text, source=self._source("nudge"))
        self._reply(
            f"💬 指令已注入 ({len(text)} 字)\n"
            "当前 LLM 调用无法中断；下一次工程师 round / 下一 mission prompt 会看到。"
        )

    def _cmd_ask(self, arg: str) -> None:
        """Answer inline. The operator said this is a question, so nothing is
        queued and no role beyond the Manager is involved."""
        question = arg.strip()
        if not question:
            self._reply("用法: /ask <问题>\n直接回答，不会排进任务队列")
            return
        from ...webapi.manager_bridge import _answer_inline

        question, _intent, _control, _route = self._intake_operator_text(question)
        self._reply(_esc(_answer_inline(self.life_dir.name, self.life_dir, question)))

    def _cmd_help(self, _arg: str) -> None:
        self._reply(help_text(self.transport.display_name))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    """Escape HTML special chars in reply body text."""
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
