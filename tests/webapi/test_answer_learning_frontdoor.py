"""Turns the front door already judged as having nothing to learn cost nothing.

The classifier decides whether a message is a greeting, a settings change, a
control, or a message-only reply. The bridge reuses that judgement: such turns
never start the paid learning pass. Everything else is still queued.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from argus.life import answer_learning
from argus.manager import config_intent, front_door
from argus.webapi import manager_bridge, manager_state


def _make_project(root: Path, sid: str) -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text(
        json.dumps({"type": "mission.started", "text": "hi", "ts": time.time()}) + "\n",
        encoding="utf-8",
    )
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    return life


@pytest.fixture()
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("ARGUS_SKILL_ANSWER_LEARNING", "1")
    monkeypatch.setattr(answer_learning, "enqueue_answer", lambda **kw: calls.append(kw))
    manager_state._STATES.clear()
    yield calls
    manager_state._STATES.clear()


def test_a_greeting_turn_does_not_queue_learning(tmp_path: Path, monkeypatch, queued) -> None:
    sid = "s-greet-nolearn"
    _make_project(tmp_path, sid)

    def classify(mem, text, chat_state, **kwargs):
        chat_state["_frontdoor_greeting_reply"] = "你好，我是 Argus Manager。"
        chat_state["_frontdoor_intake"] = {"kind": "ephemeral"}
        return None, None, "simple"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    result = manager_bridge.manager_message(sid, "你好", global_root=tmp_path)

    assert result["reply"] == "你好，我是 Argus Manager。"
    assert queued == []


def _reply_turn(tmp_path: Path, monkeypatch, *, sid: str, with_tool: bool) -> None:
    _make_project(tmp_path, sid)

    def classify(mem, text, chat_state, **kwargs):
        chat_state["_frontdoor_self_mode"] = "reply"
        chat_state["_frontdoor_intake"] = {"kind": "ephemeral"}
        chat_state["turns"] = 2  # a follow-up goes through the persistent Manager
        return None, None, "simple"

    def triage(_mem, _body, state, *, on_fragment=None, **_kwargs):
        if with_tool:
            on_fragment("phase", {"kind": "tool_use", "label": "read: notes.md",
                                  "call_id": "r-1", "status": "completed"})
        return "Here is what I found."

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(front_door, "manager_triage", triage)
    monkeypatch.setattr(
        "argus.webapi.manager_dispatch._self_skill_context_available", lambda _s: False,
    )
    manager_bridge.manager_message(sid, "what did we find?", global_root=tmp_path)


def test_a_message_only_ephemeral_reply_does_not_queue_learning(tmp_path, monkeypatch, queued) -> None:
    _reply_turn(tmp_path, monkeypatch, sid="s-ephemeral-nolearn", with_tool=False)
    assert queued == []


def test_an_ephemeral_reply_backed_by_observed_work_is_still_queued(tmp_path, monkeypatch, queued) -> None:
    _reply_turn(tmp_path, monkeypatch, sid="s-ephemeral-work", with_tool=True)
    assert len(queued) == 1


@pytest.mark.parametrize("frontdoor", [
    {"greeting": True},
    {"config": True},
    {"control": "pause"},
    {"intake_type": "ephemeral", "self_mode": "reply", "tool_evidence": False},
])
def test_classifier_marked_turns_skip_learning(tmp_path: Path, queued, frontdoor) -> None:
    (tmp_path / "projects" / "s-x").mkdir(parents=True)
    thread = manager_bridge._schedule_answer_learning(
        "s-x", life_dir=tmp_path / "projects" / "s-x", global_root=tmp_path,
        operator_text="t", reply="r", frontdoor=frontdoor,
    )
    assert thread is None
    assert queued == []


@pytest.mark.parametrize("frontdoor", [
    None,
    {"intake_type": "ephemeral", "self_mode": "inspect", "tool_evidence": False},
    {"intake_type": "ephemeral", "self_mode": "reply", "tool_evidence": True},
    {"intake_type": "objective_amendment", "self_mode": "reply", "tool_evidence": False},
])
def test_unmarked_turns_are_still_learned_from(tmp_path: Path, queued, frontdoor) -> None:
    (tmp_path / "projects" / "s-y").mkdir(parents=True)
    manager_bridge._schedule_answer_learning(
        "s-y", life_dir=tmp_path / "projects" / "s-y", global_root=tmp_path,
        operator_text="t", reply="r", frontdoor=frontdoor,
    )
    assert len(queued) == 1
