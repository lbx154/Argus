"""Asked how it is going while the team works, the Manager gets the record, not only the goal.

Replays the hosted trial s-dacac579 (2026-10-29): an hour after their last
message the operator wrote the same "继续做…" again. The Manager was told only
that task 2873b58e7a44 was running, with its objective, and answered "会继续按这个
目标推进：完成真实实验和独立检查，再交付 CVPR 格式的论文 PDF。" The record held
more: the task had restarted twice, the Reviewer had sent round 1 back at
00:50:56 with its reason, round 3 was running since 01:00:56, and the
workspace had new RESEARCH_NOTES.md, METHOD.md and smoke-run outputs.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from argus.core.session import SessionMeta, write_session_meta
from argus.life.memory import BacklogItem, LifeMemory
from argus.manager import front_door
from argus.manager.team_progress import team_progress
from argus.webapi import manager_bridge, manager_state
from tests.hand_off import manager_runner, manager_settles

NUDGE = "继续做：在已调查的三个视觉方向里选一个最有把握的，按 CVPR 论文的标准做真实实验（有基线、对照和消融），写成 CVPR 格式的论文，最后给我 PDF。"
TITLE = "确定视觉研究方向并建立首个真实评测入口"
TASK = "2873b58e7a44"
SENT_BACK = "已选定计算机视觉研究方向并建立真实数据运行环境，但切换后的关键比较路径尚未在实际短跑中验证，需补齐后才能确认本轮完成。"
# Seconds before the nudge (01:09:51), as recorded in the trial's events.jsonl.
PREVIOUS_MESSAGE = 3938  # 00:04:13
RECORD = [
    (4012, {"type": "life.mission.started", "item_id": TASK, "title": TITLE}),           # 00:02:59
    (4011, {"type": "round.start", "item_id": TASK, "round_index": 1}),
    (3525, {"type": "life.mission.requeued", "item_id": TASK, "title": TITLE,           # 00:11:06
            "error": "orphaned: previous process did not finish"}),
    (3525, {"type": "life.mission.started", "item_id": TASK, "title": TITLE}),
    (3525, {"type": "round.start", "item_id": TASK, "round_index": 1}),
    (1866, {"type": "life.mission.requeued", "item_id": TASK, "title": TITLE,           # 00:38:45
            "error": "orphaned: previous process did not finish"}),
    (1864, {"type": "life.mission.started", "item_id": TASK, "title": TITLE}),
    (1853, {"type": "round.start", "item_id": TASK, "round_index": 1}),
    (1300, {"type": "engineer.progress", "kind": "tool_use", "agent_layer": "engineer", "item_id": TASK,
            "tool_name": "apply_patch", "text": "apply_patch: *** Begin Patch\n*** Add File: METHOD.md\n+# Method"}),
    (1278, {"type": "round.main.completed", "item_id": TASK, "round_index": 1, "exit_code": 0}),  # 00:48:33
    (1135, {"type": "round.review.completed", "item_id": TASK, "round_index": 1,                # 00:50:56
            "status": "engineer", "review_source": "reviewer", "reason": SENT_BACK}),
    (1135, {"type": "round.start", "item_id": TASK, "round_index": 2}),
    (536, {"type": "round.main.completed", "item_id": TASK, "round_index": 2, "exit_code": 0}),   # 01:00:55
    (535, {"type": "round.start", "item_id": TASK, "round_index": 3}),                            # 01:00:56
]
WRITTEN = ["RESEARCH_NOTES.md", "METHOD.md", ".argus/evidence/experiment/switch-smoke-20261029-1/summary.png"]


@pytest.fixture(autouse=True)
def _daemon_runs_the_project(monkeypatch):
    monkeypatch.setattr(front_door, "_daemon_running", lambda _life_dir: True)


def _trial(root: Path, sid: str, now: float) -> tuple[Path, Path]:
    life = root / "projects" / sid
    workspace = root / "workspace" / sid
    life.mkdir(parents=True)
    workspace.mkdir(parents=True)
    write_session_meta(root, SessionMeta(id=sid, created=now - 9000, last_active=now, cwd=str(life),
                                         workdir=str(workspace), launch_cwd=str(workspace)))
    with (life / "events.jsonl").open("w", encoding="utf-8") as log:
        for index, (before, event) in enumerate(RECORD):
            log.write(json.dumps({**event, "ts": now - before, "event_id": f"e{index}"}, ensure_ascii=False) + "\n")
    (life / "transcript.jsonl").write_text(
        json.dumps({"ts": now - PREVIOUS_MESSAGE, "role": "operator", "text": NUDGE}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    memory = LifeMemory.open(life)
    memory.backlog.add(BacklogItem.new(
        title=TITLE, objective="围绕一个自主选择的计算机视觉研究方向开展原创研究。", item_id=TASK))
    running = memory.backlog.claim_next()
    assert running is not None and running.id == TASK
    for relative in [*WRITTEN, "data/old.csv", ".argus/PIPELINE_STATE.json"]:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    old = now - PREVIOUS_MESSAGE - 600
    os.utime(workspace / "data" / "old.csv", (old, old))
    manager_state._STATES.clear()
    return life, workspace


def test_a_nudge_while_the_team_works_reaches_the_manager_with_the_record(tmp_path, monkeypatch):
    sid = "s-status-facts"
    _trial(tmp_path, sid, time.time())
    runner = manager_runner(hand=manager_settles(), reply="第 1 轮的检查退回了……")
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_a: runner)

    manager_bridge.manager_message(sid, NUDGE, global_root=tmp_path)

    told = runner.turns[0]
    assert TITLE in told
    # The latest review's verdict, in the Reviewer's own words.
    assert f'the Reviewer sent it back to the Engineer for another round: "{SENT_BACK}"' in told
    # The round under way, and how long the task has been at it.
    assert "round 3 running since" in told
    assert "started" in told and "1 h 6 min ago" in told
    # What happened since the operator's previous message, restarts included.
    assert "Since the operator's previous message" in told and "1 h 5 min ago" in told
    assert "orphaned: previous process did not finish" in told
    assert "round 2 finished and was handed over" in told
    # The files written since then; a figure is named as one; older files are not.
    assert "RESEARCH_NOTES.md" in told and "METHOD.md" in told
    assert ".argus/evidence/experiment/switch-smoke-20261029-1/summary.png (figure)" in told
    # Neither an older file nor Argus's own state is the team's output.
    assert "old.csv" not in told and "PIPELINE_STATE" not in told


def test_the_record_told_to_the_manager(tmp_path, monkeypatch):
    """The block exactly as the Manager reads it (a prompt snapshot)."""
    monkeypatch.setenv("TZ", "UTC")  # the trial host's
    time.tzset()
    try:
        now = 1793236191.66  # 2026-10-29 01:09:51 in the trial's host time
        life, workspace = _trial(tmp_path, "s-status-snapshot", now)
        for relative in WRITTEN:
            mtime = now - 300 - WRITTEN.index(relative)
            os.utime(workspace / relative, (mtime, mtime))
        [item] = LifeMemory.open(life).backlog.active()

        told = team_progress(life, workspace, [item], NUDGE, now=now)
    finally:
        monkeypatch.undo()
        time.tzset()

    assert told == (
        "What Argus recorded about this work; a status question or a nudge is answered with these "
        "facts (team-handoff rules). Host time, now 01:09:51:\n"
        f"- task {TASK}, {TITLE}: started 00:02:59, 1 h 6 min ago; round 3 running since 01:00:56 (8 min).\n"
        f'  latest review (00:50:56, round 1): the Reviewer sent it back to the Engineer for another round: "{SENT_BACK}"\n'
        "Since the operator's previous message (00:04:13, 1 h 5 min ago):\n"
        f"- 00:11:06 task {TASK}'s run stopped before it finished (orphaned: previous process did not finish); it started again\n"
        f"- 00:38:45 task {TASK}'s run stopped before it finished (orphaned: previous process did not finish); it started again\n"
        "- 00:48:33 round 1 finished and was handed over\n"
        f'- 00:50:56 review of round 1: the Reviewer sent it back to the Engineer for another round: "{SENT_BACK}"\n'
        "- 00:50:56 round 2 started\n"
        "- 01:00:55 round 2 finished and was handed over\n"
        "- 01:00:56 round 3 started\n"
        "Files written in the workspace since then, newest first (3 of 3): RESEARCH_NOTES.md, METHOD.md, "
        ".argus/evidence/experiment/switch-smoke-20261029-1/summary.png (figure)"
    )


def test_the_handoff_rule_says_to_answer_a_status_question_with_the_record():
    rule = Path(front_door.__file__).resolve().parents[1] / "builtin_skills" / "manager" / "team-handoff" / "SKILL.md"
    text = " ".join(rule.read_text(encoding="utf-8").split())
    assert "When the operator asks how it is going, or nudges the team on" in text
    assert "Answer with those facts, not the goal: what was done" in text
    assert "what was found (the latest review's verdict and its reason" in text
    assert "what is next (the round under way and what the review asked for)" in text
    assert "Do not restate the goal or only promise to keep going." in text
