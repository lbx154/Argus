"""Tests for telegram_bot.py — command routing and notification formatting."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import TypedDict
from unittest.mock import MagicMock, patch

import pytest

from argus_skill.life import JournalEntry
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.telegram_bot import TelegramPoller, _CommandRouter


class _ApiCallRecord(TypedDict):
    token: str
    method: str
    payload: dict[str, object]
    timeout: float


class _SingleIterationEvent(threading.Event):
    def wait(self, timeout: float | None = None) -> bool:
        self.set()
        return True


def _offset_from_payload(payload: dict[str, object] | None) -> int:
    if payload is None:
        return 0
    offset = payload.get("offset")
    assert isinstance(offset, int)
    return offset


def _offset_from_call(call: _ApiCallRecord) -> int:
    offset = call["payload"].get("offset")
    assert isinstance(offset, int)
    return offset

# ---------------------------------------------------------------------------
# _CommandRouter tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def life_dir(tmp_path: Path) -> Path:
    d = tmp_path / "life"
    d.mkdir()
    # Seed minimal backlog + journal
    (d / "backlog.jsonl").touch()
    (d / "journal.jsonl").touch()
    (d / "identity.md").write_text("# test\n")
    return d


@pytest.fixture()
def status_life_dir(tmp_path: Path) -> Path:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    done = mem.backlog.add(BacklogItem.new(title="done", objective="finished work"))
    mem.backlog.mark_done(done.id)
    failed = mem.backlog.add(BacklogItem.new(title="failed", objective="bad work"))
    mem.backlog.mark_failed(failed.id, error="boom")
    skipped = mem.backlog.add(BacklogItem.new(title="skipped", objective="later work"))
    mem.backlog.update(skipped.id, status="skipped")
    first = json.dumps({"text": "old guidance"}) + "\n"
    second = json.dumps({"text": "fresh guidance"}) + "\n"
    (tmp_path / "inbox.jsonl").write_text(first + second, encoding="utf-8")
    (tmp_path / "inbox.offset").write_text(
        str(len(first.encode("utf-8"))),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def status_life_dir_with_active(tmp_path: Path) -> Path:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    pending = mem.backlog.add(BacklogItem.new(title="pending", objective="queued work"))
    running = mem.backlog.add(BacklogItem.new(title="running", objective="in flight"))
    mem.backlog.mark_running(running.id)
    done = mem.backlog.add(BacklogItem.new(title="done", objective="finished work"))
    mem.backlog.mark_done(done.id)
    failed = mem.backlog.add(BacklogItem.new(title="failed", objective="bad work"))
    mem.backlog.mark_failed(failed.id, error="boom")
    skipped = mem.backlog.add(BacklogItem.new(title="skipped", objective="later work"))
    mem.backlog.update(skipped.id, status="skipped")
    first = json.dumps({"text": "old guidance"}) + "\n"
    second = json.dumps({"text": "fresh guidance"}) + "\n"
    (tmp_path / "inbox.jsonl").write_text(first + second, encoding="utf-8")
    (tmp_path / "inbox.offset").write_text(
        str(len(first.encode("utf-8"))),
        encoding="utf-8",
    )
    assert pending.id
    return tmp_path


@pytest.fixture()
def status_life_dir_with_stale_running(tmp_path: Path) -> Path:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    older = mem.backlog.add(BacklogItem.new(title="older", objective="first stale row"))
    newer = mem.backlog.add(BacklogItem.new(title="newer", objective="current task row"))
    mem.backlog.update(older.id, status="running", started_ts=10.0)
    mem.backlog.update(newer.id, status="running", started_ts=20.0)
    done = mem.backlog.add(BacklogItem.new(title="done", objective="finished work"))
    mem.backlog.mark_done(done.id)
    failed = mem.backlog.add(BacklogItem.new(title="failed", objective="bad work"))
    mem.backlog.mark_failed(failed.id, error="boom")
    skipped = mem.backlog.add(BacklogItem.new(title="skipped", objective="later work"))
    mem.backlog.update(skipped.id, status="skipped")
    (tmp_path / "inbox.jsonl").write_text(
        json.dumps({"text": "fresh guidance"}) + "\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def status_life_dir_completed_continuous(tmp_path: Path) -> Path:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    done = mem.backlog.add(BacklogItem.new(title="done", objective="finished work"))
    mem.backlog.mark_done(done.id)
    (tmp_path / "continuous.json").write_text(
        json.dumps(
            {
                "enabled": False,
                "objective": "持续优化项目",
                "done_reason": "planner declared project done",
                "done_at": "2026-05-12T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "inbox.jsonl").write_text(
        json.dumps({"text": "fresh guidance"}) + "\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def command_life_dir(tmp_path: Path) -> Path:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    (tmp_path / "identity.md").write_text("identity: initial\n", encoding="utf-8")
    pending = mem.backlog.add(BacklogItem.new(title="pending", objective="queued work"))
    running = mem.backlog.add(BacklogItem.new(title="running", objective="in flight"))
    mem.backlog.mark_running(running.id)
    done = mem.backlog.add(BacklogItem.new(title="done", objective="finished work"))
    mem.backlog.mark_done(done.id)
    skipped = mem.backlog.add(BacklogItem.new(title="skipped", objective="later work"))
    mem.backlog.update(skipped.id, status="skipped")
    mem.journal.append(JournalEntry.new(kind="mission_complete", title="older", summary="old"))
    mem.journal.append(JournalEntry.new(kind="mission_failed", title="newer", summary="new"))
    (tmp_path / "continuous.json").write_text(
        json.dumps({"enabled": True, "objective": "持续优化项目"})
    )
    assert pending.id
    return tmp_path


@pytest.fixture()
def admin_life_dir(tmp_path: Path) -> Path:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    (tmp_path / "identity.md").write_text("identity: initial\n", encoding="utf-8")
    (tmp_path / "continuous.json").write_text(
        json.dumps({"enabled": False, "objective": "持续优化项目"}),
        encoding="utf-8",
    )
    return tmp_path


class TestCommandRouter:
    def _make_router(self, life_dir: Path) -> _CommandRouter:
        return _CommandRouter(
            life_dir=life_dir, token="fake-token", chat_id="12345",
        )

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_add_task(self, mock_send: MagicMock, life_dir: Path) -> None:
        router = self._make_router(life_dir)
        router.dispatch("/add 修复登录: 修复登录页面的CSS问题")
        assert mock_send.called
        reply = mock_send.call_args[0][2]
        assert "任务已添加" in reply
        assert "修复登录" in reply
        # Verify backlog has the item
        from argus_skill.life.memory import LifeMemory
        mem = LifeMemory.open(life_dir)
        pending = mem.backlog.pending()
        assert len(pending) == 1
        assert pending[0].title == "修复登录"
        assert "CSS" in pending[0].objective

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_add_flags_smoke(self, mock_send: MagicMock, life_dir: Path) -> None:
        router = self._make_router(life_dir)
        router.dispatch("/add --once --cycles=2 --budget=$1.50 title: objective")
        reply = mock_send.call_args[0][2]
        assert "未知命令" not in reply
        mem = LifeMemory.open(life_dir)
        item = mem.backlog.pending()[0]
        assert item.title == "title"
        assert item.objective == "objective"
        assert item.iterate is False
        assert item.iteration_max_cycles == 2
        assert item.iteration_budget_usd == 1.5

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_add_free_text(self, mock_send: MagicMock, life_dir: Path) -> None:
        """Idle free text (no slash command) should be treated as /add."""
        router = self._make_router(life_dir)
        router.dispatch("优化性能，减少页面加载时间")
        assert mock_send.called
        reply = mock_send.call_args[0][2]
        assert "收到，我会把这当作一个新任务来做" in reply
        assert "进展" in reply
        assert "优化性能" in reply

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_running_free_text_becomes_nudge(
        self,
        mock_send: MagicMock,
        life_dir: Path,
    ) -> None:
        mem = LifeMemory.open(life_dir)
        running = mem.backlog.add(BacklogItem.new(
            title="running task",
            objective="in flight",
        ))
        mem.backlog.mark_running(running.id)
        (life_dir / "daemon.pid").write_text(str(os.getpid()), encoding="utf-8")

        router = self._make_router(life_dir)
        router.dispatch("现在别这么做，先跑端到端 smoke")

        reply = mock_send.call_args[0][2]
        assert "交给当前任务" in reply
        assert "不会打断正在进行的 LLM 调用" in reply
        assert "/add" in reply
        assert "/status" in reply
        assert mem.backlog.pending() == []

        record = json.loads((life_dir / "inbox.jsonl").read_text(encoding="utf-8").strip())
        assert record["text"] == "现在别这么做，先跑端到端 smoke"
        event = json.loads((life_dir / "events.jsonl").read_text(encoding="utf-8").strip())
        assert event["type"] == "life.inbox.queued"
        assert event["source"] == "telegram.free_text"

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_status(self, mock_send: MagicMock, life_dir: Path) -> None:
        router = self._make_router(life_dir)
        router.dispatch("/status")
        reply = mock_send.call_args[0][2]
        assert "状态" in reply
        assert "守护进程" in reply

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_status_separates_active_queue_and_history(
        self,
        mock_send: MagicMock,
        status_life_dir: Path,
    ) -> None:
        router = self._make_router(status_life_dir)
        router.dispatch("/status")
        reply = mock_send.call_args[0][2]
        assert "active: 0 pending · 0 running" in reply
        assert "history: 1 done · 1 failed · 1 skipped" in reply
        assert "failed" in reply
        assert "收件箱: 1 条待处理" in reply
        assert "budget   : per-mission $30.00 · daily $180.00 · remaining $180.00" in reply

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_status_shows_active_work_when_present(
        self,
        mock_send: MagicMock,
        status_life_dir_with_active: Path,
    ) -> None:
        router = self._make_router(status_life_dir_with_active)
        router.dispatch("/status")
        reply = mock_send.call_args[0][2]
        assert "active: 1 pending · 1 running" in reply
        assert "history: 1 done · 1 failed · 1 skipped" in reply
        assert "running" in reply
        assert "收件箱: 1 条待处理" in reply
        assert "budget   : per-mission $30.00 · daily $180.00 · remaining $180.00" in reply

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_status_prefers_latest_running_item(
        self,
        mock_send: MagicMock,
        status_life_dir_with_stale_running: Path,
    ) -> None:
        router = self._make_router(status_life_dir_with_stale_running)
        router.dispatch("/status")
        reply = mock_send.call_args[0][2]
        assert "active: 0 pending · 2 running" in reply
        assert "🔖 ID:" in reply
        assert "🔧 <b>当前任务:</b> newer" in reply
        assert "🎯 current task row" in reply
        assert "older" not in reply

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_status_shows_completed_continuous_metadata(
        self,
        mock_send: MagicMock,
        status_life_dir_completed_continuous: Path,
    ) -> None:
        router = self._make_router(status_life_dir_completed_continuous)
        router.dispatch("/status")
        reply = mock_send.call_args[0][2]
        assert "持续模式: 已完成" in reply
        assert "planner declared project done" in reply
        assert "完成于: 2026-05-12T00:00:00Z" in reply

    @pytest.mark.parametrize(
        ("command_template", "item_title"),
        [
            ("/help", None),
            ("/status", None),
            ("/config", None),
            ("/config cycles=8 budget=25", None),
            ("/identity", None),
            ("/identity set identity: updated", None),
            ("/project", None),
            ("/project set project: updated", None),
            ("/backend", None),
            ("/backend memory", None),
            ("/reset", None),
            ("/skills ls", None),
            ("/skills promote missing-skill", None),
            ("/backlog", None),
            ("/backlog all", None),
            ("/add --once --cycles=2 --budget=$1.50 title: objective", None),
            ("/done {item_id}", "pending"),
            ("/skip {item_id}", "running"),
            ("/rm {item_id}", "skipped"),
            ("/stop {item_id}", "pending"),
            ("/journal 1", None),
            ("/note 请记录这个想法", None),
            ("/nudge 请注意错误处理", None),
            ("/run --once", None),
            ("/start 持续优化项目", None),
            ("/continuous start 持续优化项目", None),
            ("/continuous stop", None),
        ],
    )
    @patch("argus_skill.life.telegram_bot.render_run_command", return_value="run transcript")
    @patch("argus_skill.life.telegram_bot._send_message")
    def test_readme_documented_telegram_commands_are_accepted(
        self,
        mock_send: MagicMock,
        mock_render: MagicMock,
        command_life_dir: Path,
        command_template: str,
        item_title: str | None,
    ) -> None:
        router = self._make_router(command_life_dir)
        if item_title is not None:
            mem = LifeMemory.open(command_life_dir)
            item = next(it for it in mem.backlog.all() if it.title == item_title)
            command = command_template.format(item_id=item.id)
        else:
            command = command_template
        router.dispatch(command)
        reply = mock_send.call_args[0][2]
        assert "未知命令" not in reply
        assert reply.strip()
        if command.startswith("/run"):
            assert mock_render.called

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_config_updates_session_defaults(
        self,
        mock_send: MagicMock,
        admin_life_dir: Path,
    ) -> None:
        router = self._make_router(admin_life_dir)
        router.dispatch("/config cycles=8 budget=25 continuous=true")
        reply = mock_send.call_args[0][2]
        assert "cycles = 8" in reply
        assert "budget = $25.00" in reply
        cfg = json.loads((admin_life_dir / "continuous.json").read_text())
        assert cfg["enabled"] is True
        assert cfg["objective"] == "持续优化项目"

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_identity_set_backend_reset_and_skills_smoke(
        self,
        mock_send: MagicMock,
        admin_life_dir: Path,
    ) -> None:
        router = self._make_router(admin_life_dir)
        router.dispatch("/identity set identity: updated")
        reply = mock_send.call_args[0][2]
        assert "identity card updated" in reply
        assert "未知命令" not in reply
        assert "updated" in (admin_life_dir / "identity.md").read_text(encoding="utf-8")

        (admin_life_dir / "project.md").write_text("project: initial\n", encoding="utf-8")
        router.dispatch("/project set project: updated")
        reply = mock_send.call_args[0][2]
        assert "project card updated" in reply
        assert "updated" in (admin_life_dir / "project.md").read_text(encoding="utf-8")
        assert "identity: updated" not in (admin_life_dir / "project.md").read_text(encoding="utf-8")
        assert "updated" in (admin_life_dir / "identity.md").read_text(encoding="utf-8")

        router.dispatch("/backend memory")
        reply = mock_send.call_args[0][2]
        assert "backend: memory" in reply

        router.dispatch("/reset")
        reply = mock_send.call_args[0][2]
        assert "reset:" in reply

        router.dispatch("/skills ls")
        reply = mock_send.call_args[0][2]
        assert "未知命令" not in reply

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_backlog_empty(self, mock_send: MagicMock, life_dir: Path) -> None:
        router = self._make_router(life_dir)
        router.dispatch("/backlog")
        reply = mock_send.call_args[0][2]
        assert "为空" in reply

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_backlog_all_shows_history(
        self,
        mock_send: MagicMock,
        command_life_dir: Path,
    ) -> None:
        router = self._make_router(command_life_dir)
        router.dispatch("/backlog all")
        reply = mock_send.call_args[0][2]
        assert "全部任务" in reply
        assert "pending" in reply
        assert "running" in reply
        assert "done" in reply
        assert "skipped" in reply

    @pytest.mark.parametrize(
        ("command", "title", "expected_status"),
        [
            ("/done", "pending", "done"),
            ("/skip", "running", "skipped"),
            ("/rm", "skipped", None),
        ],
    )
    @patch("argus_skill.life.telegram_bot._send_message")
    def test_status_change_commands_mutate_backlog(
        self,
        mock_send: MagicMock,
        command_life_dir: Path,
        command: str,
        title: str,
        expected_status: str | None,
    ) -> None:
        mem = LifeMemory.open(command_life_dir)
        item = next(it for it in mem.backlog.all() if it.title == title)
        router = self._make_router(command_life_dir)
        router.dispatch(f"{command} {item.id}")
        reply = mock_send.call_args[0][2]
        assert "未知命令" not in reply

        mem = LifeMemory.open(command_life_dir)
        items = {it.title: it for it in mem.backlog.all()}
        if expected_status is None:
            assert title not in items
        else:
            assert items[title].status == expected_status

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_journal_and_note_surfaces_history(
        self,
        mock_send: MagicMock,
        command_life_dir: Path,
    ) -> None:
        router = self._make_router(command_life_dir)
        router.dispatch("/journal 1")
        journal_reply = mock_send.call_args[0][2]
        assert "最近日志" in journal_reply
        assert "newer" in journal_reply
        assert "older" not in journal_reply

        router.dispatch("/note 请记录这个想法")
        note_reply = mock_send.call_args[0][2]
        assert "note appended" in note_reply
        notes = LifeMemory.open(command_life_dir).journal.tail(1)
        assert notes[-1].kind == "user_note"
        assert notes[-1].summary == "请记录这个想法"

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_stop_disables_item_iteration(
        self,
        mock_send: MagicMock,
        command_life_dir: Path,
    ) -> None:
        mem = LifeMemory.open(command_life_dir)
        item = next(it for it in mem.backlog.all() if it.title == "pending")
        router = self._make_router(command_life_dir)
        router.dispatch(f"/stop {item.id}")
        reply = mock_send.call_args[0][2]
        assert "iteration disabled" in reply or "迭代" in reply
        item = next(it for it in LifeMemory.open(command_life_dir).backlog.all() if it.id == item.id)
        assert item.status == "done"
        assert item.iterate is False

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_continuous_stop_remains_explicit(
        self,
        mock_send: MagicMock,
        life_dir: Path,
    ) -> None:
        (life_dir / "continuous.json").write_text(
            json.dumps({"enabled": True, "objective": "持续优化项目"})
        )
        router = self._make_router(life_dir)
        router.dispatch("/continuous stop")
        reply = mock_send.call_args[0][2]
        assert "持续模式已暂停" in reply
        cfg = json.loads((life_dir / "continuous.json").read_text())
        assert cfg["enabled"] is False
        assert cfg["objective"] == "持续优化项目"

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_stop_without_id_is_item_usage(
        self,
        mock_send: MagicMock,
        life_dir: Path,
    ) -> None:
        (life_dir / "continuous.json").write_text(
            json.dumps({"enabled": True, "objective": "持续优化项目"})
        )
        router = self._make_router(life_dir)
        router.dispatch("/stop")
        reply = mock_send.call_args[0][2]
        assert "用法: /stop <id>" in reply
        cfg = json.loads((life_dir / "continuous.json").read_text())
        assert cfg["enabled"] is True

    @patch("argus_skill.life.telegram_bot.render_run_command", return_value="run transcript")
    @patch("argus_skill.life.telegram_bot._send_message")
    def test_run_uses_shared_helper(
        self,
        mock_send: MagicMock,
        mock_render: MagicMock,
        life_dir: Path,
    ) -> None:
        router = self._make_router(life_dir)
        router.dispatch("/run --once --max-missions=2")
        reply = mock_send.call_args[0][2]
        assert "run transcript" in reply
        assert mock_render.called
        assert mock_render.call_args[0][1] == ["--once", "--max-missions=2"]

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_nudge(self, mock_send: MagicMock, life_dir: Path) -> None:
        router = self._make_router(life_dir)
        router.dispatch("/nudge 请注意错误处理")
        reply = mock_send.call_args[0][2]
        assert "指令已注入" in reply
        # Verify inbox.jsonl was written
        inbox = life_dir / "inbox.jsonl"
        assert inbox.exists()
        record = json.loads(inbox.read_text().strip())
        assert record["text"] == "请注意错误处理"
        events = json.loads((life_dir / "events.jsonl").read_text().strip())
        assert events["type"] == "life.inbox.queued"
        assert events["source"] == "telegram.nudge"

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_start(self, mock_send: MagicMock, life_dir: Path) -> None:
        router = self._make_router(life_dir)
        router.dispatch("/start 持续优化项目")
        reply = mock_send.call_args[0][2]
        assert "持续模式已开启" in reply
        # Verify continuous.json
        cfg = json.loads((life_dir / "continuous.json").read_text())
        assert cfg["enabled"] is True
        assert cfg["objective"] == "持续优化项目"

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_start_rejects_empty_objective(self, mock_send: MagicMock, life_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "codex")
        router = self._make_router(life_dir)
        router.dispatch("/start")
        reply = mock_send.call_args[0][2]
        assert "non-empty --objective" in reply
        assert not (life_dir / "continuous.json").exists()

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_start_rejects_memory_backend(self, mock_send: MagicMock, life_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARGUS_SKILL_LIFE_BACKEND", "memory")
        router = self._make_router(life_dir)
        router.dispatch("/start 持续优化项目")
        reply = mock_send.call_args[0][2]
        assert "cannot plan" in reply
        assert not (life_dir / "continuous.json").exists()

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_continuous_stop(self, mock_send: MagicMock, life_dir: Path) -> None:
        (life_dir / "continuous.json").write_text(
            json.dumps({"enabled": True, "objective": "test"})
        )
        router = self._make_router(life_dir)
        router.dispatch("/continuous stop")
        reply = mock_send.call_args[0][2]
        assert "暂停" in reply
        cfg = json.loads((life_dir / "continuous.json").read_text())
        assert cfg["enabled"] is False
        assert cfg["objective"] == "test"  # preserved

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_help(self, mock_send: MagicMock, life_dir: Path) -> None:
        router = self._make_router(life_dir)
        router.dispatch("/help")
        reply = mock_send.call_args[0][2]
        assert "/add <code>&lt;text&gt;</code> [--once] [--cycles=N] [--budget=$X] — 添加任务" in reply
        assert "/status — 查看守护进程、持续模式、当前任务、backlog/history、收件箱和预算/花费" in reply
        assert "/config" in reply
        assert "/identity" in reply
        assert "/project" in reply
        assert "/backend" in reply
        assert "/reset" in reply
        assert "/skills" in reply
        assert "/backlog [all]" in reply
        assert "/done" in reply
        assert "/skip" in reply
        assert "/rm" in reply
        assert "/stop <id> — 关闭任务迭代；必要时会把待办项标记为已完成" in reply
        assert "/journal" in reply
        assert "/note" in reply
        assert "/run" in reply
        assert "/continuous" in reply
        readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")
        assert "* `/status` - summary of daemon, continuous mode, current work, backlog/history, inbox, and budget/cost." in readme
        assert "* `/project` - view the project card." in readme
        assert "* `/project set <text>` - update the project card with one message." in readme
        assert "* `/stop <id>` - disable iteration on an item; finalizes a pending item as done when applicable." in readme
        assert "identity, backlog, recent journal" not in readme

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_unknown_command(self, mock_send: MagicMock, life_dir: Path) -> None:
        router = self._make_router(life_dir)
        router.dispatch("/foo")
        reply = mock_send.call_args[0][2]
        assert "未知命令" in reply

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_bot_mention_stripped(self, mock_send: MagicMock, life_dir: Path) -> None:
        """'/status@mybot' should work like '/status'."""
        router = self._make_router(life_dir)
        router.dispatch("/status@whatcanisay1111111_bot")
        reply = mock_send.call_args[0][2]
        assert "状态" in reply


# ---------------------------------------------------------------------------
# Offset persistence tests
# ---------------------------------------------------------------------------

class TestOffset:
    def test_read_write_offset(self, tmp_path: Path) -> None:
        from argus_skill.life.telegram_bot import _read_offset, _write_offset
        assert _read_offset(tmp_path) is None
        _write_offset(tmp_path, 42)
        assert _read_offset(tmp_path) == 42

    def test_read_missing(self, tmp_path: Path) -> None:
        from argus_skill.life.telegram_bot import _read_offset
        assert _read_offset(tmp_path) is None


# ---------------------------------------------------------------------------
# Poller init tests
# ---------------------------------------------------------------------------

class TestPoller:
    def test_disabled_without_env(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            p = TelegramPoller(life_dir=Path("/tmp"), token="", chat_id="")
            assert not p.enabled

    def test_enabled_with_config(self) -> None:
        p = TelegramPoller(life_dir=Path("/tmp"), token="abc", chat_id="123")
        assert p.enabled

    def test_allows_matching_sender(self) -> None:
        p = TelegramPoller(
            life_dir=Path("/tmp"),
            token="abc",
            chat_id="123",
            user_id="456",
        )
        assert p._message_allowed({"chat": {"id": "123"}, "from": {"id": "456"}})

    def test_rejects_mismatched_sender(self) -> None:
        p = TelegramPoller(
            life_dir=Path("/tmp"),
            token="abc",
            chat_id="123",
            user_id="456",
        )
        assert not p._message_allowed({"chat": {"id": "123"}, "from": {"id": "999"}})

    def test_user_filter_is_optional(self) -> None:
        p = TelegramPoller(life_dir=Path("/tmp"), token="abc", chat_id="123")
        assert p._message_allowed({"chat": {"id": "123"}, "from": {"id": "999"}})

    def test_poll_loop_processes_updates_and_resumes_from_offset(
        self,
        life_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from argus_skill.life import telegram_bot as tg
        from argus_skill.life.memory import LifeMemory

        mem = LifeMemory.open(life_dir)
        mem.init()
        (life_dir / "telegram.offset").write_text("5", encoding="utf-8")

        first_batch = [
            {
                "update_id": 5,
                "message": {
                    "chat": {"id": "123"},
                    "from": {"id": "456"},
                    "text": "/add first task: build the first thing",
                },
            },
            {
                "update_id": 6,
                "message": {
                    "chat": {"id": "999"},
                    "from": {"id": "456"},
                    "text": "/add ignored chat: do not persist",
                },
            },
            {
                "update_id": 7,
                "message": {
                    "chat": {"id": "123"},
                    "from": {"id": "000"},
                    "text": "/nudge ignored user",
                },
            },
            {
                "update_id": 8,
                "message": {
                    "chat": {"id": "123"},
                    "from": {"id": "456"},
                    "text": "/nudge keep going",
                },
            },
        ]
        second_batch = [
            {
                "update_id": 9,
                "message": {
                    "chat": {"id": "123"},
                    "from": {"id": "456"},
                    "text": "/add second task: keep going",
                },
            },
        ]
        calls_first: list[_ApiCallRecord] = []
        calls_second: list[_ApiCallRecord] = []
        written_offsets: list[int] = []

        real_write_offset = tg._write_offset

        def fake_api_first(token: str, method: str, payload: dict[str, object] | None = None, *, timeout: float = 35) -> dict[str, object] | None:
            calls_first.append({
                "token": token,
                "method": method,
                "payload": dict(payload or {}),
                "timeout": timeout,
            })
            if method == "getUpdates":
                offset = _offset_from_payload(payload)
                if offset == 5:
                    return {"ok": True, "result": first_batch}
                return None
            if method == "sendMessage":
                return {"ok": True, "result": {}}
            return {"ok": False, "result": []}

        def fake_api_second(token: str, method: str, payload: dict[str, object] | None = None, *, timeout: float = 35) -> dict[str, object] | None:
            calls_second.append({
                "token": token,
                "method": method,
                "payload": dict(payload or {}),
                "timeout": timeout,
            })
            if method == "getUpdates":
                offset = _offset_from_payload(payload)
                if offset == 9:
                    return {"ok": True, "result": second_batch}
                return None
            if method == "sendMessage":
                return {"ok": True, "result": {}}
            return {"ok": False, "result": []}

        def record_write_offset(life_dir_arg: Path, offset: int) -> None:
            written_offsets.append(offset)
            real_write_offset(life_dir_arg, offset)

        monkeypatch.setattr(tg, "_write_offset", record_write_offset)

        def run_once(api_func) -> None:
            monkeypatch.setattr(tg, "_api_call", api_func)
            stop_event = _SingleIterationEvent()
            poller = TelegramPoller(
                life_dir=life_dir,
                token="token",
                chat_id="123",
                user_id="456",
                stop_event=stop_event,
            )
            poller._poll_loop()

        run_once(fake_api_first)
        first_backlog = LifeMemory.open(life_dir).backlog.pending()
        first_inbox = json.loads((life_dir / "inbox.jsonl").read_text().strip())
        first_offset = (life_dir / "telegram.offset").read_text().strip()

        run_once(fake_api_second)
        second_backlog = LifeMemory.open(life_dir).backlog.pending()
        second_offset = (life_dir / "telegram.offset").read_text().strip()

        assert [_offset_from_call(call) for call in calls_first if call["method"] == "getUpdates"][:1] == [5]
        assert [_offset_from_call(call) for call in calls_second if call["method"] == "getUpdates"][:1] == [9]
        assert len(first_backlog) == 1
        assert first_backlog[0].objective == "build the first thing"
        assert first_inbox["text"] == "keep going"
        assert len(second_backlog) == 2
        assert second_backlog[1].objective == "keep going"
        assert written_offsets == sorted(written_offsets)
        assert first_offset == "9"
        assert second_offset == "10"


# ---------------------------------------------------------------------------
# Notification format tests
# ---------------------------------------------------------------------------

class TestNotifyFormat:
    def test_mission_complete_format(self) -> None:
        from argus_skill.life.notify import _format_telegram_message
        msg = _format_telegram_message({
            "kind": "mission_complete",
            "title": "修复CSS布局",
            "summary": "status=done; rounds=5; elapsed=42.3s; cost_usd=$1.23",
            "cost_usd": 1.23,
            "ts": time.time(),
            "extra": {
                "objective": "修复首页的响应式布局问题",
                "cumulative_cost_usd": 70.5,
                "iteration": {"cycle": 2, "max_cycles": 6},
            },
        })
        assert "任务完成" in msg
        assert "修复CSS布局" in msg
        assert "修复首页" in msg
        assert "迭代 2/6" in msg
        assert "本次 $1.23" in msg
        assert "累计" in msg
        assert "$70.50" in msg

    def test_planner_cycle_format(self) -> None:
        from argus_skill.life.notify import _format_telegram_message
        msg = _format_telegram_message({
            "kind": "planner_cycle",
            "title": "planner cycle #3",
            "summary": "generated 2 task(s): 优化错误处理, 添加测试",
            "cost_usd": 0.5,
            "ts": time.time(),
            "extra": {"cumulative_cost_usd": 71.0},
        })
        assert "规划完成" in msg
        assert "优化错误处理" in msg
        assert "添加测试" in msg

    def test_budget_pause_format(self) -> None:
        from argus_skill.life.notify import _format_telegram_message
        msg = _format_telegram_message({
            "kind": "budget_pause",
            "title": "paused before '大任务'",
            "summary": "daily cap reached",
            "ts": time.time(),
            "extra": {"cumulative_cost_usd": 180.0},
        })
        assert "预算暂停" in msg
        assert "daily cap" in msg

    def test_html_escaping(self) -> None:
        from argus_skill.life.notify import _format_telegram_message
        msg = _format_telegram_message({
            "kind": "mission_complete",
            "title": "fix <script> & stuff",
            "summary": "status=done; rounds=1",
            "ts": time.time(),
            "extra": {"objective": "fix <b>this</b>"},
        })
        assert "&lt;script&gt;" in msg
        assert "&lt;b&gt;" in msg
