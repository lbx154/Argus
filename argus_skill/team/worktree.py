"""Per-teammate git worktree — the physical-isolation primitive that makes
concurrent teammates shared-nothing on the filesystem. Each teammate edits
only inside its own worktree on its own branch, so two teammates can never
overwrite each other's files; the lead merges branches/shards afterwards.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _validate_identifier(name: str, value: str) -> None:
    if not value or value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise ValueError(f"invalid worktree {name}: {value!r}")


def _validate_branch_name(branch: str) -> None:
    result = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise ValueError(f"invalid worktree branch name: {branch!r}")


def path_for(repo_root: Path, team_id: str, member_id: str) -> Path:
    _validate_identifier("team_id", team_id)
    _validate_identifier("member_id", member_id)
    return Path(repo_root) / ".argus_team" / team_id / "wt" / member_id


def create(repo_root: Path, *, team_id: str, member_id: str, base_ref: str = "HEAD") -> Path:
    """Create (or reset) a git worktree + branch for one teammate, return its path."""
    dest = path_for(repo_root, team_id, member_id)
    branch = f"argus-team/{team_id}/{member_id}"
    _validate_branch_name(branch)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", "-B", branch, str(dest), base_ref],
        cwd=repo_root, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    return dest


def remove(repo_root: Path, path: Path) -> None:
    """Remove a teammate worktree (best-effort; safe to call twice)."""
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(path)],
        cwd=repo_root, check=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
