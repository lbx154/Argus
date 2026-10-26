"""The Manager's one ``hand_off`` form: the rules Argus enforces on it, and the team's ticket."""
import pytest

from argus.manager.form import config_intents, manager_actions, ticket


def _call(actions, **payload):
    try:
        actions.dispatch("hand_off", {"summary": "Why.", "assumptions": [], **payload})
    except ValueError as refusal:
        return str(refusal)
    return ""


def test_a_ticket_carries_its_goal_acceptance_and_route():
    routed: list[dict] = []
    actions = manager_actions(
        "做一份完整的调研报告", active_mission=False, operator_available=True,
        route=lambda payload: routed.append(payload) or "decision",
    )
    assert "goal, acceptance and vertical" in _call(actions, to="team", goal="调研报告", acceptance=["报告存在"])
    assert _call(
        actions, to="team", goal="调研报告", acceptance=["报告存在"], constraints=["只用公开数据"],
        vertical="research", research_target_level="exploratory", name="向量库调研",
    ) == ""
    assert actions.handoff.to == "team" and actions.handoff.value == "decision"
    [payload] = routed
    assert payload["execution_task"] == ticket(actions.handoff.payload)
    assert payload["precise_constraints"] == ["只用公开数据"]
    assert payload["session_title"] == "向量库调研"
    assert payload["vertical"] == "research" and payload["research_target_level"] == "exploratory"


def test_a_held_ticket_never_tells_the_team_to_wait_for_start():
    # Trial s-efe9deac: told of the hold as operator text, the Manager wrote
    # "wait for 开始执行" into goal, acceptance and constraints; after the click
    # the team still waited and only re-checked the plan.
    from argus.core.launch_settings import launch_hold_context

    message = "请直接给一个完整的论文 我要投ACL\n\n" + launch_hold_context("写论文")
    actions = manager_actions(message, active_mission=False, operator_available=True, route=lambda _p: "decision")
    held = dict(to="team", goal="完成 ACL 论文", acceptance=["交付 paper/main.pdf"], vertical="research")
    for field, text in (
        ("goal", "完成 ACL 论文。必须先排队等待用户点击“开始执行”。"),
        ("acceptance", ["用户点击“开始执行”前只排队，不开展文献调研。"]),
        ("constraints", ["交给团队后先别开工：Argus 会把排好的任务停在开始之前。"]),
    ):
        assert "take waiting for Start out" in _call(actions, **{**held, field: text})
    assert _call(actions, **held) == ""
    # Without a hold the same words are the operator's own and pass as written.
    plain = manager_actions("等我点“开始执行”再做实验", active_mission=False, operator_available=True,
                            route=lambda _p: "decision")
    assert _call(plain, **{**held, "constraints": ["等我点“开始执行”再做实验"]}) == ""


def test_an_answer_kept_with_the_manager_cannot_go_to_the_team():
    actions = manager_actions("/ask 这个怎么做", active_mission=False, team_allowed=False, operator_available=True)
    assert "yourself" in _call(actions, to="team", goal="g", acceptance=["a"])


def test_a_question_offers_two_to_five_different_answers():
    actions = manager_actions("A 和 B 冲突了", active_mission=False, operator_available=True)
    ask = {"question": "选哪个？", "options": [{"label": "A"}, {"label": "a"}]}
    assert "two to five" in _call(actions, to="operator", ask=ask)
    ask["options"][1]["label"] = "B"
    assert _call(actions, to="operator", ask=ask) == ""


def test_no_question_when_the_operator_cannot_be_asked():
    actions = manager_actions("A 和 B 冲突了", active_mission=False, operator_available=False)
    ask = {"question": "选哪个？", "options": [{"label": "A"}, {"label": "B"}]}
    assert "cannot be asked" in _call(actions, to="operator", ask=ask)


@pytest.mark.parametrize(("control", "active", "refusal"), [
    ({"kind": "pause", "quote": "别停"}, True, "own words"),
    ({"kind": "steer", "quote": "改用中文", "directive": "中文"}, False, "nothing to steer"),
    ({"kind": "steer", "quote": "改用中文"}, True, "directive"),
])
def test_a_control_is_the_operators_explicit_word(control, active, refusal):
    actions = manager_actions("请暂停，改用中文", active_mission=active, operator_available=True)
    assert refusal in _call(actions, to="manager", control=control)


def test_settings_are_applied_by_argus_not_the_team():
    actions = manager_actions("把模型换成 x", active_mission=False, operator_available=True)
    config = [{"knob": "model", "roles": ["engineer", "engineer"], "value": "x"}]
    assert "to=manager" in _call(actions, to="team", goal="g", acceptance=["a"], vertical="software", config=config)
    assert _call(actions, to="manager", config=config) == ""
    assert config_intents(actions.handoff.get("config"))[0].roles == ("engineer",)


def test_the_ticket_carries_goal_checks_limits_and_readings():
    text = ticket({
        "goal": "比较三个向量数据库", "acceptance": ["报告含延迟对比"], "constraints": ["只用开源"],
        "context": ["notes/brief.md"], "assumptions": [{"chose": "单机部署", "instead_of": "集群"}],
    })
    assert text.startswith("比较三个向量数据库")
    for line in ("- 报告含延迟对比", "- 只用开源", "- notes/brief.md", "- 单机部署 (not: 集群)"):
        assert line in text


def test_an_answer_names_a_waiting_task_and_one_of_its_offered_answers() -> None:
    from argus.manager.form import manager_actions

    actions = manager_actions("v2", active_mission=False, operator_available=True, waiting={"m-1": ["option-1"]})
    base = {"to": "manager", "summary": "Taking v2.", "assumptions": []}
    for answer, refusal in (
        ({"item": "m-9", "decision": "Use v2."}, "waiting on the operator"),
        ({"item": "m-1", "option": "option-7", "decision": "Use v2."}, "answer.option"),
    ):
        with pytest.raises(ValueError, match=refusal):
            actions.dispatch("hand_off", {**base, "answer": answer})
    actions.dispatch("hand_off", {**base, "answer": {"item": "m-1", "option": "option-1", "decision": "Use v2."}})
    assert actions.handoff.get("answer")["option"] == "option-1"


def test_a_workflow_setup_is_whole_and_only_while_one_is_under_way() -> None:
    from argus.manager.form import manager_actions

    settle = {"to": "manager", "summary": "Prepared.", "assumptions": [], "workflow_setup": {"action": "prepare"}}
    with pytest.raises(ValueError, match="No workflow setup"):
        manager_actions("go", active_mission=False).dispatch("hand_off", settle)
    setting_up = manager_actions("go", active_mission=False, setting_up=True)
    with pytest.raises(ValueError, match="name, purpose, brief"):
        setting_up.dispatch("hand_off", settle)
    with pytest.raises(ValueError, match="two to four"):
        setting_up.dispatch("hand_off", {"to": "operator", "summary": "Asking.", "assumptions": [], "ask": {
            "question": "Which scope?", "options": [{"label": str(n)} for n in range(5)]}})
