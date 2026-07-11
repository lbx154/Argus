"""Workspace-confined artifact allowlist, metadata, and previews."""

from __future__ import annotations

import mimetypes
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.event_catalog import EventType
from ..core.session import read_session_meta
from ..life.memory import _read_jsonl_tail_history
from .project_state import project_life_dir, resolve_global_root

_TEXT_ARTIFACT_SUFFIXES = {
    ".bib", ".cfg", ".csv", ".html", ".ini", ".json", ".jsonl", ".log",
    ".md", ".py", ".rst", ".sh", ".tex", ".toml", ".tsv", ".txt", ".yaml",
    ".yml",
}
_INLINE_IMAGE_MIMES = {"image/gif", "image/jpeg", "image/png", "image/webp"}


def project_workspace(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> Path | None:
    root = resolve_global_root(global_root)
    meta = read_session_meta(root, sid)
    if meta is None or not meta.cwd.strip():
        return None
    try:
        workspace = Path(meta.cwd).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return workspace if workspace.is_dir() else None


def safe_artifact_path(workspace: Path, relative_path: str) -> tuple[str, Path] | None:
    raw = str(relative_path or "").strip().replace("\\", "/")
    if not raw or "\x00" in raw:
        return None
    rel = PurePosixPath(raw)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    normalized = rel.as_posix()
    if normalized in {"", "."}:
        return None
    try:
        resolved = (workspace / normalized).resolve(strict=False)
        resolved.relative_to(workspace)
    except (OSError, RuntimeError, ValueError):
        return None
    return normalized, resolved


def latest_evidence_files(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> list[dict[str, str]]:
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return []
    events = _read_jsonl_tail_history(
        life_dir / "events.jsonl",
        1,
        predicate=lambda row: str(row.get("type") or "")
        == EventType.LIFE_MISSION_COMPLETED,
    )
    latest = events[-1] if events else {}
    report = latest.get("planner_report") if isinstance(latest, dict) else {}
    evidence = report.get("evidence_files") if isinstance(report, dict) else None
    if not isinstance(evidence, list) or not evidence:
        return []
    return [
        {
            "path": str(item["path"]).strip(),
            "why": str(item.get("why") or "").strip(),
        }
        for item in evidence
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    ]


def manager_live_view_files(workspace: Path) -> list[dict[str, str]]:
    from ..manager.live_view import load_live_view_decision

    view = load_live_view_decision(workspace)
    if view is None:
        return []
    return [
        {
            "path": path,
            "why": view.reason,
            "source": "manager_live",
            "group_title": view.title,
        }
        for path in view.paths
    ]


def artifact_metadata(
    workspace: Path,
    relative_path: str,
    *,
    why: str = "",
    preview_bytes: int = 0,
) -> dict[str, Any] | None:
    safe = safe_artifact_path(workspace, relative_path)
    if safe is None:
        return None
    normalized, resolved = safe
    try:
        exists = resolved.is_file()
        stat = resolved.stat() if exists else None
    except OSError:
        exists = False
        stat = None
    mime = mimetypes.guess_type(normalized)[0] or "application/octet-stream"
    suffix = resolved.suffix.lower()
    kind = (
        "text" if suffix in _TEXT_ARTIFACT_SUFFIXES
        else "image" if mime in _INLINE_IMAGE_MIMES
        else "pdf" if mime == "application/pdf"
        else "binary"
    )
    row: dict[str, Any] = {
        "path": normalized,
        "name": Path(normalized).name,
        "why": why,
        "exists": exists,
        "kind": kind,
        "mime": mime,
        "size": int(stat.st_size) if stat is not None else 0,
        "mtime": float(stat.st_mtime) if stat is not None else None,
    }
    if preview_bytes > 0 and exists and kind == "text":
        try:
            with resolved.open("rb") as handle:
                raw = handle.read(preview_bytes + 1)
            row["preview"] = raw[:preview_bytes].decode("utf-8", errors="replace")
            row["truncated"] = len(raw) > preview_bytes
        except OSError:
            row["preview"] = ""
            row["truncated"] = False
    return row


def list_project_artifacts(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> list[dict[str, Any]] | None:
    if project_life_dir(sid, global_root=global_root) is None:
        return None
    workspace = project_workspace(sid, global_root=global_root)
    if workspace is None:
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    evidence_rows = [
        *manager_live_view_files(workspace),
        *(
            {
                **evidence,
                "source": "reviewer_evidence",
                "group_title": "Latest reviewed result",
            }
            for evidence in latest_evidence_files(sid, global_root=global_root)
        ),
    ]
    for evidence in evidence_rows:
        row = artifact_metadata(workspace, evidence["path"], why=evidence["why"])
        if row is not None and row["path"] not in seen:
            row["source"] = evidence["source"]
            row["group_title"] = evidence["group_title"]
            seen.add(row["path"])
            rows.append(row)
    return rows


def get_project_artifact(
    sid: str,
    artifact_path: str,
    *,
    global_root: Path | str | None = None,
    preview_bytes: int = 128 * 1024,
) -> dict[str, Any] | None:
    artifacts = list_project_artifacts(sid, global_root=global_root)
    if artifacts is None:
        return None
    workspace = project_workspace(sid, global_root=global_root)
    if workspace is None:
        return None
    safe_requested = safe_artifact_path(workspace, artifact_path)
    if safe_requested is None:
        return None
    requested = safe_requested[0]
    allowed = next((row for row in artifacts if row["path"] == requested), None)
    if allowed is None or not allowed["exists"]:
        return None
    row = artifact_metadata(
        workspace,
        requested,
        why=str(allowed.get("why") or ""),
        preview_bytes=max(0, min(int(preview_bytes), 512 * 1024)),
    )
    if row is None:
        return None
    row["source"] = str(allowed.get("source") or "reviewer_evidence")
    row["group_title"] = str(allowed.get("group_title") or "")
    return row


def resolved_project_artifact(
    sid: str,
    artifact_path: str,
    *,
    global_root: Path | str | None = None,
) -> tuple[dict[str, Any], Path] | None:
    info = get_project_artifact(
        sid,
        artifact_path,
        global_root=global_root,
        preview_bytes=0,
    )
    workspace = project_workspace(sid, global_root=global_root)
    if info is None or workspace is None:
        return None
    safe = safe_artifact_path(workspace, str(info["path"]))
    if safe is None or not safe[1].is_file():
        return None
    return info, safe[1]


__all__ = [
    "artifact_metadata",
    "get_project_artifact",
    "latest_evidence_files",
    "list_project_artifacts",
    "manager_live_view_files",
    "project_workspace",
    "resolved_project_artifact",
    "safe_artifact_path",
]
