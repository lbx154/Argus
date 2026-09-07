from __future__ import annotations

import csv
import json

import pytest
from fastapi.testclient import TestClient

from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.webapi import counterexample_dashboard, server
from argus_skill.webapi.counterexample_dashboard import (
    build_counterexample_dashboard,
)


def _write_csv(path, fieldnames, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_dashboard_projects_verified_rejected_and_parallel_candidates(tmp_path) -> None:
    _write_csv(
        tmp_path / "inputs" / "priority_pool.csv",
        ["ID", "题目", "具体描述", "分类", "来源等级", "验证级别"],
        [
            {"ID": "1", "题目": "One", "具体描述": "A", "分类": "strong", "来源等级": "A", "验证级别": "lead"},
            {"ID": "2", "题目": "Two", "具体描述": "B", "分类": "strong", "来源等级": "B", "验证级别": "lead"},
            {"ID": "3", "题目": "Three", "具体描述": "C", "分类": "strong", "来源等级": "A", "验证级别": "lead"},
        ],
    )
    _write_csv(
        tmp_path / "outputs" / "results.csv",
        ["ID", "disposition", "counterexample_or_refutation"],
        [{"ID": "1", "disposition": "published_refutation", "counterexample_or_refutation": "refuted"}],
    )
    _write_csv(
        tmp_path / "outputs" / "rejected.csv",
        ["ID", "rejection_reason"],
        [{"ID": "2", "rejection_reason": "variant"}],
    )
    parallel = tmp_path / "parallel" / "3"
    parallel.mkdir(parents=True)
    (parallel / "README.md").write_text("working")

    dashboard = build_counterexample_dashboard(tmp_path)

    by_id = {row["id"]: row for row in dashboard["candidates"]}
    assert by_id["1"]["status"] == "verified"
    assert by_id["1"]["progress"] == 100
    assert by_id["2"]["status"] == "rejected"
    assert by_id["3"]["status"] == "constructing"
    assert dashboard["counts"] == {"verified": 1, "rejected": 1, "constructing": 1}


@pytest.mark.parametrize("directory", ["inputs", "outputs", "research", "evidence", "parallel"])
def test_dashboard_does_not_follow_workspace_directory_escapes(
    tmp_path, directory, require_symlink_support,
) -> None:
    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    workspace.mkdir()
    _write_csv(external / "inputs" / "priority_pool.csv", ["ID", "题目"], [{"ID": "1", "题目": "External"}])
    _write_csv(external / "outputs" / "results.csv", ["ID"], [{"ID": "1"}])
    (external / "research").mkdir()
    (external / "research" / "MATH_STATE.json").write_text(json.dumps({
        "claims": [{"claim_id": "C_1", "version": 1, "natural_statement": "rejected_near_miss"}],
    }))
    (external / "evidence" / "1").mkdir(parents=True)
    (external / "evidence" / "1" / "README.md").write_text("external evidence")
    (external / "parallel" / "1").mkdir(parents=True)
    (external / "parallel" / "1" / "README.md").write_text("external work")
    if directory != "inputs":
        _write_csv(workspace / "inputs" / "priority_pool.csv", ["ID"], [{"ID": "1"}])
    (workspace / directory).symlink_to(external / directory, target_is_directory=True)

    dashboard = build_counterexample_dashboard(workspace)
    if directory == "inputs":
        assert dashboard["candidates"] == []
    else:
        candidate = dashboard["candidates"][0]
        assert candidate["status"] == "queued"
        assert candidate["evidence_path"] == ""
        assert candidate["parallel_files"] == 0


def test_dashboard_caps_csv_reads_before_parsing_remaining_rows(tmp_path, monkeypatch) -> None:
    _write_csv(tmp_path / "inputs" / "priority_pool.csv", ["ID"], [{"ID": "1"}])
    def rows(_handle):
        yield {"ID": "1"}
        raise AssertionError("candidate limit must bound parsing, not just the returned list")
    monkeypatch.setattr(counterexample_dashboard, "_MAX_CANDIDATES", 1)
    monkeypatch.setattr(counterexample_dashboard.csv, "DictReader", rows)

    assert build_counterexample_dashboard(tmp_path)["total"] == 1


def test_dashboard_preserves_non_symlink_evidence_requirement(
    tmp_path, require_symlink_support,
) -> None:
    _write_csv(tmp_path / "inputs" / "priority_pool.csv", ["ID"], [{"ID": "1"}])
    (tmp_path / "report.md").write_text("ordinary workspace file")
    evidence = tmp_path / "evidence" / "1"
    evidence.mkdir(parents=True)
    (evidence / "README.md").symlink_to(tmp_path / "report.md")

    candidate = build_counterexample_dashboard(tmp_path)["candidates"][0]
    assert candidate["status"] == "queued"
    assert candidate["evidence_path"] == ""


def test_dashboard_bounds_empty_directory_traversal(tmp_path, monkeypatch) -> None:
    root = tmp_path / "parallel" / "1"
    root.mkdir(parents=True)
    for index in range(210):
        (root / str(index)).mkdir()
    calls = 0
    scandir = counterexample_dashboard.os.scandir
    def counted(path):
        nonlocal calls
        calls += 1
        return scandir(path)
    monkeypatch.setattr(counterexample_dashboard.os, "scandir", counted)

    assert counterexample_dashboard._parallel_state(tmp_path, "1") == (0, 0.0)
    assert calls == 1


def test_counterexample_route_smoke_uses_registered_workspace_read_only(tmp_path) -> None:
    state = tmp_path / "state"
    workspace = tmp_path / "workspace"
    _write_csv(workspace / "inputs" / "priority_pool.csv", ["ID", "题目"], [{"ID": "1", "题目": "Candidate"}])
    write_session_meta(state, SessionMeta(
        id="lab-smoke", created=1, last_active=1, cwd=str(workspace), workdir=str(workspace),
    ))
    before = {path.relative_to(workspace): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}
    with TestClient(server.create_app(global_root=state, auth_token="lab-token")) as client:
        url = "/api/projects/lab-smoke/counterexamples"
        assert client.get(url).status_code == 401
        headers = {"Authorization": "Bearer lab-token"}
        response = client.get(url, headers=headers)
        assert response.status_code == 200
        assert response.json()["candidates"][0]["title"] == "Candidate"
        assert client.get("/api/projects/unknown/counterexamples", headers=headers).status_code == 404
        assert client.post(url, headers=headers).status_code == 405
    after = {path.relative_to(workspace): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}
    assert before == after
