"""Tests for the deterministic process-ledger extractor (read-only self-distillation step 1)."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.life.process_distill import (
    extract_mission_feats,
    extract_process_ledger,
)


def _write_events(tmp_path: Path, events: list[dict]) -> Path:
    p = tmp_path / "events.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return tmp_path


def test_segments_missions_and_counts_rounds(tmp_path):
    proj = _write_events(tmp_path, [
        {"type": "life.mission.started", "item_id": "m1"},
        {"type": "round.start", "round": "1"},
        {"type": "checks.done", "round": "1", "text": "checks: 1/1 pass"},
        {"type": "life.mission.completed", "item_id": "m1", "success": "True",
         "status": "done", "rounds": "1", "cost_usd": "0.5"},
        {"type": "life.mission.started", "item_id": "m2"},
        {"type": "life.mission.completed", "item_id": "m2", "success": "False",
         "status": "blocked", "rounds": "2", "cost_usd": "1.0"},
    ])
    led = extract_process_ledger(proj)
    assert led["n_missions"] == 2
    assert led["success_rate"] == 0.5
    assert led["total_cost_usd"] == 1.5
    ids = {m["id"] for m in led["missions"]}
    assert ids == {"m1", "m2"}


def test_check_fail_streak(tmp_path):
    proj = _write_events(tmp_path, [
        {"type": "life.mission.started", "item_id": "m1"},
        {"type": "checks.done", "round": "1", "text": "checks: 0/2 pass"},
        {"type": "checks.done", "round": "2", "text": "checks: 0/2 pass"},
        {"type": "checks.done", "round": "3", "text": "checks: 0/2 pass"},
        {"type": "checks.done", "round": "4", "text": "checks: 2/2 pass"},
        {"type": "life.mission.completed", "item_id": "m1", "success": "True",
         "status": "done", "rounds": "4", "cost_usd": "0"},
    ])
    [m] = extract_mission_feats(proj / "events.jsonl")
    assert m.max_check_fail_streak == 3


def test_forward_progress_contradiction(tmp_path):
    # forward_progress=True while the round status is NOT done == the crux incentive contradiction
    proj = _write_events(tmp_path, [
        {"type": "life.mission.started", "item_id": "m1"},
        {"type": "round.review.completed", "round_index": "1", "status": "continue",
         "failure_cause": "execution_mistake",
         "planner_report": "{'forward_progress': True, 'headline': 'x'}",
         "checklist": "[{'item': 'a', 'satisfied': False}]"},
        {"type": "round.review.completed", "round_index": "2", "status": "done",
         "planner_report": "{'forward_progress': True}"},
        {"type": "life.mission.completed", "item_id": "m1", "success": "False",
         "status": "blocked", "rounds": "2", "cost_usd": "0"},
    ])
    [m] = extract_mission_feats(proj / "events.jsonl")
    assert m.n_fp_contradictions == 1          # round 1 contradicts, round 2 (done) does not
    assert m.failure_causes == ["execution_mistake"]


def test_harvests_lessons_and_recurring(tmp_path):
    lesson = "environment parity requires the real blackwell wheels"
    proj = _write_events(tmp_path, [
        {"type": "life.mission.started", "item_id": "m1"},
        {"type": "round.review.completed", "round_index": "1", "status": "blocked",
         "failure_cause": "skill_gap", "mission_lesson": lesson},
        {"type": "life.mission.completed", "item_id": "m1", "success": "False",
         "status": "blocked", "rounds": "1", "cost_usd": "0"},
        {"type": "life.mission.started", "item_id": "m2"},
        {"type": "round.review.completed", "round_index": "1", "status": "blocked",
         "failure_cause": "skill_gap", "mission_lesson": lesson.upper()},  # same lesson, diff case
        {"type": "life.mission.completed", "item_id": "m2", "success": "False",
         "status": "blocked", "rounds": "1", "cost_usd": "0"},
    ])
    led = extract_process_ledger(proj)
    assert led["n_mission_lessons"] == 2
    # same lesson re-discovered across 2 missions == an un-internalized process gap
    assert any(v == 2 for v in led["recurring_lessons"].values())
    assert led["failure_cause_hist"]["skill_gap"] == 2


def test_stalls_and_nudges(tmp_path):
    proj = _write_events(tmp_path, [
        {"type": "life.mission.started", "item_id": "m1"},
        {"type": "round.stall", "round_index": "2", "semantic_stall_streak": "1"},
        {"type": "engineer.failure_nudge", "round": "2"},
        {"type": "life.mission.completed", "item_id": "m1", "success": "False",
         "status": "blocked", "rounds": "3", "cost_usd": "0"},
    ])
    [m] = extract_mission_feats(proj / "events.jsonl")
    assert m.n_stalls == 1
    assert m.n_failure_nudges == 1


def test_harvests_process_lessons(tmp_path):
    # the PRIMARY per-mission channel: reviewer emits process_lesson on round.review.completed
    proj = _write_events(tmp_path, [
        {"type": "life.mission.started", "item_id": "m1"},
        {"type": "round.review.completed", "round_index": "1", "status": "done",
         "process_lesson": "detect a repeated environmental failure within 2 rounds, do not retry blindly"},
        {"type": "life.mission.completed", "item_id": "m1", "success": "True",
         "status": "done", "rounds": "1", "cost_usd": "0"},
    ])
    led = extract_process_ledger(proj)
    assert led["n_process_lessons"] == 1
    assert "detect a repeated environmental failure" in led["process_lessons"][0]


def test_missing_events_file(tmp_path):
    led = extract_process_ledger(tmp_path)
    assert led["n_missions"] == 0
    assert led["missions"] == []
