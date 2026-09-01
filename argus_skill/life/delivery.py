"""Canonical, safe delivery receipts for terminal Argus goals.

A delivery exists only when the overall operator goal is complete *and* a
reviewed or contract-declared file can actually be opened. Intermediate mission
success, resume progress, Manager live views, and summary-only outcomes never
become completion receipts.
"""

from __future__ import annotations

import html
import re
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

DELIVERY_SCHEMA_VERSION = 1
MAX_DELIVERY_TARGETS = 6

_MARKDOWN_TARGET_RE = re.compile(
    r"!?\[[^\]\r\n]*\]\(\s*(?:<(?P<angled>[^>\r\n]+)>|(?P<plain>[^\s)\r\n]+))",
)
_INLINE_CODE_RE = re.compile(r"(?<!`)`([^`\r\n]{1,2048})`(?!`)")
_BOOK_TITLE_TARGET_RE = re.compile(r"《([^》\r\n]{1,2048})》")
_WINDOWS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])/?[A-Za-z]:[\\/][^\s<>\[\]()`\"']+",
)
_TERMINAL_PUNCTUATION = " \t\r\n\"'<>[](){}.,;，。；："


def _referenced_path_candidates(text: object) -> list[str]:
    body = str(text or "")
    candidates: list[str] = []
    for match in _MARKDOWN_TARGET_RE.finditer(body):
        candidates.append(match.group("angled") or match.group("plain") or "")
    candidates.extend(match.group(1) for match in _INLINE_CODE_RE.finditer(body))
    candidates.extend(match.group(1) for match in _BOOK_TITLE_TARGET_RE.finditer(body))
    candidates.extend(match.group(0) for match in _WINDOWS_PATH_RE.finditer(body))
    return candidates


def _workspace_relative_reference(workspace: Path, value: object) -> str | None:
    """Resolve one explicit completion-message path inside ``workspace``."""
    raw = html.unescape(str(value or "")).strip(_TERMINAL_PUNCTUATION)
    if not raw or "\x00" in raw:
        return None
    lowered = raw.casefold()
    if lowered.startswith(("http://", "https://", "data:", "javascript:")):
        return None
    if lowered.startswith("file:"):
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return None
        if parsed.netloc not in {"", "localhost"}:
            return None
        raw = unquote(parsed.path)
    else:
        raw = unquote(raw).split("#", 1)[0].split("?", 1)[0]
    raw = raw.strip(_TERMINAL_PUNCTUATION)
    # Markdown commonly serializes a Windows file URL as /C:/path/file.pdf.
    if re.match(r"^/[A-Za-z]:[\\/]", raw):
        raw = raw[1:]
    if not raw:
        return None

    try:
        root = workspace.expanduser().resolve(strict=True)
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(root).as_posix()
        else:
            relative = raw.replace("\\", "/")
            resolved = (root / relative).resolve(strict=True)
            relative = resolved.relative_to(root).as_posix()
    except (OSError, RuntimeError, ValueError):
        return None
    return _safe_existing_path(root, relative)


def referenced_delivery_paths(
    workspace: Path | str,
    texts: Iterable[object],
    *,
    limit: int = MAX_DELIVERY_TARGETS,
) -> list[str]:
    """Return safe files explicitly linked by an accepted completion summary.

    This is deliberately not a workspace scan. Only paths named in the terminal
    message are considered, and every candidate still passes the same confined,
    credential-aware policy as Manager live-view and artifact downloads.
    """
    root = Path(workspace)
    max_paths = max(0, int(limit))
    if max_paths == 0:
        return []
    paths: list[str] = []
    for text in texts:
        for candidate in _referenced_path_candidates(text):
            path = _workspace_relative_reference(root, candidate)
            if path and path not in paths:
                paths.append(path)
            if len(paths) >= max_paths:
                return paths
    return paths


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
    "referenced_delivery_paths",
]
