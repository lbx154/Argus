"""Canonical, safe delivery receipts for terminal Argus goals.

A delivery exists only when the overall operator goal is complete *and* a file
its Engineer's hand_off named (``files``) or its vertical contract declares can
actually be opened. Intermediate mission success, resume progress, Manager live
views, and summary-only outcomes never become completion receipts.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from ..core.file_facts import file_facts
from ..core.launch_settings import strip_launch_note

DELIVERY_SCHEMA_VERSION = 1
MAX_DELIVERY_TARGETS = 6
# Snapshots keep each delivery readable after later turns overwrite the same
# file. Only modest text files are copied; anything larger or binary is
# recorded by hash so the history still proves *which* version was delivered.
MAX_SNAPSHOT_BYTES = 512 * 1024
SNAPSHOT_DIRNAME = "delivery_snapshots"
# Each work item keeps the files of its most recent deliveries; older versions
# no item still references are deleted so the store cannot grow without bound.
MAX_SNAPSHOT_DELIVERIES_PER_ITEM = 10
_SNAPSHOT_INDEX = "index.json"
_SNAPSHOT_VERSION = re.compile(r"[0-9a-f]{32}")


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
        "source": source,
        "why": str(why or "").strip()[:500],
    }


#: Who stands behind each declared file, as its receipt says.
_DECLARED_WHY = {
    "reviewer_accepted": "Declared by the Engineer and accepted by the Reviewer.",
    "engineer_declared": "Declared by the Engineer; no independent review accepted it.",
    "solo_output": "Solo output for this completed task.",
}


def declared_source(review_source: object) -> str:
    """The source of the files an Engineer's hand_off declared, by who settled the task."""
    return "reviewer_accepted" if review_source == "reviewer" else "engineer_declared"


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
        from ..verticals._base import load_vertical_contract

        vertical = resolve_vertical(state_root)
        contract = load_vertical_contract(vertical, project_root=state_root)
        paths = contract.primary_deliverables(stage=stage)
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


def _snapshot_dir(state_root: Path) -> Path:
    return state_root / SNAPSHOT_DIRNAME


def _snapshot_index(root: Path) -> dict[str, list[dict[str, Any]]]:
    try:
        raw = json.loads((root / _SNAPSHOT_INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        str(item): [
            row for row in rows
            if isinstance(row, dict) and isinstance(row.get("n"), int) and isinstance(row.get("files"), dict)
        ]
        for item, rows in (raw.items() if isinstance(raw, dict) else [])
        if isinstance(rows, list)
    }


def _is_text(data: bytes) -> bool:
    if len(data) > MAX_SNAPSHOT_BYTES or b"\0" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _keep_copy(root: Path, path: Path, data: bytes, last: str) -> tuple[str, bool]:
    """The version of one delivered file, copying its text unless the last copy matches."""
    if not _is_text(data):
        return ":".join(map(str, file_facts(path))), False
    if _SNAPSHOT_VERSION.fullmatch(last):
        try:
            if (root / last).read_bytes() == data:
                return last, True
        except OSError:
            pass
    version = uuid.uuid4().hex
    tmp = root / f"{version}.tmp"
    tmp.write_bytes(data)
    tmp.replace(root / version)
    return version, True


def snapshot_delivery_targets(
    workspace: Path,
    state_root: Path,
    targets: Iterable[dict[str, str]],
    *,
    item_id: str = "",
) -> tuple[list[dict[str, Any]], int]:
    """Keep a copy of each delivered text file so later deliveries cannot erase it.

    Returns the snapshots and this item's delivery number. A file whose bytes
    equal the item's last copy keeps that copy's version, and a delivery of
    the same versions keeps its number, so a replay or rebuild of unchanged
    files is not a new delivery. Large or binary files are named by their
    ``size:mtime_ns:ctime_ns`` and not copied.
    """
    from argus.core.file_lock import exclusive_file_lock

    root = _snapshot_dir(state_root)
    out: list[dict[str, Any]] = []
    try:
        root.mkdir(parents=True, exist_ok=True)
        with open(root / ".lock", "a+") as lock, exclusive_file_lock(
            lock, lock_name="delivery snapshot index lock"
        ):
            index = _snapshot_index(root)
            deliveries = index.setdefault(item_id or "_", [])
            last = {path: version for row in deliveries for path, version in row["files"].items()}
            for target in targets:
                rel = str(target.get("path") or "")
                try:
                    data = (workspace / rel).read_bytes()
                except OSError:
                    continue
                version, stored = _keep_copy(root, workspace / rel, data, str(last.get(rel) or ""))
                out.append({"path": rel, "version": version, "size": len(data), "stored": stored})
            files = {entry["path"]: entry["version"] for entry in out}
            if deliveries and deliveries[-1]["files"] == files:
                number = deliveries[-1]["n"]
            else:
                number = deliveries[-1]["n"] + 1 if deliveries else 1
                deliveries.append({"n": number, "files": files})
                del deliveries[:-MAX_SNAPSHOT_DELIVERIES_PER_ITEM]
            tmp = root / f"{_SNAPSHOT_INDEX}.{os.getpid()}.tmp"
            tmp.write_text(json.dumps(index), encoding="utf-8")
            tmp.replace(root / _SNAPSHOT_INDEX)
            keep = {
                version for rows in index.values() for row in rows for version in row["files"].values()
            } | {_SNAPSHOT_INDEX, ".lock"}
            for blob in root.iterdir():
                if blob.is_file() and blob.name not in keep:
                    blob.unlink(missing_ok=True)
    except (OSError, TimeoutError):
        return out, 0
    return out, number


def read_delivery_snapshot(state_root: Path | str, version: str) -> str | None:
    """Return the stored text of a delivered file version, if it was kept."""
    if not _SNAPSHOT_VERSION.fullmatch(str(version or "")):
        return None
    try:
        return (_snapshot_dir(Path(state_root)) / version).read_text(encoding="utf-8")
    except OSError:
        return None


def delivery_snapshot_diff(
    state_root: Path | str, previous: dict[str, Any], current: dict[str, Any]
) -> str | None:
    """Unified diff between two delivered versions of the same file."""
    before = read_delivery_snapshot(state_root, str(previous.get("version") or ""))
    after = read_delivery_snapshot(state_root, str(current.get("version") or ""))
    if before is None or after is None:
        return None
    path = str(current.get("path") or previous.get("path") or "file")
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def delivery_is_current(
    workspace: Path | str, state_root: Path | str, snapshots: Iterable[object],
) -> bool:
    """Whether every file a delivery recorded still holds the version it delivered.

    A kept text copy is compared byte for byte, a large or binary file by its
    file facts. A missing, pruned or unreadable version counts as a change, so
    nothing leans on a review of files it can no longer show are unchanged.
    """
    rows = [row for row in snapshots or () if isinstance(row, dict)]
    try:
        root = Path(workspace).expanduser().resolve(strict=True)
        store = _snapshot_dir(Path(state_root).expanduser().resolve(strict=True))
    except (OSError, RuntimeError, TypeError):
        return False
    for row in rows:
        path = _safe_existing_path(root, row.get("path"))
        version = str(row.get("version") or "")
        if path is None:
            return False
        try:
            if row.get("stored"):
                if not _SNAPSHOT_VERSION.fullmatch(version) or (
                    (store / version).read_bytes() != (root / path).read_bytes()
                ):
                    return False
            elif ":".join(map(str, file_facts(root / path))) != version:
                return False
        except OSError:
            return False
    return bool(rows)


_DOC_SUFFIXES = {".html", ".pdf", ".md", ".markdown", ".csv", ".tsv", ".rst", ".txt"}
_TEST_FILE_RE = re.compile(r"^test_|_test\.[^.]+$|\.(?:test|spec)\.[^.]+$", re.IGNORECASE)


def _is_test_path(path: str) -> bool:
    parts = Path(path).parts
    return bool(parts) and (
        any(part.casefold() in {"test", "tests", "__tests__", "spec"} for part in parts[:-1])
        or bool(_TEST_FILE_RE.search(parts[-1]))
    )


def changed_workspace_paths(
    workspace: Path | str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[str] | None:
    """Files that differ between two ``workspace_facts`` of ``workspace``, deliverables first.

    Docs lead, then other outputs, then tests. Removed files are not
    deliverables and drop out.
    ``None`` when either side is unknown, which is never evidence that nothing
    changed.
    """
    if before.get("unavailable") or after.get("unavailable"):
        return None

    def rows(facts: dict[str, Any]) -> dict[str, str]:
        if "files" in facts:
            return {str(row[0]): json.dumps(row[1:]) for row in facts["files"]}
        return {str(row[1]): json.dumps(row) for row in facts.get("changes") or ()}

    old, new = rows(before), rows(after)
    paths = {path for path in {*old, *new} if old.get(path) != new.get(path)}
    if before.get("tree") != after.get("tree"):
        # The turn committed: what the commits changed left ``git status``.
        import subprocess

        try:
            paths.update(subprocess.run(
                ["git", "diff", "--name-only", "-z", str(before.get("tree")), str(after.get("tree"))],
                cwd=workspace, check=True, capture_output=True, timeout=10,
            ).stdout.decode("utf-8", "replace").split("\0"))
        except (OSError, subprocess.SubprocessError):
            return None
    root = Path(workspace)
    return sorted(
        (path for path in paths if path and (root / path).is_file()),
        key=lambda path: (
            _is_test_path(path), Path(path).suffix.lower() not in _DOC_SUFFIXES, path,
        ),
    )


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
    review_source: str = "",
    residual_risk: str = "",
    workspace: Path | str | None,
    state_root: Path | str | None,
    stage: str = "",
    deliverables: Iterable[tuple[object, str]] = (),
    stage_package: bool = False,
) -> dict[str, Any] | None:
    """Build a receipt only for a real, openable deliverable.

    A terminal receipt needs the whole goal complete. A ``stage_package`` task
    (a reviewed interim research package, or the operator's own request while
    the campaign goes on) gets a non-terminal receipt once its own Reviewer
    accepted it; the campaign and ``overall_complete`` stay as they are, so it
    cannot stand in for the final result. ``deliverables`` are the declared
    files, each with its source (``_DECLARED_WHY``).
    """
    interim = (
        stage_package and not overall_complete
        and review_status == "done" and review_source == "reviewer"
    )
    if not success or not (overall_complete or interim):
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
        # Declared files lead, then the vertical contract's. A Manager live
        # view is deliberately excluded: it is presentation state, not proof
        # of a final artifact.
        candidates.extend(
            target for path, source in deliverables
            if (target := _target(root, path, source=source, why=_DECLARED_WHY[source])) is not None
        )
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

    kind = (
        "submission_certified" if final_submission_certified
        else "stage_package" if interim
        else "task_completed"
    )
    delivered_at = time.time()
    # The same item can be delivered again after later work changes its files.
    # Its number is the delivered content: a replay or rebuild of unchanged
    # files keeps this ID (no duplicate notice), while a redelivery with changed
    # files gets a new one (new notice, its own snapshot in history).
    snapshots, revision = snapshot_delivery_targets(
        root, manifest_root, targets, item_id=normalized_item_id,
    )
    return {
        "schema_version": DELIVERY_SCHEMA_VERSION,
        "delivery_id": f"delivery:{normalized_item_id}:{kind}:{revision}",
        "kind": kind,
        "item_id": normalized_item_id,
        # The settings a request was launched with are not what was delivered.
        "title": strip_launch_note(title)[:240] or "Completed task",
        "summary": str(summary or "").strip()[:1200],
        "status": str(status or "done").strip()[:80] or "done",
        "review_status": str(review_status or "not_assessed").strip()[:80]
        or "not_assessed",
        # Who settled that status: ``reviewer`` is a separate review role;
        # ``engineer_self_review`` and friends are the worker checking itself.
        # A ``done`` status alone does not say which, so the UI needs this.
        "review_source": str(review_source or "").strip()[:80],
        # One check approved unverified on an accepted risk; the delivery
        # card shows it instead of a plain pass.
        "residual_risk": " ".join(str(residual_risk or "").split())[:600],
        "delivered_at": delivered_at,
        "primary_target": dict(targets[0]),
        "targets": targets,
        "snapshots": snapshots,
    }


__all__ = [
    "DELIVERY_SCHEMA_VERSION",
    "MAX_DELIVERY_TARGETS",
    "build_delivery_receipt",
    "changed_workspace_paths",
    "declared_source",
    "delivery_is_current",
    "delivery_snapshot_diff",
    "read_delivery_snapshot",
    "snapshot_delivery_targets",
]
