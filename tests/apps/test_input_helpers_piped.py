"""Regression tests for non-TTY stdin handling in read_pasted_message.

Bug: when a multi-line task spec was heredoc-piped into the REPL, each
non-blank line was dispatched as its OWN free-text mission, fragmenting
one logical task into 3-5 separate runs. The fix reads piped stdin in
"block" mode: consecutive non-empty lines coalesce into one message;
blank line / EOF / leading "/" terminates the block.
"""
from __future__ import annotations

import io

import pytest

from argus_skill.apps import _input_helpers as ih


@pytest.fixture(autouse=True)
def _clear_pushback():
    ih._piped_pushback.clear()
    yield
    ih._piped_pushback.clear()


class _PipedStdin(io.StringIO):
    """StringIO that reports isatty() == False, matching `cmd <heredoc`."""

    def isatty(self) -> bool:  # noqa: D401
        return False


def _drive(monkeypatch, script: str):
    fake = _PipedStdin(script)
    monkeypatch.setattr(ih.sys, "stdin", fake)
    # input() reads from sys.stdin via the C runtime; we monkeypatch the
    # builtin so every call returns the next line of our buffer.
    lines = iter(script.splitlines())

    def fake_input(prompt: str = "") -> str:
        try:
            return next(lines)
        except StopIteration as exc:
            raise EOFError from exc

    monkeypatch.setattr("builtins.input", fake_input)


def test_piped_multiline_block_returns_one_message(monkeypatch):
    script = (
        "Build a tiny expense tracker library.\n"
        "Requirements:\n"
        "1. tracker.py with Expense + Tracker.\n"
        "2. tests/test_tracker.py with 6+ pytest cases.\n"
        "3. Run pytest at the end.\n"
    )
    _drive(monkeypatch, script)

    msg = ih.read_pasted_message("argus › ")
    assert msg is not None
    # All five lines must arrive as a single logical message.
    assert msg.count("\n") == 4
    assert msg.startswith("Build a tiny expense tracker library.")
    assert msg.endswith("3. Run pytest at the end.")


def test_readline_prompt_marks_ansi_zero_width():
    prompt = "\x1b[36margus\x1b[0m ❯ "
    safe = ih._readline_prompt(prompt)

    assert "\001\x1b[36m\002" in safe
    assert "\001\x1b[0m\002" in safe
    assert safe.replace("\001", "").replace("\002", "") == prompt


def test_piped_blocks_separated_by_blank_line_become_separate_messages(monkeypatch):
    script = (
        "first mission body\n"
        "with two lines\n"
        "\n"
        "second mission body\n"
    )
    _drive(monkeypatch, script)

    first = ih.read_pasted_message("> ")
    second = ih.read_pasted_message("> ")
    third = ih.read_pasted_message("> ")
    assert first == "first mission body\nwith two lines"
    assert second == "second mission body"
    assert third is None


def test_piped_slash_command_is_returned_alone(monkeypatch):
    script = (
        "do the work\n"
        "/exit\n"
    )
    _drive(monkeypatch, script)

    body = ih.read_pasted_message("> ")
    cmd = ih.read_pasted_message("> ")
    assert body == "do the work"
    assert cmd == "/exit"


def test_piped_slash_command_after_block_pushes_back(monkeypatch):
    """A `/...` line that appears mid-stream terminates the current block
    AND is queued so the next read_pasted_message returns it."""
    script = (
        "line one\n"
        "line two\n"
        "/quit\n"
    )
    _drive(monkeypatch, script)

    body = ih.read_pasted_message("> ")
    cmd = ih.read_pasted_message("> ")
    assert body == "line one\nline two"
    assert cmd == "/quit"


def test_piped_bracketed_paste_preserves_blank_lines_and_slashes(monkeypatch):
    script = (
        "\x1b[200~## Heading\n"
        "\n"
        "body line\n"
        "/literal-not-command\n"
        "\n"
        "tail\x1b[201~\n"
        "/exit\n"
    )
    _drive(monkeypatch, script)

    body = ih.read_pasted_message("> ")
    cmd = ih.read_pasted_message("> ")
    assert body == "## Heading\n\nbody line\n/literal-not-command\n\ntail"
    assert cmd == "/exit"


def test_piped_eof_with_no_input_returns_none(monkeypatch):
    _drive(monkeypatch, "")
    assert ih.read_pasted_message("> ") is None
