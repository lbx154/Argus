"""Tests for telegram_bot.py — command routing and notification formatting."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


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


class TestCommandRouter:
    def _make_router(self, life_dir: Path) -> "router":
        from argus_skill.life.telegram_bot import _CommandRouter
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
    def test_add_free_text(self, mock_send: MagicMock, life_dir: Path) -> None:
        """Free text (no slash command) should be treated as /add."""
        router = self._make_router(life_dir)
        router.dispatch("优化性能，减少页面加载时间")
        assert mock_send.called
        reply = mock_send.call_args[0][2]
        assert "任务已添加" in reply

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_status(self, mock_send: MagicMock, life_dir: Path) -> None:
        router = self._make_router(life_dir)
        router.dispatch("/status")
        reply = mock_send.call_args[0][2]
        assert "状态" in reply
        assert "守护进程" in reply

    @patch("argus_skill.life.telegram_bot._send_message")
    def test_backlog_empty(self, mock_send: MagicMock, life_dir: Path) -> None:
        router = self._make_router(life_dir)
        router.dispatch("/backlog")
        reply = mock_send.call_args[0][2]
        assert "为空" in reply

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
        assert record["source"] == "telegram"

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
    def test_stop(self, mock_send: MagicMock, life_dir: Path) -> None:
        # First enable
        (life_dir / "continuous.json").write_text(
            json.dumps({"enabled": True, "objective": "test"})
        )
        router = self._make_router(life_dir)
        router.dispatch("/stop")
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
        assert "/add" in reply
        assert "/status" in reply

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
        from argus_skill.life.telegram_bot import TelegramPoller
        with patch.dict("os.environ", {}, clear=True):
            p = TelegramPoller(life_dir=Path("/tmp"), token="", chat_id="")
            assert not p.enabled

    def test_enabled_with_config(self) -> None:
        from argus_skill.life.telegram_bot import TelegramPoller
        p = TelegramPoller(life_dir=Path("/tmp"), token="abc", chat_id="123")
        assert p.enabled


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
