"""必经步骤: steps a person lays on the canvas bind the team (core.waypoints)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.core import waypoints as wp
from argus.core.operator_context import OperatorContextStore, build_operator_context_block
from argus.life.memory import Backlog, BacklogItem
from argus.life.supervisor import LifeSupervisor
from argus.life.waypoint_cards import raise_due_checkpoints
from argus.manager.stage_decider import final_stage_completion_blockers
from argus.planner.planner import _plan
from argus.webapi import canvas as store
from argus.webapi.manager_pending_question import _apply_operator_answer


def _route(life, texts, *, checkpoint=(), chain=True):
    """Lay steps on the canvas, linked 先后 in the given order; returns their ids."""
    ids = []
    for index, text in enumerate(texts):
        rev = store.read_canvas(life)["revision"]
        _canvas, card = store.add_card(life, rev, kind="step", text=text, checkpoint=index in checkpoint)
        ids.append(card["id"])
    if chain:
        for source, target in zip(ids, ids[1:]):
            store.add_link(life, store.read_canvas(life)["revision"], source=source, target=target, kind="order")
    return ids


def _task(backlog, title, step="", status="pending"):
    item = backlog.add(BacklogItem.new(title=title, objective=title, waypoint_id=step))
    if status != "pending":
        backlog.update(item.id, status=status)
    return item.id


def _payload(to="engineer", tasks=(), **extra):
    return {"to": to, "summary": "s", "assumptions": [], "tasks": list(tasks), **extra}


@pytest.fixture
def life(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    return root


def test_step_cards_number_along_order_links_and_any_order_steps_stay_unnumbered(life):
    first, second, third = _route(life, ["复现 baseline", "用 vllm 做 rollout", "写方法部分"], chain=False)
    store.add_card(life, store.read_canvas(life)["revision"], kind="step", text="随时可做的一步")
    # Linked out of creation order: third comes before first.
    store.add_link(life, store.read_canvas(life)["revision"], source=third, target=first, kind="order")
    store.add_link(life, store.read_canvas(life)["revision"], source=first, target=second, kind="order")
    # A link that would close a cycle is ignored rather than wedging the route.
    store.add_link(life, store.read_canvas(life)["revision"], source=second, target=third, kind="order")
    steps = wp.read_route(life)
    assert [(s.text, s.number) for s in steps] == [
        ("写方法部分", 1), ("复现 baseline", 2), ("用 vllm 做 rollout", 3), ("随时可做的一步", None),
    ]
    assert steps[1].after == (third,)
    assert wp.ancestors(steps)[second] == {first, third}
    assert steps[0].mark() == "①"


def test_an_idea_becomes_a_step_with_a_checkpoint_and_back(life):
    _c, card = store.add_card(life, 0, kind="idea", text="先给我看方法")
    _c, card = store.update_card(life, 1, card["id"], kind="step", checkpoint=True)
    assert card["kind"] == "step" and card["checkpoint"] is True
    _c, card = store.update_card(life, 2, card["id"], kind="idea")
    assert card["kind"] == "idea" and "checkpoint" not in card
    with pytest.raises(store.CanvasError):
        store.update_card(life, 3, card["id"], checkpoint=True)


def test_steps_reach_every_role_as_one_binding_route_and_a_change_bumps_the_revision(life):
    ids = _route(life, ["复现 baseline", "用 vllm 做 rollout"], checkpoint=(1,))
    first = wp.sync_route_directive(life)
    assert first > 0
    store_ = OperatorContextStore(life)
    assert store_.has_unhandled_input("planner")
    block, revision = build_operator_context_block("planner", life, consume_once=False)
    assert revision == first
    assert "必经步骤" in block and f"1. [{ids[0]}] 复现 baseline" in block
    assert "checkpoint" in block and "never drop" in block
    # Same route: nothing written. Reworded step: the old route is revoked, one live route remains.
    assert wp.sync_route_directive(life) == first
    store_.acknowledge("planner", first)
    store.update_card(life, store.read_canvas(life)["revision"], ids[1], text="用 sglang 做 rollout")
    second = wp.sync_route_directive(life)
    assert second > first and store_.has_unhandled_input("planner")
    block, _ = build_operator_context_block("engineer", life, consume_once=False)
    assert "用 sglang 做 rollout" in block and "用 vllm" not in block
    # No steps left: the route is withdrawn.
    for step_id in ids:
        store.delete_card(life, store.read_canvas(life)["revision"], step_id)
    assert wp.sync_route_directive(life) > second
    assert "必经步骤" not in build_operator_context_block("planner", life, consume_once=False)[0]


def test_planned_tasks_carry_their_step_and_keep_its_order(life):
    backlog = Backlog(life / "backlog.jsonl")
    one, two, three = _route(life, ["a", "b", "c"])
    route = tuple(wp.read_step_states(life, backlog.history()))
    verdict = _plan(_payload(tasks=[
        {"title": "t1", "objective": "o", "waypoint_id": one},
        {"title": "t2", "objective": "o", "waypoint_id": two},
    ]), open_ended=False, stages=(), route=route)
    assert [task.waypoint_id for task in verdict.new_tasks] == [one, two]
    with pytest.raises(ValueError, match="comes first and has no task"):
        _plan(_payload(tasks=[{"title": "t3", "objective": "o", "waypoint_id": three}]),
              open_ended=False, stages=(), route=route)
    with pytest.raises(ValueError, match="name one of the operator's steps"):
        _plan(_payload(tasks=[{"title": "x", "objective": "o", "waypoint_id": "hc-nope"}]),
              open_ended=False, stages=(), route=route)
    # The backlog keeps the step; a later step's task waits for the earlier ones.
    t1 = _task(backlog, "t1", one)
    t2 = _task(backlog, "t2", two)
    loose = _task(backlog, "between steps")
    assert next(i for i in backlog.history() if i.id == t2).waypoint_id == two
    assert wp.held_item_ids(life, backlog.active()) == {t2}
    assert {item.id for item in backlog.ready()} == {t1, loose}
    backlog.update(t1, status="done")
    assert wp.held_item_ids(life, backlog.active(), backlog.history()) == set()


def test_the_planner_cannot_finish_while_a_step_is_open(life):
    backlog = Backlog(life / "backlog.jsonl")
    one, two = _route(life, ["a", "b"])
    _task(backlog, "t1", one, status="done")
    route = tuple(wp.read_step_states(life, backlog.history()))
    with pytest.raises(ValueError, match="to=done: the operator's step 2"):
        _plan(_payload(to="done"), open_ended=False, stages=(), route=route)
    blockers = final_stage_completion_blockers(
        None, current_stage="done", stage_order=["done"], project_root=life,
        allow_early_completion=True,
    )
    assert any("required steps are not all done" in blocker and two in blocker for blocker in blockers)
    _task(backlog, "t2", two, status="done")
    route = tuple(wp.read_step_states(life, backlog.history()))
    assert _plan(_payload(to="done"), open_ended=False, stages=(), route=route).project_done
    assert wp.route_blocker(route) == ""


def test_a_checkpoint_parks_the_project_on_a_question_and_the_answer_lets_the_next_step_start(life):
    backlog = Backlog(life / "backlog.jsonl")
    one, two, three = _route(life, ["写数据读取", "写汇总", "画图"], checkpoint=(1,))
    _task(backlog, "t1", one, status="done")
    t2 = _task(backlog, "t2", two, status="done")
    t3 = _task(backlog, "t3", three)
    states = wp.read_step_states(life, backlog.history())
    assert [s.status for s in states] == ["done", "review", "todo"]
    # Nothing starts while the person has not looked.
    assert t3 in wp.held_item_ids(life, backlog.active(), backlog.history())
    events = []
    assert raise_due_checkpoints(backlog, events.append) == 1
    assert raise_due_checkpoints(backlog, events.append) == 0  # one card per step
    card_item = next(i for i in backlog.active() if i.pending_question)
    assert card_item.status == "parked" and "第 2 步完成了" in card_item.pending_question
    assert card_item.operator_decision["decision_kind"] == wp.CHECKPOINT_DECISION
    assert [o["id"] for o in card_item.operator_decision["options"]] == ["continue", "revise"]
    assert wp.read_step_states(life, backlog.history())[1].question_item_id == card_item.id
    mem = SimpleNamespace(backlog=backlog, project_root=life)
    result = _apply_operator_answer(mem, card_item, "继续", manager_decision="继续",
                                    decision_option="continue", decision_id=card_item.operator_decision["id"])
    assert result["resolved"] and result["resume_requested"]
    assert wp.read_state(life)["approved"][two] == [t2]
    assert [s.status for s in wp.read_step_states(life, backlog.history())] == ["done", "done", "todo"]
    assert wp.held_item_ids(life, backlog.active(), backlog.history()) == set()
    closed = next(i for i in backlog.history() if i.id == card_item.id)
    assert closed.status == "done" and not closed.pending_question


def test_asking_for_changes_at_a_checkpoint_reworks_that_step_and_asks_again(life):
    backlog = Backlog(life / "backlog.jsonl")
    one, two = _route(life, ["写汇总", "画图"], checkpoint=(0,))
    _task(backlog, "t1", one, status="done")
    raise_due_checkpoints(backlog, lambda event: None)
    card_item = next(i for i in backlog.active() if i.pending_question)
    mem = SimpleNamespace(backlog=backlog, project_root=life)
    result = _apply_operator_answer(mem, card_item, "金额要保留两位小数", manager_decision="金额要保留两位小数")
    assert result["resolved"]
    resumed = next(i for i in backlog.active() if i.id == card_item.id)
    assert resumed.status == "pending" and resumed.waypoint_id == one
    assert "金额要保留两位小数" in resumed.objective
    assert wp.read_step_states(life, backlog.history())[0].status == "running"
    backlog.update(resumed.id, status="done")
    assert wp.read_step_states(life, backlog.history())[0].status == "review"
    assert raise_due_checkpoints(backlog, lambda event: None) == 1


def test_a_step_the_planner_doubts_becomes_a_question_to_the_person_not_a_skip(life, monkeypatch):
    monkeypatch.setattr("argus.core.autonomy.operator_questions_can_be_asked", lambda *a, **k: True)
    backlog = Backlog(life / "backlog.jsonl")
    one, two = _route(life, ["复现 baseline", "用 vllm 做 rollout"])
    _task(backlog, "t1", one, status="done")
    route = tuple(wp.read_step_states(life, backlog.history()))
    question = "第 2 步要用 vllm，但这台机器没有 GPU。怎么办？"
    verdict = _plan(_payload(to="operator", ask={"question": question, "options": [
        {"id": "keep", "label": "保留"}, {"id": "remove", "label": "去掉"}]}, about_waypoint=two),
        open_ended=False, stages=(), route=route)
    assert verdict.handoff == "operator" and verdict.about_waypoint == two
    with pytest.raises(ValueError, match="about_waypoint goes with to=operator"):
        _plan(_payload(about_waypoint=two), open_ended=False, stages=(), route=route)
    supervisor = SimpleNamespace(
        memory=SimpleNamespace(backlog=backlog, root=life), config=SimpleNamespace(continuous_objective="做个小项目"),
        _emit=lambda event: True, _waypoint_states=lambda: list(route),
    )
    assert LifeSupervisor._park_planner_question(
        supervisor, None, question, "doubt", options=verdict.operator_options, about_waypoint=two,
    )
    card_item = next(i for i in backlog.active() if i.pending_question)
    card = card_item.operator_decision
    assert card["decision_kind"] == wp.DISPUTE_DECISION and card["waypoint_id"] == two
    assert [o["id"] for o in card["options"]] == ["keep", "remove"]
    states = wp.read_step_states(life, backlog.history())
    assert states[1].status == "question" and wp.route_blocker(states)
    # The person removes it: the card goes back to an idea and nothing is left open.
    mem = SimpleNamespace(backlog=backlog, project_root=life)
    result = _apply_operator_answer(mem, card_item, "去掉", manager_decision="去掉", decision_option="remove",
                                    decision_id=card["id"])
    assert result["resolved"]
    assert next(c for c in store.read_canvas(life)["cards"] if c["id"] == two)["kind"] == "idea"
    assert wp.route_blocker(wp.read_step_states(life, backlog.history())) == ""


def test_the_manager_reads_steps_by_number_and_status(life):
    backlog = Backlog(life / "backlog.jsonl")
    one, _two = _route(life, ["复现 baseline", "用 vllm 做 rollout"], checkpoint=(1,))
    _task(backlog, "t1", one, status="running")
    block = store.canvas_context_block(life, rows=backlog.history)
    assert "必经步骤 ① " in block and "（进行中）" in block
    assert "② " in block and "到这步先停下来给人看" in block


def test_new_operator_work_starts_the_route_at_its_next_step(life):
    backlog = Backlog(life / "backlog.jsonl")
    one, two = _route(life, ["写核心函数", "加命令行"])
    states = wp.read_step_states(life, backlog.history())
    assert wp.next_step_for_new_work(states).step.id == one
    objective, check = wp.scope_to_step("做 wordstat", states[0].step)
    assert "carries out only the operator's step 1" in objective and "«写核心函数»" in check
    first = _task(backlog, "t1", one)
    # While a step is being worked, new work is not tied to any step.
    assert wp.next_step_for_new_work(wp.read_step_states(life, backlog.history())) is None
    backlog.update(first, status="done")
    assert wp.next_step_for_new_work(wp.read_step_states(life, backlog.history())).step.id == two
