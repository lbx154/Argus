from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.tools.weekly_report import main, week_bounds, weekly_report

W42 = 1791763200.0  # Monday 2026-10-12 00:00 UTC
W41 = W42 - 7 * 86400
W40 = W41 - 7 * 86400


def _usage(call: str, label: str, cost: float, task: str = "") -> dict:
    return {"type": "usage.recorded", "call_id": call, "run_label": label, "mission_id": task,
            "input_tokens": 100, "output_tokens": 10, "cost_usd": cost, "status": "completed"}


def _started(task: str, attempt: int = 1) -> dict:
    return {"type": "life.mission.started", "item_id": task, "attempt": attempt}


def _settled(task: str, success: bool, rounds: int = 1, **fields) -> dict:
    return {"type": "life.mission.completed", "item_id": task, "success": success, "rounds": rounds, **fields}


def _review(task: str, status: str) -> dict:
    return {"type": "round.review.completed", "item_id": task, "status": status}


ALPHA = {
    W40: [_usage("old", "engineer-r1", 100.0, "zzz:attempt:1"), _settled("zzz", True)],
    W41: [
        _started("eee"),
        _usage("a0", "engineer-r1", 3.0, "eee:attempt:1"),
        _settled("eee", False, status="failed"),
    ],
    W42: [
        # aaa passes in one round; bbb after a "continue"; ccc fails; ddd is parked.
        _started("aaa"),
        _usage("a1", "engineer-r1", 2.0, "aaa:attempt:1"),
        _usage("a2", "reviewer", 1.0, "aaa:attempt:1"),
        _review("aaa", "done"),
        _settled("aaa", True),
        _started("bbb"),
        _usage("a3", "engineer-r1", 1.0, "bbb:attempt:1"),
        _usage("a4", "reviewer", 0.5, "bbb:attempt:1"),
        _review("bbb", "continue"),
        _usage("a5", "reviewer", 0.5, "bbb:attempt:1"),
        _review("bbb", "done"),
        _settled("bbb", True, rounds=2),
        _started("ccc"),
        _usage("a6", "engineer-r1", 1.0, "ccc:attempt:1"),
        _settled("ccc", False, status="failed"),
        _started("ddd"),
        _settled("ddd", False, status="parked"),
        # A Planner turn that added no work.
        {"type": "life.planner.start"},
        _usage("a7", "planner.cycle1", 0.5),
        {"type": "life.planner.idle"},
    ],
}

BETA = {
    W41: [
        _started("ggg"),
        _usage("b0", "engineer-r1", 2.0, "ggg:attempt:1"),
        _usage("b1", "reviewer", 1.0, "ggg:attempt:1"),
        _review("ggg", "done"),
        _settled("ggg", True),
    ],
    W42: [
        # A retry passes in one round, but not on its first attempt.
        _started("fff", attempt=2),
        _usage("b2", "engineer-r1", 1.0, "fff:attempt:2"),
        _usage("b3", "reviewer", 1.0, "fff:attempt:2"),
        _review("fff", "done"),
        _settled("fff", True),
    ],
}


def _write(project: Path, weeks: dict[float, list[dict]]) -> None:
    project.mkdir(parents=True)
    rows = [{**row, "ts": week + 3600.0 * index} for week, week_rows in weeks.items()
            for index, row in enumerate(week_rows)]
    events = [row for row in rows if row["type"] != "usage.recorded"]
    calls = [{**{key: value for key, value in row.items() if key not in {"type", "ts"}}, "completed_at": row["ts"]}
             for row in rows if row["type"] == "usage.recorded"]
    (project / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in events))
    (project / "usage.jsonl").write_text("".join(json.dumps(row) + "\n" for row in calls))


@pytest.fixture
def home(tmp_path: Path) -> Path:
    _write(tmp_path / "projects" / "alpha", ALPHA)
    _write(tmp_path / "projects" / "beta", BETA)
    (tmp_path / "projects" / "empty").mkdir()  # no event log: not a project yet
    return tmp_path


def test_week_bounds() -> None:
    assert week_bounds("2026-W42") == ("2026-W42", W42, W42 + 7 * 86400)
    # By default the last full week: on Wednesday of W43, that is W42.
    assert week_bounds(now=W42 + 9 * 86400) == ("2026-W42", W42, W42 + 7 * 86400)
    with pytest.raises(ValueError):
        week_bounds("2026-42")


def test_report_sums_a_week_across_projects(home: Path) -> None:
    report = weekly_report([home], "2026-W42")

    assert report["week"] == "2026-W42"
    assert [row["project"] for row in report["projects"]] == ["alpha", "beta"]
    total = report["total"]
    assert {key: total[key] for key in (
        "projects", "started", "passed", "failed", "parked", "first_round_passed", "reviews",
        "continue_verdicts", "passed_task_reviews", "calls", "no_new_information_calls",
    )} == {
        "projects": 2, "started": 5, "passed": 3, "failed": 1, "parked": 1, "first_round_passed": 1,
        "reviews": 4, "continue_verdicts": 1, "passed_task_reviews": 4, "calls": 9, "no_new_information_calls": 1,
    }
    assert total["success_rate"] == 0.75
    assert total["first_round_pass_rate"] == 0.25
    assert total["reviews_per_passed_task"] == 1.33
    assert total["cost_usd"] == 8.5
    assert total["no_new_information_usd"] == 0.5
    # CE-1: all spend over the passed tasks.
    assert total["cost_per_passed_task_usd"] == 2.8333

    alpha = report["projects"][0]
    assert (alpha["passed"], alpha["failed"], alpha["parked"], alpha["cost_usd"]) == (2, 1, 1, 6.5)
    assert alpha["first_round_pass_rate"] == pytest.approx(1 / 3, abs=1e-4)

    # The week before, and the change; W40's spend falls in neither week.
    previous = report["previous_week"]
    assert (previous["passed"], previous["failed"], previous["first_round_passed"], previous["cost_usd"]) == (1, 1, 1, 6.0)
    assert previous["success_rate"] == 0.5 and previous["cost_per_passed_task_usd"] == 6.0
    change = report["change"]
    assert (change["passed"], change["success_rate"], change["cost_usd"]) == (2, 0.25, 2.5)
    assert change["cost_per_passed_task_usd"] == -3.1667


def test_a_week_without_work_has_no_rates(home: Path) -> None:
    report = weekly_report([home], "2026-W30")
    assert report["projects"] == []
    assert report["total"]["success_rate"] is None and report["total"]["cost_per_passed_task_usd"] is None
    assert report["change"]["success_rate"] is None


def test_cli_prints_markdown_or_json_and_writes_the_week(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    project = home / "projects" / "alpha"
    assert main([str(project), "--week", "2026-W42"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("# Argus weekly report 2026-W42 (2026-10-12 to 2026-10-18, UTC)")
    assert "| tasks passed | 2 | 0 | +2 |" in text
    assert "| success rate | 66.7% | 0.0% | +66.7 pt |" in text
    assert "| cost per passed task | $3.25 | - | - |" in text

    out = tmp_path / "reports"
    assert main([str(home), "--week", "2026-W42", "--json", "--out", str(out)]) == 0
    assert json.loads(capsys.readouterr().out) == weekly_report([home], "2026-W42")
    assert json.loads((out / "2026-W42.json").read_text()) == weekly_report([home], "2026-W42")
    assert (out / "2026-W42.md").read_text().startswith("# Argus weekly report 2026-W42")


def test_cli_rejects_a_bad_week_or_no_project(home: Path, tmp_path: Path) -> None:
    for argv in ([str(home), "--week", "W42"], [str(tmp_path / "projects" / "empty")]):
        with pytest.raises(SystemExit) as exit_info:
            main(argv)
        assert exit_info.value.code == 2
