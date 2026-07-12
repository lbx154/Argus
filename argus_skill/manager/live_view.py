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
    ".bib", ".cfg", ".csv", ".gif", ".html", ".ini", ".jpeg", ".jpg",
    ".json", ".jsonl", ".log", ".md", ".pdf", ".png", ".py", ".rst",
    ".sh", ".tex", ".ts", ".tsv", ".txt", ".webp", ".yaml", ".yml",
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


def load_live_view_decision(project_root: Path | str) -> LiveViewDecision | None:
    """Load the latest Manager declaration, failing closed on malformed data."""
    manifest = Path(project_root) / LIVE_VIEW_MANIFEST
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
) -> tuple[bool, LiveViewDecision | None]:
    """Parse the optional ``live_view`` key from a Manager JSON response."""
    payload = _response_payload(raw_text)
    if payload is None or "live_view" not in payload:
        return False, None
    view = parse_live_view(payload.get("live_view"))
    decided = payload.get("live_view") is None or view is not None
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
            or Path(path).suffix.casefold() not in {".html", ".md", ".txt"}
            or not content
            or len(content.encode("utf-8")) > MAX_PRESENTATION_BYTES
        ):
            continue
        presentations.append(ManagerPresentation(path=path, content=content))
    return tuple(presentations)


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


def apply_manager_rendering_response(
    project_root: Path | str,
    raw_text: str,
) -> LiveViewDecision | None:
    """Confined writer for Manager-authored presentation content + manifest."""
    payload = _response_payload(raw_text)
    if payload is None:
        return load_live_view_decision(project_root)
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
    decided, view = parse_live_view_response(raw_text)
    if "live_view" in payload and not decided:
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
    if decided or presentations:
        live_dir = _manager_live_root(project_root)
        for presentation in presentations:
            target = live_dir / Path(presentation.path).name
            tmp = target.with_name(
                f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp"
            )
            try:
                tmp.write_text(presentation.content, encoding="utf-8")
                os.replace(tmp, target)
            finally:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass
    apply_live_view_decision(project_root, decided=decided, view=view)
    return view if decided else load_live_view_decision(project_root)


def manager_rendering_prompt(
    project_root: Path | str,
    *,
    review: object | None = None,
) -> str:
    """Prompt block making right-sidebar presentation Manager-owned."""
    current = load_live_view_decision(project_root)
    current_text = (
        json.dumps(
            {
                "title": current.title,
                "reason": current.reason,
                "paths": list(current.paths),
            },
            ensure_ascii=False,
        )
        if current is not None
        else "null"
    )
    status = str(getattr(review, "status", "") or "")
    reason = str(getattr(review, "reason", "") or "")
    return (
        "## Right-sidebar presentation — MANAGER ownership\n"
        "You alone own what Argus Web renders in the right sidebar. Do not assign "
        "rendering work, Manager paths, or presentation-only files to Engineer.\n"
        "Use read-only tools to inspect current intermediate artifacts. Never "
        "write files with tools. You "
        "may point the panel directly at a useful existing text/image/PDF artifact. "
        "If it is missing, stale, or unattractive, author presentation content in "
        "the final JSON for a single-file path under `.argus/live/`; the harness "
        "will write it safely. Never alter source evidence, task outputs, code, or "
        "paper claims merely for display.\n"
        f"Current live view: {current_text}\n"
        f"Latest reviewer status: {status or '(none)'}\n"
        f"Latest reviewer reason: {reason or '(none)'}\n"
        "Choose 1-6 safe workspace-relative files, or null when no side view helps. "
        "In your final JSON include:\n"
        '"live_view": null | {"title": "<short title>", "reason": "<why this is '
        'useful now>", "paths": ["<existing artifact or .argus/live/file>", ...]},\n'
        '"presentations": [{"path": ".argus/live/<file>.md", "content": '
        '"<Manager-authored Markdown or HTML>"}]\n'
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
    manifest = _manager_argus_root(project_root) / LIVE_VIEW_MANIFEST.name
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
    "manager_rendering_prompt",
    "normalize_live_view_path",
    "parse_manager_presentations",
    "parse_live_view",
    "parse_live_view_response",
]
