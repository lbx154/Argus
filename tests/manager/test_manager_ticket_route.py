"""The Manager's ticket carries the team's route; the inbox and nudges carry no authority of their own."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.apps._inbox import queue_inbox_message
from argus.apps._inbox_delivery import DurableInboxReceiver
from argus.core.operator_context import OperatorContextStore
from argus.manager import Manager
from argus.manager.form import manager_actions
from argus.skills.vertical_select import persist_vertical
from argus.webapi import manager_dispatch, mission_items


class _Runner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run_exec(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        raise AssertionError("a ticket's route needs no routing call")


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    return Manager(project_root=tmp_path, runner=_Runner())


def _team(**fields):
    return {"to": "team", "summary": "A multi-stage study.", "assumptions": [],
            "goal": "对比 4 个开源向量数据库", "acceptance": ["报告存在"], **fields}


def test_the_ticket_route_is_the_dispatch_decision_without_a_routing_call(manager):
    actions = manager_actions("帮我做个研究", active_mission=False, operator_available=True,
                              route=manager.adopt_route)
    actions.dispatch("hand_off", _team(vertical="research", research_target_level="exploratory",
                                       constraints=["同一台机器"], name="向量库对比"))
    decision = actions.handoff.value
    assert (decision.choice, decision.vertical, decision.research_target_level) == ("existing", "research", "exploratory")
    assert decision.research_direction_mode == "locked"
    assert decision.execution_task.startswith("对比 4 个开源向量数据库")
    assert decision.precise_constraints == ("同一台机器",)
    assert decision.session_title == "向量库对比"
    assert manager.runner.calls == []


def test_a_route_the_host_cannot_commit_is_refused_in_the_turn(manager):
    actions = manager_actions("帮我做个研究", active_mission=False, operator_available=True,
                              route=manager.adopt_route)
    with pytest.raises(ValueError, match="research_target_level"):
        actions.dispatch("hand_off", _team(vertical="research"))
    with pytest.raises(ValueError, match="domain"):
        actions.dispatch("hand_off", _team(vertical="software", domain="chemistry"))
    assert actions.handoff is None


def test_a_question_is_shown_once_from_the_ask():
    actions = manager_actions("A 和 B 冲突了", active_mission=False, operator_available=True)
    ask = {"question": "选 A 还是 B？", "options": [{"label": "A"}, {"label": "B"}]}
    actions.dispatch("hand_off", {"to": "operator", "summary": "Conflict.", "assumptions": [], "ask": ask})
    emitter = SimpleNamespace(life_dir=None, respond=lambda reply, result: reply)
    shown = manager_dispatch._settle_manager_turn(
        None, "请选择：A 还是 B？", actions.handoff, {}, emitter,
        source_channel="web", source_message_id="", operator_text="A 和 B 冲突了",
    )
    assert shown.count("A 还是 B") == 1 and shown.startswith("选 A 还是 B？")


def test_inbox_text_is_guidance_never_a_lasting_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    life, project = tmp_path / "life", tmp_path / "project"
    life.mkdir()
    project.mkdir()
    persist_vertical(project, "software", workflow_mode="direct")
    queue_inbox_message(life, "以后都用中文回复", source="operator")
    receiver = DurableInboxReceiver(life, consumer="supervisor", project_root=project)
    [text] = receiver.receive(mission_id="", limit=10)
    assert str(text) == "以后都用中文回复"
    receiver.settle([text])
    assert not OperatorContextStore(life).records()


def test_a_nudge_is_a_turn_of_the_manager_session(tmp_path, monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setattr(mission_items, "project_life_dir", lambda sid, global_root=None: tmp_path)
    monkeypatch.setattr("argus.webapi.manager_bridge.manager_message",
                        lambda sid, text, **kw: sent.append((sid, text, kw["source_channel"])) or {"kind": "chat"})
    assert mission_items.enqueue_nudge("s-1", " 先做图表 ") == {"kind": "chat"}
    assert sent == [("s-1", "先做图表", "web")]


def test_a_new_operator_request_chooses_its_own_route_contract_over_the_campaign_it_replaces(tmp_path, monkeypatch):
    """Trial s-dacac579: "写一篇ai论文" fixed exploratory; the later CVPR request could not raise it.

    A staged ticket replaces the running campaign's objective (``dispatch.enqueue_mission``),
    so it picks its target level as a fresh project would. Only a direct bounded
    ticket joins the campaign and keeps its contract.
    """
    from argus.daemon.state import compare_and_swap_continuous_config, read_continuous_state
    from argus.life.memory import LifeMemory
    from argus.manager import front_door
    from tests.hand_off import manager_runner

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    persist_vertical(tmp_path, "research", workflow_mode="staged", research_target_level="exploratory",
                     research_direction_mode="broad")
    assert compare_and_swap_continuous_config(
        tmp_path, expected=read_continuous_state(tmp_path), enabled=True,
        objective="撰写一篇中文人工智能文献综述。", open_ended=True,
    )
    request = "写一篇cvpr水平的，质量要高，到时候给我pdf"

    def handed(**fields):
        return "hand_off", _team(goal="以CVPR水平开展计算机视觉研究并交付PDF", vertical="research",
                                 research_target_level="publishable", research_direction_mode="broad",
                                 target_venue="CVPR", lifetime="bounded", **fields)

    mem = SimpleNamespace(project_root=tmp_path, backlog=LifeMemory.open(tmp_path).backlog)
    staged = manager_runner(Manager(project_root=tmp_path, runner=_Runner()), hand=handed(workflow_mode="staged"))
    reply, handoff = front_door.manager_turn(mem, request, {}, runner=staged)
    assert handoff is not None, reply
    assert (handoff.value.research_target_level, handoff.value.target_venue) == ("publishable", "CVPR")

    direct = manager_runner(Manager(project_root=tmp_path, runner=_Runner()), hand=handed(workflow_mode="direct"))
    chat_state: dict = {}
    _reply, handoff = front_door.manager_turn(mem, request, chat_state, runner=direct)
    assert handoff is None
    assert "the active route contract fixes it" in chat_state["_self_failure"]["detail"]
