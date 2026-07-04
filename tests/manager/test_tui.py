"""TUI gating + dispatch reuse. The full-screen UI can't run headless, so we test
the seams: availability gate, the slash registry it completes against, and that
``dispatch_command`` (shared by line REPL + TUI) routes free text and commands."""
from __future__ import annotations

from types import SimpleNamespace
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


def test_style_dict_is_a_valid_prompt_toolkit_style() -> None:
    """Regression: a hand-edited ``_STYLE`` entry with a typo'd color/class
    name should fail loudly here, not as a silent runtime crash inside the
    full-screen Application."""
    from prompt_toolkit.styles import Style

    Style.from_dict(tui._STYLE)  # must not raise


def test_clip_cell_truncates_with_single_ellipsis_char() -> None:
    assert tui._clip_cell("short", 10) == "short"
    clipped = tui._clip_cell("a" * 20, 10)
    assert clipped.endswith("…")
    assert len(clipped) == 10


def test_header_and_activity_strings_avoid_ambiguous_width_glyphs() -> None:
    """Emoji/symbols with Unicode East-Asian "ambiguous" width (⚡ ⚙ 💭 ❓ …)
    desync prompt_toolkit's column math from real terminal rendering when
    packed into a single fixed-width status line next to CJK text — this is
    the exact corruption class ("字符错位/重叠") reported against the header.
    Keep those glyphs out of the tui module's own source entirely."""
    import inspect

    source = inspect.getsource(tui)
    for glyph in ("⚡", "⚙", "💭", "❓", "🚀"):
        assert glyph not in source, f"ambiguous-width glyph {glyph!r} found in tui.py"


def test_decision_choices_include_budget_approval_options() -> None:
    choices = tui._decision_choices(
        "Do you approve paid/live benchmark execution and what spend cap?"
    )

    assert any("$1" in c for c in choices)
    assert any("$30" in c for c in choices)
    assert any("不批准" in c for c in choices)


def test_modal_fragments_highlight_selected_choice() -> None:
    choices = ["批准", "不批准"]
    frags = tui._modal_fragments("Approve this?", choices, 1, width=40)
    text = "".join(t for _style, t in frags)

    assert "decision needed" in text
    assert "› 不批准" in text
    assert any(style == "class:modal.selected" and "不批准" in t for style, t in frags)


def test_slash_registry_covers_core_commands() -> None:
    cmds = {c for c, _ in manager_repl.SLASH_COMMANDS}
    assert cmds == {"/help", "/exit"}


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


def test_dispatch_pasted_daemon_cli_starts_executor_not_task(
    tmp_path, monkeypatch, capsys
) -> None:
    from argus_skill.daemon import life_worker
    from argus_skill.life.memory import LifeMemory

    mem = LifeMemory.open(root=tmp_path)
    cs: dict[str, Any] = {"backend": "memory", "config": {}, "global_root": tmp_path}
    captured: dict[str, Any] = {}

    def fake_read(life_dir: Any) -> Any:
        return SimpleNamespace(alive=False, pid=None, uptime_seconds=None, backend=None)

    def fake_spawn(cfg: Any) -> int:
        captured["life_dir"] = cfg.life_dir
        captured["backend"] = cfg.backend
        return 0

    def fake_wait(life_dir: Any) -> Any:
        return SimpleNamespace(alive=True, pid=12345, uptime_seconds=0.0, backend="memory")

    monkeypatch.setattr(life_worker, "read_daemon_status", fake_read)
    monkeypatch.setattr(life_worker, "spawn_detached_daemon", fake_spawn)
    monkeypatch.setattr(life_worker, "wait_for_daemon_status", fake_wait)

    out = manager_repl.dispatch_command(
        "argus-skill --daemon", "argus-skill --daemon", mem, cs, tmp_path, _Plain()
    )

    assert out is None
    assert mem.backlog.pending() == []
    assert captured["life_dir"] == tmp_path
    assert captured["backend"] == "memory"
    screen = capsys.readouterr().out
    assert "/daemon start" in screen
    assert "daemon: started (pid 12345)" in screen


def test_dispatch_unknown_argus_cli_invocation_is_not_queued(tmp_path, capsys) -> None:
    from argus_skill.life.memory import LifeMemory

    mem = LifeMemory.open(root=tmp_path)
    cs: dict[str, Any] = {"backend": "memory"}

    manager_repl.dispatch_command(
        "argus-skill --definitely-not-a-task",
        "argus-skill --definitely-not-a-task",
        mem,
        cs,
        tmp_path,
        _Plain(),
    )

    assert mem.backlog.pending() == []
    assert "not queued" in capsys.readouterr().out


def test_manager_triage_no_runner_is_task(tmp_path) -> None:
    """memory backend → no manager runner → triage returns None (it's a task)."""
    from argus_skill.life.memory import LifeMemory
    mem = LifeMemory.open(root=tmp_path)
    assert manager_repl.manager_triage(mem, "hello", {"backend": "memory"}) is None


def test_manager_triage_chat_returns_reply(tmp_path, monkeypatch) -> None:
    """A conversational line → the manager's reply text (caller does NOT enqueue)."""
    from argus_skill.life.memory import LifeMemory
    mem = LifeMemory.open(root=tmp_path)

    class _Runner:
        last_thread_id = "t1"

        def chat_reply_if_conversational(self, objective, sink, seed_thread_id=None):
            sink.handle_event({"type": "round.main.completed", "last_message": "hi there"})
            return True

    monkeypatch.setattr(manager_repl, "_ensure_manager_runner", lambda cs, m: _Runner())
    assert manager_repl.manager_triage(mem, "hello", {}) == "hi there"


def test_manager_triage_task_falls_through(tmp_path, monkeypatch) -> None:
    from argus_skill.life.memory import LifeMemory
    mem = LifeMemory.open(root=tmp_path)

    class _Runner:
        last_thread_id = None

        def chat_reply_if_conversational(self, objective, sink, seed_thread_id=None):
            return False  # not conversational → task

    monkeypatch.setattr(manager_repl, "_ensure_manager_runner", lambda cs, m: _Runner())
    assert manager_repl.manager_triage(mem, "optimize the kernel", {}) is None


def test_enqueue_mission_heads_the_queue(tmp_path) -> None:
    from argus_skill.life.memory import BacklogItem, LifeMemory
    mem = LifeMemory.open(root=tmp_path)
    mem.backlog.add(BacklogItem.new(title="old", objective="old", priority=100))
    cs: dict[str, Any] = {"backend": "memory", "config": {}}
    item, _alive, _pid = manager_repl.enqueue_mission(mem, "grind kernel 012", cs)
    pending = mem.backlog.pending()
    assert pending[0].objective == "grind kernel 012"
    assert pending[0].id == item.id
    assert cs["last_objective"] == "grind kernel 012"


class _Plain:
    """A no-op theme stub (handlers only call color methods)."""
    def __getattr__(self, _):
        return lambda *a, **k: (a[0] if a else "")
