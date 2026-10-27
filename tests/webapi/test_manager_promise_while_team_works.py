"""While the team works, what the Manager tells the operator is what Argus records.

Replays project s-fe7a9f4f (2026-10-27): the team was finishing a bounded task
("…推荐深入方向，供用户选择") when the operator wrote "继续自主完成研究". The
Manager's turn answered "我会自主选择 A「可调五杆平台」继续深入…" and started on
the research in its own turn, filing no ``hand_off``; the Planner's next cycle
kept the old objective and found nothing left to do.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus.core.operator_context import build_operator_context_block
from argus.daemon.state import read_continuous_state, write_continuous_config
from argus.life.memory import BacklogItem, LifeMemory
from argus.manager import front_door
from argus.webapi import manager_bridge, manager_state
from tests.hand_off import manager_runner, manager_settles

OLD_OBJECTIVE = (
    "为一种新的科研实验机器人本体提出并比较至少三种结构差异明显的候选概念，"
    "形成中文决策报告，推荐深入方向，供用户选择。"
)
PROMISE = "我会自主选择 A「可调五杆平台」继续深入，先用可复现的计算研究检验。"
DIRECTION = (
    "自主选择方案 A「可调五杆平台」继续深入研究：确定机构参数，做可复现的计算对照实验，"
    "给出样机验证方案，写成完整的中文研究报告。不再停下来等用户挑方向。"
)


def _team_working(root: Path, sid: str) -> tuple[Path, LifeMemory, BacklogItem]:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text("", encoding="utf-8")
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    memory = LifeMemory.open(life)
    memory.backlog.add(BacklogItem.new(title="科研机器人本体候选方案比较", objective=OLD_OBJECTIVE))
    running = memory.backlog.claim_next()
    assert running is not None
    # The operator's first request ran as a finite campaign.
    write_continuous_config(life, enabled=True, objective=OLD_OBJECTIVE, open_ended=False)
    manager_state._STATES.clear()
    return life, memory, running


def _replies(life: Path) -> list[str]:
    events = [json.loads(line) for line in (life / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    return [event.get("text", "") for event in events if event.get("type") == "ui.argus" and not event.get("in_progress")]


def test_continuing_on_its_own_while_the_team_works_reaches_the_planners_next_cycle(tmp_path, monkeypatch):
    sid = "s-promise-steer"
    life, memory, running = _team_working(tmp_path, sid)

    def manager(message: str):
        # As in the incident, the first turn answers and files nothing; asked
        # again, it files the direction it promised.
        if len(runner.turns) == 1:
            return None
        return manager_settles(
            control={"kind": "steer", "quote": "继续自主完成研究", "directive": DIRECTION},
            lifetime="standing",
        )

    runner = manager_runner(hand=manager, reply=PROMISE)
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: runner)

    result = manager_bridge.manager_message(sid, "继续自主完成研究", global_root=tmp_path)

    # The Manager was told the team is working, on what, and that it is finite.
    assert "科研机器人本体候选方案比较" in runner.turns[0]
    assert "供用户选择" in runner.turns[0]
    assert len(runner.turns) == 2
    assert result["kind"] == "control" and result["control"] == "steer"
    assert result["continuous"] is True

    # The running task finishes; the Planner's next cycle reads the new direction.
    memory.backlog.mark_done(running.id)
    campaign = read_continuous_state(life)
    assert campaign.enabled and campaign.open_ended
    assert DIRECTION in campaign.objective
    # The newer direction supersedes the finite ticket it continues.
    assert campaign.objective.index(DIRECTION) > campaign.objective.index("供用户选择")
    planner_context, _revision = build_operator_context_block("planner", life, consume_once=False)
    assert DIRECTION in planner_context


def test_a_reply_that_hands_nothing_off_while_the_team_works_is_not_sent(tmp_path, monkeypatch):
    sid = "s-promise-unrecorded"
    life, _memory, _running = _team_working(tmp_path, sid)
    runner = manager_runner(hand=None, reply=PROMISE)
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: runner)

    result = manager_bridge.manager_message(sid, "继续自主完成研究", global_root=tmp_path)

    # Asked once more to file what the turn changes for the team; it filed nothing.
    assert len(runner.turns) == 2
    assert result["kind"] == "error"
    assert PROMISE not in result["reply"]
    assert all(PROMISE not in text for text in _replies(life))
    assert read_continuous_state(life).objective == OLD_OBJECTIVE


def test_an_answer_that_changes_nothing_for_the_team_is_sent_as_written(tmp_path, monkeypatch):
    sid = "s-promise-answer"
    life, _memory, _running = _team_working(tmp_path, sid)
    answer = "团队正在写比较报告，三种方案已经有初稿。"
    runner = manager_runner(hand=manager_settles(), reply=answer)
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: runner)

    result = manager_bridge.manager_message(sid, "进展如何？", global_root=tmp_path)

    assert len(runner.turns) == 1
    assert result["kind"] == "chat"
    assert result["reply"] == answer
    assert read_continuous_state(life).objective == OLD_OBJECTIVE


def test_with_no_team_at_work_a_plain_reply_needs_no_hand_off(tmp_path, monkeypatch):
    sid = "s-promise-idle"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text("", encoding="utf-8")
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    manager_state._STATES.clear()
    runner = manager_runner(hand=None, reply="你好。")
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: runner)

    result = manager_bridge.manager_message(sid, "你好", global_root=tmp_path)

    assert len(runner.turns) == 1
    assert result["kind"] == "chat" and result["reply"] == "你好。"
    assert "The team is working" not in runner.turns[0]
