from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from argus.core.timeline import estimate
from argus.core.timeline_store import record


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


def test_failed_publication_keeps_last_complete_revision(tmp_path, monkeypatch):
    data = request([task("pilot", 2)])
    record(tmp_path, data, expected_version=0, reason="Initial")

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("argus.core.atomic_replace.os.replace", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        record(tmp_path, data, expected_version=1, reason="Update")
    paths = list((tmp_path / ".argus" / "timeline").glob("*.json"))
    assert len(paths) == 1
    assert json.loads(paths[0].read_text())["version"] == 1


def test_cli_real_entrypoint_records_and_renders(tmp_path):
    source = tmp_path / "proposal.json"
    source.write_text(json.dumps(request([task("pilot", 2)])))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "argus.core.timeline",
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
        [sys.executable, "-m", "argus.core.timeline", "--input", str(source)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert bad.returncode == 2 and "proposals" in bad.stderr
