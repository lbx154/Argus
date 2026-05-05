"""Smoke tests for the chat REPL plumbing.

These don't drive the readline loop (interactive); they only verify
that the parser hooks accept the expected slash-commands and that the
state-dir validation rejects missing directories.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from argus_skill.apps.chat_app import add_chat_subcommand, cmd_chat


def test_chat_subcommand_registers() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    add_chat_subcommand(sub)
    args = parser.parse_args(["chat", "--state-dir", "/nonexistent/abc"])
    assert args.cmd == "chat"
    assert args.state_dir == "/nonexistent/abc"
    # Tri-state default: neither flag set → None (auto-detect at runtime).
    assert args.verbose is None
    assert args.no_plain_text_inject is False
    assert args.from_start is False


def test_chat_subcommand_explicit_verbose() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    add_chat_subcommand(sub)
    args = parser.parse_args(["chat", "--state-dir", "/x", "--verbose"])
    assert args.verbose is True


def test_chat_subcommand_explicit_quiet() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    add_chat_subcommand(sub)
    args = parser.parse_args(["chat", "--state-dir", "/x", "--quiet"])
    assert args.verbose is False


def test_chat_rejects_missing_state_dir(tmp_path: Path) -> None:
    args = argparse.Namespace(
        state_dir=str(tmp_path / "does-not-exist"),
        verbose=None,
        no_plain_text_inject=False,
        from_start=False,
    )
    rc = cmd_chat(args)
    assert rc == 2


def _make_args(state_dir: Path, **overrides) -> argparse.Namespace:
    base = dict(
        state_dir=str(state_dir),
        verbose=False,            # quiet so the test doesn't race on tail thread
        no_plain_text_inject=True,
        from_start=False,
        color=False,              # deterministic plain output for assertions
        compact_banner=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_chat_banner_includes_auto_follow_up_row_from_status_json(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """``chat_app`` must read ``auto_follow_up`` from status.json and forward
    it to the banner so the user sees whether autonomous chaining is on.
    """
    state = tmp_path / "state"
    state.mkdir()
    (state / "inbox.jsonl").touch()
    (state / "outbox.jsonl").touch()
    (state / "status.json").write_text(
        json.dumps(
            {
                "daemon_pid": 1234,
                "mode": "mission",
                "mission_id": "m1",
                "mission_status": "running",
                "plan_mode": "auto",
                "auto_follow_up": True,
                "mission_objective": "obj",
                "max_rounds": 5,
            }
        )
    )
    # End the REPL immediately on the first input() so the banner has
    # been printed and we don't hang waiting for user input.
    monkeypatch.setattr("builtins.input", lambda *a, **kw: (_ for _ in ()).throw(EOFError()))
    cmd_chat(_make_args(state))
    out = capsys.readouterr().out
    assert "auto-follow" in out
    assert "on" in out


def test_chat_banner_auto_follow_up_off_when_status_json_says_false(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "inbox.jsonl").touch()
    (state / "outbox.jsonl").touch()
    (state / "status.json").write_text(
        json.dumps(
            {
                "daemon_pid": 1234,
                "mode": "mission",
                "mission_id": "m1",
                "mission_status": "running",
                "plan_mode": "auto",
                "auto_follow_up": False,
                "mission_objective": "obj",
                "max_rounds": 5,
            }
        )
    )
    monkeypatch.setattr("builtins.input", lambda *a, **kw: (_ for _ in ()).throw(EOFError()))
    cmd_chat(_make_args(state))
    out = capsys.readouterr().out
    assert "auto-follow" in out
    assert "off" in out
    assert "first ✅ done" in out


def test_chat_banner_omits_auto_follow_up_when_status_json_lacks_it(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Legacy status.json (no auto_follow_up field) → row is omitted."""
    state = tmp_path / "state"
    state.mkdir()
    (state / "inbox.jsonl").touch()
    (state / "outbox.jsonl").touch()
    (state / "status.json").write_text(
        json.dumps(
            {
                "daemon_pid": 1234,
                "mode": "mission",
                "mission_id": "m1",
                "mission_status": "running",
                "plan_mode": "auto",
                "mission_objective": "obj",
                "max_rounds": 5,
            }
        )
    )
    monkeypatch.setattr("builtins.input", lambda *a, **kw: (_ for _ in ()).throw(EOFError()))
    cmd_chat(_make_args(state))
    out = capsys.readouterr().out
    assert "auto-follow" not in out

