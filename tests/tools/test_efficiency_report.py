from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.tools.efficiency_report import efficiency_report, main


def _usage(call: str, label: str, cost: float, input_tokens: int, task: str = "", **fields) -> dict:
    return {"type": "usage.recorded", "call_id": call, "run_label": label, "mission_id": task,
            "input_tokens": input_tokens, "output_tokens": 10, "cost_usd": cost, "status": "completed", **fields}


def _turn(role: str, turn: int, input_tokens: int) -> dict:
    return {"type": "role.session.turn", "role": role, "turns_on_session": turn, "input_tokens": input_tokens}


def _review(task: str, status: str, **fields) -> dict:
    return {"type": "round.review.completed", "item_id": task, "status": status, **fields}


def _supervision(outcome: str, sid: str, **fields) -> dict:
    return {"type": f"life.manager.supervision.{outcome}", "supervision_id": sid, **fields}


# The older generation: one Planner turn that added no work, then two waits
# an older release logged as skipped calls (under the old name); they count nothing.
OLDER = [
    {"type": "life.planner.start"},
    _usage("p1", "planner.cycle0", 1.0, 100, cached_input_tokens=80, cache_write_tokens=20),
    _turn("planner", 1, 100),
    {"type": "life.planner.waiting"},
    {"type": "life.planner.waiting", "model_call_skipped": True},
    {"type": "life.planner.waiting", "model_call_skipped": True},
]

CURRENT = [
    # A Planner turn (and its repair) that scheduled work, then one that added none.
    {"type": "life.planner.start"},
    _usage("p2", "planner.cycle1", 2.0, 300),
    _usage("p3", "planner.cycle1.repair1", 0.5, 50),
    _turn("planner", 2, 300),
    {"type": "life.planner.verdict", "tasks_added": 1},
    {"type": "life.planner.start"},
    _usage("p4", "planner.cycle2", 1.0, 500),
    _turn("planner", 9, 500),
    {"type": "life.planner.idle"},
    # Supervision: one kept the course, one steered, one was superseded.
    _usage("m1", "manager-supervision", 0.25, 10),
    _supervision("issued", "s1", call_id="m1", action="continue"),
    _supervision("applied", "s1", action="continue"),
    _usage("m2", "manager-supervision", 0.25, 10),
    _supervision("issued", "s2", call_id="m2", action="steer"),
    _supervision("applied", "s2", action="steer"),
    _usage("m3", "manager-supervision", 0.5, 10),
    _supervision("issued", "s3", call_id="m3", action="continue"),
    _supervision("failed", "s3", action="continue"),
    # Task aaa: reviewed twice on new work and passed on the job's result.
    {"type": "life.mission.started", "item_id": "aaa"},
    _usage("e1", "engineer-r1", 3.0, 1000, task="aaa:attempt:1"),
    _usage("r1", "reviewer", 1.0, 400, task="aaa:attempt:1"),
    _review("aaa", "continue", frontier_change="artifact_improved"),
    _usage("e2", "engineer-r2", 1.0, 2000, task="aaa:attempt:1"),
    _usage("r2", "reviewer", 1.0, 400, task="aaa:attempt:1"),
    _review("aaa", "continue", frontier_change="uncertainty_reduced"),
    _usage("r3", "reviewer", 1.0, 400, task="aaa:attempt:1"),
    _review("aaa", "done"),
    {"type": "life.mission.completed", "item_id": "aaa", "success": True, "elapsed_seconds": 120.5},
    # Task bbb: a failed attempt, then a passing retry.
    _usage("e3", "engineer-r1", 2.0, 1000, task="bbb:attempt:1", status="error"),
    {"type": "life.mission.completed", "item_id": "bbb", "success": False, "elapsed_seconds": 30},
    _usage("e4", "engineer-r1", 1.0, 1000, task="bbb:attempt:2"),
    _usage("r4", "reviewer", 1.0, 400, task="bbb:attempt:2"),
    _review("bbb", "continue"),
    {"type": "life.mission.completed", "item_id": "bbb", "success": True, "elapsed_seconds": 60},
    {"type": "life.mission.completed", "item_id": "ccc", "success": False, "elapsed_seconds": 5},
    _usage("c1", "curator.distill", 0.5, 20),
    _usage("c2", "curator.distill", 0.5, 20),
]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """The event log and the call ledger as a project keeps them, in time order."""
    write = lambda path, rows: path.write_text("".join(json.dumps(row) + "\n" for row in rows))  # noqa: E731
    rows = [{**row, "ts": float(index)} for index, row in enumerate(OLDER + CURRENT)]
    calls = [row for row in rows if row["type"] == "usage.recorded"]
    events = [row for row in rows if row["type"] != "usage.recorded"]
    write(tmp_path / "events.jsonl.1", [row for row in events if row["ts"] < len(OLDER)])
    # An older log also carried a copy of each call; the ledger is not counted twice.
    write(tmp_path / "events.jsonl", [*(row for row in events if row["ts"] >= len(OLDER)), calls[-1]])
    write(tmp_path / "usage.jsonl", [
        {**{key: value for key, value in row.items() if key not in {"type", "ts"}}, "completed_at": row["ts"]}
        for row in calls
    ])
    for name in ("events.jsonl", "usage.jsonl"):
        with (tmp_path / name).open("a") as handle:
            handle.write('{"call_id": "torn"\n')
    return tmp_path


def test_cli_prints_a_table_or_json(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(project)]) == 0
    table = capsys.readouterr().out
    assert table.startswith(f"{project}: 17 calls, $17.50\ncost per passed task: $8.75")
    assert "planner no new work      2   4    2.00" in table.splitlines()

    assert main([str(project), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == efficiency_report(project)


def test_cli_rejects_a_directory_without_an_event_log(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([str(tmp_path)])
    assert exit_info.value.code == 2
