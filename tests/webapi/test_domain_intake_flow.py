"""The workflow-setup card, end to end: the Manager's session asks and settles it through its form."""
from __future__ import annotations

import json
import time
from pathlib import Path

from argus.core.session import SessionMeta, write_session_meta
from argus.life.memory import BacklogItem
from argus.manager import front_door
from argus.manager.domain_intake import handle_intake, intake_card, read_intake
from argus.webapi import manager_bridge, manager_state
from tests.hand_off import manager_runner, manager_settles

SID = "s-intake"
OPTIONS = ["Cultural education with cited conventions", "Date conversion only", "Both, conventions first"]
SETUP = {
    "action": "prepare", "name": "calendar_interpretation",
    "purpose": "Explain traditional calendar conventions with source-backed cultural context",
    "brief": "Scope: cultural education; inputs: a date and its convention; checks: a second date.",
    "title": "Build a reusable calendar workflow",
    "summary": "Explain calendar conventions with cited sources and reuse checks.",
}


def _offered(root: Path) -> Path:
    manager_state._STATES.clear()
    life = root / "projects" / SID
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text(json.dumps({"type": "mission.started", "ts": time.time()}) + "\n")
    (life / "backlog.jsonl").write_text("")
    write_session_meta(root, SessionMeta(id=SID, cwd=str(life), workdir=str(life)))
    handle_intake(life, "Interpret my calendar date", {"action": "offer"}, route="simple",
                  self_mode="reply", known_verticals=())
    return life


def _answer(root: Path, life: Path, option_id: str) -> dict:
    card = intake_card(read_intake(life))
    return manager_bridge.manager_message(SID, "", global_root=root,
                                          domain_answer={"id": card["id"], "option_id": option_id})


def _asks(message: str):
    if OPTIONS[0] in message:
        return manager_settles("The operator chose a scope.", workflow_setup=SETUP)
    return "hand_off", {"to": "operator", "summary": "The scope decides the sources.", "assumptions": [],
                        "ask": {"question": "What should the workflow cover?",
                                "options": [{"label": label} for label in OPTIONS]}}


def test_the_manager_asks_with_options_and_the_chosen_one_prepares_the_workflow(tmp_path, monkeypatch) -> None:
    life = _offered(tmp_path)
    runner = manager_runner(hand=_asks)
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: runner)
    dispatched = []

    def dispatch(mem, body, chat_state, *_args, **_kwargs):
        dispatched.append((body, chat_state["_approved_domain_decision"]))
        return mem.backlog.add(BacklogItem.new(title="calendar", objective=body)), False, None

    monkeypatch.setattr(manager_bridge, "_dispatch_team_mission", dispatch)

    # "Build a workflow" on the offer card: the Manager's session asks, with options.
    asked = _answer(tmp_path, life, "build")
    assert asked["reply"] == "What should the workflow cover?"
    assert [option["label"] for option in asked["decision_card"]["options"]] == OPTIONS
    assert "reusable workflow setup is under way" in runner.turns[0]
    assert read_intake(life)["phase"] == "clarifying"
    assert not dispatched

    # The operator picks the first option; the Manager settles the setup and it is dispatched.
    prepared = _answer(tmp_path, life, "option-1")
    assert OPTIONS[0] in runner.turns[1]
    assert prepared["kind"] == "task", prepared
    [(task, decision)] = dispatched
    assert decision.choice == "new" and decision.vertical == "calendar_interpretation"
    assert decision.require_independent_review
    assert "reusable vertical calendar_interpretation" in task
    assert SETUP["brief"] in task and OPTIONS[0] in task
    assert read_intake(life)["phase"] == "prepared"
