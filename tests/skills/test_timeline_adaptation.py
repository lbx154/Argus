import copy
import json
from importlib.resources import files

import pytest

from argus.verticals.research.timeline import estimate
from argus.verticals.research.timeline_store import record


def task(key, duration, **extra):
    return dict(
        id=key,
        title=key,
        phase="experiment",
        duration_hours=duration,
        basis="test estimate",
        **extra,
    )


def option(key, duration, **extra):
    return dict(
        id=key,
        title=key,
        duration_hours=duration,
        resources={},
        basis="measured comparable implementation",
        tradeoff="Less implementation freedom",
        preserves_acceptance=True,
        **extra,
    )


def payload(tasks, deadline=6):
    return dict(
        selected_proposal_id="a",
        proposals=[dict(id="a", title="A", tasks=tasks)],
        resources={"gpu": 1},
        deadline_hours=deadline,
        adapt_to_deadline=True,
    )


def forecast(data):
    return estimate(data)["proposals"][0]


def test_deadline_selects_real_option_and_recomputes_all_three_scenarios():
    data = payload([task("build", [8, 10, 18], execution_options=[option("reuse", [2, 4, 8])])])
    original = copy.deepcopy(data)
    result = forecast(data)
    assert result["finish_hours"] == {"lower": 2, "expected": 13 / 3, "upper": 8}
    assert result["schedule"][0]["execution_option_id"] == "reuse"
    assert result["adaptation"]["changes"][0]["tradeoff"] == "Less implementation freedom"
    assert data == original
    data["deadline_hours"] = 30
    assert forecast(data)["finish_hours"]["upper"] == 18
    assert forecast(data)["schedule"][0]["execution_option_id"] == "standard"


def test_critical_path_reorders_work_instead_of_just_reporting_overrun():
    data = payload(
        [
            task("side", [3, 3, 3], resources={"gpu": 1}),
            task("core", [4, 4, 4], resources={"gpu": 1}),
            task("analysis", [8, 8, 8], depends_on=["core"]),
        ],
        deadline=12,
    )
    result = forecast(data)
    assert result["adaptation"]["baseline_finish_hours"]["expected"] == 15
    assert result["finish_hours"]["expected"] == 12
    assert result["schedule"][0]["id"] == "core"


def test_tied_parallel_paths_can_both_adopt_faster_options():
    data = payload(
        [
            task(key, [10, 10, 10], execution_options=[option("reuse", [4, 4, 4])])
            for key in ["a", "b"]
        ]
    )
    assert forecast(data)["finish_hours"]["expected"] == 4


def test_no_invented_compression_for_infeasible_deadline_or_unapproved_options():
    unsafe = option("skip-controls", [1, 1, 1])
    unsafe["preserves_acceptance"] = False
    data = payload([task("required", [10, 12, 20], execution_options=[unsafe])], deadline=2)
    result = forecast(data)
    assert result["finish_hours"]["upper"] == 20
    assert result["deadline_gap_hours"] > 0
    assert result["adaptation"]["status"] == "gap"


def test_options_cannot_borrow_unavailable_resources():
    fast = option("parallel", [1, 2, 4])
    fast["resources"] = {"gpu": 2}
    data = payload([task("required", [10, 10, 10], execution_options=[fast])])
    assert forecast(data)["finish_hours"]["expected"] == 10
    data["resources"]["gpu"] = 2
    assert forecast(data)["finish_hours"]["expected"] < 6


def test_running_task_is_never_shortened_by_deadline_adaptation():
    data = payload(
        [
            task(
                "run",
                [10, 10, 10],
                status="running",
                actual_start_hours=0,
                remaining_hours=[7, 8, 10],
                execution_options=[option("fast", [1, 1, 1])],
            )
        ]
    )
    data["now_hours"] = 2
    result = forecast(data)
    assert result["schedule"][0]["execution_option_id"] == "standard"
    assert result["finish_hours"]["upper"] == 12


def test_example_shorter_and_longer_deadlines_change_work_and_range():
    data = json.loads(
        files("argus.verticals.research").joinpath("timeline_example.json").read_text()
    )
    data["adapt_to_deadline"] = True
    data["deadline_hours"] = 120
    initial = forecast(data)
    data["deadline_hours"] = 60
    short = forecast(data)
    assert short["finish_hours"]["expected"] <= 60
    assert short["finish_hours"]["upper"] < initial["finish_hours"]["upper"]
    assert any(c["kind"] == "execution_option" for c in short["adaptation"]["changes"])
    data["deadline_hours"] = 300
    relaxed = forecast(data)
    assert "extra" not in relaxed["deferred_task_ids"]
    assert all(row["execution_option_id"] == "standard" for row in relaxed["schedule"])


def test_option_requires_its_own_estimate_and_tradeoff():
    bad = option("fast", [1, 2, 4])
    del bad["tradeoff"]
    with pytest.raises(ValueError, match="tradeoff"):
        forecast(payload([task("a", [10, 10, 10], execution_options=[bad])]))


def test_started_selected_option_is_frozen_when_deadline_is_relaxed(tmp_path):
    data = payload([task("build", [8, 10, 18], execution_options=[option("reuse", [2, 4, 8])])])
    first = record(tmp_path, data, expected_version=0, reason="Initial deadline")
    chosen = first["report"]["proposals"][0]["schedule"][0]
    data["now_hours"] = 1
    data["proposals"][0]["tasks"][0].update(
        status="running", actual_start_hours=0, remaining_hours=[1, 3, 7],
        duration_hours=chosen["duration_hours"], resources=chosen["resources"],
        execution_option_id=chosen["execution_option_id"],
    )
    record(tmp_path, data, expected_version=1, reason="Started chosen implementation")
    data["deadline_hours"] = 100
    third = record(tmp_path, data, expected_version=2, reason="Deadline relaxed")
    row = third["report"]["proposals"][0]["schedule"][0]
    assert row["execution_option_id"] == "reuse"
    assert row["duration_hours"] == [2, 4, 8]
    data["proposals"][0]["tasks"][0]["execution_option_id"] = "standard"
    with pytest.raises(ValueError, match="executed"):
        record(tmp_path, data, expected_version=3, reason="Cannot rewrite execution history")
