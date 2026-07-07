"""REPL interaction UX: honest Ctrl-C semantics, slash-command completion, and
session-resume discoverability in ``/help``.

Covers the 2026-07 line-REPL overhaul:
  * ``_build_slash_completer`` — live ``/``-triggered command dropdown.
  * ``read_message_prompt_toolkit`` — Ctrl-D → exit (None), Ctrl-C → propagate
    (caller clears + stays), normal line returns.
  * ``_render_help`` — advertises ``--continue`` / ``--resume`` + key semantics.
  * ``AgentCliRunner.run_exec`` — a Ctrl-C mid-run terminates the child agent
    CLI instead of orphaning it.
"""
from __future__ import annotations

import queue
from unittest.mock import patch

import pytest

from argus_skill.cli.theme import Theme

# --- slash completer -------------------------------------------------------


class _Doc:
    """Minimal stand-in for prompt_toolkit's Document."""

    def __init__(self, text: str) -> None:
        self.text_before_cursor = text


def test_slash_completer_offers_commands_only_after_slash():
    from argus_skill.manager.repl import _build_slash_completer

    comp = _build_slash_completer()

    hits = [c.text for c in comp.get_completions(_Doc("/st"), None)]
    assert "/status" in hits and "/start" in hits and "/stop" in hits

    # Ordinary chat must never pop a command menu.
    assert list(comp.get_completions(_Doc("hello there"), None)) == []

    # Alias rows ("alias of …") are folded out — only primaries complete.
    assert all(not c.text == "/commands" for c in comp.get_completions(_Doc("/comm"), None))


def test_slash_completer_meta_is_the_description():
    from argus_skill.manager.repl import _build_slash_completer

    comp = _build_slash_completer()
    by_text = {c.text: c for c in comp.get_completions(_Doc("/status"), None)}
    assert "/status" in by_text
    # display_meta carries the one-line help (shown in the dropdown).
    meta = by_text["/status"].display_meta_text
    assert "daemon" in meta or "backlog" in meta


# --- /help advertises resume + key semantics -------------------------------


def test_help_documents_session_resume_and_keys():
    txt = _render = __import__(
        "argus_skill.manager.repl", fromlist=["_render_help"]
    )._render_help(Theme.auto(force=False))
    for needle in ("--continue", "--resume", "Ctrl-C", "Ctrl-D", "Sessions"):
        assert needle in txt, f"/help missing: {needle}"


# --- prompt_toolkit reader: Ctrl-D / Ctrl-C / normal -----------------------


def _session_with_pipe():
    """Build a PromptSession bound to a feedable pipe input + dummy output."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    cm = create_pipe_input()
    pipe = cm.__enter__()
    session = PromptSession(input=pipe, output=DummyOutput())
    return cm, pipe, session


def test_prompt_toolkit_reader_returns_line():
    from argus_skill.manager import repl as r

    cm, pipe, session = _session_with_pipe()
    try:
        pipe.send_text("hello world\n")
        got = r.read_message_prompt_toolkit(
            "> ", mem=None, theme=None, chat_state={"prompt_session": session}
        )
        assert got == "hello world"
    finally:
        cm.__exit__(None, None, None)


def test_prompt_toolkit_reader_ctrl_d_returns_none():
    from argus_skill.manager import repl as r

    cm, pipe, session = _session_with_pipe()
    try:
        pipe.send_text("\x04")  # Ctrl-D on an empty line → EOF → exit signal
        got = r.read_message_prompt_toolkit(
            "> ", mem=None, theme=None, chat_state={"prompt_session": session}
        )
        assert got is None
    finally:
        cm.__exit__(None, None, None)


def test_prompt_toolkit_reader_ctrl_c_propagates():
    from argus_skill.manager import repl as r

    cm, pipe, session = _session_with_pipe()
    try:
        pipe.send_text("\x03")  # Ctrl-C → KeyboardInterrupt (caller clears + stays)
        with pytest.raises(KeyboardInterrupt):
            r.read_message_prompt_toolkit(
                "> ", mem=None, theme=None, chat_state={"prompt_session": session}
            )
    finally:
        cm.__exit__(None, None, None)


def test_prompt_toolkit_reader_panel_on_by_default(monkeypatch, capsys):
    """Regression: switching the INPUT engine to prompt_toolkit must show the
    SAME live four-role panel the operator gets by default with the legacy
    cbreak engine — showing multi-role progress automatically, with no
    manual step, is the whole point, and ``_use_prompt_toolkit_input`` is
    intentionally independent of ``ARGUS_SKILL_COCKPIT_LIVE`` (see its
    docstring) but both default to the panel being visible. With no env var
    set, the full panel renderer (``render_roles_snapshot``) must be called,
    not the lightweight one-liner fallback."""
    from argus_skill.manager import repl as r

    monkeypatch.delenv("ARGUS_SKILL_COCKPIT_LIVE", raising=False)
    monkeypatch.setattr(r, "_life_dir_for", lambda mem: "/tmp/x")
    with patch("argus_skill.cli.roles_status.render_roles_snapshot",
               return_value="PANEL") as panel_mock, \
         patch("argus_skill.cli.roles_status.format_prompt_status_line") as line_mock:
        cm, pipe, session = _session_with_pipe()
        try:
            pipe.send_text("hi\n")
            r.read_message_prompt_toolkit(
                "> ", mem=object(), theme=None,
                chat_state={"prompt_session": session},
            )
        finally:
            cm.__exit__(None, None, None)
    panel_mock.assert_called_once()
    line_mock.assert_not_called()
    assert "PANEL" in capsys.readouterr().out


def test_prompt_toolkit_reader_panel_hidden_when_opted_out(monkeypatch, capsys):
    """Setting ARGUS_SKILL_COCKPIT_LIVE=0 drops the prompt_toolkit engine back
    to the same lightweight one-liner the legacy engine falls back to —
    turning the panel off doesn't erase all status visibility."""
    from argus_skill.manager import repl as r

    monkeypatch.setenv("ARGUS_SKILL_COCKPIT_LIVE", "0")
    monkeypatch.setattr(r, "_life_dir_for", lambda mem: "/tmp/x")
    with patch("argus_skill.cli.roles_status.render_roles_snapshot") as panel_mock, \
         patch("argus_skill.cli.roles_status.format_prompt_status_line",
               return_value="memory · gpt-5.5") as line_mock:
        cm, pipe, session = _session_with_pipe()
        try:
            pipe.send_text("hi\n")
            r.read_message_prompt_toolkit(
                "> ", mem=object(), theme=None,
                chat_state={"prompt_session": session},
            )
        finally:
            cm.__exit__(None, None, None)
    panel_mock.assert_not_called()
    line_mock.assert_called_once()
    assert "memory · gpt-5.5" in capsys.readouterr().out


# --- run_exec terminates the child on Ctrl-C -------------------------------


def test_run_exec_terminates_child_on_ctrl_c(monkeypatch):
    """A Ctrl-C while the agent CLI is running must kill the child, not orphan
    it (else it keeps burning tokens after the operator bailed)."""
    from argus_skill.agent_cli import agent_cli_runner as acr

    runner = acr.AgentCliRunner(agent_bin="sleep", backend="codex")
    # Bypass codex-specific arg building — just launch a long-lived sleeper.
    monkeypatch.setattr(runner, "_build_command", lambda **_kw: ["sleep", "10"])
    monkeypatch.setattr(runner, "_resolve_executable", lambda c: c)
    monkeypatch.setattr(runner, "_prompt_via_stdin", lambda: False)

    captured: dict[str, object] = {}
    real_popen = acr.subprocess.Popen

    def spy_popen(*a, **k):
        proc = real_popen(*a, **k)
        captured["proc"] = proc
        return proc

    monkeypatch.setattr(acr.subprocess, "Popen", spy_popen)

    # The main read loop blocks on line_queue.get; make the first call look like
    # an operator Ctrl-C. consume_pipe threads only .put(), so they're unaffected.
    real_get = queue.Queue.get
    state = {"n": 0}

    def fake_get(self, *a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise KeyboardInterrupt
        return real_get(self, *a, **k)

    monkeypatch.setattr(queue.Queue, "get", fake_get)

    with pytest.raises(KeyboardInterrupt):
        runner.run_exec(prompt="x", resume_thread_id=None, options=acr.RunnerOptions())

    proc = captured["proc"]
    assert proc is not None
    # Child must be reaped, not left running.
    assert proc.poll() is not None


# --- /resume command + session-builder key bindings ------------------------


def test_resume_is_in_command_surface():
    from argus_skill.manager.repl import SLASH_COMMANDS, _build_slash_completer

    assert "/resume" in [c for c, _ in SLASH_COMMANDS]
    comp = _build_slash_completer()
    assert "/resume" in [c.text for c in comp.get_completions(_Doc("/res"), None)]


def test_resume_cmd_empty_root_is_graceful(tmp_path, capsys):
    from argus_skill.manager.repl import _resume_cmd

    # No sessions under this root → a friendly notice, never a crash.
    _resume_cmd(mem=None, chat_state={}, global_root=tmp_path, rest_text="")
    out = capsys.readouterr().out
    assert "No previous conversation" in out


def test_prompt_session_has_edit_keybindings():
    """The cached PromptSession carries the backspace/delete re-trigger bindings
    (so editing a slash command re-opens the completion dropdown)."""
    from argus_skill.manager import repl as r

    class _FakeMem:  # _life_dir_for() will fail on this → InMemoryHistory fallback
        pass

    sess = r._get_prompt_session({}, _FakeMem())
    assert sess.key_bindings is not None
    # backspace normalizes to Ctrl-H ('c-h'); delete stays 'delete'.
    vals = {k.value for b in sess.key_bindings.bindings for k in b.keys}
    assert "c-h" in vals and "delete" in vals


def test_prompt_toolkit_is_default_engine(monkeypatch):
    """prompt_toolkit (panel + completion) is the default input engine now —
    it no longer yields to the legacy live-cockpit env flag; only an explicit
    ARGUS_SKILL_NO_PROMPT_TOOLKIT=1 (or non-TTY) falls back."""
    from argus_skill.manager import repl as r

    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    monkeypatch.delenv("ARGUS_SKILL_NO_PROMPT_TOOLKIT", raising=False)

    monkeypatch.delenv("ARGUS_SKILL_COCKPIT_LIVE", raising=False)
    assert r._use_prompt_toolkit_input() is True
    # legacy live-cockpit flag no longer disables prompt_toolkit
    monkeypatch.setenv("ARGUS_SKILL_COCKPIT_LIVE", "1")
    assert r._use_prompt_toolkit_input() is True
    # explicit opt-out to the plain/legacy reader
    monkeypatch.setenv("ARGUS_SKILL_NO_PROMPT_TOOLKIT", "1")
    assert r._use_prompt_toolkit_input() is False


# --- conversation transcript (/resume replay + labels) ---------------------


def test_transcript_roundtrip(tmp_path):
    from argus_skill.core import transcript as T

    T.append_turn(tmp_path, "operator", "为啥会有这个")
    T.append_turn(tmp_path, "argus", "because ...")
    T.append_turn(tmp_path, "operator", "second")
    turns = T.read_turns(tmp_path)
    assert [t["role"] for t in turns] == ["operator", "argus", "operator"]
    assert T.first_operator_text(tmp_path) == "为啥会有这个"
    assert len(T.read_turns(tmp_path, limit=1)) == 1
    assert T.has_transcript(tmp_path)
    # blank turns are dropped, never crash
    T.append_turn(tmp_path, "operator", "   ")
    assert len(T.read_turns(tmp_path)) == 3


def test_transcript_makes_chat_only_session_listable(tmp_path):
    import json
    import time

    from argus_skill.core import session as S
    from argus_skill.core import transcript as T

    sid = "s-chat"
    p = tmp_path / "projects" / sid
    p.mkdir(parents=True)
    (p / "session.json").write_text(json.dumps(
        {"id": sid, "display_name": "", "objective": "", "created": time.time(),
         "last_active": time.time(), "cwd": "/x"}))
    assert S.list_sessions(tmp_path, include_empty=False) == []
    T.append_turn(p, "operator", "hello")
    assert [s.id for s in S.list_sessions(tmp_path, include_empty=False)] == [sid]


def test_resume_cmd_switches_and_labels(tmp_path, capsys):
    import json
    import time

    from argus_skill.core import transcript as T
    from argus_skill.manager import repl as r

    sid = "s-xyz9"
    p = tmp_path / "projects" / sid
    p.mkdir(parents=True)
    (p / "session.json").write_text(json.dumps(
        {"id": sid, "display_name": "", "objective": "", "created": time.time(),
         "last_active": time.time(), "cwd": "/x"}))
    T.append_turn(p, "operator", "remember my first message")
    T.append_turn(p, "argus", "sure")
    # /resume <id> flags a real switch into that session (re-exec on loop exit)
    cs: dict = {}
    r._resume_cmd(mem=None, chat_state=cs, global_root=tmp_path, rest_text=sid)
    assert cs.get("switch_to_session") == sid
    # /resume list labels by first message, not (unnamed), and does NOT switch
    cs2: dict = {}
    r._resume_cmd(mem=None, chat_state=cs2, global_root=tmp_path, rest_text="list")
    out2 = capsys.readouterr().out
    assert "remember my first message" in out2 and "(unnamed)" not in out2
    assert cs2.get("switch_to_session") is None


def test_bare_resume_switches_to_previous_conversation(tmp_path):
    """`/resume` with no id flags a switch into the most recent OTHER session
    that has a saved conversation."""
    import json
    import time

    from argus_skill.core import transcript as T
    from argus_skill.manager import repl as r

    def _mk(sid: str, msg: str | None, age: float) -> None:
        p = tmp_path / "projects" / sid
        p.mkdir(parents=True)
        (p / "session.json").write_text(json.dumps(
            {"id": sid, "display_name": "", "objective": "",
             "created": time.time() - age, "last_active": time.time() - age, "cwd": "/x"}))
        if msg:
            T.append_turn(p, "operator", msg)
            T.append_turn(p, "argus", "ok")

    _mk("s-prev", "the previous chat", 100)
    _mk("s-old", "an older chat", 500)
    # bare /resume → switches into s-prev (most recent with a transcript)
    cs: dict = {}
    r._resume_cmd(mem=None, chat_state=cs, global_root=tmp_path, rest_text="")
    assert cs.get("switch_to_session") == "s-prev"

