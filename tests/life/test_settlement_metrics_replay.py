"""Replay of s-efe9deac, 10-27 00:32:48: CARE (96a7) settled, the Planner followed.

The settlement and the follow-up verdict each appended a ``goal.*`` row to
the shared metrics log, and every Prometheus scrape rendered three
``argus_goal_*`` ratios plus a cost per accepted task. Nothing read them. The
fixture keeps the two events and the metric rows around them.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from argus.core.metrics import METRICS_FILE, metrics_snapshot, render_prometheus
from argus.core.planner_verdict import PlannerVerdictStatus
from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.life.supervisor._mission_execution_helpers import _MissionRunState

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "s_efe9deac" / "settlement_96a7_metrics.json").read_text(encoding="utf-8")
)


def _metric_names(root: Path) -> list[str]:
    path = root / METRICS_FILE
    if not path.exists():
        return []
    return [json.loads(line)["name"] for line in path.read_text(encoding="utf-8").splitlines()]


def test_settling_care_and_planning_after_it_write_no_goal_rows(tmp_path: Path) -> None:
    settled, verdict = FIXTURE["settlement"], FIXTURE["verdict"]
    memory = LifeMemory.open(tmp_path / "projects" / "s-efe9deac")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=SimpleNamespace(manager=None),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=True, open_ended=True),
    )
    supervisor._evolve_runtime_skills_after_mission = lambda **_kwargs: None
    supervisor._reflect_after_mission = lambda _state: {}
    item = BacklogItem.new(title=settled["title"], objective=settled["title"])
    item.id = settled["item_id"]
    usage = SimpleNamespace(
        cost_usd=settled["cost_usd"], known_cost_usd=settled["known_cost_usd"],
        pricing_status=settled["pricing_status"],
    )
    state = _MissionRunState(
        item=item, status=settled["status"], success=settled["success"],
        elapsed=settled["elapsed_seconds"], outcome=SimpleNamespace(),
        cost_sink=SimpleNamespace(usage_summary=lambda: usage),
    )
    state.completion_delivery = {"result": {
        "item_id": item.id, "status": settled["status"], "success": settled["success"],
    }}

    result = supervisor._emit_mission_outcome_and_build_result(state)
    assert supervisor._emit_planner_verdict(
        status=PlannerVerdictStatus(verdict["status"]),
        reason=verdict["reason"],
        completion_kind=verdict["completion_kind"],
        resume_outcome=False,
        cycle=verdict["cycle"],
        project_done=verdict["project_done"],
        task_count=verdict["task_count"],
        enqueued_tasks=verdict["enqueued_tasks"],
        skipped_duplicate_tasks=verdict["skipped_duplicate_tasks"],
    )

    assert result["cost_usd"] == settled["cost_usd"]
    assert not [name for name in _metric_names(tmp_path) if name.startswith("goal.")]


def test_recorded_goal_rows_are_ignored_and_never_rendered(tmp_path: Path) -> None:
    rows = FIXTURE["metrics"]
    assert {row["name"] for row in rows} == {"provider.call", "goal.mission", "goal.planning"}
    (tmp_path / METRICS_FILE).write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8",
    )

    snapshot = metrics_snapshot(root=tmp_path, now=FIXTURE["verdict"]["ts"] + 1)
    prometheus = render_prometheus(snapshot)

    assert "goal" not in snapshot
    assert snapshot["cost"] == {"total_usd": round(sum(
        float(row["fields"].get("cost_usd") or 0.0) for row in rows if row["name"] == "provider.call"
    ), 4)}
    assert "argus_goal_" not in prometheus
    assert "argus_cost_per_passed_task_usd" not in prometheus
    assert "argus_cost_usd_total " in prometheus
