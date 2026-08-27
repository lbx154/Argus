from __future__ import annotations

import csv

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
