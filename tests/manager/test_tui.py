"""TUI gating + dispatch reuse. The full-screen UI can't run headless, so we test
the seams: availability gate, the slash registry it completes against, and that
``dispatch_command`` (shared by line REPL + TUI) routes free text and commands."""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

from argus_skill.manager import repl as manager_repl
from argus_skill.manager import tui


def test_tui_unavailable_when_opted_out(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_NO_TUI", "1")
    assert tui.tui_available() is False


def test_tui_unavailable_without_tty(monkeypatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_NO_TUI", raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    assert tui.tui_available() is False


def test_slash_registry_covers_core_commands() -> None:
    cmds = {c for c, _ in manager_repl.SLASH_COMMANDS}
    assert {"/help", "/status", "/add", "/nudge", "/exit"} <= cmds


def test_dispatch_free_text_enqueues(tmp_path) -> None:
    from argus_skill.life.memory import LifeMemory
    mem = LifeMemory.open(root=tmp_path)
    cs: dict[str, Any] = {"backend": "memory"}

    def fake_tail(life_dir: Any, item_id: str, **kw: Any) -> dict[str, Any]:
        return {"type": "life.mission.completed", "item_id": item_id,
                "status": "success", "cost_usd": 0.0}

    with patch.object(manager_repl, "tail_mission_events", side_effect=fake_tail), \
         patch.object(manager_repl, "_daemon_alive_for", lambda d: (True, 1)):
        out = manager_repl.dispatch_command("hi", "hi", mem, cs, tmp_path, _Plain())
    assert out is None
    assert mem.backlog.pending()


def test_dispatch_exit_unknown() -> None:
    class _M:  # minimal — unknown cmd shouldn't touch mem
        pass
    out = manager_repl.dispatch_command("/bogus", "/bogus", _M(), {}, ".", _Plain())
    assert out is None


class _Plain:
    """A no-op theme stub (handlers only call color methods)."""
    def __getattr__(self, _):
        return lambda *a, **k: (a[0] if a else "")
