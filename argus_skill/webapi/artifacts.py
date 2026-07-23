"""Workspace-confined artifact allowlist, metadata, and previews."""

from __future__ import annotations

import mimetypes
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.event_catalog import EventType
from ..core.mission_view import load_mission_view
from ..core.session import read_session_meta, resolve_session_workdir
from ..life.memory import _read_jsonl_tail_history
from .project_state import project_life_dir, resolve_global_root

_TEXT_ARTIFACT_SUFFIXES = {
    ".bib", ".cfg", ".ini", ".log", ".py", ".rst", ".sh", ".tex", ".toml",
    ".ts", ".txt", ".yaml", ".yml",
}
_MARKDOWN_ARTIFACT_SUFFIXES = {".md", ".markdown"}
_JSON_ARTIFACT_SUFFIXES = {".ipynb", ".json", ".jsonl"}
_TABLE_ARTIFACT_SUFFIXES = {".csv", ".tsv"}
_INLINE_IMAGE_MIMES = {"image/gif", "image/jpeg", "image/png", "image/webp"}
_GIT_DIFF_LIMIT = 128 * 1024


def project_workspace(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> Path | None:
    root = resolve_global_root(global_root)
    meta = read_session_meta(root, sid)
    state_dir = project_life_dir(sid, global_root=root)
    if state_dir is None:
        return None
    try:
        workspace = resolve_session_workdir(meta, state_dir=state_dir)
    except (OSError, RuntimeError):
        return None
    return workspace if workspace.is_dir() else None


def artifact_workspace(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> Path | None:
    """Return the workspace where this session's agent writes artifacts.

    Artifact reads use the exact same persisted workdir as all agent roles.
    """
    root = resolve_global_root(global_root)
    meta = read_session_meta(root, sid)
    state_dir = project_life_dir(sid, global_root=root)
    if state_dir is None:
        return None
    try:
        workspace = resolve_session_workdir(meta, state_dir=state_dir)
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
    from ..manager.live_view import normalize_live_view_path

    if normalize_live_view_path(normalized) is None:
        return None
    try:
        resolved = (workspace / normalized).resolve(strict=False)
        resolved_relative = resolved.relative_to(workspace).as_posix()
    except (OSError, RuntimeError, ValueError):
        return None
    if normalize_live_view_path(resolved_relative) is None:
        return None
    return normalized, resolved


def latest_evidence_files(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> list[dict[str, str]]:
    _ = sid, global_root
    return []


def manager_live_view_files(
    sid: str,
    workspace: Path,
    *,
    global_root: Path | str | None = None,
) -> list[dict[str, str]]:
    from ..manager.live_view import load_live_view_decision, parse_live_view

    root = resolve_global_root(global_root)
    life_dir = project_life_dir(sid, global_root=root)
    if life_dir is None:
        return []
    view = load_live_view_decision(workspace, manifest_root=life_dir)
    if view is None:
        # Legacy sessions predate explicit workdir and stored the manifest in
        # their execution root. New sessions never inherit that project-global
        # pointer because it may belong to an unrelated historical session.
        meta = read_session_meta(root, sid)
        if meta is None or not meta.workdir.strip():
            view = load_live_view_decision(workspace)
    if view is None:
        # A pre-fix Manager could emit ``live_view: null`` during an ordinary
        # HOLD and unlink a still-useful session manifest. Recover the newest
        # valid declaration from this session's own event tape. New explicit
        # clears carry ``explicit_clear=true`` and stop recovery.
        updates = _read_jsonl_tail_history(
            life_dir / "events.jsonl",
            100,
            predicate=lambda row: str(row.get("type") or "")
            == "manager.live_view.updated",
            raw_predicate=lambda raw: b"manager.live_view.updated" in raw,
            raw_markers=(b"manager.live_view.updated",),
        )
        for update in reversed(updates):
            if update.get("explicit_clear") is True:
                break
            paths = update.get("paths")
            if not isinstance(paths, list) or not paths:
                continue
            recovered = parse_live_view({
                "title": update.get("title") or "Live project view",
                "reason": update.get("reason") or "Last valid Manager view.",
                "paths": paths,
            })
            if recovered is not None:
                view = recovered
                break
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


def registered_research_artifacts(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> list[dict[str, str]]:
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return []
    view = load_mission_view(life_dir)
    return [
        {
            "path": str(item.get("path") or "").strip(),
            "why": str(item.get("why") or item.get("title") or "").strip(),
            "source": "research_registered",
            "group_title": "Research artifacts",
        }
        for item in view.get("artifacts", [])
        if isinstance(item, dict) and str(item.get("path") or "").strip()
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
        "html" if suffix == ".html"
        else "markdown" if suffix in _MARKDOWN_ARTIFACT_SUFFIXES
        else "json" if suffix in _JSON_ARTIFACT_SUFFIXES
        else "table" if suffix in _TABLE_ARTIFACT_SUFFIXES
        else "text" if suffix in _TEXT_ARTIFACT_SUFFIXES
        else "image" if mime in _INLINE_IMAGE_MIMES
        else "pdf" if mime == "application/pdf"
        else "audio" if mime.startswith("audio/")
        else "video" if mime.startswith("video/")
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
    if preview_bytes > 0 and exists and kind in {
        "text", "html", "markdown", "json", "table",
    }:
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
    workspace = artifact_workspace(sid, global_root=global_root)
    if workspace is None:
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    evidence_rows = [
        *manager_live_view_files(sid, workspace, global_root=global_root),
        *registered_research_artifacts(sid, global_root=global_root),
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
        if (
            row is not None
            and evidence["source"] == "manager_live"
            and not row["exists"]
        ):
            continue
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
    workspace = artifact_workspace(sid, global_root=global_root)
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
    workspace = artifact_workspace(sid, global_root=global_root)
    if info is None or workspace is None:
        return None
    safe = safe_artifact_path(workspace, str(info["path"]))
    if safe is None or not safe[1].is_file():
        return None
    return info, safe[1]


def project_git_diff(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> dict[str, Any] | None:
    if project_life_dir(sid, global_root=global_root) is None:
        return None
    workspace = project_workspace(sid, global_root=global_root)
    if workspace is None or not (workspace / ".git").exists():
        return {
            "available": False,
            "branch": "",
            "status": "",
            "stat": "",
            "diff": "",
            "truncated": False,
        }

    def run(*args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(workspace), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return result.stdout if result.returncode == 0 else ""

    branch = run("branch", "--show-current").strip()
    status = run("status", "--short")
    stat = run("diff", "--stat", "--", ".")
    unstaged = run("diff", "--no-ext-diff", "--unified=2", "--", ".")
    staged = run("diff", "--cached", "--no-ext-diff", "--unified=2", "--", ".")
    combined = ""
    if staged:
        combined += "# Staged\n" + staged
    if unstaged:
        combined += ("\n" if combined else "") + "# Working tree\n" + unstaged
    encoded = combined.encode("utf-8")
    truncated = len(encoded) > _GIT_DIFF_LIMIT
    if truncated:
        combined = encoded[:_GIT_DIFF_LIMIT].decode("utf-8", errors="replace")
    return {
        "available": True,
        "branch": branch,
        "status": status[:16_000],
        "stat": stat[:16_000],
        "diff": combined,
        "truncated": truncated,
    }


__all__ = [
    "artifact_workspace",
    "artifact_metadata",
    "get_project_artifact",
    "latest_evidence_files",
    "list_project_artifacts",
    "manager_live_view_files",
    "project_workspace",
    "project_git_diff",
    "registered_research_artifacts",
    "resolved_project_artifact",
    "safe_artifact_path",
]
