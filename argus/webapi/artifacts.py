"""Workspace-confined artifact allowlist, metadata, and previews."""

from __future__ import annotations

import mimetypes
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.mission_view import load_mission_view
from ..core.session import read_session_meta, resolve_session_workdir
from ..life.memory import _read_jsonl_tail_history
from .map_relations import PATH_LIMIT, reply_paths
from .project_state import project_life_dir, resolve_global_root

_TEXT_ARTIFACT_SUFFIXES = {
    ".diff", ".patch",
    ".bib", ".cfg", ".css", ".js", ".mjs", ".ini", ".log", ".py", ".rst", ".sh", ".tex", ".toml",
    ".svg", ".ts", ".txt", ".yaml", ".yml",
}
_MARKDOWN_ARTIFACT_SUFFIXES = {".md", ".markdown"}
_JSON_ARTIFACT_SUFFIXES = {".ipynb", ".json", ".jsonl"}
_TABLE_ARTIFACT_SUFFIXES = {".csv", ".tsv"}
# Robot descriptions and meshes the Web viewer renders in 3D. Their sibling
# geometry (meshes, glTF buffers, textures) is read through
# ``resolve_model_asset`` relative to the registered model file.
_MODEL_ARTIFACT_SUFFIXES = {".urdf", ".stl", ".obj", ".dae", ".glb", ".gltf"}
MODEL_ASSET_SUFFIXES = frozenset(_MODEL_ARTIFACT_SUFFIXES | {
    ".bin", ".mtl", ".png", ".jpg", ".jpeg", ".webp",
})
_MODEL_MIMES = {
    ".urdf": "application/xml",
    ".stl": "model/stl",
    ".obj": "model/obj",
    ".mtl": "model/mtl",
    ".dae": "model/vnd.collada+xml",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".bin": "application/octet-stream",
}
# A loop closure lives outside the URDF tree; the viewer says so when one sits
# next to the model instead of pretending the tree is the whole mechanism.
_CONSTRAINT_FILE_HINTS = ("closure", "constraint", "loop")
_CONSTRAINT_FILE_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".txt", ".md"}
_INLINE_IMAGE_MIMES = {"image/gif", "image/jpeg", "image/png", "image/webp"}
_GIT_DIFF_LIMIT = 128 * 1024


def _effective_workspace(state_dir: Path, workspace: Path) -> Path:
    from ..core.campaign_workdir import active_campaign_workdir

    return active_campaign_workdir(state_dir, workspace) or workspace


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
        workspace = _effective_workspace(
            state_dir,
            resolve_session_workdir(meta, state_dir=state_dir),
        )
    except (OSError, RuntimeError):
        return None
    return workspace if workspace.is_dir() else None


def artifact_workspace(
    sid: str,
    *,
    global_root: Path | str | None = None,
) -> Path | None:
    """Return the repository where this campaign's agent writes artifacts."""
    root = resolve_global_root(global_root)
    meta = read_session_meta(root, sid)
    state_dir = project_life_dir(sid, global_root=root)
    if state_dir is None:
        return None
    try:
        workspace = _effective_workspace(
            state_dir,
            resolve_session_workdir(meta, state_dir=state_dir),
        )
    except (OSError, RuntimeError):
        return None
    return workspace if workspace.is_dir() else None


def safe_artifact_path(
    workspace: Path, relative_path: str, *, allowed_suffixes: frozenset[str] | None = None,
) -> tuple[str, Path] | None:
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

    suffix_options = {"allowed_suffixes": allowed_suffixes} if allowed_suffixes is not None else {}
    if normalize_live_view_path(normalized, **suffix_options) is None:
        return None
    try:
        resolved = (workspace / normalized).resolve(strict=False)
        resolved_relative = resolved.relative_to(workspace).as_posix()
    except (OSError, RuntimeError, ValueError):
        return None
    if normalize_live_view_path(resolved_relative, **suffix_options) is None:
        return None
    return normalized, resolved


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


def registered_delivery_artifacts(
    sid: str,
    *,
    global_root: Path | str | None = None,
    workspace: Path,
) -> list[dict[str, str]]:
    """Expose the files delivery receipts name, then the files Argus's replies link.

    A linked file is shown as a reply's, never as delivered. Every row still
    goes through ``artifact_metadata``, so confinement remains authoritative.
    """
    life_dir = project_life_dir(sid, global_root=global_root)
    if life_dir is None:
        return []
    view = load_mission_view(life_dir)
    delivery = view.get("delivery")
    mission = view.get("mission") if isinstance(view.get("mission"), dict) else {}
    title = "Delivered results"
    targets: list[Any] = []
    if isinstance(delivery, dict):
        title = str(delivery.get("title") or title).strip() or title
        raw_targets = delivery.get("targets")
        if isinstance(raw_targets, list):
            targets = raw_targets
    elif str(mission.get("title") or "").strip():
        title = str(mission.get("title") or title).strip()
    from ..core.transcript import read_turns

    replies: list[str] = []
    for turn in reversed(read_turns(life_dir)):
        transcript_delivery = turn.get("delivery")
        if isinstance(transcript_delivery, dict) and isinstance(transcript_delivery.get("targets"), list):
            targets.extend(transcript_delivery["targets"])
        if turn.get("role") == "argus" and turn.get("success") is not False and not turn.get("live"):
            replies.append(str(turn.get("text") or ""))

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for target in targets:
        if not isinstance(target, dict):
            continue
        path = str(target.get("path") or "").strip()
        if not path or path in seen:
            continue
        label = str(target.get("label") or "").strip()
        why = str(target.get("why") or label or "Delivered output.").strip()
        seen.add(path)
        results.append({
            "path": path,
            "why": why,
            "source": "delivery",
            "group_title": title,
        })
    linked = dict.fromkeys(path for text in replies for path in reply_paths(text, str(workspace)))
    for path in [path for path in linked if path not in seen][:PATH_LIMIT]:
        results.append({
            "path": path,
            "why": "File referenced in a saved reply; not an independently reviewed result.",
            "source": "reply",
            "group_title": "Referenced files",
        })
    return results


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
    return _file_metadata(normalized, resolved, why=why, preview_bytes=preview_bytes)


def _file_metadata(normalized: str, resolved: Path, *, why: str = "", preview_bytes: int = 0) -> dict[str, Any]:
    try:
        exists = resolved.is_file()
        stat = resolved.stat() if exists else None
    except OSError:
        exists = False
        stat = None
    suffix = resolved.suffix.lower()
    mime = _MODEL_MIMES.get(suffix) or mimetypes.guess_type(normalized)[0] or "application/octet-stream"
    kind = (
        "model" if suffix in _MODEL_ARTIFACT_SUFFIXES
        else "html" if suffix == ".html"
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
        # Authenticated local clients may show the real storage address on
        # hover; all reads still go through the protected relative-path API.
        "storage_path": str(resolved),
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
    if preview_bytes > 0 and exists and suffix == ".urdf":
        row["constraint_files"] = _constraint_files(resolved.parent)
    return row


def _constraint_files(directory: Path) -> list[str]:
    try:
        names = sorted(entry.name for entry in directory.iterdir() if entry.is_file())
    except OSError:
        return []
    return [
        name for name in names
        if not name.startswith(".")
        and Path(name).suffix.lower() in _CONSTRAINT_FILE_SUFFIXES
        and any(hint in name.lower() for hint in _CONSTRAINT_FILE_HINTS)
    ][:8]


def _model_asset_candidates(model_dir: PurePosixPath, ref: str) -> list[PurePosixPath]:
    """Workspace-relative places a model's mesh reference may point to.

    Relative references resolve against the model's own directory. ROS
    ``package://name/rest`` has no package index here, so it resolves against
    the model's enclosing directories: one named ``name`` first, then
    ``<dir>/name/rest``, then ``<dir>/rest``, nearest first.
    """
    import posixpath

    if ref.startswith("package://"):
        package, _, rest = ref[len("package://"):].partition("/")
        if not package or not rest:
            return []
        ancestors = [model_dir, *model_dir.parents]
        named = [base / rest for base in ancestors if base.name == package]
        nested = [base / package / rest for base in ancestors]
        flat = [base / rest for base in ancestors]
        raw = [*named, *nested, *flat]
    elif "://" in ref or ref.startswith(("/", "~")) or ":" in ref.split("/", 1)[0]:
        return []
    else:
        raw = [model_dir / ref]
    out: list[PurePosixPath] = []
    for candidate in raw:
        normalized = posixpath.normpath(candidate.as_posix())
        if normalized.startswith("../") or normalized in {"..", ".", ""}:
            continue
        path = PurePosixPath(normalized)
        if path not in out:
            out.append(path)
    return out


def resolve_model_asset(
    sid: str,
    model_path: str,
    ref: str,
    *,
    global_root: Path | str | None = None,
) -> tuple[str, Path] | None:
    """Return one mesh, buffer or texture a registered model file references.

    The model itself must pass the artifact allowlist; the asset must stay in
    the same workspace, pass the same sensitive-path checks and carry a model
    asset suffix. Nothing else in the workspace becomes readable this way.
    """
    raw_ref = str(ref or "").strip().replace("\\", "/")
    if not raw_ref or "\x00" in raw_ref or len(raw_ref) > 1024:
        return None
    info = get_project_artifact(sid, model_path, global_root=global_root, preview_bytes=0)
    if info is None or info.get("kind") != "model" or info.get("source") in {
        "reader_foundation", "progress_snapshot", "attachment",
    }:
        return None
    workspace = artifact_workspace(sid, global_root=global_root)
    if workspace is None:
        return None
    model_dir = PurePosixPath(str(info["path"])).parent
    for candidate in _model_asset_candidates(model_dir, raw_ref.split("?", 1)[0].split("#", 1)[0]):
        safe = safe_artifact_path(workspace, candidate.as_posix(), allowed_suffixes=MODEL_ASSET_SUFFIXES)
        if safe is not None and safe[1].is_file():
            return safe
    return None


def model_asset_mime(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    return _MODEL_MIMES.get(suffix) or mimetypes.guess_type(path)[0] or "application/octet-stream"


def _uploaded_artifact(sid: str, artifact_path: str, *, global_root=None, preview_bytes=0):
    from .attachment_uploads import _directory
    from .attachments import _ATTACHMENT_ID_RE, _validate_metadata_payload

    parts = PurePosixPath(artifact_path).parts
    if (len(parts) != 5 or parts[:3] != (".argus", "attachments", sid)
            or not _ATTACHMENT_ID_RE.fullmatch(parts[3])):
        return None
    workspace = project_workspace(sid, global_root=global_root)
    if workspace is None:
        return None
    try:
        with _directory(workspace, sid, parts[3]) as directory:
            if not directory.exists("metadata.json"):
                return None
            metadata = directory.read("metadata.json")
            name = _validate_metadata_payload(metadata, sid, parts[3])
            if artifact_path != metadata["relative_path"]:
                return None
            with directory.file(name) as handle:
                import os
                if os.fstat(handle.fileno()).st_size != metadata["size_bytes"]:
                    raise ValueError("attachment payload size mismatch")
                row = _file_metadata(artifact_path, workspace / artifact_path)
                if preview_bytes and row["kind"] in {"markdown", "json", "text", "table"}:
                    raw = handle.read(preview_bytes + 1)
                    row["preview"] = raw[:preview_bytes].decode("utf-8", errors="replace")
                    row["truncated"] = len(raw) > preview_bytes
    except FileNotFoundError:
        return None
    return {**row, "name": metadata["original_name"], "mime": metadata["mime"],
            "source": "attachment", "group_title": "Uploaded files"}


def _uploaded_artifacts(sid: str, *, global_root=None):
    import os

    from .attachment_uploads import _directory
    from .attachments import _ATTACHMENT_ID_RE, _validate_metadata_payload

    workspace = project_workspace(sid, global_root=global_root)
    if workspace is None:
        return []
    try:
        with _directory(workspace, sid) as directory:
            names = os.listdir(directory.location)
    except FileNotFoundError:
        return []
    rows = []
    for attachment_id in names:
        if not _ATTACHMENT_ID_RE.fullmatch(attachment_id):
            continue
        try:
            with _directory(workspace, sid, attachment_id) as directory:
                if not directory.exists("metadata.json"):
                    continue
                metadata = directory.read("metadata.json")
                _validate_metadata_payload(metadata, sid, attachment_id)
        except FileNotFoundError:
            # Cancellation/expiry can remove an unfinished directory during a listing.
            continue
        row = _uploaded_artifact(sid, metadata["relative_path"], global_root=global_root)
        if row is not None:
            rows.append(row)
    return rows


def list_project_artifacts(
    sid: str,
    *,
    global_root: Path | str | None = None,
    include_reading: bool = False,
) -> list[dict[str, Any]] | None:
    if project_life_dir(sid, global_root=global_root) is None:
        return None
    workspace = artifact_workspace(sid, global_root=global_root)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    evidence_rows = [
        *registered_delivery_artifacts(sid, global_root=global_root, workspace=workspace),
        *manager_live_view_files(sid, workspace, global_root=global_root),
        *registered_research_artifacts(sid, global_root=global_root),
    ] if workspace is not None else []
    for evidence in evidence_rows:
        # Reading notes have their own session registration and never become
        # reviewed research outputs through a delivery/live-view declaration.
        if PurePosixPath(evidence["path"].replace("\\", "/")).parts[:1] in {("reader-notes",), ("reader-progress",)}:
            continue
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
    if include_reading:
        from .reader_foundation import registered_foundation_artifacts

        rows.extend(registered_foundation_artifacts(resolve_global_root(global_root), sid))
    rows.extend(_uploaded_artifacts(sid, global_root=global_root))
    return rows


def get_project_artifact(
    sid: str,
    artifact_path: str,
    *,
    global_root: Path | str | None = None,
    preview_bytes: int = 128 * 1024,
) -> dict[str, Any] | None:
    if PurePosixPath(artifact_path).parts[:2] == (".argus", "attachments"):
        return _uploaded_artifact(sid, artifact_path, global_root=global_root,
                                  preview_bytes=max(0, min(int(preview_bytes), 512 * 1024)))
    if PurePosixPath(str(artifact_path).strip().replace("\\", "/")).parts[:1] == ("reader-progress",):
        from .reader_progress import registered_progress_artifact

        row = registered_progress_artifact(
            resolve_global_root(global_root), sid, artifact_path,
            preview_bytes=max(0, min(int(preview_bytes), 512 * 1024)),
        )
        return row if row is not None and row["exists"] else None
    if PurePosixPath(str(artifact_path).strip().replace("\\", "/")).parts[:1] == ("reader-notes",):
        from .reader_foundation import registered_foundation_artifact

        row = registered_foundation_artifact(
            resolve_global_root(global_root), sid, artifact_path,
            preview_bytes=max(0, min(int(preview_bytes), 512 * 1024)),
        )
        return row if row is not None and row["exists"] else None
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
    row["source"] = allowed["source"]
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
    if info is not None and info.get("source") in {"reader_foundation", "progress_snapshot", "attachment"}:
        # This address was resolved from this session's registered fixed
        # workspace by get_project_artifact, never from a request field.
        path = Path(info["storage_path"])
        return (info, path) if path.is_file() else None
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
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
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


def delivery_change(
    sid: str,
    path: str,
    before: str,
    after: str,
    *,
    global_root: Path | str | None = None,
) -> dict[str, Any] | None:
    """What changed in one delivered file between two of its deliveries."""
    from ..life.delivery import delivery_snapshot_diff, read_delivery_snapshot

    state_dir = project_life_dir(sid, global_root=global_root)
    if state_dir is None:
        return None
    before_available = read_delivery_snapshot(state_dir, before) is not None
    after_available = read_delivery_snapshot(state_dir, after) is not None
    diff = (
        delivery_snapshot_diff(
            state_dir, {"path": path, "version": before}, {"path": path, "version": after}
        )
        or ""
        if before_available and after_available
        else ""
    )
    encoded = diff.encode("utf-8")
    truncated = len(encoded) > _GIT_DIFF_LIMIT
    if truncated:
        diff = encoded[:_GIT_DIFF_LIMIT].decode("utf-8", errors="replace")
    return {
        "path": path,
        "available": before_available and after_available,
        "diff": diff,
        "truncated": truncated,
    }


__all__ = [
    "artifact_workspace",
    "delivery_change",
    "artifact_metadata",
    "get_project_artifact",
    "list_project_artifacts",
    "manager_live_view_files",
    "project_workspace",
    "project_git_diff",
    "registered_delivery_artifacts",
    "registered_research_artifacts",
    "resolved_project_artifact",
    "safe_artifact_path",
]
