"""Manager-owned declaration for the Web cockpit's live project view.

The declaration deliberately contains only *which project files matter now*.
It does not encode paper/code/data product semantics: the grounded Manager
chooses the files after inspecting the workspace, and the Web client renders
their actual MIME type. The manifest is session-state data while selected files
remain workspace-relative, so sequential sessions sharing one repository never
inherit each other's sidebar choice.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from ..roles.task_contract import argus_command

LIVE_VIEW_MANIFEST = Path(".argus") / "live-view.json"
MANAGER_LIVE_DIR = Path(".argus") / "live"
MAX_LIVE_VIEW_ITEMS = 6

_SENSITIVE_PARTS = frozenset({
    ".argus", ".aws", ".git", ".gnupg", ".ssh",
    "credentials", "keys", "private", "secrets",
})
_SENSITIVE_NAMES = frozenset(
    {
        ".env",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credentials.json",
        "application_default_credentials.json",
        "client_secret.json",
        "id_dsa",
        "id_ed25519",
        "id_rsa",
        "service-account.json",
        "secrets.yaml",
        "secrets.yml",
        "secrets.json",
        "token",
        "token.txt",
    }
)
_SENSITIVE_SUFFIXES = frozenset({".key", ".p12", ".pem", ".pfx"})
_RENDERABLE_SUFFIXES = frozenset({
    ".diff", ".patch", ".zip", ".npz", ".npy", ".parquet", ".safetensors",
    ".aac", ".bib", ".cfg", ".css", ".js", ".mjs", ".csv", ".docx", ".flac", ".gif", ".html", ".ini",
    ".ipynb", ".jpeg", ".jpg", ".json", ".jsonl", ".log", ".m4a", ".m4v",
    ".markdown", ".md", ".mov", ".mp3", ".mp4", ".ogg", ".ogv", ".pdf",
    ".png", ".pptx", ".py", ".rst", ".sh", ".svg", ".tex", ".toml", ".ts", ".tsv", ".txt", ".wav",
    ".webm", ".webp", ".xlsx", ".yaml", ".yml",
    # Robot and 3D model results open in the Web 3D viewer.
    ".urdf", ".stl", ".obj", ".dae", ".glb", ".gltf",
})


@dataclass(frozen=True)
class LiveViewDecision:
    """One grounded Manager choice for the operator's live side panel."""

    title: str
    paths: tuple[str, ...]
    reason: str = ""


def manager_workspace_context(
    project_root: Path | str,
    *,
    manifest_root: Path | str | None = None,
) -> dict[str, object]:
    """Return the exact path/capability card shared by every Manager surface."""
    workspace = Path(project_root).expanduser().resolve()
    state_root = Path(manifest_root or workspace).expanduser().resolve()
    wiki_dirs: list[str] = []
    try:
        from ..wiki.auto_hooks import discover_wikis

        wiki_dirs = [str(path.resolve()) for path in discover_wikis(workspace)]
    except Exception:  # noqa: BLE001 — capability context is fail-soft
        wiki_dirs = []
    return {
        "workspace": str(workspace),
        "state_root": str(state_root),
        "checkpoint": str(state_root / "CHECKPOINT.md"),
        "project_skill_dir": str(state_root / "skills"),
        "wiki_dirs": wiki_dirs,
        "live_view_manifest": str(state_root / LIVE_VIEW_MANIFEST),
        "presentation_root": str(workspace / MANAGER_LIVE_DIR),
        "artifact_path_rule": "all selected paths are workspace-relative",
        "manager_live_view_tool": argus_command(
            "argus.tools.manager_live_view", "--workspace", workspace, "--state-dir", state_root,
        ),
    }


def normalize_live_view_path(
    value: object, *, allowed_suffixes: frozenset[str] = _RENDERABLE_SUFFIXES,
) -> str | None:
    """Return a safe, workspace-relative POSIX path without touching disk."""
    raw = str(value or "").strip().replace("\\", "/")
    if not raw or "\x00" in raw:
        return None
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        return None
    parts = tuple(part.casefold() for part in path.parts)
    manager_owned = len(parts) == 3 and parts[:2] == (".argus", "live")
    name = path.name.casefold()
    if parts and parts[0] == ".argus" and not manager_owned:
        return None
    checked_parts = parts[2:] if manager_owned else parts
    if any(
        part in _SENSITIVE_PARTS or part.startswith(".")
        for part in checked_parts
    ):
        return None
    if name in _SENSITIVE_NAMES or name.startswith(".env."):
        return None
    stem = path.stem.casefold().replace("-", "_")
    if stem in {
        "api_key",
        "application_default_credentials",
        "client_secret",
        "credential",
        "credentials",
        "private_key",
        "refresh_token",
        "secret",
        "secrets",
        "service_account",
    }:
        return None
    if path.suffix.casefold() in _SENSITIVE_SUFFIXES:
        return None
    if path.suffix.casefold() not in allowed_suffixes:
        return None
    normalized = path.as_posix()
    return normalized if normalized not in {"", "."} else None


def parse_live_view(value: object) -> LiveViewDecision | None:
    """Validate the optional ``live_view`` object from a Manager verdict."""
    if not isinstance(value, dict):
        return None
    raw_paths = value.get("paths")
    if not isinstance(raw_paths, list):
        return None
    paths: list[str] = []
    for raw in raw_paths:
        path = normalize_live_view_path(raw)
        if path and path not in paths:
            paths.append(path)
        if len(paths) >= MAX_LIVE_VIEW_ITEMS:
            break
    if not paths:
        return None
    title = str(value.get("title") or "Live project view").strip()[:120]
    reason = str(value.get("reason") or "").strip()[:500]
    return LiveViewDecision(title=title or "Live project view", paths=tuple(paths), reason=reason)


def load_live_view_decision(
    project_root: Path | str,
    *,
    manifest_root: Path | str | None = None,
) -> LiveViewDecision | None:
    """Load the latest Manager declaration, failing closed on malformed data."""
    manifest = Path(manifest_root or project_root) / LIVE_VIEW_MANIFEST
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return parse_live_view(payload)


def _manager_argus_root(project_root: Path | str) -> Path:
    root = Path(project_root).expanduser().resolve()
    argus_dir = root / ".argus"
    if argus_dir.is_symlink():
        raise ValueError("manager .argus directory must not be a symlink")
    argus_dir.mkdir(parents=True, exist_ok=True)
    return argus_dir


def apply_live_view_decision(
    project_root: Path | str,
    *,
    decided: bool,
    view: LiveViewDecision | None,
    manifest_root: Path | str | None = None,
) -> None:
    """Atomically persist (or explicitly clear) the Manager's latest choice.

    ``decided=False`` preserves an older declaration for compatibility with a
    backend/model that returned the pre-live-view JSON shape. ``decided=True``
    with ``view=None`` is an explicit Manager decision that no side panel is
    useful for this project right now.
    """
    if not decided:
        return
    manifest = _manager_argus_root(
        manifest_root or project_root
    ) / LIVE_VIEW_MANIFEST.name
    if view is None:
        try:
            manifest.unlink()
        except FileNotFoundError:
            pass
        return
    manifest.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, **asdict(view), "paths": list(view.paths)}
    tmp = manifest.with_name(
        f".{manifest.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, manifest)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "LIVE_VIEW_MANIFEST",
    "MANAGER_LIVE_DIR",
    "MAX_LIVE_VIEW_ITEMS",
    "LiveViewDecision",
    "apply_live_view_decision",
    "load_live_view_decision",
    "manager_workspace_context",
    "normalize_live_view_path",
    "parse_live_view",
]
