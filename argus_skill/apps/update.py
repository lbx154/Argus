"""Safe source-checkout updater for the Argus CLI."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from ..core.runtime_identity import source_root

PUBLIC_REPOSITORY = "https://github.com/lbx154/Argus.git"


def public_branch_ref(branch: str) -> str:
    """Return the published ref that corresponds to the checked-out branch."""
    name = str(branch or "").strip() or "main"
    if any(ord(char) < 32 for char in name):
        raise UpdateError("source branch contains invalid control characters")
    return f"refs/heads/{name}"


def public_upstream(branch: str) -> str:
    name = str(branch or "").strip() or "main"
    return f"lbx154/Argus/{name}"


class UpdateError(RuntimeError):
    """Raised when an update cannot be completed without risking local work."""


@dataclass(frozen=True)
class UpdateResult:
    root: Path
    upstream: str
    before_revision: str
    after_revision: str

    @property
    def changed(self) -> bool:
        return self.before_revision != self.after_revision


@dataclass(frozen=True)
class UpdateCheck:
    root: Path
    upstream: str
    current_revision: str
    upstream_revision: str
    branch: str
    dirty: bool

    @property
    def update_available(self) -> bool:
        return bool(self.upstream_revision) and self.current_revision != self.upstream_revision

    @property
    def can_update(self) -> bool:
        return bool(self.branch) and not self.dirty


CommandRunner = Callable[[Sequence[str], Path, float | None], subprocess.CompletedProcess[str]]
ProgressReporter = Callable[[str], None]


def _run_command(
    command: Sequence[str],
    cwd: Path,
    timeout: float | None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise UpdateError(f"required executable not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(f"command timed out: {' '.join(command)}") from exc
    except OSError as exc:
        raise UpdateError(f"could not run {' '.join(command)}: {exc}") from exc


def _checked(
    runner: CommandRunner,
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: float | None = None,
) -> str:
    result = runner(command, cwd, timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise UpdateError(f"{' '.join(command)} failed: {detail}")
    return result.stdout.strip()


def inspect_source_checkout(
    root: Path | None = None,
    *,
    runner: CommandRunner = _run_command,
) -> UpdateCheck:
    """Compare the loaded source checkout with public ``main`` without changing it."""
    checkout = (root or source_root()).expanduser().resolve()
    if not (checkout / "pyproject.toml").is_file():
        raise UpdateError(
            "this Argus installation is not a source checkout; reinstall it "
            "from the latest release instead"
        )
    git_root = Path(
        _checked(runner, ["git", "rev-parse", "--show-toplevel"], cwd=checkout)
    ).resolve()
    if git_root != checkout:
        raise UpdateError(
            f"loaded source root {checkout} does not match Git root {git_root}"
        )
    dirty = bool(
        _checked(
            runner,
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=checkout,
        )
    )
    branch = _checked(runner, ["git", "branch", "--show-current"], cwd=checkout)
    current = _checked(runner, ["git", "rev-parse", "HEAD"], cwd=checkout)
    upstream_ref = public_branch_ref(branch)
    upstream = public_upstream(branch)
    remote = _checked(
        runner,
        ["git", "ls-remote", PUBLIC_REPOSITORY, upstream_ref],
        cwd=checkout,
        timeout=60.0,
    )
    upstream_revision = remote.split(None, 1)[0] if remote.strip() else ""
    if not upstream_revision:
        raise UpdateError(f"published branch {upstream!r} did not return a revision")
    return UpdateCheck(
        root=checkout,
        upstream=upstream,
        current_revision=current,
        upstream_revision=upstream_revision,
        branch=branch,
        dirty=dirty,
    )


def update_source_checkout(
    root: Path | None = None,
    *,
    runner: CommandRunner = _run_command,
    python_executable: str | None = None,
    on_progress: ProgressReporter | None = None,
) -> UpdateResult:
    """Fast-forward from the matching published branch and reinstall the checkout."""
    report = on_progress or (lambda _phase: None)
    report("validating")
    checkout = (root or source_root()).expanduser().resolve()
    if not (checkout / "pyproject.toml").is_file():
        raise UpdateError(
            "this Argus installation is not a source checkout; reinstall it "
            "from the latest release instead"
        )

    git_root = Path(
        _checked(runner, ["git", "rev-parse", "--show-toplevel"], cwd=checkout)
    ).resolve()
    if git_root != checkout:
        raise UpdateError(
            f"loaded source root {checkout} does not match Git root {git_root}"
        )

    dirty = _checked(
        runner,
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=checkout,
    )
    if dirty:
        raise UpdateError(
            "source checkout has local changes; commit, stash, or remove them "
            "before running `argus update`"
        )

    branch = _checked(
        runner,
        ["git", "branch", "--show-current"],
        cwd=checkout,
    )
    if not branch:
        raise UpdateError("source checkout is detached; switch to a branch first")
    upstream_ref = public_branch_ref(branch)
    upstream = public_upstream(branch)
    before = _checked(runner, ["git", "rev-parse", "HEAD"], cwd=checkout)
    report("pulling")
    _checked(
        runner,
        ["git", "pull", "--ff-only", PUBLIC_REPOSITORY, upstream_ref],
        cwd=checkout,
        timeout=None,
    )
    after = _checked(runner, ["git", "rev-parse", "HEAD"], cwd=checkout)

    if before != after:
        report("installing")
        executable = python_executable or sys.executable
        _checked(
            runner,
            [executable, "-m", "pip", "install", "-e", str(checkout)],
            cwd=checkout,
            timeout=None,
        )

    report("complete")
    return UpdateResult(
        root=checkout,
        upstream=upstream,
        before_revision=before,
        after_revision=after,
    )


def run_update() -> int:
    try:
        result = update_source_checkout()
    except UpdateError as exc:
        sys.stderr.write(f"argus: update failed: {exc}\n")
        return 2

    if result.changed:
        print(f"Argus updated from {result.upstream}.")
        print("Run `argus` to activate the updated cockpit and safe daemon handoff.")
    else:
        print(f"Argus is already up to date ({result.upstream}).")
    return 0


__all__ = [
    "PUBLIC_REPOSITORY",
    "UpdateCheck",
    "UpdateError",
    "UpdateResult",
    "inspect_source_checkout",
    "public_branch_ref",
    "public_upstream",
    "run_update",
    "update_source_checkout",
]
