"""Compact semantic fingerprint for open-ended terminal-idle campaigns.

The terminal guard is deliberately narrower than a recursive workspace hash.
Planner/Wiki/runtime logs are process data, not new operator intent.  Re-reading
and hashing them after every ``project_done`` verdict both wastes I/O on remote
workspaces and lets the agent's own bookkeeping wake the Planner again.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

_VOLATILE_KEYS = frozenset({
    "created_at",
    "event_sequence",
    "last_event_ts",
    "rendered_at",
    "rendering_timestamp",
    "sequence",
    "ts",
    "updated_at",
})

_IGNORED_TOP_LEVEL = frozenset({
    ".agents",
    ".argus",
    ".autors",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
})

_IGNORED_PROJECT_PREFIXES = (
    "research/raw/",
)

_IGNORED_PROJECT_FILES = frozenset({
    "mission-view.json",
    "mission-view.lock",
    "research/GROUND_TRUTH.md",
})


def semantic_terminal_value(value: Any, *, field_name: str = "") -> Any:
    """Drop refresh-only metadata while preserving semantic JSON state."""
    if isinstance(value, dict):
        return {
            str(key): semantic_terminal_value(item, field_name=str(key))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key).lower() not in _VOLATILE_KEYS
        }
    if isinstance(value, list):
        items = [
            semantic_terminal_value(item, field_name=field_name)
            for item in value
        ]
        if "summary" in field_name.lower():
            return sorted(
                items,
                key=lambda item: json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
        return items
    return value


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        semantic_terminal_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _ignored_project_path(
    path_text: str,
    *,
    extra_prefixes: tuple[str, ...] = (),
) -> bool:
    normalized = path_text.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    if not normalized:
        return True
    path = PurePosixPath(normalized)
    if path.parts and path.parts[0] in _IGNORED_TOP_LEVEL:
        return True
    if normalized in _IGNORED_PROJECT_FILES:
        return True
    return any(
        normalized.startswith(prefix)
        for prefix in (*_IGNORED_PROJECT_PREFIXES, *extra_prefixes)
    )


def _file_content_digest(path: Path) -> bytes:
    try:
        if not path.is_file():
            return b"missing-or-non-file"
        size = path.stat().st_size
        # Large-file head/tail sampling bounds RAM in this status fingerprint.
        if size <= 4 * 1024 * 1024:
            raw = path.read_bytes()
        else:
            with path.open("rb") as handle:
                head = handle.read(64 * 1024)
                handle.seek(max(0, size - 64 * 1024))
                tail = handle.read(64 * 1024)
            raw = str(size).encode() + b"\0" + head + b"\0" + tail
        if path.suffix.lower() == ".json" and size <= 4 * 1024 * 1024:
            try:
                raw = _json_bytes(json.loads(raw.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        return hashlib.sha256(raw).digest()
    except OSError:
        # Transient workspace I/O is not evidence that the research state
        # changed. Keep the marker stable and let canonical state/inbox changes
        # drive replanning.
        return b"unavailable"


def _git_status_records(raw: bytes) -> list[tuple[str, str]]:
    records = [item for item in raw.split(b"\0") if item]
    parsed: list[tuple[str, str]] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        status = record[:2].decode("ascii", errors="replace")
        path_text = record[3:].decode("utf-8", errors="surrogateescape")
        parsed.append((status, path_text))
        if "R" in status or "C" in status:
            if index < len(records):
                old_path = records[index].decode(
                    "utf-8",
                    errors="surrogateescape",
                )
                parsed.append((f"{status}:source", old_path))
                index += 1
    return parsed


def _git_project_digest(
    project_root: Path,
    *,
    extra_prefixes: tuple[str, ...],
) -> bytes | None:
    if not (project_root / ".git").exists():
        return None
    try:
        tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=project_root,
            check=True,
            capture_output=True,
        ).stdout.strip()
        status_raw = subprocess.run(
            [
                "git",
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=normal",
            ],
            cwd=project_root,
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return b"git-state-unavailable"

    digest = hashlib.sha256()
    digest.update(b"git-terminal-state-v1\0")
    digest.update(tree)
    digest.update(b"\0")
    for status, path_text in _git_status_records(status_raw):
        if _ignored_project_path(path_text, extra_prefixes=extra_prefixes):
            continue
        digest.update(status.encode("utf-8", errors="replace"))
        digest.update(b"\0")
        digest.update(path_text.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(_file_content_digest(project_root / path_text))
        digest.update(b"\0")
    return digest.digest()


def _fallback_project_digest(
    project_root: Path,
    *,
    extra_prefixes: tuple[str, ...],
) -> bytes:
    """Small-tree fallback for non-git workspaces and unit-test fixtures."""
    digest = hashlib.sha256()
    digest.update(b"fallback-terminal-state-v1\0")
    try:
        for dirpath, dirnames, filenames in os.walk(project_root):
            relative_dir = Path(dirpath).relative_to(project_root)
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if not _ignored_project_path(
                    str((relative_dir / name).as_posix()) + "/",
                    extra_prefixes=extra_prefixes,
                )
                and not name.endswith(".egg-info")
            ]
            for name in sorted(filenames):
                path = Path(dirpath) / name
                relative = path.relative_to(project_root).as_posix()
                if (
                    _ignored_project_path(
                        relative,
                        extra_prefixes=extra_prefixes,
                    )
                    or name == "REVIEW.md"
                ):
                    continue
                digest.update(relative.encode("utf-8", errors="surrogateescape"))
                digest.update(b"\0")
                digest.update(_file_content_digest(path))
                digest.update(b"\0")
    except OSError:
        digest.update(b"walk-unavailable")
    return digest.digest()


def _review_files(artifact_root: Path) -> list[Path]:
    """Find canonical review files without descending into environments/logs."""
    found: list[Path] = []
    try:
        for dirpath, dirnames, filenames in os.walk(artifact_root):
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if name not in _IGNORED_TOP_LEVEL
                and not name.endswith(".egg-info")
            ]
            if "REVIEW.md" in filenames:
                found.append(Path(dirpath) / "REVIEW.md")
    except OSError:
        return []
    return sorted(found)


def build_terminal_idle_signature(
    *,
    objective: str,
    stage: str,
    backlog: Iterable[tuple[str, str, str]],
    artifact_root: Path,
    project_root: Path,
    state_root: Path | None,
    completion_contract: dict[str, Any] | None,
) -> str:
    """Return a bounded signature of state that can justify a new Planner call."""
    digest = hashlib.sha256()
    digest.update(b"open-ended-terminal-idle-v3\0")
    digest.update(str(objective or "").encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(stage or "").encode("utf-8"))
    digest.update(b"\0")
    digest.update(_json_bytes(sorted(backlog)))
    digest.update(b"\0")

    from ..core.pipeline_state import read_pipeline_state

    try:
        digest.update(_json_bytes(read_pipeline_state(artifact_root)))
    except (OSError, ValueError):
        digest.update(b"pipeline-state-unavailable")
    digest.update(b"\0")

    for path in _review_files(artifact_root):
        try:
            relative = path.relative_to(artifact_root).as_posix()
        except ValueError:
            relative = str(path)
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(_file_content_digest(path))
        digest.update(b"\0")

    extra_prefixes: tuple[str, ...] = ()
    if state_root is not None:
        try:
            relative_state_root = state_root.resolve().relative_to(
                project_root.resolve()
            )
        except (OSError, ValueError):
            pass
        else:
            state_prefix = relative_state_root.as_posix().strip("/")
            if state_prefix and state_prefix != ".":
                extra_prefixes = (state_prefix + "/",)

    project_digest = _git_project_digest(
        project_root,
        extra_prefixes=extra_prefixes,
    )
    digest.update(
        project_digest
        if project_digest is not None
        else _fallback_project_digest(
            project_root,
            extra_prefixes=extra_prefixes,
        )
    )
    digest.update(b"\0")
    if completion_contract:
        digest.update(_json_bytes(completion_contract))
    return digest.hexdigest()


def build_project_state_signature(
    *,
    project_root: Path,
    state_root: Path | None = None,
) -> str:
    """Fingerprint current project semantics without runtime/event bookkeeping."""
    digest = hashlib.sha256()
    digest.update(b"project-state-v1\0")
    extra_prefixes: tuple[str, ...] = ()
    if state_root is not None:
        try:
            relative_state_root = state_root.resolve().relative_to(
                project_root.resolve()
            )
        except (OSError, ValueError):
            pass
        else:
            state_prefix = relative_state_root.as_posix().strip("/")
            if state_prefix and state_prefix != ".":
                extra_prefixes = (state_prefix + "/",)
    project_digest = _git_project_digest(
        project_root,
        extra_prefixes=extra_prefixes,
    )
    digest.update(
        project_digest
        if project_digest is not None
        else _fallback_project_digest(
            project_root,
            extra_prefixes=extra_prefixes,
        )
    )
    return digest.hexdigest()


def project_unchanged_since(
    *,
    project_root: Path,
    cutoff: float,
    state_root: Path | None = None,
) -> bool:
    """Legacy-cert adapter: reject when any semantic project file is newer."""
    if cutoff <= 0:
        return False
    extra_prefixes: tuple[str, ...] = ()
    if state_root is not None:
        try:
            relative_state_root = state_root.resolve().relative_to(
                project_root.resolve()
            )
        except (OSError, ValueError):
            pass
        else:
            state_prefix = relative_state_root.as_posix().strip("/")
            if state_prefix and state_prefix != ".":
                extra_prefixes = (state_prefix + "/",)
    try:
        for dirpath, dirnames, filenames in os.walk(project_root):
            relative_dir = Path(dirpath).relative_to(project_root)
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if not _ignored_project_path(
                    str((relative_dir / name).as_posix()) + "/",
                    extra_prefixes=extra_prefixes,
                )
                and not name.endswith(".egg-info")
            ]
            for name in filenames:
                path = Path(dirpath) / name
                relative = path.relative_to(project_root).as_posix()
                if _ignored_project_path(relative, extra_prefixes=extra_prefixes):
                    continue
                if path.stat().st_mtime > cutoff:
                    return False
    except OSError:
        return False
    return True


__all__ = [
    "build_project_state_signature",
    "build_terminal_idle_signature",
    "project_unchanged_since",
    "semantic_terminal_value",
]
