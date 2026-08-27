"""Safe source-checkout updater for the Argus CLI."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from ..core.runtime_identity import source_root

PUBLIC_REPOSITORY = "https://github.com/lbx154/Argus.git"
_PUBLIC_MAIN_REF = "refs/heads/main"
_PUBLIC_UPSTREAM = "lbx154/Argus/main"


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


CommandRunner = Callable[[Sequence[str], Path, float], subprocess.CompletedProcess[str]]


def _run_command(
    command: Sequence[str],
    cwd: Path,
    timeout: float,
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
    timeout: float = 30.0,
) -> str:
    result = runner(command, cwd, timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise UpdateError(f"{' '.join(command)} failed: {detail}")
    return result.stdout.strip()


def update_source_checkout(
    root: Path | None = None,
    *,
    runner: CommandRunner = _run_command,
    python_executable: str | None = None,
) -> UpdateResult:
    """Fast-forward from public main and reinstall the loaded source checkout."""
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
    before = _checked(runner, ["git", "rev-parse", "HEAD"], cwd=checkout)
    _checked(
        runner,
        ["git", "pull", "--ff-only", PUBLIC_REPOSITORY, _PUBLIC_MAIN_REF],
        cwd=checkout,
        timeout=300.0,
    )
    after = _checked(runner, ["git", "rev-parse", "HEAD"], cwd=checkout)

    if before != after:
        executable = python_executable or sys.executable
        _checked(
            runner,
            [executable, "-m", "pip", "install", "-e", str(checkout)],
            cwd=checkout,
            timeout=900.0,
        )

    return UpdateResult(
        root=checkout,
        upstream=_PUBLIC_UPSTREAM,
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


__all__ = ["PUBLIC_REPOSITORY", "UpdateError", "UpdateResult", "run_update", "update_source_checkout"]
