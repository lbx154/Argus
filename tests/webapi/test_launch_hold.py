from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from argus.core.launch_settings import (
    launch_hold_context,
    launch_note,
    launch_target_level_stated,
    normalize_launch_settings,
    strip_launch_note,
)
from argus.core.project_contract import (
    AUTHORITY_MANAGER,
    AUTHORITY_OPERATOR,
    load_contract,
)
from argus.core.session import SessionMeta, write_session_meta
from argus.daemon import life_worker as daemon_worker
from argus.life.memory import BacklogItem, LifeMemory
from argus.webapi import launch_hold, manager_bridge, server
from argus.webapi.daemon_services import DaemonServices


def test_default_settings_are_no_settings():
    assert normalize_launch_settings(None) is None
    assert normalize_launch_settings({}) is None
    assert normalize_launch_settings({"target_level": "auto", "deliverable": "any"}) is None
    assert normalize_launch_settings({"target_level": "doctoral", "first_result_hours": 0.1}) is None
    assert normalize_launch_settings({"hold": True}) == {
        "first_result_hours": None, "target_level": "auto", "deliverable": "any", "hold": True,
    }


def test_launch_note_states_each_choice_in_the_request_language():
    settings = normalize_launch_settings({"first_result_hours": 4, "target_level": "exploratory", "deliverable": "report"})
    zh = launch_note(settings, language_hint="帮我研究一下这个问题")
    assert "4 小时内交付" in zh and "研究定位：探索性" in zh and "成果形式：一份书面报告" in zh
    assert launch_target_level_stated(zh)
    en = launch_note({**settings, "first_result_hours": 24.0}, language_hint="study this")
    assert "within 1 day" in en and "Research ambition: exploratory" in en
    assert launch_note(None) == ""
    # The hold before Start is Argus's own state, never operator text the
    # Manager could copy into a ticket (trial s-efe9deac).
    assert launch_note({"hold": True}, language_hint="研究") == ""
    assert "开始执行" not in launch_note({"hold": True, "deliverable": "paper"}, language_hint="写论文")
    assert "Start" not in launch_note({"hold": True, "deliverable": "paper"}, language_hint="write a paper")
    assert not launch_target_level_stated(launch_note({"first_result_hours": 2.0}, language_hint="研究"))
    # Titles and summaries read the request without the note appended to it.
    assert strip_launch_note("帮我研究一下这个问题" + zh) == "帮我研究一下这个问题"
    assert strip_launch_note("study this" + en) == "study this"


def _status(life, item_id):
    return next(row.status for row in LifeMemory.open(life).backlog.history() if row.id == item_id)


def _project(tmp_path, sid):
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life)))
    return life


def test_hold_view_only_while_held_work_is_waiting(tmp_path):
    life = _project(tmp_path, "s-hold-view")
    record = launch_hold.write_launch_hold(
        life, {"item": {"id": "a", "title": "Survey X"}}, {"hold": True}, operator_text="研究 X",
    )
    assert record["item_id"] == "a"
    pending = [{"id": "a", "status": "pending"}]
    view = launch_hold.launch_hold_view(life, daemon_alive=False, backlog=pending)
    assert view is not None and view["title"] == "Survey X" and view["understood"] == {}
    assert launch_hold.launch_hold_view(life, daemon_alive=True, backlog=pending) is None
    assert launch_hold.launch_hold_view(life, daemon_alive=False, backlog=[{"id": "a", "status": "skipped"}]) is None
    assert launch_hold.clear_launch_hold(life) and launch_hold.read_launch_hold(life) is None


@pytest.mark.parametrize("streaming", [False, True])
def test_held_task_is_queued_but_not_started_until_confirmed(tmp_path, monkeypatch, streaming):
    sid = "s-hold-route"
    life = _project(tmp_path, sid)
    item = LifeMemory.open(life).backlog.add(BacklogItem.new(title="Survey X", objective="Survey X"))
    seen = {}

    def handoff(sid_, text, **kwargs):
        seen.update(kwargs)
        return {"kind": "task", "item": {"id": item.id, "title": "Survey X", "status": "pending"}}

    starts = []
    monkeypatch.setattr(manager_bridge, "manager_message", handoff)
    services = DaemonServices(read_status=daemon_worker.read_daemon_status,
                              start=lambda *args, **kwargs: starts.append(kwargs) or {"rc": 0})
    with TestClient(server.create_app(global_root=tmp_path, daemon_services=services)) as client:
        response = client.post(
            f"/api/projects/{sid}/message" + ("/stream" if streaming else ""),
            json={"text": "研究 X", "launch": {"first_result_hours": 4, "hold": True}},
        )
        assert response.status_code == 200
        if streaming:
            frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            result = next(frame["result"] for frame in frames if frame["type"] == "done")
        else:
            result = response.json()
        assert seen["launch"]["first_result_hours"] == 4
        assert starts == []
        assert result["launch_hold"]["item_id"] == item.id
        assert "点“开始执行”就开始" in result["reply"]
        snapshot = client.get(f"/api/projects/{sid}/snapshot?compact=true").json()
        assert snapshot["launch_hold"]["title"] == "Survey X"

        confirmed = client.post(f"/api/projects/{sid}/launch/confirm")
        assert confirmed.status_code == 200
        assert len(starts) == 1 and starts[0]["reclaim_idle"] is True
        assert launch_hold.read_launch_hold(life) is None
        assert client.post(f"/api/projects/{sid}/launch/confirm").status_code == 409


def test_message_without_hold_still_starts_immediately(tmp_path, monkeypatch):
    sid = "s-hold-none"
    _project(tmp_path, sid)
    monkeypatch.setattr(manager_bridge, "manager_message",
                        lambda *a, **k: {"kind": "task", "item": {"id": "a", "status": "pending"}})
    starts = []
    services = DaemonServices(read_status=daemon_worker.read_daemon_status,
                              start=lambda *args, **kwargs: starts.append(kwargs) or {"rc": 0})
    with TestClient(server.create_app(global_root=tmp_path, daemon_services=services)) as client:
        result = client.post(f"/api/projects/{sid}/message", json={"text": "研究 X"}).json()
    assert len(starts) == 1 and "launch_hold" not in result


def test_discard_route_returns_the_request_for_editing(tmp_path):
    sid = "s-hold-discard-route"
    life = _project(tmp_path, sid)
    item = LifeMemory.open(life).backlog.add(BacklogItem.new(title="Survey X", objective="Survey X"))
    launch_hold.write_launch_hold(life, {"item": {"id": item.id}}, {"hold": True}, operator_text="研究 X")
    with TestClient(server.create_app(global_root=tmp_path)) as client:
        response = client.post(f"/api/projects/{sid}/launch/discard")
    assert response.status_code == 200
    assert response.json()["request"] == "研究 X"
    assert _status(life, item.id) == "skipped"


def test_inferred_target_level_is_manager_owned_and_chosen_level_operator_owned(tmp_path):
    from types import SimpleNamespace

    from argus.manager.front_door import _record_goal_contract

    life = _project(tmp_path, "s-target-authority")
    mem = SimpleNamespace(project_root=life)
    decision = SimpleNamespace(precise_constraints=(), research_target_level="publishable",
                               target_venue="", exclusions=(), assumptions=())
    _record_goal_contract(mem, "写一篇论文", decision)
    clause = next(c for c in load_contract(life).clauses if c.text.startswith("research target level"))
    assert clause.authority == AUTHORITY_MANAGER

    chosen = "写一篇论文" + launch_note({"target_level": "publishable"}, language_hint="写")
    _record_goal_contract(mem, chosen, decision)
    clause = next(c for c in load_contract(life).clauses if c.text.startswith("research target level"))
    assert clause.authority == AUTHORITY_OPERATOR


def test_any_daemon_start_supersedes_the_hold(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from argus.webapi import daemon_lifecycle

    life = _project(tmp_path, "s-hold-superseded")
    launch_hold.write_launch_hold(life, {"item": {"id": "a"}}, {"hold": True})
    alive = SimpleNamespace(alive=True, pid=1, started_at_iso="x", uptime_seconds=1.0, life_dir=life)
    monkeypatch.setattr(daemon_worker, "read_daemon_status", lambda *_: alive)
    monkeypatch.setattr(daemon_lifecycle, "_daemon_dict", lambda status: {"alive": True})
    result = daemon_lifecycle.start_project_daemon("s-hold-superseded", global_root=tmp_path)
    assert result["already_alive"] is True
    assert launch_hold.read_launch_hold(life) is None


_PLAN = {"path": "RL_RESEARCH_PLAN.md", "summary": "1. 选 0.5B 模型和 GSM8K。\n2. 先复现 GRPO 基线。\n3. 再试新算法。"}


def test_the_ticket_carries_the_plan_file_to_the_route():
    from argus.manager.domain_author import RouteContext
    from argus.manager.form import _route
    from tests.hand_off import ticket

    _, payload = ticket("RL 一个小模型", plan=_PLAN)
    route = _route(payload)
    assert route["plan"] == _PLAN
    decision = RouteContext(known_verticals=("software",)).decide({**route, "choice": "existing", "vertical": "software"})
    assert decision.plan == _PLAN


@pytest.mark.parametrize("streaming", [False, True])
def test_held_card_shows_the_plan_and_the_manager_closes_the_turn(tmp_path, monkeypatch, streaming):
    # Trial s-99ca9ad6: the card listed one queued title as "the plan", the
    # 18k-character plan file was nowhere on it, and a fixed sentence replaced
    # the Manager's own words.
    from argus.manager import Manager, front_door
    from argus.manager.domain_author import VerticalDecision
    from argus.webapi import manager_state
    from tests.hand_off import manager_runner, ticket

    sid = "s-hold-plan"
    life = _project(tmp_path, sid)
    manager_state._STATES.pop(sid, None)
    manager = Manager(life)
    said = "我先写好了计划框架，放在 RL_RESEARCH_PLAN.md。你确认方向后再开始。"
    # The Manager hands the request it was given, not Argus's note beside it.
    runner = manager_runner(manager, reply=said, hand=lambda message: ticket(message.partition("\n\n## Argus")[0]))
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_args: runner)
    monkeypatch.setattr(manager, "decide_vertical", lambda *_args, **_kwargs: VerticalDecision(
        choice="existing", vertical="research", workflow_mode="direct",
        execution_task="RL 一个小模型", research_target_level="exploratory", plan=dict(_PLAN),
    ))
    starts = []
    services = DaemonServices(read_status=daemon_worker.read_daemon_status,
                              start=lambda *args, **kwargs: starts.append(kwargs) or {"rc": 0})
    with TestClient(server.create_app(global_root=tmp_path, daemon_services=services)) as client:
        response = client.post(
            f"/api/projects/{sid}/message" + ("/stream" if streaming else ""),
            json={"text": "RL 一个小模型，计划写详细点", "launch": {"hold": True}},
        )
        frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        result = next(frame["result"] for frame in frames if frame["type"] == "done") if streaming else response.json()
        assert starts == [] and result["reply"] == said
        # The Manager hears of the hold beside the request, as Argus's state,
        # not as words of the operator's it would copy into the ticket.
        [told] = runner.turns
        request, _, context = told.partition("## Argus 的开工确认")
        assert context and "不要把“等待开始执行”" in context
        assert "开始执行" not in request and "先别开工" not in request
        [row] = LifeMemory.open(life).backlog.active()
        assert row.manager_decision["plan"] == _PLAN
        snapshot = client.get(f"/api/projects/{sid}/snapshot?compact=true").json()
        assert snapshot["launch_hold"]["plan"] == _PLAN


def test_the_hold_reaches_the_manager_as_argus_state_in_the_conversation_language():
    zh = launch_hold_context("帮我写一篇 ACL 论文")
    assert zh.startswith("## Argus 的开工确认（系统状态，不是用户对工作的要求）")
    assert "不要把“等待开始执行”“先别开工”写进目标、验收或约束" in zh
    en = launch_hold_context("write an ACL paper")
    assert "not a requirement on the work" in en and "do not write waiting for Start" in en



def test_s_efe9deac_request_reaches_the_manager_without_the_hold(tmp_path, monkeypatch):
    # Replay of trial s-efe9deac: the hold rode in the operator text, the
    # Manager copied "等我点开始执行" into the ticket and the team kept waiting
    # after Start. Only Argus's state block beside the request may mention it.
    from argus.manager import front_door
    from argus.webapi import manager_state
    from tests.hand_off import manager_runner

    record = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "s_efe9deac"
                         / "launch_hold_request.json").read_text(encoding="utf-8"))
    assert "开始执行" in record["pre_fix_manager_message"]
    sid = "s-efe9deac"
    _project(tmp_path, sid)
    manager_state._STATES.pop(sid, None)
    runner = manager_runner(hand=None, reply="先确认目标和计划。")
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_args: runner)
    with TestClient(server.create_app(global_root=tmp_path)) as client:
        response = client.post(f"/api/projects/{sid}/message",
                               json={"text": record["operator_text"], "launch": record["launch"]})
    assert response.status_code == 200
    [told] = runner.turns
    request, heading, context = told.partition("## Argus 的开工确认")
    assert heading and record["operator_text"] in request
    assert "开始执行" not in request and "先别开工" not in request
    assert "开始执行" in context
