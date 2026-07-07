"""TUI gating + dispatch reuse. The full-screen UI can't run headless, so we test
the seams: availability gate, the slash registry it completes against, and that
``dispatch_command`` (shared by line REPL + TUI) routes free text and commands."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from argus_skill.manager import repl as manager_repl
from argus_skill.manager import tui


class _M:  # minimal mem stub — unknown/bogus commands must never touch it
    pass


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


def test_slash_registry_covers_dispatch_commands() -> None:
    """``SLASH_COMMANDS`` powers /help *and* TUI tab-completion (see
    ``tui.py``'s ``FuzzyWordCompleter``). It used to silently drift down to
    just ``{"/help", "/exit"}`` while ``dispatch_command`` grew to ~20 real
    commands — this test parses the dispatcher's own source so that class of
    regression fails loudly instead of quietly shipping an incomplete /help.
    """
    import inspect
    import re

    source = inspect.getsource(manager_repl.dispatch_command)
    dispatched: set[str] = set()
    for m in re.finditer(r'cmd (?:==|in)\s*(\([^()]*\)|"/[a-zA-Z]+")', source):
        dispatched.update(re.findall(r'"(/[a-zA-Z]+)"', m.group(1)))
    # /verbose and /quiet are deliberately-retired no-ops: dispatch_command
    # still explains they were removed, but they are intentionally left out
    # of SLASH_COMMANDS/help/completion so operators don't learn a dead
    # command.
    dispatched -= {"/verbose", "/quiet"}

    registered = {c for c, _ in manager_repl.SLASH_COMMANDS}
    missing = dispatched - registered
    assert not missing, (
        f"dispatch_command handles {sorted(missing)} but SLASH_COMMANDS "
        f"(which drives /help + TUI completion) does not list them"
    )


def test_help_sections_only_reference_registered_commands() -> None:
    """Every command named in ``_HELP_SECTIONS`` must exist in
    ``SLASH_COMMANDS`` — otherwise ``_help_command_rows().pop`` silently
    swallows a typo'd/renamed command instead of surfacing it anywhere."""
    registered = {c for c, _ in manager_repl.SLASH_COMMANDS}
    for _section, cmds in manager_repl._HELP_SECTIONS:
        for cmd in cmds:
            assert cmd in registered, f"{cmd!r} in _HELP_SECTIONS is not in SLASH_COMMANDS"


def test_render_help_lists_real_commands() -> None:
    """/help must actually list commands (regression: it used to be pure
    natural-language prose with zero of the ~20 real slash commands in it,
    so a mistyped command had nowhere to send the operator)."""
    text = manager_repl._render_help(_Plain())
    for cmd in ("/status", "/roles", "/daemon", "/doctor", "/add", "/journal"):
        assert cmd in text, f"{cmd!r} missing from /help output"
    # Aliases are folded into their primary command's row, not listed bare.
    assert "/done  (= /skip, /rm)" in text


def test_unknown_command_suggests_closest_match() -> None:
    """A near-miss slash command gets a "did you mean" hint instead of the
    old dead-loop "(try /help)" (which, before this fix, pointed at a /help
    that did not list any commands either)."""
    out = manager_repl.dispatch_command("/stauts", "/stauts", _M(), {}, ".", _Plain())
    assert out is None
    assert manager_repl._closest_slash_command("/stauts") == "/status"
    # A string with no real overlap with any registered command name.
    assert manager_repl._closest_slash_command("/zzznonsense123") is None


def test_bottom_hint_line_right_aligns_status_on_wide_terminal() -> None:
    from argus_skill.cli.theme import Theme
    theme = Theme(enabled=False, width=100)
    out = manager_repl._bottom_hint_line(theme, "Copilot · gpt-5.5")
    assert out.startswith("  Enter send · /help commands")
    assert out.rstrip().endswith("Copilot · gpt-5.5")
    assert len(out) <= 100


def test_bottom_hint_line_drops_status_on_narrow_terminal() -> None:
    from argus_skill.cli.theme import Theme
    theme = Theme(enabled=False, width=40)
    out = manager_repl._bottom_hint_line(theme, "Copilot · gpt-5.5")
    assert "Copilot" not in out
    assert "Enter send" in out


def test_bottom_hint_line_omits_status_when_empty() -> None:
    from argus_skill.cli.theme import Theme
    theme = Theme(enabled=False, width=100)
    out = manager_repl._bottom_hint_line(theme, "")
    assert out.strip() == "Enter send · /help commands"



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

    def fake_spawn(cfg: Any, *, quiet: bool = False) -> int:
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


def test_daemon_restart_does_not_demand_objective_for_ambient_continuous_default(
    tmp_path, monkeypatch, capsys
) -> None:
    """Regression: a plain `argus` launch defaults chat_state["config"]
    ["continuous"] to True (see _seed_chat_state's default_continuous — any
    non-memory, non---bounded session) even though the operator never typed
    /continuous start <objective>. /daemon restart/start used to read that
    ambient True straight into continuous_mode_error, which correctly
    demands a non-empty objective for *real* continuous mode — hard-failing
    with "--continuous requires a non-empty --objective" and leaving NO
    daemon running, even though the original boot-time autospawn (which
    reads argparse's continuous=False default instead) had just started one
    fine. `/daemon restart --drain` must not regress a working daemon to no
    daemon at all just because nobody has opted into continuous planning."""
    from argus_skill.daemon import life_worker
    from argus_skill.life.memory import LifeMemory

    mem = LifeMemory.open(root=tmp_path)
    cs: dict[str, Any] = {
        "backend": "codex",
        "config": {"continuous": True},  # the ambient default, not operator intent
        "continuous_objective": "",
        "global_root": tmp_path,
        "open_ended": True,
    }
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        life_worker, "read_daemon_status",
        lambda life_dir: SimpleNamespace(alive=False, pid=None, uptime_seconds=None, backend=None),
    )

    def fake_spawn(cfg: Any, *, quiet: bool = False) -> int:
        captured["continuous"] = cfg.continuous
        return 0

    monkeypatch.setattr(life_worker, "spawn_detached_daemon", fake_spawn)
    monkeypatch.setattr(
        life_worker, "wait_for_daemon_status",
        lambda life_dir: SimpleNamespace(alive=True, pid=1, uptime_seconds=0.0, backend="codex"),
    )

    manager_repl._daemon_cmd(mem, "restart", cs)

    screen = capsys.readouterr().out
    assert "requires a non-empty" not in screen
    assert captured.get("continuous") is False


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
