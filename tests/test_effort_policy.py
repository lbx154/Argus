"""Reasoning effort by need (core.effort_policy) and the Manager's early words."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from argus.core.effort_policy import is_bounded_mission, manager_chat_effort, mission_efforts
from argus.core.session import SessionMeta, write_session_meta
from argus.core.transcript import read_turns
from argus.manager import front_door
from argus.webapi import server
from tests.hand_off import replying

_KNOBS = (
    "ARGUS_SKILL_SELF_REASONING_EFFORT",
    "ARGUS_SKILL_MANAGER_REASONING_EFFORT",
    "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
    "ARGUS_SKILL_ENGINEER_INITIAL_REASONING_EFFORT",
    "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
)


@pytest.fixture(autouse=True)
def _no_operator_knobs(monkeypatch, tmp_path):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    for knob in _KNOBS:
        monkeypatch.delenv(knob, raising=False)


def test_manager_chat_runs_medium_and_rules_on_a_stage_at_high():
    assert manager_chat_effort() == "medium"
    assert manager_chat_effort(deciding=True) == "high"


def test_operator_effort_wins_for_the_manager(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_REASONING_EFFORT", "xhigh")
    assert manager_chat_effort() == "xhigh"
    monkeypatch.setenv("ARGUS_SKILL_SELF_REASONING_EFFORT", "low")
    assert manager_chat_effort(deciding=True) == "low"


@pytest.mark.parametrize(("mode", "paper", "vertical", "bounded"), [
    ("direct", False, "software", True),
    ("direct", False, "", True),
    ("direct", False, "research", False),
    ("direct", False, "paper-writing", False),
    ("direct", True, "software", False),
    ("staged", False, "software", False),
])
def test_bounded_means_direct_and_not_research_or_paper(mode, paper, vertical, bounded):
    assert is_bounded_mission(workflow_mode=mode, paper_mission=paper, vertical=vertical) is bounded


def test_bounded_work_runs_medium_deeper_work_high_never_xhigh():
    small = mission_efforts(bounded=True)
    assert (small.engineer_initial, small.engineer, small.reviewer) == ("medium", "high", "medium")
    deep = mission_efforts(bounded=False)
    assert (deep.engineer_initial, deep.engineer, deep.reviewer) == ("high", "high", "high")


def test_operator_effort_wins_for_engineer_and_reviewer(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "xhigh")
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "high")
    chosen = mission_efforts(bounded=True)
    assert (chosen.engineer_initial, chosen.engineer, chosen.reviewer) == ("xhigh", "xhigh", "high")
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_INITIAL_REASONING_EFFORT", "low")
    assert mission_efforts(bounded=True).engineer_initial == "low"


def test_persisted_switch_counts_as_operator_effort(monkeypatch):
    from argus.core.knob_store import write_persisted_knob

    assert write_persisted_knob("ARGUS_SKILL_REVIEWER_REASONING_EFFORT", "xhigh")
    assert mission_efforts(bounded=True).reviewer == "xhigh"


def test_first_words_reach_the_activity_stream_before_the_reply(tmp_path, monkeypatch):
    """The blocking POST has no stream: the first message lands in events.jsonl at once."""
    sid = "s-early-words"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    seen_during_turn: list[list[dict]] = []

    def events() -> list[dict]:
        path = life / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def turn(_mem, _body, _state, *, on_fragment):
        on_fragment("delta", {"text": "我先看一下 core.py", "live": True})
        on_fragment("delta", {"text": "我先看一下 core.py，再看测试", "live": True})
        seen_during_turn.append(events())
        return "`format_ratio` 把比例格式化成百分比。"

    monkeypatch.setattr(front_door, "manager_turn", replying(turn))
    with TestClient(server.create_app(global_root=tmp_path)) as client:
        body = client.post(f"/api/projects/{sid}/message", json={"text": "format_ratio 是干嘛的？"}).json()
    assert body["reply"] == "`format_ratio` 把比例格式化成百分比。"

    drafts = [e for e in seen_during_turn[0] if e.get("type") == "ui.argus"]
    assert [(e["text"], e.get("in_progress"), e.get("live")) for e in drafts] == [("我先看一下 core.py", True, True)]
    replies = [e for e in events() if e.get("type") == "ui.argus"]
    assert replies[-1]["message_id"] == drafts[0]["message_id"]
    assert not replies[-1].get("in_progress")
    # Only the final reply is journaled.
    assert [t["text"] for t in read_turns(life) if t["role"] == "argus"] == [body["reply"]]


def _early_words_project(tmp_path, sid):
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    return life


def _argus_rows(life):
    path = life / "events.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return [row for row in rows if row.get("type") == "ui.argus"]


@pytest.mark.parametrize("ending", ["cancelled", "crashed"])
def test_first_words_are_closed_when_the_turn_ends_without_a_reply(tmp_path, monkeypatch, ending):
    """A cancelled or crashed turn leaves no early line at the end of the chat after reload."""
    from argus.webapi import manager_bridge

    life = _early_words_project(tmp_path, f"s-early-{ending}")
    stop = [False]

    def turn(_mem, _body, _state, *, on_fragment):
        on_fragment("delta", {"text": "我先看一下 core.py", "live": True})
        stop[0] = ending == "cancelled"
        return "too late"

    def crash(*_args, **_kwargs):
        raise RuntimeError("lost the project lock")

    monkeypatch.setattr(front_door, "manager_turn", replying(turn))
    if ending == "crashed":
        monkeypatch.setattr(manager_bridge, "tell_team_stage_ruling", crash)
        with pytest.raises(RuntimeError):
            manager_bridge.manager_message(life.name, "format_ratio 是干嘛的？", global_root=tmp_path, turn_id="web-7")
    else:
        result = manager_bridge.manager_message(life.name, "format_ratio 是干嘛的？", global_root=tmp_path,
                                                cancelled=lambda: stop[0], turn_id="web-7")
        assert result["kind"] == "cancelled"

    rows = _argus_rows(life)
    assert [(row["text"], row.get("draft_closed")) for row in rows] == [("我先看一下 core.py", None), ("", True)]
    assert {row["message_id"] for row in rows} == {"web-7-argus"}
    # Like the draft, the closing row is in progress: readers that skip drafts skip it.
    assert all(row.get("in_progress") for row in rows)


def test_a_failed_turn_replaces_its_first_words_with_the_error(tmp_path, monkeypatch):
    from argus.webapi.manager_bridge import manager_message

    life = _early_words_project(tmp_path, "s-early-failed")

    def turn(_mem, _body, _state, *, on_fragment):
        on_fragment("delta", {"text": "我先看一下 core.py", "live": True})
        raise RuntimeError("model service went away")

    monkeypatch.setattr(front_door, "manager_turn", replying(turn))
    assert manager_message(life.name, "看一下", global_root=tmp_path, turn_id="web-9")["kind"] == "error"
    rows = _argus_rows(life)
    assert len(rows) == 2 and rows[-1]["message_id"] == "web-9-argus" and not rows[-1].get("in_progress")
    assert not any(row.get("draft_closed") for row in rows)


def test_a_replied_turn_writes_no_closing_row(tmp_path, monkeypatch):
    from argus.webapi.manager_bridge import manager_message

    life = _early_words_project(tmp_path, "s-early-replied")

    def turn(_mem, _body, _state, *, on_fragment):
        on_fragment("delta", {"text": "我先看一下 core.py", "live": True})
        return "看完了。"

    monkeypatch.setattr(front_door, "manager_turn", replying(turn))
    assert manager_message(life.name, "看一下", global_root=tmp_path, turn_id="web-8")["kind"] == "chat"
    assert not any(row.get("draft_closed") for row in _argus_rows(life))
