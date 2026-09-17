from __future__ import annotations

import copy
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from argus.verticals.research.timeline import estimate
from argus.verticals.research.timeline_store import record


def task(key, hours, *, deps=(), resources=None, **kwargs):
    return dict(
        id=key,
        title=key,
        phase="experiment",
        depends_on=list(deps),
        resources=resources or {},
        duration_hours=[hours, hours, hours],
        basis="explicit test estimate",
        **kwargs,
    )


def request(tasks, **kwargs):
    return dict(
        proposals=[dict(id="idea-a", title="Idea A", tasks=tasks)],
        selected_proposal_id="idea-a",
        resources={"gpu": 1},
        **kwargs,
    )


def selected(report):
    return report["proposals"][0]


def test_dependencies_and_shared_capacity_determine_calendar_time():
    data = request(
        [
            task("build", 2),
            task("a", 4, deps=["build"], resources={"gpu": 1}),
            task("b", 3, deps=["build"], resources={"gpu": 1}),
            task("write", 2, deps=["a", "b"]),
        ]
    )
    assert selected(estimate(data))["finish_hours"]["expected"] == 11
    data["resources"]["gpu"] = 2
    assert selected(estimate(data))["finish_hours"]["expected"] == 8


def test_three_point_estimate_and_deadline_never_compress_required_work():
    step = task("pilot", 4)
    step["duration_hours"] = [1, 4, 13]
    report = selected(estimate(request([step], deadline_hours=3)))
    assert report["finish_hours"] == {"lower": 1, "expected": 5, "upper": 13}
    assert report["deadline_gap_hours"] == 2


def test_deadline_defers_only_declared_optional_work_and_preserves_dependencies():
    data = request(
        [
            task("pilot", 2, optional=True),
            task("main", 4, deps=["pilot"]),
            task("extra", 8, optional=True),
        ],
        deadline_hours=6,
        defer_optional=True,
    )
    report = selected(estimate(data))
    assert report["deferred_task_ids"] == ["extra"]
    assert report["finish_hours"]["expected"] == 6
    assert {row["id"] for row in report["schedule"]} == {"pilot", "main"}


def test_progress_freezes_completed_work_and_reserves_running_resources():
    data = request(
        [
            task("done", 8, status="completed", actual_start_hours=0, actual_finish_hours=2),
            task(
                "running",
                5,
                status="running",
                actual_start_hours=2,
                remaining_hours=[2, 3, 4],
                resources={"gpu": 1},
            ),
            task("next", 1, resources={"gpu": 1}),
        ],
        now_hours=5,
    )
    report = selected(estimate(data))
    rows = {row["id"]: row for row in report["schedule"]}
    assert rows["done"]["finish_hours"] == 2
    assert rows["next"]["start_hours"] == 8
    assert report["finish_hours"]["expected"] == 9


def test_failed_idea_exposes_blocker_instead_of_claiming_paper_eta():
    data = request(
        [
            task(
                "pilot",
                2,
                status="failed",
                actual_start_hours=0,
                actual_finish_hours=3,
                reason="No improvement; see pilot.csv",
            ),
            task("main", 5, deps=["pilot"]),
        ],
        now_hours=3,
    )
    report = selected(estimate(data))
    assert report["finish_hours"] is None
    assert report["blocked_tasks"][0]["id"] == "main"
    assert "pilot" in report["blocked_tasks"][0]["reason"]


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["proposals"][0]["tasks"][0].update(duration_hours=[5, 2, 1]),
        lambda d: d["proposals"][0]["tasks"][0].update(duration_hours=[1, float("nan"), 3]),
        lambda d: d["proposals"][0]["tasks"][0].update(depends_on=["missing"]),
        lambda d: d["proposals"][0]["tasks"][0].update(depends_on=["a"]),
        lambda d: d["proposals"][0]["tasks"][0].update(resources={"gpu": 2}),
        lambda d: d.update(selected_proposal_id="missing"),
        lambda d: d["proposals"][0]["tasks"].append(task("a", 2)),
        lambda d: d["proposals"][0]["tasks"][0].update(status="running", actual_start_hours=0),
        lambda d: d["proposals"][0]["tasks"][0].update(status=[]),
    ],
)
def test_invalid_plans_fail_explicitly(change):
    data = request([task("a", 2)])
    change(data)
    with pytest.raises(ValueError):
        estimate(data)


def test_revision_preserves_baseline_and_explains_changed_task(tmp_path):
    data = request([task("pilot", 2)])
    first = record(tmp_path, data, expected_version=0, reason="Initial proposal")
    original = (tmp_path / ".argus" / "timeline" / "000001.json").read_bytes()
    data["proposals"][0]["tasks"][0].update(
        duration_hours=[2, 8, 14],
        reason="Evaluator bug requires rerun",
        evidence=["experiments/evaluator.log"],
    )
    second = record(tmp_path, data, expected_version=1, reason="Repair evaluator")
    assert first["version"] == 1 and second["version"] == 2
    assert second["report"]["revision"]["baseline_delta_hours"] == 6
    assert (
        second["report"]["revision"]["changed_tasks"][0]["reason"] == "Evaluator bug requires rerun"
    )
    assert (tmp_path / ".argus" / "timeline" / "000001.json").read_bytes() == original
    with pytest.raises(ValueError, match="version"):
        record(tmp_path, data, expected_version=1, reason="stale writer")


def test_revision_cannot_requeue_success_or_drop_execution_history(tmp_path):
    data = request(
        [task("pilot", 2, status="completed", actual_start_hours=0, actual_finish_hours=2)],
        now_hours=2,
    )
    record(tmp_path, data, expected_version=0, reason="Initial measured state")
    changed = copy.deepcopy(data)
    changed["proposals"][0]["tasks"] = [task("pilot", 2)]
    with pytest.raises(ValueError, match="executed"):
        record(tmp_path, changed, expected_version=1, reason="accidental reset")


def test_overdue_unchanged_task_keeps_original_target_and_unknown_cause(tmp_path):
    data = request([task("pilot", 2)])
    record(tmp_path, data, expected_version=0, reason="Initial")
    data["now_hours"] = 5
    second = record(tmp_path, data, expected_version=1, reason="Status update")
    variance = second["report"]["revision"]["task_variances"][0]
    assert variance["delay_hours"] == 5
    assert variance["reason_status"] == "unconfirmed"
    assert not variance["observed"]


def test_failed_pilot_can_be_retired_and_replaced_without_rerunning_it(tmp_path):
    data = request([task("pilot", 2)], now_hours=0)
    record(tmp_path, data, expected_version=0, reason="Initial")
    pilot = data["proposals"][0]["tasks"][0]
    pilot.update(
        status="failed",
        actual_start_hours=0,
        actual_finish_hours=3,
        reason="Method failed to beat baseline",
        evidence=["pilot.csv"],
        optional=True,
    )
    data["now_hours"] = 3
    data["proposals"][0]["tasks"].append(task("repair", 4, reason="Test revised mechanism"))
    result = record(
        tmp_path, data, expected_version=1, reason="Revise mechanism within selected idea"
    )
    assert selected(result["report"])["finish_hours"]["expected"] == 7
    assert selected(result["report"])["schedule"][0]["status"] == "failed"
    assert result["report"]["revision"]["task_variances"][0]["observed"]


def test_resource_reduction_does_not_reject_completed_historical_allocation():
    data = request(
        [
            task(
                "done",
                1,
                resources={"gpu": 4},
                status="completed",
                actual_start_hours=0,
                actual_finish_hours=1,
            ),
            task("next", 2, resources={"gpu": 1}),
        ],
        now_hours=1,
    )
    assert selected(estimate(data))["finish_hours"]["expected"] == 3


def test_multi_resource_queue_does_not_overlap_reserved_gpu():
    data = request(
        [
            task("write", 4, resources={"researcher": 1}),
            task("joint", 2, resources={"gpu": 1, "researcher": 1}),
            task("gpu-only", 5, resources={"gpu": 1}),
        ]
    )
    data["resources"]["researcher"] = 1
    rows = {r["id"]: r for r in selected(estimate(data))["schedule"]}
    assert rows["joint"]["start_hours"] == 4
    assert rows["gpu-only"]["start_hours"] == 6


def test_candidate_selection_keeps_comparisons_separate(tmp_path):
    data = request([task("big", 20)])
    data["proposals"].append(dict(id="idea-b", title="Alternative", tasks=[task("small", 5)]))
    record(tmp_path, data, expected_version=0, reason="Compare ideas")
    data["selected_proposal_id"] = "idea-b"
    result = record(tmp_path, data, expected_version=1, reason="Operator selects idea B")
    assert result["report"]["revision"]["baseline_delta_hours"] == -15
    assert result["report"]["revision"]["baseline_selected_proposal_id"] == "idea-a"


def test_concurrent_revision_writers_cannot_overwrite_each_other(tmp_path):
    data = request([task("pilot", 2)])
    record(tmp_path, data, expected_version=0, reason="Initial")

    def write():
        try:
            return record(tmp_path, data, expected_version=1, reason="Concurrent update")["version"]
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: write(), range(2)))
    assert outcomes.count(2) == 1
    assert sum("version conflict" in str(outcome) for outcome in outcomes) == 1
    assert len(list((tmp_path / ".argus" / "timeline").glob("*.json"))) == 2


def test_failed_publication_keeps_last_complete_revision(tmp_path, monkeypatch):
    data = request([task("pilot", 2)])
    record(tmp_path, data, expected_version=0, reason="Initial")

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("argus.verticals.research.timeline_store.os.replace", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        record(tmp_path, data, expected_version=1, reason="Update")
    paths = list((tmp_path / ".argus" / "timeline").glob("*.json"))
    assert len(paths) == 1
    assert json.loads(paths[0].read_text())["version"] == 1


def test_overflow_cannot_publish_nonfinite_forecast():
    data = request([task("a", 1e308), task("b", 1e308, deps=["a"])])
    with pytest.raises(ValueError):
        estimate(data)


def test_cli_real_entrypoint_records_and_renders(tmp_path):
    source = tmp_path / "proposal.json"
    source.write_text(json.dumps(request([task("pilot", 2)])))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "argus.verticals.research.timeline",
            "--input",
            str(source),
            "--project-root",
            str(tmp_path),
            "--expected-version",
            "0",
            "--reason",
            "Initial proposal",
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == 0, result.stderr
    assert "Idea A" in result.stdout and "2.0 h" in result.stdout
    assert (tmp_path / ".argus" / "timeline" / "000001.json").exists()
    source.write_text("{}")
    bad = subprocess.run(
        [sys.executable, "-m", "argus.verticals.research.timeline", "--input", str(source)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert bad.returncode == 2 and "proposals" in bad.stderr
