"""Smoke tests for the chat REPL plumbing.

These don't drive the readline loop (interactive); they only verify
that the parser hooks accept the expected slash-commands and that the
state-dir validation rejects missing directories.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from argus_skill.apps.chat_app import add_chat_subcommand, cmd_chat


def test_chat_subcommand_registers() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    add_chat_subcommand(sub)
    args = parser.parse_args(["chat", "--state-dir", "/nonexistent/abc"])
    assert args.cmd == "chat"
    assert args.state_dir == "/nonexistent/abc"
    assert args.verbose is False
    assert args.no_plain_text_inject is False
    assert args.from_start is False


def test_chat_rejects_missing_state_dir(tmp_path: Path) -> None:
    args = argparse.Namespace(
        state_dir=str(tmp_path / "does-not-exist"),
        verbose=False,
        no_plain_text_inject=False,
        from_start=False,
    )
    rc = cmd_chat(args)
    assert rc == 2
