"""Tests for Planner checklist authoring (``checklist_ops`` parse + apply)."""
from __future__ import annotations

import json

from argus_skill.planner.planner import parse_planner_text


def _planner_json(**over):
    base = {
        "project_done": False,
        "reason": "next batch",
        "waiting": False,
        "waiting_reason": "",
        "new_tasks": [{
            "title": "t", "impact_score": 5, "impact_area": "correctness",
            "evidence": "e", "scope": "bounded", "objective": "o",
        }],
    }
    base.update(over)
    return json.dumps(base)


def test_absent_checklist_ops_defaults_to_empty():
    v = parse_planner_text(_planner_json())
    assert v.checklist_ops == []


def test_checklist_ops_parsed():
    v = parse_planner_text(_planner_json(checklist_ops=[
        {"op": "add", "stage": "simulate", "id": "simulate.seeds",
         "statement": "run >=3 seeds", "evidence_hint": "runs/"},
        {"op": "bogus", "stage": "x", "id": "y"},          # dropped (unknown op)
        {"op": "remove", "stage": "", "id": "z"},          # dropped (no stage)
    ]))
    assert len(v.checklist_ops) == 1
    op = v.checklist_ops[0]
    assert op["op"] == "add" and op["stage"] == "simulate" and op["id"] == "simulate.seeds"


def test_checklist_ops_survive_waiting_and_no_task_paths():
    # waiting verdict
    v = parse_planner_text(_planner_json(
        waiting=True, new_tasks=[],
        waiting_contract={
            "blocker_fingerprint": "job:test",
            "recheck_condition": "the job reaches a terminal state",
            "recheck_token": "running-v1",
            "allow_verification_probe": False,
            "recheck_after_seconds": 0,
        },
        checklist_ops=[{"op": "seed", "stage": "scope", "id": ""}],
    ))
    assert v.waiting is True
    assert v.checklist_ops and v.checklist_ops[0]["op"] == "seed"
