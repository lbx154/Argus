"""Persistent Manager session (webapi.manager_bridge).

The Manager session stays alive across turns (its thread id is resumed); a new
web process seeds it once with a STRUCTURED handoff. The Manager triage is
stubbed so the tests stay offline.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from argus.webapi import manager_dispatch, manager_state


def _make_project(root: Path, sid: str = "s-rot00001") -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text("", encoding="utf-8")
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    (life / "session.json").write_text(
        json.dumps({"id": sid, "created": time.time(), "last_active": time.time()}),
        encoding="utf-8",
    )
    return life


def test_warm_manager_contexts_are_bounded_and_oldest_is_closed(
    monkeypatch,
) -> None:
    manager_state._STATES.clear()
    manager_state._CONTROL_GENERATIONS.clear()
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_WARM_CONTEXT_LIMIT", "2")
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_WARM_CONTEXT_IDLE_SECONDS", "99999")
    closed: list[str] = []

    class Runner:
        def __init__(self, sid: str) -> None:
            self.sid = sid

        def reset_chat_session(self) -> None:
            closed.append(self.sid)

    now = time.monotonic()
    manager_state._STATES.update({
        "s-old": {
            "manager_runner": Runner("s-old"),
            "last_access_monotonic": now - 2,
        },
        "s-new": {
            "manager_runner": Runner("s-new"),
            "last_access_monotonic": now - 1,
        },
    })
    manager_state.interrupt_manager_turns("s-old")

    manager_state._chat_state_for("s-third")

    assert "s-old" not in manager_state._STATES
    assert "s-old" not in manager_state._CONTROL_GENERATIONS
    assert {"s-new", "s-third"} <= set(manager_state._STATES)
    assert closed == ["s-old"]


def test_handoff_names_identity_and_path(tmp_path: Path) -> None:
    life = _make_project(tmp_path, "s-rot00002")
    ho = manager_dispatch._build_handoff(life)
    assert "Argus Manager" in ho
    assert str(life) in ho
    assert "events.jsonl" in ho  # tells it where to self-serve state
