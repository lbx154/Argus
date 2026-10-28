"""A Manager decision about the campaign, not one task, reaches whoever can act on it.

Trial s-dacac579 (2026-10-28): after the Reviewer accepted a one-shot review,
the Planner handed its turn to the Manager six times ("turn continuous mode
off"). Three Manager questions for the operator and two stops were each
recorded as "superseded": a planner idle names no task, so the question had
no card, and no mission was running to park. Nothing was ever applied, and
the Planner was asked again every few minutes.
"""
from __future__ import annotations

from types import SimpleNamespace

from argus.core.event_catalog import EventType
from argus.core.models import RunnerResult
from argus.core.role_actions import PREFIX
from argus.core.role_tool_bridge import bridge_request
from argus.daemon.state import write_continuous_config
from argus.life.memory import Backlog
from argus.life.supervisor.pending_notify import planning_slot_card
from argus.manager import Manager, supervision

OBJECTIVE = "撰写一篇中文人工智能文献综述。"
QUESTION = "综述已通过审阅；要关闭持续推进，还是安排下一项？"


class _Backend:
    """A Manager that files ``calls`` in order; ``refusals`` keeps what the form refused."""

    def __init__(self, *calls: dict) -> None:
        self.calls = calls
        self.refusals: list[str] = []

    def fork(self):
        return self

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        for call in self.calls:
            payload = {"summary": "综述已通过审阅，团队没有新的事可做。", "evidence_refs": ["backlog.jsonl"],
                       "assumptions": [], **call}
            try:
                bridge_request(PREFIX, "hand_off", payload, env=options.extension_env)
            except ValueError as refusal:
                self.refusals.append(str(refusal))
                continue
            break
        return RunnerResult(exit_code=0, call_id="offline", agent_messages=["Done."])


def _planner_idle() -> dict:
    return {"type": EventType.LIFE_PLANNER_IDLE, "handoff": "manager", "process_stuck": True,
            "reason": "综述已交付并独立核查；请关闭误开启的持续模式。",
            "consult_reason": "the Planner says the process is stuck"}


def test_a_question_about_the_campaign_goes_on_the_planning_card_once(tmp_path) -> None:
    write_continuous_config(tmp_path, enabled=True, objective=OBJECTIVE, open_ended=True)
    ask = {"to": "operator", "ask": {"question": QUESTION, "options": []}}

    first = supervision.supervise(Manager(tmp_path, runner=_Backend(ask)), tmp_path, _planner_idle())

    assert first["status"] == "applied"
    [card] = [row for row in Backlog(tmp_path / "backlog.jsonl").active() if row.pending_question]
    assert card.pending_question == QUESTION and "operator_decision" in card.tags
    assert first["effects"]["question_item_id"] == card.id

    again = supervision.supervise(Manager(tmp_path, runner=_Backend(ask)), tmp_path, _planner_idle())

    assert again["status"] == "applied" and again["effects"]["question_item_id"] == card.id
    assert [row.id for row in Backlog(tmp_path / "backlog.jsonl").active()] == [card.id]


def test_a_stop_with_no_mission_run_is_refused_in_the_turn(tmp_path) -> None:
    write_continuous_config(tmp_path, enabled=True, objective=OBJECTIVE, open_ended=True)
    backend = _Backend({"to": "stop", "note": "停放以免重复调度。"},
                       {"to": "operator", "ask": {"question": QUESTION, "options": []}})
    remark = {"type": EventType.LIFE_OBSERVER_NOTE, "to": "manager", "stall_reason": "observer",
              "text": "规划者反复被调用，无法收尾。", "consult_reason": "the Observer says the work loops"}

    record = supervision.supervise(Manager(tmp_path, runner=backend), tmp_path, remark)

    assert backend.refusals and "nothing to park" in backend.refusals[0]
    assert record["status"] == "applied" and record["decision"]["handoff"] == "operator"


def test_a_planner_handoff_is_not_put_to_the_manager_while_the_campaign_waits_on_the_operator(
    tmp_path, monkeypatch,
) -> None:
    admitted: list[dict] = []
    monkeypatch.setattr(supervision, "_admit", lambda _manager, _root, event: admitted.append(event) or True)
    manager = SimpleNamespace(runner=SimpleNamespace(fork=lambda: None))
    idle = {"type": EventType.LIFE_PLANNER_IDLE, "handoff": "manager", "reason": "请关闭持续模式。"}
    assert supervision.schedule_supervision(manager, tmp_path, idle) is True

    backlog = Backlog(tmp_path / "backlog.jsonl")
    card, _new = planning_slot_card(backlog, OBJECTIVE)
    backlog.update(card.id, pending_question=QUESTION)

    assert supervision.schedule_supervision(manager, tmp_path, idle) is False
    assert len(admitted) == 1
