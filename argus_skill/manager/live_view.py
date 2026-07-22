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

from ..roles.prompts.manager import (
    manager_rendering_prompt,
    manager_workspace_capability_prompt,
)

LIVE_VIEW_MANIFEST = Path(".argus") / "live-view.json"
MANAGER_LIVE_DIR = Path(".argus") / "live"
MAX_LIVE_VIEW_ITEMS = 6
MAX_PRESENTATION_BYTES = 256 * 1024

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
    ".aac", ".bib", ".cfg", ".csv", ".flac", ".gif", ".html", ".ini",
    ".ipynb", ".jpeg", ".jpg", ".json", ".jsonl", ".log", ".m4a", ".m4v",
    ".markdown", ".md", ".mov", ".mp3", ".mp4", ".ogg", ".ogv", ".pdf",
    ".png", ".py", ".rst", ".sh", ".tex", ".toml", ".ts", ".tsv", ".txt", ".wav",
    ".webm", ".webp", ".yaml", ".yml",
})


@dataclass(frozen=True)
class LiveViewDecision:
    """One grounded Manager choice for the operator's live side panel."""

    title: str
    paths: tuple[str, ...]
    reason: str = ""


@dataclass(frozen=True)
class ManagerPresentation:
    """One Manager-authored, presentation-only sidebar file."""

    path: str
    content: str


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
        "manager_live_view_tool": (
            "python -m argus_skill.tools.manager_live_view "
            f"--workspace {workspace} --state-dir {state_root}"
        ),
    }


def normalize_live_view_path(value: object) -> str | None:
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
    if path.suffix.casefold() not in _RENDERABLE_SUFFIXES:
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
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return parse_live_view(payload)


def _response_payload(raw_text: str) -> dict[str, object] | None:
    cleaned = str(raw_text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        cleaned = cleaned.rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(cleaned)
    except (TypeError, ValueError, json.JSONDecodeError):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(cleaned[start : end + 1])
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return payload if isinstance(payload, dict) else None


def parse_live_view_response(
    raw_text: str,
    *,
    null_means_clear: bool = False,
) -> tuple[bool, LiveViewDecision | None]:
    """Parse the optional ``live_view`` key from a Manager JSON response."""
    payload = _response_payload(raw_text)
    if payload is None or "live_view" not in payload:
        return False, None
    if payload.get("live_view") is None:
        return bool(payload.get("clear_live_view") is True or null_means_clear), None
    view = parse_live_view(payload.get("live_view"))
    decided = view is not None
    return decided, view


def parse_manager_presentations(raw_text: str) -> tuple[ManagerPresentation, ...]:
    """Validate bounded presentation content returned by Manager."""
    payload = _response_payload(raw_text)
    rows = payload.get("presentations") if payload is not None else None
    if not isinstance(rows, list):
        return ()
    presentations: list[ManagerPresentation] = []
    for row in rows[:MAX_LIVE_VIEW_ITEMS]:
        if not isinstance(row, dict):
            continue
        path = normalize_live_view_path(row.get("path"))
        raw_content = row.get("content")
        content = raw_content if isinstance(raw_content, str) else ""
        if (
            path is None
            or not path.startswith(f"{MANAGER_LIVE_DIR.as_posix()}/")
            or Path(path).suffix.casefold() not in {
                ".csv", ".html", ".json", ".markdown", ".md", ".tsv", ".txt",
            }
            or not content
            or len(content.encode("utf-8")) > MAX_PRESENTATION_BYTES
        ):
            continue
        presentations.append(ManagerPresentation(path=path, content=content))
    return tuple(presentations)


def _manager_progress_context(
    project_root: Path | str,
    *,
    manifest_root: Path | str | None = None,
) -> dict[str, object]:
    try:
        from ..core.mission_view import load_mission_view

        view = load_mission_view(manifest_root or project_root)
    except Exception:  # noqa: BLE001 — rendering context is fail-soft
        return {}
    dag = [row for row in view.get("dag", []) if isinstance(row, dict)]
    active = next(
        (
            row for row in dag
            if str(row.get("status") or "")
            in {"running", "in_progress", "claimed"}
        ),
        None,
    )
    pending = next(
        (row for row in dag if str(row.get("status") or "") == "pending"),
        None,
    )
    mission = view.get("mission", {}) if isinstance(view.get("mission"), dict) else {}
    return {
        "stage": view.get("stage", {}),
        "round": view.get("round", {}),
        "active_role": view.get("active_role", ""),
        "current_node": {
            "title": str((active or {}).get("title") or mission.get("title") or ""),
            "status": str((active or {}).get("status") or mission.get("status") or ""),
        },
        "completed_nodes": [
            str(row.get("title") or "")
            for row in dag
            if str(row.get("status") or "") == "done"
        ][-6:],
        "next_node": str((pending or {}).get("title") or ""),
        "verified_artifacts": [
            str(row.get("title") or row.get("path") or "")
            for row in view.get("artifacts", [])
            if isinstance(row, dict) and row.get("exists")
        ][-6:],
    }


def _useful_manager_presentation(content: str) -> bool:
    lines = [line.strip() for line in str(content or "").splitlines() if line.strip()]
    return len(str(content or "").encode("utf-8")) >= 160 and len(lines) >= 5


def manager_checkpoint_refresh_required(
    project_root: Path | str,
    raw_text: str,
    *,
    manifest_root: Path | str | None = None,
) -> bool:
    """Whether a stage ruling failed to maintain its Manager-owned checkpoint."""
    current = load_live_view_decision(project_root, manifest_root=manifest_root)
    if current is None or not any(
        path.startswith(f"{MANAGER_LIVE_DIR.as_posix()}/")
        for path in current.paths
    ):
        return False
    payload = _response_payload(raw_text)
    if payload is None or payload.get("clear_live_view") is True:
        return False
    _decided, selected = parse_live_view_response(raw_text)
    target = selected or current
    manager_paths = [
        path for path in target.paths
        if path.startswith(f"{MANAGER_LIVE_DIR.as_posix()}/")
    ]
    if not manager_paths:
        return False
    presentations = {item.path: item.content for item in parse_manager_presentations(raw_text)}
    return any(
        not _useful_manager_presentation(presentations.get(path, ""))
        for path in manager_paths
    )


def repair_manager_checkpoint_response(
    project_root: Path | str,
    raw_text: str,
    *,
    manifest_root: Path | str | None = None,
) -> str:
    """Guarantee a substantive boundary checkpoint from Manager + mission state."""
    payload = _response_payload(raw_text)
    current = load_live_view_decision(project_root, manifest_root=manifest_root)
    if payload is None or current is None or payload.get("clear_live_view") is True:
        return raw_text
    _decided, selected = parse_live_view_response(raw_text)
    target = selected or current
    manager_paths = [
        path for path in target.paths
        if path.startswith(f"{MANAGER_LIVE_DIR.as_posix()}/")
    ]
    if not manager_paths:
        return raw_text
    context = _manager_progress_context(project_root, manifest_root=manifest_root)
    current_node = context.get("current_node") if isinstance(context.get("current_node"), dict) else {}
    completed = [str(item) for item in context.get("completed_nodes", []) if str(item)]
    artifacts = [str(item) for item in context.get("verified_artifacts", []) if str(item)]
    next_node = str(context.get("next_node") or "")
    lines = [
        f"# {target.title}",
        "",
        "## Current node",
        f"- {str(current_node.get('title') or 'No active DAG node')}"
        f" — `{str(current_node.get('status') or 'idle')}`",
        f"- Active role: `{str(context.get('active_role') or 'none')}`",
        "",
        "## Verified progress",
    ]
    lines.extend(f"- {item}" for item in completed[-5:])
    lines.extend(f"- Artifact: `{item}`" for item in artifacts[-4:])
    if not completed and not artifacts:
        lines.append("- No reviewed increment has been registered yet.")
    lines.extend([
        "",
        "## Current blocker",
        f"- {str(payload.get('reason') or target.reason or 'Awaiting the next verified result.')}",
        "",
        "## Next action",
        f"- {next_node or 'Finish the current node and hand it to Reviewer.'}",
        "",
    ])
    content = "\n".join(lines)
    payload["live_view"] = {
        "title": target.title,
        "reason": target.reason,
        "paths": list(target.paths),
    }
    existing = {
        item.path: item.content for item in parse_manager_presentations(raw_text)
        if _useful_manager_presentation(item.content)
    }
    payload["presentations"] = [
        {"path": path, "content": existing.get(path, content)}
        for path in manager_paths
    ]
    return json.dumps(payload, ensure_ascii=False)


def _manager_argus_root(project_root: Path | str) -> Path:
    root = Path(project_root).expanduser().resolve()
    argus_dir = root / ".argus"
    if argus_dir.is_symlink():
        raise ValueError("manager .argus directory must not be a symlink")
    argus_dir.mkdir(parents=True, exist_ok=True)
    return argus_dir


def _manager_live_root(project_root: Path | str) -> Path:
    argus_dir = _manager_argus_root(project_root)
    live_dir = argus_dir / "live"
    if live_dir.is_symlink():
        raise ValueError("manager live directory must not be a symlink")
    live_dir.mkdir(parents=True, exist_ok=True)
    return live_dir


def _write_manager_presentation(target: Path, content: str) -> None:
    """Atomically replace one harness-owned presentation file."""
    tmp = target.with_name(
        f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _workspace_artifact_exists(project_root: Path | str, relative_path: str) -> bool:
    """Check one selected artifact against the canonical execution workspace."""
    root = Path(project_root).expanduser().resolve()
    try:
        target = (root / relative_path).resolve(strict=True)
        target.relative_to(root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return False
    return target.is_file() and not target.is_symlink()


def _fallback_manager_presentation(
    payload: dict[str, object],
    view: LiveViewDecision,
    path: str,
) -> str:
    """Build a minimal truthful page when Manager selected a new text view but
    accidentally omitted its ``presentations`` entry.

    This contains only fields already authored by Manager in the same response;
    it never manufactures research claims or edits task artifacts.
    """
    suffix = Path(path).suffix.casefold()
    if suffix not in {".md", ".markdown", ".txt"}:
        raise ValueError(
            "missing Manager presentation content for non-text live view"
        )
    action = str(payload.get("action") or "").strip()
    target_stage = str(payload.get("target_stage") or "").strip()
    decision_reason = str(payload.get("reason") or "").strip()
    if suffix == ".txt":
        lines = [view.title]
        if view.reason:
            lines.extend(("", view.reason))
        if action or target_stage or decision_reason:
            lines.extend(("", "Latest Manager decision"))
            if action:
                lines.append(f"Action: {action}")
            if target_stage:
                lines.append(f"Stage: {target_stage}")
            if decision_reason:
                lines.append(f"Reason: {decision_reason}")
        return "\n".join(lines).rstrip() + "\n"
    lines = [f"# {view.title}"]
    if view.reason:
        lines.extend(("", view.reason))
    if action or target_stage or decision_reason:
        lines.extend(("", "## Latest Manager decision"))
        if action:
            lines.append(f"- Action: `{action}`")
        if target_stage:
            lines.append(f"- Stage: `{target_stage}`")
        if decision_reason:
            lines.append(f"- Reason: {decision_reason}")
    return "\n".join(lines).rstrip() + "\n"


def apply_manager_rendering_response(
    project_root: Path | str,
    raw_text: str,
    *,
    manifest_root: Path | str | None = None,
    null_means_clear: bool = False,
) -> LiveViewDecision | None:
    """Confined writer for Manager-authored presentation content + manifest."""
    payload = _response_payload(raw_text)
    if payload is None:
        return load_live_view_decision(
            project_root,
            manifest_root=manifest_root,
        )
    raw_presentations = payload.get("presentations")
    if raw_presentations is not None and (
        not isinstance(raw_presentations, list)
        or len(raw_presentations) > MAX_LIVE_VIEW_ITEMS
    ):
        raise ValueError("invalid Manager presentations list")
    presentations = parse_manager_presentations(raw_text)
    if isinstance(raw_presentations, list) and len(presentations) != len(raw_presentations):
        raise ValueError("invalid Manager presentation entry")
    if len({item.path for item in presentations}) != len(presentations):
        raise ValueError("duplicate Manager presentation path")
    clear_value = payload.get("clear_live_view")
    if clear_value not in {None, False, True}:
        raise ValueError("invalid clear_live_view flag")
    if clear_value is True and payload.get("live_view") is not None:
        raise ValueError("clear_live_view conflicts with a selected live_view")
    decided, view = parse_live_view_response(
        raw_text,
        null_means_clear=null_means_clear,
    )
    if payload.get("live_view") is not None and not decided:
        raise ValueError("invalid Manager live_view")
    raw_view = payload.get("live_view")
    if isinstance(raw_view, dict):
        raw_paths = raw_view.get("paths")
        if (
            not isinstance(raw_paths, list)
            or len(raw_paths) > MAX_LIVE_VIEW_ITEMS
            or view is None
            or len(view.paths) != len(raw_paths)
        ):
            raise ValueError("invalid Manager live_view paths")
    if presentations and (
        view is None
        or any(item.path not in view.paths for item in presentations)
    ):
        raise ValueError("Manager presentations must be selected in live_view")
    if view is not None:
        presented_paths = {item.path for item in presentations}
        materialized_paths = [
            path
            for path in view.paths
            if (
                path in presented_paths
                or (
                    path.startswith(f"{MANAGER_LIVE_DIR.as_posix()}/")
                    and Path(path).suffix.casefold() in {
                        ".md", ".markdown", ".txt",
                    }
                )
                or _workspace_artifact_exists(project_root, path)
            )
        ]
        if not materialized_paths:
            raise ValueError(
                "Manager live_view has no materialized artifact in the canonical workspace"
            )
    if decided or presentations:
        live_dir = _manager_live_root(project_root)
        for presentation in presentations:
            target = live_dir / Path(presentation.path).name
            _write_manager_presentation(target, presentation.content)
        presented_paths = {item.path for item in presentations}
        if view is not None:
            for path in view.paths:
                if (
                    not path.startswith(f"{MANAGER_LIVE_DIR.as_posix()}/")
                    or path in presented_paths
                ):
                    continue
                target = live_dir / Path(path).name
                _write_manager_presentation(
                    target,
                    _fallback_manager_presentation(payload, view, path),
                )
    apply_live_view_decision(
        project_root,
        decided=decided,
        view=view,
        manifest_root=manifest_root,
    )
    return view if decided else load_live_view_decision(
        project_root,
        manifest_root=manifest_root,
    )


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
    "MAX_PRESENTATION_BYTES",
    "LiveViewDecision",
    "ManagerPresentation",
    "apply_manager_rendering_response",
    "apply_live_view_decision",
    "load_live_view_decision",
    "manager_checkpoint_refresh_required",
    "manager_rendering_prompt",
    "manager_workspace_capability_prompt",
    "manager_workspace_context",
    "normalize_live_view_path",
    "parse_manager_presentations",
    "parse_live_view",
    "parse_live_view_response",
    "repair_manager_checkpoint_response",
]
