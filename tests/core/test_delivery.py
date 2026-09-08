from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from argus_skill.life.delivery import (
    build_delivery_receipt,
    referenced_delivery_paths,
    reviewed_change_paths,
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


def test_framework_format_lists_recover_only_safe_explicit_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    figures = workspace / "paper" / "figures"
    figures.mkdir(parents=True)
    for suffix in ("pptx", "pdf", "png", "key"):
        (figures / f"method.{suffix}").write_bytes(b"output")
    (figures / "unmentioned.pptx").write_bytes(b"other output")
    (tmp_path / "outside.pptx").write_bytes(b"private")
    if os.name != "nt":
        (figures / "escape.pptx").symlink_to(tmp_path / "outside.pptx")

    assert referenced_delivery_paths(workspace, [
        "Delivered `paper/figures/method.{pptx,pdf,png,key,exe}`.",
        "paper/figures/method.pptx is editable.",
        "Reject ../outside.{pptx,pdf} and paper/figures/escape.{pptx,pdf}.",
    ]) == [
        "paper/figures/method.pptx", "paper/figures/method.pdf", "paper/figures/method.png",
    ]


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


def _reviewed_edit_events(paths: list[str], *, item_id: str = "task-web") -> list[dict]:
    return [
        {"item_id": item_id, **event} for event in [
            {"type": "life.mission.started"},
            {
                "type": "engineer.progress", "kind": "tool_use",
                "agent_layer": "engineer", "tool_name": "apply_patch",
                "text": "apply_patch: *** Begin Patch\n" + "\n".join(
                    f"*** Add File: {path}\n+contents" for path in paths
                ),
            },
            {"type": "round.review.started"},
            *[{
                "type": "engineer.progress", "kind": "tool_use",
                "agent_layer": "reviewer", "tool_name": "view",
                "text": "view: " + json.dumps({"path": path}),
            } for path in paths],
            {"type": "round.review.completed", "status": "done", "review_source": "reviewer"},
        ]
    ]


def test_reviewed_changes_recover_product_without_delivering_context_or_fixtures(tmp_path) -> None:
    names = [
        "REPORT.md", "index.html", "app.js", "package.json", "tests/example.html",
        "tmp/preview.html", ".autors/receipt.md", ".env", "credentials.json",
    ]
    for name in [*names, "existing.html", "unmentioned.html"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("contents", encoding="utf-8")
    outside = tmp_path.parent / "outside.html"
    outside.write_text("private", encoding="utf-8")
    events = _reviewed_edit_events([str(tmp_path / name) for name in names] + [str(outside)])
    events.insert(-1, {
        "item_id": "task-web", "type": "engineer.progress", "kind": "tool_use",
        "agent_layer": "reviewer", "tool_name": "view",
        "text": 'view: {"path": "existing.html"}',
    })
    (tmp_path / "events.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")

    assert reviewed_change_paths(tmp_path, tmp_path, "task-web") == ["index.html", "REPORT.md"]
    assert reviewed_change_paths(tmp_path, tmp_path, "other-task") == []


@pytest.mark.parametrize("ending", [
    [{"type": "round.review.completed", "status": "revise", "review_source": "reviewer"}],
    [{"type": "round.review.completed", "status": "done", "review_source": "engineer"}],
    [{"type": "round.review.started"}],
    [{"type": "life.mission.started"}],
])
def test_reviewed_changes_never_reuse_a_previous_accepted_review(tmp_path, ending) -> None:
    (tmp_path / "index.html").write_text("product", encoding="utf-8")
    events = _reviewed_edit_events(["index.html"])
    events.extend({"item_id": "task-web", **event} for event in ending)
    (tmp_path / "events.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")

    assert reviewed_change_paths(tmp_path, tmp_path, "task-web") == []


@pytest.mark.parametrize("failed_event_index", [1, 3])
def test_failed_file_edits_or_reads_do_not_become_delivery_evidence(tmp_path, failed_event_index) -> None:
    (tmp_path / "index.html").write_text("product", encoding="utf-8")
    events = _reviewed_edit_events(["index.html"])
    events.insert(failed_event_index + 1, {**events[failed_event_index], "status": "failed"})
    (tmp_path / "events.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")

    assert reviewed_change_paths(tmp_path, tmp_path, "task-web") == []
