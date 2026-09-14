"""Host-managed clean reference worktree for kernel baseline measurement."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_METADATA = "kernel-baseline-workspace.json"


@dataclass(frozen=True)
class BaselineWorkspace:
    project_root: Path
    reference_root: Path
    candidate_dirty: bool
    changed_paths: tuple[str, ...]
    machine_metadata: Path
    cache_root: Path

    def prompt_block(self) -> str:
        changes = ", ".join(self.changed_paths[:12]) or "none"
        return (
            "## Kernel baseline isolation\n"
            f"- clean_reference_root: `{self.reference_root}`\n"
            f"- candidate_root: `{self.project_root}`\n"
            f"- candidate_tracked_changes: {changes}\n"
            f"- runtime_cache_root: `{self.cache_root}`\n"
            "Put Triton, TorchInductor, CUDA and temporary compiler caches under "
            "runtime_cache_root, never under profile/ or research/. "
            "Run unmodified-reference correctness/timing only in clean_reference_root. "
            "Make repairs and optimization edits only in candidate_root. Never revert, "
            "checkout, or overwrite candidate files to manufacture a clean baseline. "
            "If clean reference correctness is red, record that fact, repair the "
            "candidate, and treat the first green candidate as corrected_reference "
            "before performance optimization."
        )


def _run(
    argv: list[str], *, cwd: Path, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _git_root(project_root: Path) -> Path | None:
    result = _run(["git", "rev-parse", "--show-toplevel"], cwd=project_root)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        root = Path(result.stdout.strip()).resolve(strict=True)
    except OSError:
        return None
    return root if root == project_root else None


def _changed_paths(project_root: Path) -> tuple[str, ...]:
    result = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"],
        cwd=project_root,
    )
    if result.returncode != 0:
        return ()
    paths: list[str] = []
    for line in result.stdout.splitlines():
        value = line[3:].strip() if len(line) >= 4 else ""
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        if value and value not in paths:
            paths.append(value)
    return tuple(paths)


def _valid_existing(path: Path, project_root: Path, head: str) -> bool:
    if not path.is_dir():
        return False
    root = _git_root(path)
    if root != path:
        return False
    current = _run(["git", "rev-parse", "HEAD"], cwd=path)
    common = _run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=path,
    )
    source_common = _run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=project_root,
    )
    return (
        current.returncode == 0
        and current.stdout.strip() == head
        and common.returncode == 0
        and source_common.returncode == 0
        and Path(common.stdout.strip()).resolve()
        == Path(source_common.stdout.strip()).resolve()
    )


def prepare_baseline_workspace(
    project_root: Path | str,
    state_root: Path | str,
) -> BaselineWorkspace | None:
    """Create/reuse a detached clean worktree without touching candidate files."""
    project = Path(project_root).expanduser().resolve(strict=True)
    if _git_root(project) != project:
        return None
    state = Path(state_root).expanduser().resolve()
    worktrees = state / "runtime-worktrees"
    reference = worktrees / "kernel-baseline"
    metadata = state / _METADATA
    cache_root = state / "runtime-cache" / "kernel"
    for name in ("triton", "torchinductor", "cuda", "tmp"):
        (cache_root / name).mkdir(parents=True, exist_ok=True)
    head_result = _run(["git", "rev-parse", "HEAD"], cwd=project)
    if head_result.returncode != 0:
        return None
    head = head_result.stdout.strip()

    if not _valid_existing(reference, project, head):
        if reference.exists():
            _run(
                ["git", "worktree", "remove", "--force", str(reference)],
                cwd=project,
            )
            if reference.exists():
                shutil.rmtree(reference)
        worktrees.mkdir(parents=True, exist_ok=True)
        result = _run(
            ["git", "worktree", "add", "--detach", str(reference), head],
            cwd=project,
            timeout=None,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "failed to create clean kernel baseline worktree: "
                + (result.stderr.strip() or result.stdout.strip())
            )
    changed = _changed_paths(project)
    payload = {
        "schema_version": 1,
        "project_root": str(project),
        "reference_root": str(reference),
        "source_head": head,
        "candidate_dirty": bool(changed),
        "changed_paths": list(changed),
    }
    metadata.parent.mkdir(parents=True, exist_ok=True)
    temporary = metadata.with_suffix(metadata.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, metadata)
    return BaselineWorkspace(
        project_root=project,
        reference_root=reference,
        candidate_dirty=bool(changed),
        changed_paths=changed,
        machine_metadata=metadata,
        cache_root=cache_root,
    )


__all__ = ["BaselineWorkspace", "prepare_baseline_workspace"]
