"""Canonical, safe delivery receipts for terminal Argus goals.

A delivery exists only when the overall operator goal is complete *and* a
reviewed or contract-declared file can actually be opened. Intermediate mission
success, resume progress, Manager live views, and summary-only outcomes never
become completion receipts.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterable

DELIVERY_SCHEMA_VERSION = 1
MAX_DELIVERY_TARGETS = 6


def _safe_existing_path(workspace: Path, value: object) -> str | None:
    """Return one render-safe existing workspace-relative file path.

    Reuse the Manager live-view path policy so a delivery target cannot escape
    the campaign workspace or expose a credential/state file.  Resolving the
    path before accepting it also rejects symlinks which leave the workspace.
    """
    from ..manager.live_view import normalize_live_view_path

    normalized = normalize_live_view_path(value)
    if normalized is None:
        return None
    try:
        resolved = (workspace / normalized).resolve(strict=True)
        resolved.relative_to(workspace)
    except (OSError, RuntimeError, ValueError):
        return None
    return normalized if resolved.is_file() else None


def _target(
    workspace: Path,
    path: object,
    *,
    source: str,
    why: str = "",
    label: str = "",
) -> dict[str, str] | None:
    safe_path = _safe_existing_path(workspace, path)
    if safe_path is None:
        return None
    return {
        "path": safe_path,
        "label": str(label or Path(safe_path).name).strip()[:240] or Path(safe_path).name,
        "source": str(source or "reviewed_output").strip()[:80] or "reviewed_output",
        "why": str(why or "").strip()[:500],
    }


def _reviewed_targets(
    workspace: Path,
    candidates: Iterable[object],
) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    for candidate in candidates:
        result = _target(
            workspace,
            candidate,
            source="reviewer_evidence",
            why="Reviewed evidence for this completed mission.",
        )
        if result is not None:
            targets.append(result)
    return targets


def _vertical_primary_targets(
    workspace: Path,
    state_root: Path,
    stage: str,
) -> list[dict[str, str]]:
    """Return declared primary outputs for the completed stage, if any.

    A vertical contract is an explicit product declaration, unlike a broad
    file-system search.  Errors stay fail-soft because an otherwise completed
    mission must never be hidden behind a presentation lookup.
    """
    if not stage:
        return []
    try:
        from ..skills.vertical_select import resolve_vertical
        from ..verticals._base import load_vertical, vertical_stage_primary_deliverables

        vertical = resolve_vertical(state_root)
        definition = load_vertical(vertical, project_root=state_root)
        paths = vertical_stage_primary_deliverables(definition, stage=stage)
    except Exception:  # noqa: BLE001 - delivery presentation is non-authoritative
        return []
    return [
        result
        for path in paths
        if (
            result := _target(
                workspace,
                path,
                source="vertical_primary",
                why=f"Primary deliverable declared for stage {stage}.",
            )
        ) is not None
    ]


def build_delivery_receipt(
    *,
    item_id: str,
    title: str,
    summary: str,
    success: bool,
    overall_complete: bool,
    status: str,
    review_status: str,
    final_submission_certified: bool,
    workspace: Path | str | None,
    state_root: Path | str | None,
    stage: str = "",
    reviewer_artifacts: Iterable[object] = (),
) -> dict[str, Any] | None:
    """Build a terminal receipt only for a real, openable deliverable."""
    if not success or not overall_complete:
        return None
    normalized_item_id = str(item_id or "").strip()
    if not normalized_item_id:
        return None
    root: Path | None = None
    manifest_root: Path | None = None
    try:
        if workspace is not None and str(workspace).strip():
            root = Path(workspace).expanduser().resolve(strict=True)
        if state_root is not None and str(state_root).strip():
            manifest_root = Path(state_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        root = None
        manifest_root = None

    candidates: list[dict[str, str]] = []
    if root is not None and manifest_root is not None and root.is_dir() and manifest_root.is_dir():
        # Reviewer-named evidence is strongest. The vertical contract is an
        # explicit deliverable declaration. A Manager live view is deliberately
        # excluded: it is presentation state, not proof of a final artifact.
        candidates.extend(_reviewed_targets(root, reviewer_artifacts))
        candidates.extend(_vertical_primary_targets(root, manifest_root, str(stage or "")))

    targets: list[dict[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = candidate["path"]
        if path in seen:
            continue
        seen.add(path)
        targets.append(candidate)
        if len(targets) >= MAX_DELIVERY_TARGETS:
            break

    if not targets:
        return None

    kind = "submission_certified" if final_submission_certified else "task_completed"
    return {
        "schema_version": DELIVERY_SCHEMA_VERSION,
        # An item reaches a given terminal completion state once.  A stable ID
        # lets every surface deduplicate reconnect/replay notifications.
        "delivery_id": f"delivery:{normalized_item_id}:{kind}",
        "kind": kind,
        "item_id": normalized_item_id,
        "title": str(title or "Completed task").strip()[:240] or "Completed task",
        "summary": str(summary or "").strip()[:1200],
        "status": str(status or "done").strip()[:80] or "done",
        "review_status": str(review_status or "not_assessed").strip()[:80]
        or "not_assessed",
        "delivered_at": time.time(),
        "primary_target": dict(targets[0]),
        "targets": targets,
    }


__all__ = [
    "DELIVERY_SCHEMA_VERSION",
    "MAX_DELIVERY_TARGETS",
    "build_delivery_receipt",
]
