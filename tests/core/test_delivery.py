from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from argus.life.delivery import (
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
    assert receipt["delivery_id"].startswith("delivery:task-1:task_completed:")
    assert receipt["primary_target"]["path"] == "final.md"
    assert [target["path"] for target in receipt["targets"]] == ["final.md"]


def test_title_words_do_not_reorder_delivery_targets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    (workspace / "paper").mkdir(parents=True)
    state.mkdir()
    (workspace / "paper" / "main.pdf").write_bytes(b"%PDF-1.4\n")
    shots = [f"shots/screen_{index}.png" for index in range(7)]
    for shot in shots:
        (workspace / shot).parent.mkdir(exist_ok=True)
        (workspace / shot).write_bytes(b"\x89PNG")

    receipt = build_delivery_receipt(
        item_id="task-1",
        title="Figure out why the build fails; rewrite the first paragraph",
        summary="修复登录失败：之前试图读取空配置",
        success=True,
        overall_complete=True,
        status="done",
        review_status="done",
        final_submission_certified=False,
        workspace=workspace,
        state_root=state,
        reviewer_artifacts=["paper/main.pdf", *shots],
    )

    assert receipt is not None
    assert receipt["primary_target"]["path"] == "paper/main.pdf"


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


def _stage_package_receipt(tmp_path: Path, **overrides):
    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    workspace.mkdir(exist_ok=True)
    state.mkdir(exist_ok=True)
    (workspace / "package.md").write_text("# Round 1 results\n", encoding="utf-8")
    fields = dict(
        item_id="task-package",
        title="Package the first real comparison",
        summary="Real results, failures and unmet requirements.",
        success=True,
        overall_complete=False,
        status="done",
        review_status="done",
        review_source="reviewer",
        final_submission_certified=False,
        workspace=workspace,
        state_root=state,
        reviewer_artifacts=["package.md"],
        stage_package=True,
    )
    fields.update(overrides)
    return build_delivery_receipt(**fields)


@pytest.mark.parametrize("overrides", [
    {"review_source": "engineer_self_review"},
    {"review_status": "continue"},
    {"success": False},
    {"stage_package": False},
])
def test_stage_package_needs_an_independent_accepted_review(tmp_path: Path, overrides) -> None:
    # An unreviewed summary or a status string must never stand in for a delivery.
    assert _stage_package_receipt(tmp_path, **overrides) is None


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


@pytest.mark.parametrize("failed_event_index", [1, 3])
def test_failed_file_edits_or_reads_do_not_become_delivery_evidence(tmp_path, failed_event_index) -> None:
    (tmp_path / "index.html").write_text("product", encoding="utf-8")
    events = _reviewed_edit_events(["index.html"])
    events.insert(failed_event_index + 1, {**events[failed_event_index], "status": "failed"})
    (tmp_path / "events.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")

    assert reviewed_change_paths(tmp_path, tmp_path, "task-web") == []


def test_first_delivery_stays_readable_after_a_later_delivery_overwrites_the_file(
    tmp_path: Path,
) -> None:
    from argus.life.delivery import (
        MAX_SNAPSHOT_BYTES,
        delivery_snapshot_diff,
        read_delivery_snapshot,
    )

    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    workspace.mkdir()
    state.mkdir()

    def deliver(item: str) -> dict:
        receipt = build_delivery_receipt(
            item_id=item, title=item, summary="", success=True, overall_complete=True,
            status="done", review_status="done", final_submission_certified=False,
            workspace=workspace, state_root=state,
            reviewer_artifacts=["app.py", "big.txt"],
        )
        assert receipt is not None
        return receipt

    (workspace / "app.py").write_text("print('v1')\n", encoding="utf-8")
    (workspace / "big.txt").write_text("x" * (MAX_SNAPSHOT_BYTES + 1), encoding="utf-8")
    first = deliver("task-1")
    (workspace / "app.py").write_text("print('v2')\n", encoding="utf-8")
    second = deliver("task-2")

    first_app = next(s for s in first["snapshots"] if s["path"] == "app.py")
    second_app = next(s for s in second["snapshots"] if s["path"] == "app.py")
    assert read_delivery_snapshot(state, first_app["version"]) == "print('v1')\n"
    assert read_delivery_snapshot(state, second_app["version"]) == "print('v2')\n"
    diff = delivery_snapshot_diff(state, first_app, second_app) or ""
    assert "-print('v1')" in diff and "+print('v2')" in diff

    # Large files are identified by their file facts only; nothing is copied.
    big = next(s for s in first["snapshots"] if s["path"] == "big.txt")
    assert big["stored"] is False and big["version"].startswith(f"{MAX_SNAPSHOT_BYTES + 1}:")
    assert read_delivery_snapshot(state, big["version"]) is None


def test_snapshot_store_keeps_only_recent_versions_per_item(tmp_path: Path) -> None:
    from argus.life.delivery import (
        MAX_SNAPSHOT_DELIVERIES_PER_ITEM,
        SNAPSHOT_DIRNAME,
        read_delivery_snapshot,
    )

    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    workspace.mkdir()
    state.mkdir()
    receipts = []
    for n in range(MAX_SNAPSHOT_DELIVERIES_PER_ITEM + 5):
        (workspace / "notes.md").write_text(f"version {n}\n", encoding="utf-8")
        receipt = build_delivery_receipt(
            item_id="notes", title="notes", summary="", success=True, overall_complete=True,
            status="done", review_status="done", final_submission_certified=False,
            workspace=workspace, state_root=state, reviewer_artifacts=["notes.md"],
        )
        assert receipt is not None
        receipts.append(receipt)

    blobs = [p for p in (state / SNAPSHOT_DIRNAME).iterdir() if len(p.name) == 32]
    assert len(blobs) == MAX_SNAPSHOT_DELIVERIES_PER_ITEM
    assert read_delivery_snapshot(state, receipts[0]["snapshots"][0]["version"]) is None
    assert read_delivery_snapshot(state, receipts[-1]["snapshots"][0]["version"]) == (
        f"version {MAX_SNAPSHOT_DELIVERIES_PER_ITEM + 4}\n"
    )


def test_a_delivery_is_current_until_a_delivered_file_changes(tmp_path: Path) -> None:
    from argus.life.delivery import delivery_is_current

    receipt = _stage_package_receipt(tmp_path)
    workspace, state = tmp_path / "workspace", tmp_path / "state"

    assert delivery_is_current(workspace, state, receipt["snapshots"])
    (workspace / "package.md").write_text("# Round 1 results, edited\n", encoding="utf-8")
    assert not delivery_is_current(workspace, state, receipt["snapshots"])
    # No recorded version proves nothing unchanged.
    assert not delivery_is_current(workspace, state, [])


def test_changed_files_list_docs_and_outputs_before_tests(tmp_path: Path) -> None:
    from argus.core.workspace_facts import workspace_facts
    from argus.life.delivery import changed_workspace_paths

    (tmp_path / "pkg").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "old.txt").write_text("gone\n", encoding="utf-8")
    (tmp_path / "same.md").write_text("unchanged\n", encoding="utf-8")
    before = workspace_facts(tmp_path)
    (tmp_path / "tests" / "test_pkg.py").write_text("def test(): pass\n", encoding="utf-8")
    (tmp_path / "pkg" / "__init__.py").write_text("x = 22\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Pkg\n", encoding="utf-8")
    (tmp_path / "old.txt").unlink()
    after = workspace_facts(tmp_path)

    assert changed_workspace_paths(tmp_path, before, after) == [
        "README.md", "pkg/__init__.py", "tests/test_pkg.py",
    ]
    assert changed_workspace_paths(tmp_path, before, before) == []
    assert changed_workspace_paths(tmp_path, {"unavailable": True}, after) is None


def test_a_manager_turn_delivers_every_file_it_changed(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from argus.apps._self_reply import SelfReplyMixin
    from argus.life.delivery import MAX_DELIVERY_TARGETS

    workspace, state = tmp_path / "workspace", tmp_path / "state"
    (workspace / "tests").mkdir(parents=True)
    state.mkdir()
    (workspace / "moneyfmt.py").write_text("def fmt(v): ...\n", encoding="utf-8")
    result = SimpleNamespace(started_at=1.0, call_id="")
    before = SelfReplyMixin._workspace_facts(workspace, state)

    # A reply turn that changed nothing delivers nothing.
    assert SelfReplyMixin._delivery(result, "Here is how it works.", workspace, state, "t-0", before) is None

    for index in range(MAX_DELIVERY_TARGETS):
        (workspace / "tests" / f"test_case_{index}.py").write_text("def test(): pass\n", encoding="utf-8")
    (workspace / "moneyfmt.py").write_text("def format_amount_cn(v): ...\n", encoding="utf-8")
    # The reply names no file, as Manager replies often do.
    receipt = SelfReplyMixin._delivery(
        result, "已添加 `format_amount_cn(value)`，已补充测试。", workspace, state, "t-1", before,
    )

    assert receipt is not None
    paths = [target["path"] for target in receipt["targets"]]
    assert len(paths) == MAX_DELIVERY_TARGETS
    assert paths[0] == "moneyfmt.py"
    assert all(path.startswith("tests/") for path in paths[1:])
    assert receipt["review_source"] == "solo_output"
