"""Persist and validate a campaign's primary repository root.

A session can start in a parent workspace, clone the real target repository, and
then spend the rest of its life operating in that child repository.  Keeping the
parent as the execution/artifact root creates two ``research/`` trees and makes
Manager/Reviewer evidence invisible to one another.  This module lets a
Planner-selected, project-relative Git root become the campaign root without
changing the stable Argus state directory. The relative path may resolve through
a symlink to a repository outside the session workspace.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

CAMPAIGN_WORKDIR_FILENAME = "campaign-workdir.json"


def campaign_workdir_path(state_root: Path | str) -> Path:
    return Path(state_root) / CAMPAIGN_WORKDIR_FILENAME


def normalize_task_workdir(
    value: object,
    *,
    base_root: Path | str | None = None,
) -> str:
    """Normalize a Planner-authored project-relative execution root."""
    raw = str(value or "").strip()
    if not raw or raw == ".":
        return ""
    candidate = Path(raw)
    if candidate.is_absolute():
        if base_root is None:
            raise ValueError(
                "TASK_WORKDIR must be a project-relative path without '..'"
            )
        base = Path(base_root).expanduser().resolve()
        resolved = candidate.expanduser().resolve()
        try:
            candidate = resolved.relative_to(base)
        except ValueError as exc:
            raise ValueError(
                "TASK_WORKDIR must be inside the active project"
            ) from exc
    if ".." in candidate.parts:
        raise ValueError("TASK_WORKDIR must be a project-relative path without '..'")
    if candidate == Path("."):
        return ""
    normalized = candidate.as_posix().strip("/")
    if not normalized:
        return ""
    return normalized


def resolve_task_workdir(base_root: Path | str, value: object) -> Path:
    """Resolve a task root from *base_root*, allowing external symlink targets."""
    base = Path(base_root).expanduser().resolve(strict=True)
    relative = normalize_task_workdir(value, base_root=base)
    try:
        target = base if not relative else (base / relative).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"TASK_WORKDIR is not a directory: {value!r}") from exc
    if not target.is_dir():
        raise ValueError(f"TASK_WORKDIR is not a directory: {value!r}")
    return target


def _git_toplevel(path: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return Path(result.stdout.strip()).resolve(strict=True)
    except OSError:
        return None


def active_campaign_workdir(
    state_root: Path | str,
    base_root: Path | str,
) -> Path | None:
    """Return a valid persisted campaign repository root, otherwise ``None``."""
    base = Path(base_root).expanduser().resolve()
    try:
        payload = json.loads(
            campaign_workdir_path(state_root).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw = str(payload.get("workdir") or "").strip()
    if not raw:
        return None
    recorded_base = str(payload.get("base_workdir") or "").strip()
    if recorded_base:
        try:
            if Path(recorded_base).expanduser().resolve() != base:
                return None
        except OSError:
            return None
    try:
        target = Path(raw).expanduser().resolve(strict=True)
    except OSError:
        return None
    if target == base or not target.is_dir():
        return None
    if _git_toplevel(target) != target:
        return None
    return target


def adopt_campaign_workdir(
    *,
    state_root: Path | str,
    base_root: Path | str,
    current_root: Path | str,
    requested: object,
) -> Path:
    """Validate and persist a Planner-selected Git repository as campaign root."""
    base = Path(base_root).expanduser().resolve(strict=True)
    current = Path(current_root).expanduser().resolve(strict=True)
    requested_path = Path(str(requested or "").strip()).expanduser()
    if requested_path.is_absolute():
        try:
            target = requested_path.resolve(strict=True)
        except OSError as exc:
            raise ValueError(
                f"TASK_WORKDIR is not a directory: {requested!r}"
            ) from exc
        if not target.is_dir():
            raise ValueError(f"TASK_WORKDIR is not a directory: {requested!r}")
        if target != current:
            for allowed_root in (current, base):
                try:
                    target.relative_to(allowed_root)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError("TASK_WORKDIR must be inside the active project")
    else:
        relative = normalize_task_workdir(requested, base_root=base)
        # Legacy persisted DAG nodes are relative to the original session root
        # even after an earlier sibling has adopted another campaign repository.
        target = current if not relative else resolve_task_workdir(base, relative)
    if target == current:
        return current
    if _git_toplevel(target) != target:
        raise ValueError(
            "TASK_WORKDIR adoption requires the root of a real Git repository"
        )

    payload = {
        "schema_version": 1,
        "base_workdir": str(base),
        "workdir": str(target),
    }
    path = campaign_workdir_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return target


__all__ = [
    "CAMPAIGN_WORKDIR_FILENAME",
    "active_campaign_workdir",
    "adopt_campaign_workdir",
    "campaign_workdir_path",
    "normalize_task_workdir",
    "resolve_task_workdir",
]
