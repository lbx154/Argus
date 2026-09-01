from __future__ import annotations

import json
import os
from pathlib import Path

from argus_skill.life.delivery import (
    build_delivery_receipt,
    referenced_delivery_paths,
)


def test_delivery_receipt_prefers_reviewer_evidence_and_rejects_unsafe_paths(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    workspace.mkdir()
    state.mkdir()
    (workspace / "final.md").write_text("# Final\n", encoding="utf-8")
    (workspace / "fallback.md").write_text("# Fallback\n", encoding="utf-8")
    live_root = state / ".argus"
    live_root.mkdir()
    (live_root / "live-view.json").write_text(
        json.dumps({
            "title": "Current result",
            "reason": "Useful fallback.",
            "paths": ["fallback.md"],
        }),
        encoding="utf-8",
    )

    receipt = build_delivery_receipt(
        item_id="task-1",
        title="Create final result",
        summary="Verified final result.",
        success=True,
        overall_complete=True,
        status="done",
        review_status="done",
        final_submission_certified=False,
        workspace=workspace,
        state_root=state,
        reviewer_artifacts=["final.md", "../secret.txt", ".env"],
    )

    assert receipt is not None
    assert receipt["delivery_id"] == "delivery:task-1:task_completed"
    assert receipt["primary_target"]["path"] == "final.md"
    assert [target["path"] for target in receipt["targets"]] == ["final.md"]


def test_completion_links_resolve_to_safe_workspace_relative_files(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    report = workspace / "final report.pdf"
    source = workspace / "source.tex"
    secret = workspace / ".env"
    outside = tmp_path / "outside.pdf"
    report.write_bytes(b"pdf")
    source.write_text("source", encoding="utf-8")
    secret.write_text("TOKEN=no", encoding="utf-8")
    outside.write_bytes(b"outside")
    report_link = report.resolve().as_posix()
    if os.name == "nt":
        report_link = f"/{report_link}"

    paths = referenced_delivery_paths(
        workspace,
        [
            f"[PDF](<{report_link}>) and `source.tex`",
            f"[outside]({outside.resolve().as_uri()}) [secret](.env)",
        ],
    )

    assert paths == ["final report.pdf", "source.tex"]


def test_reviewed_chinese_book_title_resolves_to_existing_delivery(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "餐饮企业运营手册.md").write_text("# 手册\n", encoding="utf-8")

    assert referenced_delivery_paths(
        workspace,
        ["已完整审阅《餐饮企业运营手册.md》；内容符合交付条件。"],
    ) == ["餐饮企业运营手册.md"]


def test_intermediate_success_has_no_delivery_even_with_an_artifact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    workspace.mkdir()
    state.mkdir()
    (workspace / "partial.md").write_text("partial\n", encoding="utf-8")

    assert build_delivery_receipt(
        item_id="task-partial",
        title="Resume task",
        summary="One stage advanced.",
        success=True,
        overall_complete=False,
        status="done",
        review_status="done",
        final_submission_certified=False,
        workspace=workspace,
        state_root=state,
        reviewer_artifacts=["partial.md"],
    ) is None


def test_delivery_receipt_does_not_exist_without_a_renderable_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    workspace.mkdir()
    state.mkdir()

    receipt = build_delivery_receipt(
        item_id="task-2",
        title="Finish analysis",
        summary="The bounded analysis is complete.",
        success=True,
        overall_complete=True,
        status="done",
        review_status="done",
        final_submission_certified=False,
        workspace=workspace,
        state_root=state,
    )

    assert receipt is None


def test_failed_mission_has_no_delivery_receipt(tmp_path: Path) -> None:
    assert build_delivery_receipt(
        item_id="task-3",
        title="Blocked task",
        summary="",
        success=False,
        overall_complete=False,
        status="blocked",
        review_status="blocked",
        final_submission_certified=False,
        workspace=tmp_path,
        state_root=tmp_path,
    ) is None
