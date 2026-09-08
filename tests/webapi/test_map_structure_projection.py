"""Structural enrichment of the map projection: plan-identity task fields,
plan supersede events, and portfolio formation visibility."""

from __future__ import annotations

import json
from pathlib import Path

from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.team import task_board
from argus_skill.webapi.map_feed import MapFeed
from argus_skill.webapi.map_view import normalize_events, read_map

SECRET = "hunter2secret999"


def append(life: Path, event: dict) -> None:
    with (life / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def plain_session(root: Path, sid: str = "s-structure"):
    write_session_meta(root, SessionMeta(id=sid, created=1, last_active=1))
    life = root / "projects" / sid
    memory = LifeMemory.open(life)
    return sid, life, memory


def team_sample(root: Path, sid: str = "s-structure-team"):
    workdir = root / "workspaces" / sid
    workdir.mkdir(parents=True)
    write_session_meta(root, SessionMeta(id=sid, workdir=str(workdir), created=1))
    life = root / "projects" / sid
    memory = LifeMemory.open(life)
    memory.backlog.add(BacklogItem(
        id="parent", ts=1, title="Choose a research idea",
        objective="Compare routes", status="running",
    ))
    board = workdir / ".argus" / "teams" / "research-team"
    task_board.form(board, [
        {"task_id": "route-01", "role": "idea-route", "title": "Investigate route one",
         "objective": "Read primary sources for route one."},
    ])
    append(life, {"type": "life.mission.started", "item_id": "parent", "ts": 2})
    append(life, {"type": "idea.portfolio.formed", "item_id": "parent",
                  "team_root": str(board), "ts": 10, "width": 12, "route_count": 12,
                  "task_count": 24})
    return sid, life, board


def formations(value: dict) -> list[dict]:
    return [e for e in value["events"] if e["type"] == "idea.portfolio.formed"]


def test_tasks_carry_plan_structure_fields_redacted_and_clipped(tmp_path: Path) -> None:
    sid, life, memory = plain_session(tmp_path)
    memory.backlog.add(BacklogItem(
        id="a", ts=1, title="Coverage", objective="Compare methods",
        node_key="analysis/coverage", parallel_safe=True,
        owns_paths=["src/analysis/", f"notes api_key = {SECRET}"],
        superseded_by_plan_id="plan-2",
        superseded_reason=f"replaced: api_key = {SECRET} " + "x" * 7000,
    ))
    task = read_map(sid, tmp_path, life)["tasks"][0]
    assert task["node_key"] == "analysis/coverage"
    assert task["parallel_safe"] is True
    assert task["superseded_by_plan_id"] == "plan-2"
    assert task["superseded_reason"].startswith("replaced:")
    assert len(task["superseded_reason"]) <= 6000
    assert SECRET not in task["superseded_reason"]
    assert "<REDACTED:secret>" in task["superseded_reason"]
    assert isinstance(task["owns_paths"], list)
    assert "src/analysis/" in task["owns_paths"]
    assert all(SECRET not in path for path in task["owns_paths"])


def test_supersede_event_passes_the_filter_with_lean_passthrough() -> None:
    rows = [
        {"type": "life.plan.node.superseded", "item_id": "a", "ts": 5,
         "reason": f"retired: api_key = {SECRET}",
         "superseded_by_plan_id": "plan-2", "source": "planner"},
        {"type": "life.plan.node.superseded", "item_id": "a", "ts": 6},
    ]
    events = normalize_events(rows, {"a"})
    assert [e["type"] for e in events] == ["life.plan.node.superseded"] * 2
    assert events[0]["item_id"] == "a"
    assert events[0]["superseded_by_plan_id"] == "plan-2"
    assert events[0]["reason"].startswith("retired:")
    assert SECRET not in events[0]["reason"]
    assert "<REDACTED:secret>" in events[0]["reason"]
    assert SECRET not in events[0]["text"]
    # The normalized shape stays lean: optional keys only appear when present.
    assert "reason" not in events[1]
    assert "superseded_by_plan_id" not in events[1]


def test_supersede_event_reaches_the_live_feed(tmp_path: Path) -> None:
    sid, life, memory = plain_session(tmp_path)
    memory.backlog.add(BacklogItem(id="a", ts=1, title="Coverage", objective="Compare"))
    append(life, {"type": "life.plan.node.superseded", "item_id": "a", "ts": 5,
                  "reason": "Planner retired the node",
                  "superseded_by_plan_id": "plan-9", "source": "planner"})
    value = read_map(sid, tmp_path, life)
    matching = [e for e in value["events"] if e["type"] == "life.plan.node.superseded"]
    assert len(matching) == 1
    assert matching[0]["reason"] == "Planner retired the node"
    assert matching[0]["superseded_by_plan_id"] == "plan-9"


def test_portfolio_formation_is_forwarded_once_with_stable_identity(tmp_path: Path) -> None:
    sid, life, board = team_sample(tmp_path)
    value = read_map(sid, tmp_path, life)
    formed = formations(value)
    assert len(formed) == 1
    event = formed[0]
    assert event["item_id"] == "parent"
    assert event["ts"] == 10
    assert event["team_id"] == "research-team"
    assert event["width"] == 12
    assert event["text"] == ""
    # Formation is journal history: it must never be broadcast as removable,
    # so its id must stay outside map_feed's "team:" removability namespace.
    assert not event["id"].startswith("team:")
    again = formations(read_map(sid, tmp_path, life))[0]
    assert again["id"] == event["id"]
    assert again["revision"] == event["revision"]


def test_formation_survives_incremental_reads_without_duplication(tmp_path: Path) -> None:
    sid, life, board = team_sample(tmp_path)
    feed = MapFeed()
    first = feed.read(sid, tmp_path, life)
    assert len(formations(first)) == 1
    # Team-side progress invalidates the projection but must not resend or
    # remove the unchanged formation observation.
    task_board.claim_top(board, "worker-1", now=20)
    incremental = feed.read(sid, tmp_path, life, first["cursor"])
    assert incremental["incremental"] is True
    assert formations(incremental) == []
    assert incremental["removed_event_ids"] == []
    # Journal-side progress must not resend it either.
    append(life, {"type": "round.start", "item_id": "parent", "ts": 30})
    tail = feed.read(sid, tmp_path, life, incremental["cursor"])
    assert tail["incremental"] is True
    assert formations(tail) == []
    # A repeated formation record for the same parent keeps one observation.
    append(life, {"type": "idea.portfolio.formed", "item_id": "parent",
                  "team_root": str(board), "ts": 40, "width": 12})
    repeated = feed.read(sid, tmp_path, life)
    assert [e["id"] for e in formations(repeated)] == [e["id"] for e in formations(first)]
