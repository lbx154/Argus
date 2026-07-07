"""Fallback + safety tests for the idle live cockpit input path.

``read_message_with_live_cockpit`` pins a live four-role panel above the
prompt by DEFAULT (no manual opt-in required) so the operator always sees
what Manager/Planner/Engineer/Reviewer are doing without typing ``/roles``
or setting an env var. It must still degrade to the plain
``read_pasted_message`` path on every unsupported condition (non-TTY,
explicitly disabled via ``ARGUS_SKILL_COCKPIT_LIVE=0``, no daemon, …) so the
core input path is never at risk. These tests exercise the degrade paths
without a real terminal.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import argus_skill.apps._input_helpers as input_helpers
from argus_skill.manager import repl as manager_repl


class _Theme:
    width = 80

    def bold_green(self, s: str) -> str:  # pragma: no cover - trivial
        return s

    def gray(self, s: str) -> str:  # pragma: no cover - trivial
        return s

    def dim(self, s: str) -> str:  # pragma: no cover - trivial
        return s


def _run(prompt: str = "argus > ", mem: Any = object()) -> str | None:
    return manager_repl.read_message_with_live_cockpit(prompt, mem, _Theme())


def test_non_tty_delegates_to_plain_read():
    # In the test harness stdin is not a TTY → must delegate verbatim.
    with patch.object(input_helpers, "read_pasted_message",
                      return_value="SENTINEL") as m:
        assert _run() == "SENTINEL"
        m.assert_called_once()


def test_default_enabled_reaches_daemon_check(monkeypatch):
    """No env var set (the shipped default) must behave exactly like
    ``ARGUS_SKILL_COCKPIT_LIVE=1`` — i.e. it must NOT stop at the
    enabled/disabled gate. It still degrades to plain input here because no
    live daemon is mocked (see ``test_tty_but_no_daemon_delegates``); the
    point of this test is that it reaches that SAME later gate by default,
    not an earlier "disabled by default" one."""
    monkeypatch.delenv("ARGUS_SKILL_COCKPIT_LIVE", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)

    class _St:
        alive = False
        pid = None

    with patch.object(manager_repl, "_life_dir_for", return_value="/tmp/x"), \
         patch("argus_skill.daemon.life_worker.read_daemon_status",
               return_value=_St()), \
         patch.object(input_helpers, "read_pasted_message",
                      return_value="PLAIN") as m:
        assert _run() == "PLAIN"
        m.assert_called_once()


def test_env_disabled_delegates(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_COCKPIT_LIVE", "0")
    with patch.object(input_helpers, "read_pasted_message",
                      return_value="PLAIN") as m:
        assert _run() == "PLAIN"
        m.assert_called_once()


def test_tty_but_no_daemon_delegates(monkeypatch):
    # Force the TTY + termios gates open, but report no live daemon → still plain.
    monkeypatch.setenv("ARGUS_SKILL_COCKPIT_LIVE", "1")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)

    class _St:
        alive = False
        pid = None

    with patch.object(manager_repl, "_life_dir_for", return_value="/tmp/x"), \
         patch("argus_skill.daemon.life_worker.read_daemon_status",
               return_value=_St()), \
         patch.object(input_helpers, "read_pasted_message",
                      return_value="NODAEMON") as m:
        assert _run() == "NODAEMON"
        m.assert_called_once()


def test_exports_symbol():
    assert "read_message_with_live_cockpit" in manager_repl.__all__


def test_live_cockpit_enabled_default_and_opt_out(monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_COCKPIT_LIVE", raising=False)
    assert manager_repl._live_cockpit_enabled() is True
    monkeypatch.setenv("ARGUS_SKILL_COCKPIT_LIVE", "0")
    assert manager_repl._live_cockpit_enabled() is False
    monkeypatch.setenv("ARGUS_SKILL_COCKPIT_LIVE", "1")
    assert manager_repl._live_cockpit_enabled() is True


def test_live_follow_enabled_default_and_opt_out(monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_FOLLOW_LIVE", raising=False)
    assert manager_repl._live_follow_enabled() is True
    monkeypatch.setenv("ARGUS_SKILL_FOLLOW_LIVE", "0")
    assert manager_repl._live_follow_enabled() is False
    monkeypatch.setenv("ARGUS_SKILL_FOLLOW_LIVE", "1")
    assert manager_repl._live_follow_enabled() is True
