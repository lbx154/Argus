"""Unit tests for ``argus_skill.apps.go_app`` (the one-shot ``argus-skill go``).

The full end-to-end flow (mission create → daemon spawn → chat REPL) is hard
to fake in unit tests, so this file focuses on the helpers that previously
broke real users — most importantly ``_drain_pasted_lines`` /
``_prompt_objective``, which had to learn to swallow multi-line pastes.
"""
from __future__ import annotations

import io
import os
import sys

import pytest

from argus_skill.apps import go_app


# ---------------------------------------------------------------------------
# _drain_pasted_lines
# ---------------------------------------------------------------------------

def _stdin_from_pipe(content: bytes, monkeypatch: pytest.MonkeyPatch):
    """Replace ``sys.stdin`` with a real OS pipe holding ``content``."""
    r, w = os.pipe()
    if content:
        os.write(w, content)
    os.close(w)  # EOF after content
    fake_stdin = os.fdopen(r, "rb", buffering=0)

    class _StdinShim:
        # _drain_pasted_lines only calls fileno()
        def fileno(self) -> int:
            return fake_stdin.fileno()

    monkeypatch.setattr(sys, "stdin", _StdinShim())
    return fake_stdin  # caller can close at end (or rely on fd close in shim)


def test_drain_returns_empty_when_nothing_queued(monkeypatch: pytest.MonkeyPatch):
    _stdin_from_pipe(b"", monkeypatch)
    assert go_app._drain_pasted_lines(timeout=0.05) == []


def test_drain_collects_multi_line_paste(monkeypatch: pytest.MonkeyPatch):
    _stdin_from_pipe(b"line2\nline3\nline4\n", monkeypatch)
    out = go_app._drain_pasted_lines(timeout=0.05)
    assert out == ["line2", "line3", "line4"]


def test_drain_strips_bracketed_paste_markers(monkeypatch: pytest.MonkeyPatch):
    # Many terminals wrap pastes in \x1b[200~ ... \x1b[201~.
    _stdin_from_pipe(b"\x1b[200~tail of paste\nfinal\n\x1b[201~", monkeypatch)
    out = go_app._drain_pasted_lines(timeout=0.05)
    assert out == ["tail of paste", "final"]


def test_drain_skips_blank_lines(monkeypatch: pytest.MonkeyPatch):
    _stdin_from_pipe(b"   \n\n  next  \n\n", monkeypatch)
    out = go_app._drain_pasted_lines(timeout=0.05)
    assert out == ["next"]


def test_drain_caps_at_max_bytes(monkeypatch: pytest.MonkeyPatch):
    payload = (b"x" * 50 + b"\n") * 1000  # ~51 KB
    _stdin_from_pipe(payload, monkeypatch)
    out = go_app._drain_pasted_lines(timeout=0.05, max_bytes=200)
    # We capped — should have at most a handful of rows, not 1000
    assert 0 < len(out) <= 5


def test_drain_handles_no_fileno(monkeypatch: pytest.MonkeyPatch):
    # If sys.stdin doesn't expose a real fd (rare in tests), bail cleanly.
    monkeypatch.setattr(sys, "stdin", io.StringIO("nope\n"))
    assert go_app._drain_pasted_lines(timeout=0.01) == []


# ---------------------------------------------------------------------------
# _prompt_objective
# ---------------------------------------------------------------------------

def test_prompt_objective_single_line(monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setattr("builtins.input", lambda: "do the thing")
    monkeypatch.setattr(go_app, "_drain_pasted_lines", lambda *a, **kw: [])
    obj = go_app._prompt_objective()
    assert obj == "do the thing"


def test_prompt_objective_joins_pasted_continuation(
    monkeypatch: pytest.MonkeyPatch, capsys
):
    monkeypatch.setattr("builtins.input", lambda: "line one")
    monkeypatch.setattr(
        go_app, "_drain_pasted_lines", lambda *a, **kw: ["line two", "line three"]
    )
    obj = go_app._prompt_objective()
    assert obj == "line one line two line three"
    out = capsys.readouterr().out
    assert "collected 3" in out


def test_prompt_objective_eof_returns_empty(monkeypatch: pytest.MonkeyPatch):
    def _raise() -> str:
        raise EOFError
    monkeypatch.setattr("builtins.input", _raise)
    monkeypatch.setattr(go_app, "_drain_pasted_lines", lambda *a, **kw: [])
    assert go_app._prompt_objective() == ""


def test_prompt_objective_strips_each_line(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("builtins.input", lambda: "  outer  ")
    monkeypatch.setattr(
        go_app, "_drain_pasted_lines", lambda *a, **kw: ["  inner  "]
    )
    obj = go_app._prompt_objective()
    assert obj == "outer inner"


# ---------------------------------------------------------------------------
# add_go_subcommand
# ---------------------------------------------------------------------------

def test_add_go_subcommand_defaults():
    import argparse
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    go_app.add_go_subcommand(sub)
    ns = p.parse_args(["go"])
    assert ns.cmd == "go"
    assert ns.objective is None
    assert ns.plan_mode == "auto"
    assert ns.max_rounds == 20
    assert ns.attach_only is False


def test_add_go_subcommand_passes_objective():
    import argparse
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    go_app.add_go_subcommand(sub)
    ns = p.parse_args(["go", "build a thing", "--plan-mode", "off", "--max-rounds", "3"])
    assert ns.objective == "build a thing"
    assert ns.plan_mode == "off"
    assert ns.max_rounds == 3
