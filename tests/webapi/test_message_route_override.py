"""Operator-stated message category (auto / chat / task).

``auto`` and ``task`` keep the front-door classifier in charge of scope and
topology. ``chat`` explicitly stays inline and skips classification.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from argus.core.transcript import append_turn
from argus.webapi import manager_bridge, manager_dispatch, manager_state
from argus.webapi.manager_dispatch import (
    _classify_operator_turn,
    _ClassifyResult,
    _TurnEmitter,
)
from argus.webapi.routes.models import MessageIn

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from argus.webapi import server  # noqa: E402

_SID = "s-override0"


def _make_project(root: Path, sid: str = _SID) -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text(
        json.dumps({"type": "mission.started", "text": "hi", "ts": time.time()})
        + "\n",
        encoding="utf-8",
    )
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    return life


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    _make_project(tmp_path)
    return TestClient(server.create_app(global_root=tmp_path))


# --------------------------------------------------------------------------
# request model
# --------------------------------------------------------------------------


def test_route_override_defaults_to_auto() -> None:
    assert MessageIn(text="hi").route_override == "auto"


@pytest.mark.parametrize("value", ["auto", "chat", "task"])
def test_route_override_accepts_the_three_categories(value: str) -> None:
    assert MessageIn(text="hi", route_override=value).route_override == value


@pytest.mark.parametrize("value", ["", "simple", "complex", "TASK", "bogus"])
def test_route_override_rejects_anything_else(value: str) -> None:
    with pytest.raises(ValueError):
        MessageIn(text="hi", route_override=value)


# --------------------------------------------------------------------------
# the classifier short-circuit
# --------------------------------------------------------------------------


def _classify(
    tmp_path: Path,
    *,
    route_override: str,
    monkeypatch,
    classifier_answer: tuple = (None, None, "simple"),
) -> tuple[_ClassifyResult, list[str], list[str]]:
    """Run one turn and report (result, phase labels, classifier call log)."""
    calls: list[str] = []
    phases: list[str] = []

    def _fake_classify(mem, text, chat_state, **kwargs):  # noqa: ANN001, ANN003
        calls.append(text)
        return classifier_answer

    monkeypatch.setattr(
        "argus.manager.config_intent._front_door_classify",
        _fake_classify,
    )

    def _fragment(kind: str, payload: dict) -> None:
        if kind == "phase":
            phases.append(str(payload.get("label") or ""))

    emitter = _TurnEmitter(life_dir=tmp_path, turn_id="t1", fragment=_fragment)
    result = _classify_operator_turn(
        object(),
        "优化推理吞吐",
        {},
        False,
        tmp_path,
        "",
        emitter,
        lambda: False,
        route_override=route_override,
    )
    assert isinstance(result, _ClassifyResult)
    return result, phases, calls


def test_auto_still_runs_the_front_door_classifier(tmp_path, monkeypatch) -> None:
    result, phases, calls = _classify(
        tmp_path, route_override="auto", monkeypatch=monkeypatch
    )

    assert calls == ["优化推理吞吐"]
    assert result.route == "simple"
    assert phases == ["正在理解你的请求…"]


def test_empty_override_behaves_like_auto(tmp_path, monkeypatch) -> None:
    _result, _phases, calls = _classify(
        tmp_path, route_override="", monkeypatch=monkeypatch
    )

    assert calls == ["优化推理吞吐"]


def test_forced_chat_skips_the_classifier(tmp_path, monkeypatch) -> None:
    result, phases, calls = _classify(
        tmp_path,
        route_override="chat",
        monkeypatch=monkeypatch,
        # Would route the other way if it ran, so a wrong route proves a call.
        classifier_answer=(None, None, "complex"),
    )

    assert calls == [], "the front-door model must not be called at all"
    assert result.route == "simple"
    assert result.intent is None
    assert result.control is None
    assert result.self_mode == "inspect"
    assert result.frontdoor_failure == ""
    assert phases == ["对话模式：Manager 正在准备回复…"]


def test_forced_chat_restores_history_without_classification(
    tmp_path: Path,
    monkeypatch,
) -> None:
    life = _make_project(tmp_path)
    append_turn(life, "operator", "Write the report.")
    append_turn(life, "argus", "The report is still in progress.")
    manager_state._STATES.clear()
    seen: list[str] = []

    def _classify(*args, **kwargs):
        raise AssertionError("explicit chat must not call the classifier")

    def _triage(mem, body, chat_state, **kwargs):
        assert kwargs["self_mode"] == "inspect"
        seen.append(body)
        return "The report is still in progress."

    monkeypatch.setattr(
        "argus.manager.config_intent._front_door_classify", _classify,
    )
    monkeypatch.setattr(
        "argus.manager.front_door.manager_triage", _triage,
    )

    result = manager_bridge.manager_message(
        _SID, "你写完了？", global_root=tmp_path, route_override="chat",
    )

    assert result == {"kind": "chat", "reply": "The report is still in progress."}
    assert len(seen) == 1
    assert "SESSION HANDOFF" in seen[0]
    assert "Write the report." in seen[0]
    assert "The report is still in progress." in seen[0]
    assert seen[0].count("你写完了？") == 1
    assert manager_state._STATES[_SID]["startup_handoffs"] == 1


@pytest.mark.parametrize("pending_count", [1, 2])
def test_forced_chat_does_not_interpret_or_resolve_pending_questions(
    tmp_path: Path, monkeypatch, pending_count: int,
) -> None:
    from argus.core.transcript import read_turns
    from argus.life.memory import BacklogItem, LifeMemory

    life = _make_project(tmp_path)
    memory = LifeMemory.open(life)
    for index in range(pending_count):
        item = BacklogItem.new(title=f"Pending {index}", objective="Run a separate study")
        item.status = "paused_operator"
        item.pending_question = f"Choose an execution option for {index}?"
        memory.backlog.add(item)
    before = [item.to_jsonable() for item in memory.backlog.history()]
    manager_state._STATES.clear()
    calls = []

    def reply(_mem, body, _chat_state, **kwargs):
        assert kwargs.get("self_mode") == "inspect", "Chat cannot run pending-answer interpretation"
        calls.append(body)
        return "Here is the explanation."

    monkeypatch.setattr("argus.manager.front_door.manager_triage", reply)
    monkeypatch.setattr("argus.manager.config_intent._front_door_classify",
                        lambda *a, **kw: pytest.fail("An explicit Chat must not be classified again"))
    result = manager_bridge.manager_message(
        _SID, "Explain what the rank means.", global_root=tmp_path, route_override="chat",
    )

    assert result == {"kind": "chat", "reply": "Here is the explanation."}
    assert len(calls) == 1
    assert [item.to_jsonable() for item in memory.backlog.history()] == before
    assert not (life / "operator_context.jsonl").exists()
    assert not (life / "inbox.jsonl").exists()
    assert [turn["text"] for turn in read_turns(life) if turn["role"] == "operator"] == ["Explain what the rank means."]


def test_forced_chat_does_not_replay_a_matching_recent_research_task(tmp_path, monkeypatch):
    from argus.life.memory import BacklogItem, LifeMemory

    life = _make_project(tmp_path)
    text = "Explain the algorithm."
    memory = LifeMemory.open(life)
    memory.backlog.add(BacklogItem.new(title="Research task", objective=text))
    manager_state._STATES.clear()
    monkeypatch.setattr("argus.manager.front_door.manager_triage", lambda *a, **kw: "An inline explanation.")
    monkeypatch.setattr("argus.manager.config_intent._front_door_classify",
                        lambda *a, **kw: pytest.fail("An explicit Chat must not be classified again"))

    result = manager_bridge.manager_message(_SID, text, global_root=tmp_path, route_override="chat")

    assert result == {"kind": "chat", "reply": "An inline explanation."}
    assert len(memory.backlog.history()) == 1


@pytest.mark.parametrize("route", ["simple", "complex"])
def test_task_mode_keeps_manager_in_charge_of_topology(tmp_path, monkeypatch, route) -> None:
    result, phases, calls = _classify(
        tmp_path,
        route_override="task",
        monkeypatch=monkeypatch,
        classifier_answer=(None, None, route),
    )

    assert calls == ["优化推理吞吐"]
    assert result.route == route
    assert phases == ["正在理解你的请求…"]


def test_forced_turn_still_carries_the_message_and_a_task_id(
    tmp_path,
    monkeypatch,
) -> None:
    result, _phases, _calls = _classify(
        tmp_path, route_override="task", monkeypatch=monkeypatch
    )

    assert result.send_body == "优化推理吞吐"
    assert result.root_task_id


def test_forced_turn_drops_a_previous_turns_frontdoor_leftovers(
    tmp_path,
    monkeypatch,
) -> None:
    """No classifier ran, so last turn's greeting/failure must not decide this one."""
    monkeypatch.setattr(
        "argus.manager.config_intent._front_door_classify",
        lambda *a, **k: (None, None, "simple"),  # noqa: ARG005
    )
    chat_state = {
        "_frontdoor_greeting_reply": "你好呀",
        "_frontdoor_fast_reply": "stale answer",
        "_frontdoor_failure": "classifier returned no valid route",
    }
    emitter = _TurnEmitter(
        life_dir=tmp_path, turn_id="t1", fragment=lambda *a: None  # noqa: ARG005
    )

    result = _classify_operator_turn(
        object(),
        "优化推理吞吐",
        chat_state,
        False,
        tmp_path,
        "",
        emitter,
        lambda: False,
        route_override="chat",
    )

    assert isinstance(result, _ClassifyResult)
    assert result.greeting_reply == ""
    assert result.fast_reply == ""
    assert result.frontdoor_failure == ""
    assert "_frontdoor_greeting_reply" not in chat_state
    assert "_frontdoor_fast_reply" not in chat_state
    assert "_frontdoor_failure" not in chat_state


def test_only_chat_forces_a_route() -> None:
    assert manager_dispatch._FORCED_ROUTES == {"chat": "simple"}


# --------------------------------------------------------------------------
# endpoint plumbing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sent", "expected"),
    [("chat", "chat"), ("task", "task")],
)
def test_endpoint_forwards_the_override(
    client: TestClient,
    monkeypatch,
    sent: str,
    expected: str,
) -> None:
    seen: dict = {}

    def _bridge(sid, text, **kwargs):  # noqa: ANN001, ANN003
        seen.update(kwargs)
        return {"kind": "chat", "reply": "ok"}

    monkeypatch.setattr(
        "argus.webapi.manager_bridge.manager_message", _bridge
    )

    response = client.post(
        f"/api/projects/{_SID}/message",
        json={"text": "优化推理吞吐", "route_override": sent},
    )

    assert response.status_code == 200
    assert seen.get("route_override") == expected


def test_endpoint_omits_the_override_for_auto(client: TestClient, monkeypatch) -> None:
    seen: dict = {}

    def _bridge(sid, text, **kwargs):  # noqa: ANN001, ANN003
        seen.update(kwargs)
        return {"kind": "chat", "reply": "ok"}

    monkeypatch.setattr(
        "argus.webapi.manager_bridge.manager_message", _bridge
    )

    response = client.post(
        f"/api/projects/{_SID}/message",
        json={"text": "你好", "route_override": "auto"},
    )

    assert response.status_code == 200
    assert "route_override" not in seen


def test_endpoint_defaults_to_auto_when_the_field_is_absent(
    client: TestClient,
    monkeypatch,
) -> None:
    """Older clients that never send the field must keep classifying."""
    seen: dict = {}

    def _bridge(sid, text, **kwargs):  # noqa: ANN001, ANN003
        seen.update(kwargs)
        return {"kind": "chat", "reply": "ok"}

    monkeypatch.setattr(
        "argus.webapi.manager_bridge.manager_message", _bridge
    )

    response = client.post(f"/api/projects/{_SID}/message", json={"text": "你好"})

    assert response.status_code == 200
    assert "route_override" not in seen


def test_endpoint_rejects_an_unknown_category(client: TestClient) -> None:
    response = client.post(
        f"/api/projects/{_SID}/message",
        json={"text": "你好", "route_override": "bogus"},
    )

    assert response.status_code == 422
