"""The Manager's rung of the ladder is process debugging (user, 2026-10-26: "卡住时 还是需要经理对流程来debug").

Work that climbs to the Manager, or that a role hands to it, means the process
is stuck. The Manager gets a "process stuck" brief and answers with a
diagnosis and one of three moves: fixed it (the holder retries), the Planner
replans around the diagnosis, or the person decides what only they can. It
may not decide what the person wants, author the plan, accept a candidate, or
declare a stage or the project done.
"""
from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.event_catalog import EventType
from argus.core.handoff import HandOffActions
from argus.life.memory import BacklogItem, LifeMemory
from argus.manager import supervision
from argus.manager.process_debug import process_stuck_brief, record_diagnosis

_REFS = [{"path": "events.jsonl"}]


def _climbed(item_id: str = "t1") -> dict:
    return {
        "type": EventType.ROUND_REVIEW_COMPLETED, "item_id": item_id, "status": "manager",
        "review_source": "unchanged_return", "reason": "Nothing changed since the Engineer and the Reviewer.",
    }


@pytest.fixture()
def admitted(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(supervision, "_admit", lambda _manager, _root, event: seen.append(event) or True)
    manager = SimpleNamespace(runner=SimpleNamespace(fork=lambda: None))

    def admit(root: Path, event: dict) -> dict | None:
        seen.clear()
        supervision.schedule_supervision(manager, root, event)
        return seen[-1] if seen else None

    return admit


def test_work_that_climbed_to_the_manager_is_a_stuck_process(tmp_path, admitted) -> None:
    event = admitted(tmp_path, _climbed())
    assert event["process_stuck"] is True
    assert "the process is stuck" in event["consult_reason"]


def test_a_planner_handing_to_the_manager_is_a_stuck_process_not_a_stage_question(tmp_path, admitted) -> None:
    event = admitted(tmp_path, {
        "type": EventType.LIFE_PLANNER_IDLE, "handoff": "manager",
        "reason": "The GPU job fails with the same CUDA OOM on every route.",
    })
    assert event["process_stuck"] is True
    assert "CUDA OOM" in event["consult_reason"]


def _actions(root: Path, event: dict) -> HandOffActions:
    observation = SimpleNamespace(facts={"evidence_refs": _REFS, "recent_events": []})
    return HandOffActions("supervise", check=partial(supervision._check, root=root, event=event,
                                                     observation=observation), operator_available=True)


def _call(actions: HandOffActions, to: str, **fields) -> str:
    payload = {"to": to, "summary": "The job ran out of GPU memory.", "evidence_refs": ["events.jsonl"],
               "assumptions": [], **fields}
    try:
        actions.dispatch("hand_off", payload)
    except ValueError as refusal:
        return str(refusal)
    return ""


@pytest.mark.parametrize(("to", "fields"), [
    ("team", {"note": "Lowered the batch to fit the 24G card; retry."}),
    ("planner", {"note": "实验因显存不足失败，按单卡 24G 重新规划"}),
    ("operator", {"ask": {"question": "要不要申请一张更大的卡？", "options": [{"label": "申请"}, {"label": "不申请"}]}}),
])
def test_the_manager_may_fix_and_retry_hand_the_planner_its_diagnosis_or_ask_the_person(
    tmp_path, monkeypatch, to, fields,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    event = {**_climbed(), "process_stuck": True}
    assert _call(_actions(tmp_path, event), to, **fields) == ""


@pytest.mark.parametrize(("to", "fields", "says"), [
    # Stopping the work is not a process fix.
    ("stop", {"note": "Give up."}, "debug the process"),
    # A move without the diagnosis leaves nobody knowing the cause.
    ("team", {}, "diagnosis"),
    ("planner", {}, "diagnosis"),
    # Which reading of the person's words holds is theirs, not the Manager's.
    ("team", {"note": "Fixed the path.", "assumptions": [{"chose": "read X as Y", "instead_of": "read X as Z"}]},
     "theirs to decide"),
])
def test_the_manager_may_not_decide_the_work_at_its_rung(tmp_path, monkeypatch, to, fields, says) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    event = {**_climbed(), "process_stuck": True}
    assert says in _call(_actions(tmp_path, event), to, **fields)


def test_without_the_person_the_manager_may_choose_a_reading_and_it_is_recorded(tmp_path, monkeypatch) -> None:
    """Only under "别问我" (the person cannot be asked) does the Manager decide in their place."""
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    event = {**_climbed(), "process_stuck": True}
    reading = [{"chose": "use the public split", "instead_of": "wait for the private split"}]
    assert _call(_actions(tmp_path, event), "team", note="Switched to the public split.", assumptions=reading) == ""


def test_the_brief_names_what_repeated_the_errors_and_the_last_changes(tmp_path) -> None:
    rows = [
        {"type": "life.mission.failed", "item_id": "t1", "error": "CUDA out of memory"},
        {"type": "round.main.completed", "item_id": "t1", "summary": "Retried with the same batch."},
        {"type": "life.mission.failed", "item_id": "t1", "error": "CUDA out of memory"},
        {"type": "life.mission.failed", "item_id": "other", "error": "unrelated"},
    ]
    (tmp_path / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    brief = process_stuck_brief(tmp_path, {**_climbed(), "consult_reason": "the work climbed to you"})
    assert "What repeated:\n- 2x CUDA out of memory" in brief
    assert "Retried with the same batch." in brief and "unrelated" not in brief
    assert "not yours to decide here" in brief


def test_a_fixed_cause_resumes_the_stuck_task_with_the_diagnosis_on_its_record(tmp_path) -> None:
    backlog = LifeMemory.open(tmp_path).backlog
    task = backlog.add(BacklogItem.new(title="Train the probe", objective="train"))
    backlog.update(task.id, park_reason="research_open")
    record = {"id": "s1", "trigger": {"item_id": task.id, "process_stuck": True},
              "decision": {"handoff": "team", "reason": "Out of GPU memory.",
                           "directive": "Lowered the batch to 8 for the 24G card."}}

    effects = record_diagnosis(tmp_path, record)

    assert effects == {"diagnosis": "Lowered the batch to 8 for the 24G card.", "move": "fixed_and_retry",
                       "resumed_item_id": task.id}
    resumed = next(row for row in backlog.active() if row.id == task.id)
    assert resumed.status == "pending"
    assert resumed.notes == "Manager's diagnosis: Lowered the batch to 8 for the 24G card."


def test_a_diagnosis_for_the_planner_leaves_the_task_for_the_planner(tmp_path) -> None:
    backlog = LifeMemory.open(tmp_path).backlog
    task = backlog.add(BacklogItem.new(title="Train the probe", objective="train"))
    backlog.update(task.id, park_reason="research_open")
    record = {"id": "s2", "trigger": {"item_id": task.id, "process_stuck": True},
              "decision": {"handoff": "planner", "reason": "OOM", "directive": "按单卡 24G 重新规划"}}

    assert record_diagnosis(tmp_path, record)["move"] == "to_planner"
    assert next(row for row in backlog.active() if row.id == task.id).status == "parked"
