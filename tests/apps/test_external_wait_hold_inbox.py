"""A held mission slot is given up as soon as an operator message is waiting."""
from __future__ import annotations

from argus.apps._inbox import queue_inbox_message
from argus.apps._runtime_execute import _external_wait_hold_with_quiet_inbox


def test_hold_answers_only_while_the_inbox_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    life = tmp_path / "life"
    life.mkdir()
    assert _external_wait_hold_with_quiet_inbox(None, life) is None
    answer = _external_wait_hold_with_quiet_inbox(lambda: True, life)
    assert answer is not None and answer() is True
    queue_inbox_message(life, "Stop after this grid and plot what you have.", source="operator")
    assert answer() is False
    assert _external_wait_hold_with_quiet_inbox(lambda: False, None)() is False
    assert _external_wait_hold_with_quiet_inbox(lambda: True, None)() is True
