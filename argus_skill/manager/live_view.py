"""Manager-owned declaration for the Web cockpit's live project view.

The declaration deliberately contains only *which project files matter now*.
It does not encode paper/code/data product semantics: the grounded Manager
chooses the files after inspecting the workspace, and the Web client renders
their actual MIME type. Keeping this as project-local data also lets the view
survive Web/API restarts without adding another service-side state machine.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

LIVE_VIEW_MANIFEST = Path(".argus") / "live-view.json"
MAX_LIVE_VIEW_ITEMS = 6

_SENSITIVE_PARTS = frozenset({".argus", ".aws", ".git", ".gnupg", ".ssh"})
_SENSITIVE_NAMES = frozenset(
    {
        ".env",
        "credentials",
        "credentials.json",
        "id_dsa",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
    }
)
_SENSITIVE_SUFFIXES = frozenset({".key", ".p12", ".pem", ".pfx"})


@dataclass(frozen=True)
class LiveViewDecision:
    """One grounded Manager choice for the operator's live side panel."""

    title: str
    paths: tuple[str, ...]
    reason: str = ""


def normalize_live_view_path(value: object) -> str | None:
    """Return a safe, workspace-relative POSIX path without touching disk."""
    raw = str(value or "").strip().replace("\\", "/")
    if not raw or "\x00" in raw:
        return None
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        return None
    parts = tuple(part.casefold() for part in path.parts)
    name = path.name.casefold()
    if any(part in _SENSITIVE_PARTS for part in parts):
        return None
    if name in _SENSITIVE_NAMES or name.startswith(".env."):
        return None
    if path.suffix.casefold() in _SENSITIVE_SUFFIXES:
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


def load_live_view_decision(project_root: Path | str) -> LiveViewDecision | None:
    """Load the latest Manager declaration, failing closed on malformed data."""
    manifest = Path(project_root) / LIVE_VIEW_MANIFEST
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return parse_live_view(payload)


def format_live_view_contract(project_root: Path | str) -> str:
    """Render the Manager's selected operator-facing files for the Engineer."""
    view = load_live_view_decision(project_root)
    if view is None:
        return ""
    paths = "\n".join(f"- `{path}`" for path in view.paths)
    reason = f"\nWhy these files: {view.reason}" if view.reason else ""
    return (
        "## Manager-selected live Web view\n"
        f"Panel title: {view.title}\n"
        "The right sidebar reads exactly these workspace-relative files:\n"
        f"{paths}{reason}\n\n"
        "Create or update the selected file(s) when they are deliverables of "
        "this task. Do not silently substitute a different path: the operator "
        "would see an empty panel. If a selected path is no longer appropriate, "
        "state that explicitly for the Manager instead of pretending it rendered."
    )


def apply_live_view_decision(
    project_root: Path | str,
    *,
    decided: bool,
    view: LiveViewDecision | None,
) -> None:
    """Atomically persist (or explicitly clear) the Manager's latest choice.

    ``decided=False`` preserves an older declaration for compatibility with a
    backend/model that returned the pre-live-view JSON shape. ``decided=True``
    with ``view=None`` is an explicit Manager decision that no side panel is
    useful for this project right now.
    """
    if not decided:
        return
    manifest = Path(project_root) / LIVE_VIEW_MANIFEST
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
    "MAX_LIVE_VIEW_ITEMS",
    "LiveViewDecision",
    "apply_live_view_decision",
    "format_live_view_contract",
    "load_live_view_decision",
    "normalize_live_view_path",
    "parse_live_view",
]
