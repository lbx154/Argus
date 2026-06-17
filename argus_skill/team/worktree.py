"""Per-teammate git worktree — the physical-isolation primitive that makes
concurrent teammates shared-nothing on the filesystem. Each teammate edits
only inside its own worktree on its own branch, so two teammates can never
overwrite each other's files; the lead merges branches/shards afterwards.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def path_for(repo_root: Path, team_id: str, member_id: str) -> Path:
    return Path(repo_root) / ".argus_team" / team_id / "wt" / member_id


def create(repo_root: Path, *, team_id: str, member_id: str, base_ref: str = "HEAD") -> Path:
    """Create (or reset) a git worktree + branch for one teammate, return its path."""
    dest = path_for(repo_root, team_id, member_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    branch = f"argus-team/{team_id}/{member_id}"
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
