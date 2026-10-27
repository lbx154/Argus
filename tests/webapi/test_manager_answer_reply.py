"""What the operator reads after the Manager files their answer: always a reply, in their language."""

from __future__ import annotations

from types import SimpleNamespace

from argus.core.operator_context import OperatorContextStore, persist_once_answer
from argus.core.operator_decision import build_operator_decision
from argus.life.memory import Backlog, BacklogItem
from argus.webapi.manager_pending_question import _apply_operator_answer, apply_manager_answer

QUESTION = "split_cents 按哪种规则？"


def _parked(tmp_path, *, title="拆分金额", question=QUESTION, **card):
    backlog = Backlog(tmp_path / "backlog.jsonl")
    item = backlog.add(BacklogItem.new(title=title, objective="work", item_id="t-split"))
    decision = {**build_operator_decision(item_id=item.id, title=title, reason="blocked", question=question), **card}
    backlog.update(item.id, park_reason="operator_pause", pending_question=question, operator_decision=decision)
    mem = SimpleNamespace(backlog=backlog, project_root=tmp_path, global_root=tmp_path)
    return mem, next(row for row in backlog.active() if row.id == item.id)


def test_a_bare_choice_that_does_not_apply_is_told_in_the_conversations_language(tmp_path) -> None:
    mem, stale = _parked(tmp_path)
    # Someone answered the card first; this "2" arrives on the stale snapshot.
    mem.backlog.update(stale.id, pending_question="")

    result = _apply_operator_answer(mem, stale, "2", manager_decision="rule 2")

    assert result["error"] == "question is no longer pending"
    assert result["reply"] == "这个回答没能用上：问题可能已经答过，或者已经不在了。"


def test_an_answer_whose_task_ended_during_the_turn_is_told_not_raised(tmp_path) -> None:
    mem, _item = _parked(tmp_path)
    # The form check saw t-done waiting; it left the live backlog before the answer applied.
    handoff = {"answer": {"item": "t-done", "decision": "rule 2"}}

    applied = apply_manager_answer(mem, handoff, "2", reply="好的，按规则 2 继续。")

    assert applied["error"] == "question is no longer pending"
    assert applied["reply"] == "这个回答没能用上：问题可能已经答过，或者已经不在了。"


def test_a_deployment_answer_keeps_the_managers_reply_and_settles_the_answer(tmp_path) -> None:
    mem, item = _parked(
        tmp_path, title="Framework change", question="Adopt the reviewed change?",
        decision_kind="framework_deployment",
        options=[{"id": "adopt", "label": "Adopt"}, {"id": "decline", "label": "Decline"}],
    )
    record = persist_once_answer(tmp_path, "decline", source="operator.pending_answer", mission_id=item.id)

    result = _apply_operator_answer(
        mem, item, "decline", manager_decision="decline", manager_reply="好，不换了，保持现在的版本。",
        decision_option="decline", operator_context_persisted=True, operator_context_revision=record.revision,
    )

    assert result["resolved"]
    assert result["reply"] == "好，不换了，保持现在的版本。"
    assert not OperatorContextStore(tmp_path).has_unhandled_input("planner")
