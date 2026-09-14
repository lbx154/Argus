"""Safe source-checkout updater for the Argus CLI."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urlsplit

from ..core.runtime_identity import source_root
from .update_install import validate_pip_target
from .update_launcher import preserve_windows_launchers

PUBLIC_REPOSITORY = "https://github.com/lbx154/Argus.git"


def public_branch_ref(branch: str) -> str:
    """Return the published ref that corresponds to the checked-out branch."""
    name = str(branch or "").strip() or "main"
    if any(ord(char) < 32 for char in name):
        raise UpdateError("source branch contains invalid control characters")
    return f"refs/heads/{name}"


def public_upstream(branch: str, repository: str = "lbx154/Argus") -> str:
    name = str(branch or "").strip() or "main"
    return f"{repository}/{name}"


class UpdateError(RuntimeError):
    """Raised when an update cannot be completed without risking local work."""

    def __init__(self, message: str, *, result: UpdateResult | None = None) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class UpdateResult:
    root: Path
    upstream: str
    before_revision: str
    after_revision: str
    installed: bool = False

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
            encoding="utf-8",
            errors="replace",
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


def _published_source(checkout: Path, runner: CommandRunner) -> tuple[str, str]:
    """Keep the installed source channel; never redirect an unknown checkout."""
    remote = _checked(runner, ["git", "remote", "get-url", "origin"], cwd=checkout)
    try:
        parsed = urlsplit(remote.replace("git@github.com:", "ssh://git@github.com/", 1))
        port = parsed.port
    except ValueError as exc:
        raise UpdateError("unsupported source origin; expected a trusted Argus GitHub repository") from exc
    if (
        parsed.scheme not in {"https", "ssh"}
        or parsed.hostname != "github.com"
        or parsed.password is not None
        or parsed.username not in ({None} if parsed.scheme == "https" else {"git"})
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise UpdateError("unsupported source origin; expected a trusted Argus GitHub repository")
    repository = parsed.path.strip("/").removesuffix(".git")
    trusted = {
        "lbx154/argus": "lbx154/Argus",
        "lbx154/argus-skill": "lbx154/argus-skill",
        "microsoft/argusagent": "microsoft/ArgusAgent",
    }
    slug = trusted.get(repository.casefold())
    if slug is None:
        raise UpdateError("unsupported source origin; expected a trusted Argus GitHub repository")
    return remote, slug


def _editable_install_command(
    checkout: Path, executable: str, runner: CommandRunner,
) -> list[str]:
    """Resolve a working installer before advancing the checkout."""
    probe = runner([executable, "-m", "pip", "--version"], checkout, 30.0)
    if probe.returncode == 0:
        validate_pip_target(executable, checkout, runner)
        return [executable, "-m", "pip", "install", "-e", str(checkout)]
    uv = shutil.which("uv")
    if uv:
        _checked(runner, [uv, "--version"], cwd=checkout, timeout=30.0)
        return [uv, "pip", "install", "--python", executable, "-e", str(checkout)]
    raise UpdateError(
        "the current Python environment has no working pip and uv is unavailable; "
        "install pip in this environment or make uv available before retrying"
    )


def inspect_source_checkout(
    root: Path | None = None,
    *,
    runner: CommandRunner = _run_command,
) -> UpdateCheck:
    """Compare the checkout with its published branch without changing it."""
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
    repository, slug = _published_source(checkout, runner)
    upstream = public_upstream(branch, slug)
    remote = _checked(
        runner,
        ["git", "ls-remote", repository, upstream_ref],
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
    repository, slug = _published_source(checkout, runner)
    upstream = public_upstream(branch, slug)
    executable = python_executable or sys.executable
    install_command = _editable_install_command(checkout, executable, runner)
    before = _checked(runner, ["git", "rev-parse", "HEAD"], cwd=checkout)
    report("pulling")
    _checked(
        runner,
        ["git", "pull", "--ff-only", repository, upstream_ref],
        cwd=checkout,
        timeout=None,
    )
    after = _checked(runner, ["git", "rev-parse", "HEAD"], cwd=checkout)
    result = UpdateResult(
        root=checkout,
        upstream=upstream,
        before_revision=before,
        after_revision=after,
    )
    published = _checked(runner, ["git", "rev-parse", "FETCH_HEAD"], cwd=checkout)
    if after != published:
        raise UpdateError(
            "source branch contains unpublished local commits; these commits were preserved, "
            "but the checkout does not match the published revision",
            result=result,
        )

    # A prior attempt may have advanced Git and failed during installation.
    # Reinstall even at the same revision so retry actually repairs that state.
    report("installing")
    try:
        with preserve_windows_launchers():
            _checked(runner, install_command, cwd=checkout, timeout=None)
    except (UpdateError, OSError) as exc:
        raise UpdateError(str(exc), result=result) from exc

    report("complete")
    return replace(result, installed=True)


def run_update() -> int:
    try:
        if getattr(sys, "frozen", False):
            raise UpdateError(
                "this is a packaged desktop build; use the desktop updater "
                "to install a signed release"
            )
        if not (source_root() / "pyproject.toml").is_file():
            from .package_update import update_installed_package

            package = update_installed_package()
            print(f"Argus package refreshed using {package.installer} from {package.upstream}.")
            if package.source_note:
                print(package.source_note)
            print("Run `argus` again to activate the installed update.")
            return 0
        result = update_source_checkout()
    except UpdateError as exc:
        sys.stderr.write(f"argus: update failed: {exc}\n")
        return 2

    if result.changed:
        print(f"Argus updated from {result.upstream}.")
        print("Run `argus` to activate the updated cockpit and safe daemon handoff.")
    elif result.installed:
        print(f"Argus source is current; installation refreshed ({result.upstream}).")
        print("Run `argus` again to activate the installed update.")
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
