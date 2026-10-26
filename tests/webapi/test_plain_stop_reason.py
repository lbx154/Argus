"""Pages read why a task stopped in plain words, never the host's routing record.

Trial s-efe9deac (2026-10-26): the progress banner of a Chinese project showed
"Nothing changed since the engineer and the planner last held this work, and
nothing new arrived from outside, so it went one rung up, to the manager." —
the host's internal record, in English, naming the roles it climbed through.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argus.core.session import SessionMeta, write_session_meta
from argus.engineer.round_routing import climb_record
from argus.life.memory import BacklogItem, LifeMemory
from argus.webapi import project_state
from argus.webapi.map_view import read_map

CLIMB = climb_record(["engineer", "planner"], "manager").reason
RAW = ("Nothing changed", "rung", "planner", "manager", "规划者", "经理")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    workdir = tmp_path / "work"
    workdir.mkdir()
    write_session_meta(tmp_path, SessionMeta(
        id="s-plain", display_name="形成新机制路线", objective="完成 LLM 强化学习原创研究",
        created=100, last_active=100, cwd=str(workdir), workdir=str(workdir),
    ))
    life_dir = tmp_path / "projects/s-plain"
    backlog = LifeMemory.open(life_dir).backlog
    zh = BacklogItem.new(title="形成交互式强化学习主动获取信息的新机制路线", objective="在 Idea 内发展一条原创候选")
    en = BacklogItem.new(title="Develop an active information mechanism", objective="Find an original candidate")
    for item in (zh, en):
        backlog.add(item)
        backlog.update(item.id, status="failed", finished_ts=200.0, last_error=CLIMB)
    return tmp_path, life_dir


def _reasons(rows: list[dict]) -> dict[str, str]:
    return {row.get("title") or row.get("objective"): row["last_error"] for row in rows}


@pytest.mark.parametrize("compact", [True, False])
def test_the_snapshot_says_why_a_task_stopped_in_the_task_language(project, compact: bool) -> None:
    root, _life_dir = project
    reasons = _reasons(project_state.build_snapshot("s-plain", global_root=root, compact=compact)["backlog"])

    assert reasons["形成交互式强化学习主动获取信息的新机制路线"] == "这条路没有新进展，团队正在换个思路。"
    assert reasons["Develop an active information mechanism"] == (
        "This path is making no new progress, so the team is trying a different approach."
    )
    for said in reasons.values():
        assert not any(word in said for word in RAW), said


def test_the_map_and_the_record_each_keep_their_own_words(project) -> None:
    root, life_dir = project
    tasks = read_map("s-plain", root, life_dir, include_events=False)["tasks"]

    assert {task["summary"] for task in tasks} == {
        "这条路没有新进展，团队正在换个思路。",
        "This path is making no new progress, so the team is trying a different approach.",
    }
    # The stored record stays the host's, for the roles that read it.
    assert {item.last_error for item in LifeMemory.open(life_dir).backlog.history()} == {CLIMB}
