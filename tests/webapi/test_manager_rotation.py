"""Persistent Manager session (webapi.manager_bridge).

The Manager session stays alive across turns (its thread id is resumed); a new
web process seeds it once with a STRUCTURED handoff. The Manager triage is
stubbed so the tests stay offline.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from argus.webapi import manager_bridge, manager_dispatch, manager_state
from tests.hand_off import replying


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


def test_manager_shutdown_clears_control_generations() -> None:
    manager_state._STATES.clear()
    manager_state._CONTROL_GENERATIONS.clear()
    manager_state.interrupt_manager_turns("s-old")

    manager_state.shutdown_manager_bridge()

    assert manager_state._CONTROL_GENERATIONS == {}




def test_handoff_names_identity_and_path(tmp_path: Path) -> None:
    life = _make_project(tmp_path, "s-rot00002")
    ho = manager_dispatch._build_handoff(life)
    assert "Argus Manager" in ho
    assert str(life) in ho
    assert "events.jsonl" in ho  # tells it where to self-serve state


def test_manager_stream_announces_classification_before_model_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A silent classifier still leaves the TUI on a truthful concrete phase."""
    _make_project(tmp_path, "s-phase001")
    manager_state._STATES.clear()
    fragments: list[tuple[str, dict]] = []

    monkeypatch.setattr("argus.manager.front_door.manager_turn", replying(lambda *a, **k: "done"))

    result = manager_bridge.manager_message(
        "s-phase001",
        "what now?",
        global_root=tmp_path,
        on_fragment=lambda kind, payload: fragments.append((kind, payload)),
    )

    assert result == {"kind": "chat", "reply": "done"}


def test_manager_stream_and_persisted_reply_share_message_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    life = _make_project(tmp_path, "s-stream001")
    manager_state._STATES.clear()
    fragments: list[tuple[str, dict]] = []


    def _triage(*args, on_fragment=None, **kwargs):
        assert on_fragment is not None
        on_fragment("delta", {"text": "done", "message_id": "internal-id"})
        return "done"

    monkeypatch.setattr("argus.manager.front_door.manager_turn", replying(_triage))

    result = manager_bridge.manager_message(
        "s-stream001",
        "what now?",
        global_root=tmp_path,
        on_fragment=lambda kind, payload: fragments.append((kind, payload)),
    )

    assert result == {"kind": "chat", "reply": "done"}
    delta = next(payload for kind, payload in fragments if kind == "delta")
    persisted = [
        json.loads(line)
        for line in (life / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    reply = next(event for event in persisted if event["type"] == "ui.argus")
    assert delta["message_id"] == reply["message_id"]
    assert delta["message_id"].startswith("web-")
    assert delta["message_id"].endswith("-argus")
    assert delta["fragment_mode"] == "snapshot"


