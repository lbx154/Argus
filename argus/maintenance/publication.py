"""Publish the already-reviewed commit without running a second acceptance pipeline."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..agent_cli._process_control import windows_hidden_subprocess_kwargs
from ..core.secret_guard import redact_secrets_text


def _git(repository: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ['git', *args], cwd=repository, capture_output=True, text=True,
        timeout=120, **windows_hidden_subprocess_kwargs(),
    )
    if check and result.returncode:
        detail = redact_secrets_text((result.stderr or result.stdout).strip())[-1000:]
        raise RuntimeError(f'git {args[0]} failed: {detail}')
    return result


def publish_reviewed_change(repository: Path, candidate: str, receipt_dir: Path) -> dict[str, str]:
    """Prepare an exact source checkout and fast-forward origin/main if needed.

    The caller owns the reviewed task and the operator's adopt decision. Git
    resolves its frozen commit and prevents overwriting concurrent remote work.
    Task tests/builds belong to Engineer/Reviewer, not to this publication step.
    """
    repository = repository.expanduser().resolve(strict=True)
    revision = _git(repository, 'rev-parse', '--verify', '--end-of-options', f'{candidate}^{{commit}}').stdout.strip()
    runtime = receipt_dir.resolve() / 'deployed-runtimes' / revision
    runtime.parent.mkdir(parents=True, exist_ok=True)
    if not runtime.exists():
        _git(repository, 'worktree', 'add', '--detach', str(runtime), revision)
    if _git(runtime, 'rev-parse', 'HEAD').stdout.strip() != revision or _git(
        runtime, 'diff', '--quiet', revision, '--', check=False,
    ).returncode:
        raise ValueError('prepared runtime no longer matches the reviewed commit')

    _git(repository, 'fetch', '--no-tags', 'origin', 'main')
    if _git(repository, 'merge-base', '--is-ancestor', revision, 'refs/remotes/origin/main', check=False).returncode:
        _git(repository, 'push', 'origin', f'{revision}:refs/heads/main')
    # Retain this checkout for handoff/rollback and retry. Never discard authoring
    # evidence or force a remote ref back after a failed publication.
    return {'verdict': 'ADOPT', 'reviewed_candidate': revision, 'runtime_source_root': str(runtime)}
