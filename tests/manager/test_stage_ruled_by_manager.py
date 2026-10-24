"""A stage question goes to the Manager's session; the operator decides only when they asked to."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.apps._inbox_protocol import claim_inbox_message
from argus.core.autonomy import read_autonomous_assumptions
from argus.core.models import ReviewDecision
from argus.core.transcript import read_turns
from argus.life.memory import BacklogItem, LifeMemory
from argus.manager import Manager, front_door
from argus.skills.vertical_select import persist_vertical
from argus.webapi import manager_bridge, manager_state
from tests.hand_off import manager_runner, manager_settles, stage

pytest.importorskip("fastapi")
pytestmark = pytest.mark.usefixtures("optimize_vertical")

ROUNDING = {"chose": "floor, as format_kib does", "instead_of": "round half up, as format_gib does"}


def _project(tmp_path: Path, **route) -> tuple[Manager, Path]:
    manager_state._STATES.clear()
    root = tmp_path / "projects" / "s-stage"
    persist_vertical(root, "optimize_lab", **route)
    (root / "backlog.jsonl").write_text("", encoding="utf-8")
    return Manager(project_root=root, execution_workdir=root, runner=object()), root


def _asks_the_manager(manager: Manager, root: Path):
    return manager.decide_stage_transition(
        review=ReviewDecision(status="done", reason="Reviewer checked the stage.", next_action=""),
        project_root=root,
        run_exec=stage(to="manager", summary="Which rounding rule does format_mib use?",
                       note="floor like format_kib, or round like format_gib"),
    )


def _team_reads(root: Path) -> list[str]:
    """What the team's inbox holds, as the team claims it."""
    claims = iter(lambda: claim_inbox_message(root, current_stage=_stage_now(root)), None)
    return [claim.text for claim in claims]


def _stage_now(root: Path) -> str:
    return json.loads((root / ".argus" / "PIPELINE_STATE.json").read_text())["current_stage"]


def _rules(manager: Manager, monkeypatch, hand) -> None:
    runner = manager_runner(manager, hand=hand, reply="Ruled.")
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: runner)


def _question(root: Path) -> str:
    return json.loads((root / ".argus" / "PIPELINE_STATE.json").read_text()).get("stage_question", "")


def _team_report(root: Path) -> None:
    """The turn the web server runs when the team pushes its question."""
    manager_bridge.manager_team_report(
        "s-stage", _question(root), message_id="stage-question-1", event_fields={},
        global_root=root.parent.parent,
    )


def test_with_no_web_server_the_managers_next_turn_rules(tmp_path: Path, monkeypatch) -> None:
    """CLI-only use: nobody runs the Manager's turn at once, so the question
    goes with its next one."""
    manager, root = _project(tmp_path)
    _asks_the_manager(manager, root)
    runner = manager_runner(manager, hand=manager_settles("Floor; keep going.", stage={"move": "hold"}), reply="Ruled.")
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: runner)

    manager_bridge.manager_message("s-stage", "how is it going?", global_root=root.parent.parent)

    assert "Which rounding rule does format_mib use?" in runner.turns[0]
    assert "Floor; keep going." in _team_reads(root)[0]
    assert not _question(root)


def test_the_manager_rules_and_the_team_reads_the_choice_it_made(tmp_path: Path, monkeypatch) -> None:
    manager, root = _project(tmp_path)
    backlog = LifeMemory.open(root).backlog
    backlog.add(BacklogItem(id="t-format", ts=0.0, title="format_mib", objective="Add format_mib."))
    backlog.mark_running("t-format")
    _asks_the_manager(manager, root)
    backlog.mark_done("t-format")  # the task ends before the Manager rules
    _rules(manager, monkeypatch, manager_settles(
        "Floor like the neighbouring format_kib; keep working on this stage.",
        stage={"move": "hold"}, assumptions=[ROUNDING],
    ))

    _team_report(root)

    assert _stage_now(root) == "setup"
    [message] = _team_reads(root)
    assert "hold" in message and "chose floor, as format_kib does" in message
    [assumed] = read_autonomous_assumptions(root)
    assert (assumed["reading"], assumed["source"], assumed["item_id"]) == (ROUNDING["chose"], "stage", "t-format")
    assert "stage_question_task" not in json.loads((root / ".argus" / "PIPELINE_STATE.json").read_text())


def test_the_managers_advance_moves_the_stage(tmp_path: Path, monkeypatch) -> None:
    manager, root = _project(tmp_path)
    _asks_the_manager(manager, root)
    _rules(manager, monkeypatch, manager_settles("The evidence holds; move on.", stage={"move": "advance"}))

    _team_report(root)

    assert _stage_now(root) != "setup"


def test_the_operator_who_asked_to_take_part_is_asked_and_their_answer_is_carried_out(
    tmp_path: Path, monkeypatch,
) -> None:
    manager, root = _project(tmp_path, operator_decides_stages=True)
    _asks_the_manager(manager, root)
    assert _question(root).endswith("The operator asked to take part in stage decisions.")
    ask = ("hand_off", {"to": "operator", "summary": "The stage needs your call.", "assumptions": [], "ask": {
        "question": "Which rounding rule should format_mib use?",
        "options": [{"label": "Floor, like format_kib"}, {"label": "Round, like format_gib"}],
    }})
    ruling = manager_settles("Floor, as the operator chose.", stage={"move": "hold"})
    _rules(manager, monkeypatch, lambda message: ask if message.startswith("Team report") else ruling)

    _team_report(root)
    [turn] = [t for t in read_turns(root) if str(t.get("message_id", "")).startswith("stage-question-")]
    assert "Which rounding rule should format_mib use?" in turn["text"] and "Floor, like format_kib" in turn["text"]
    assert _team_reads(root) == []

    manager_bridge.manager_message("s-stage", "Floor, like format_kib", global_root=root.parent.parent)

    [message] = _team_reads(root)
    assert "Floor, as the operator chose." in message
