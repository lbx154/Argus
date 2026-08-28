"""Private round checkpoints that never touch the user's branch or index."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckpointResult:
    recorded: bool
    ref: str = ""
    error: str = ""


def _run(args: list[str], cwd: Path, env: dict[str, str] | None = None):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def _ref_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "mission"


def checkpoint_round(
    workdir: Path | str,
    *,
    mission_id: str,
    round_index: int,
    message: str,
) -> CheckpointResult:
    """Snapshot the worktree under ``refs/argus/checkpoints``.

    A temporary index starts from HEAD and stages the current worktree. The real
    index and branch are never read or changed.
    """
    start = Path(workdir).expanduser().resolve()
    top_result = _run(["rev-parse", "--show-toplevel"], start)
    if top_result.returncode != 0:
        return CheckpointResult(False)
    top = Path(top_result.stdout.strip())
    head_result = _run(["rev-parse", "HEAD"], top)
    tree_result = _run(["rev-parse", "HEAD^{tree}"], top)
    if head_result.returncode != 0 or tree_result.returncode != 0:
        return CheckpointResult(False)

    with tempfile.TemporaryDirectory(prefix="argus-checkpoint-") as temp:
        index = Path(temp) / "index"
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = str(index)
        if _run(["read-tree", "HEAD"], top, env).returncode != 0:
            return CheckpointResult(False, error="could not prepare checkpoint index")
        staged = _run(["add", "-A", "--", "."], top, env)
        if staged.returncode != 0:
            return CheckpointResult(False, error=staged.stderr.strip())
        written = _run(["write-tree"], top, env)
        if written.returncode != 0:
            return CheckpointResult(False, error=written.stderr.strip())
        tree = written.stdout.strip()
        if tree == tree_result.stdout.strip():
            return CheckpointResult(False)
        commit_env = dict(env)
        commit_env.update({
            "GIT_AUTHOR_NAME": "Argus Engineer",
            "GIT_AUTHOR_EMAIL": "engineer@argus.invalid",
            "GIT_COMMITTER_NAME": "Argus Checkpoint",
            "GIT_COMMITTER_EMAIL": "checkpoint@argus.invalid",
        })
        commit = _run(
            ["commit-tree", tree, "-p", head_result.stdout.strip(), "-m", message],
            top,
            commit_env,
        )
        if commit.returncode != 0:
            return CheckpointResult(False, error=commit.stderr.strip())
        ref = (
            f"refs/argus/checkpoints/{_ref_part(mission_id)}/"
            f"round-{max(1, int(round_index)):04d}"
        )
        updated = _run(["update-ref", ref, commit.stdout.strip()], top)
        if updated.returncode != 0:
            return CheckpointResult(False, error=updated.stderr.strip())
        return CheckpointResult(True, ref=ref)


__all__ = ["CheckpointResult", "checkpoint_round"]
