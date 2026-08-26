"""Install and locate the pinned upstream PPT Master toolkit."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..core import paths as core_paths

PPT_MASTER_REPOSITORY = "https://github.com/hugohe3/ppt-master.git"
PPT_MASTER_REVISION = "2e29f3d3cfc379c689b07027d0fa776b9ff79291"
INSTALL_MANIFEST = "ppt-master.install.json"
_REQUIRED_PATHS = (
    "LICENSE",
    "skills/ppt-master/SKILL.md",
    "skills/ppt-master/workflows/routing.md",
    "skills/ppt-master/workflows/generate-pptx.md",
    "skills/ppt-master/scripts/project_manager.py",
    "skills/ppt-master/scripts/svg_to_pptx.py",
    "skills/ppt-master/requirements.txt",
)


@dataclass(frozen=True)
class PptMasterStatus:
    installed: bool
    root: str
    skill_root: str
    expected_revision: str
    revision: str | None
    dependencies_installed: bool
    valid: bool
    detail: str


def install_root(global_root: Path | None = None) -> Path:
    root = Path(global_root) if global_root is not None else core_paths.tools_root()
    if global_root is not None:
        root = root / "tools"
    return root / "ppt-master"


def skill_root(global_root: Path | None = None) -> Path:
    return install_root(global_root) / "skills" / "ppt-master"


def _manifest_path(root: Path) -> Path:
    return root.parent / INSTALL_MANIFEST


def _run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(argv),
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = (
            (exc.stderr or exc.stdout or "").strip()
            if isinstance(exc, subprocess.CalledProcessError)
            else str(exc)
        )
        command = " ".join(argv)
        failure = detail or f"exit {getattr(exc, 'returncode', 1)}"
        raise RuntimeError(
            f"PPT Master command failed ({command}): {failure}"
        ) from exc


def _git_revision(root: Path, git: str) -> str | None:
    if not (root / ".git").is_dir():
        return None
    result = subprocess.run(
        [git, "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _validate_checkout(root: Path, revision: str, git: str) -> None:
    missing = [relative for relative in _REQUIRED_PATHS if not (root / relative).is_file()]
    if missing:
        raise RuntimeError(f"PPT Master checkout is incomplete: missing {', '.join(missing)}")
    license_text = (root / "LICENSE").read_text(encoding="utf-8", errors="replace")
    if "MIT License" not in license_text or "Hugo He" not in license_text:
        raise RuntimeError("PPT Master checkout has an unexpected license")
    actual = _git_revision(root, git)
    if actual != revision:
        raise RuntimeError(
            f"PPT Master revision mismatch: expected {revision}, found {actual or 'unknown'}"
        )


def _supports_sparse_checkout(git: str) -> bool:
    """Whether Git has the ``sparse-checkout`` porcelain (introduced in 2.25)."""
    result = subprocess.run(
        [git, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    words = result.stdout.strip().split()
    if result.returncode != 0 or not words:
        return False
    try:
        major, minor, *_rest = (int(part) for part in words[-1].split("."))
    except ValueError:
        return False
    return (major, minor) >= (2, 25)


def _configure_sparse_checkout(root: Path, git: str) -> bool:
    if not _supports_sparse_checkout(git):
        return False
    _run([git, "-C", str(root), "sparse-checkout", "init", "--no-cone"])
    _run(
        [
            git,
            "-C",
            str(root),
            "sparse-checkout",
            "set",
            "/LICENSE",
            "/skills/ppt-master/",
        ]
    )
    return True


def _tracked_changes(root: Path, git: str) -> str:
    return _run(
        [git, "-C", str(root), "status", "--porcelain", "--untracked-files=no"]
    ).stdout.strip()


def _prepare_checkout(
    *,
    target: Path,
    repository: str,
    revision: str,
    git: str,
) -> Path:
    temporary = target.with_name(f".ppt-master.{uuid.uuid4().hex}.tmp")
    _run([git, "init", str(temporary)])
    _run([git, "-C", str(temporary), "remote", "add", "origin", repository])
    sparse = _configure_sparse_checkout(temporary, git)
    fetch = [
        git,
        "-C",
        str(temporary),
        "fetch",
        "--depth",
        "1",
    ]
    if sparse:
        fetch.append("--filter=blob:none")
    fetch.extend(["origin", revision])
    _run(fetch)
    _run([git, "-C", str(temporary), "checkout", "--detach", "FETCH_HEAD"])
    _validate_checkout(temporary, revision, git)
    return temporary


def _remove_tree(path: Path) -> None:
    def remove_readonly(func, value, _exc_info) -> None:
        os.chmod(value, stat.S_IWRITE)
        func(value)

    shutil.rmtree(path, onerror=remove_readonly)


def _copy_private_config(source: Path, destination: Path) -> None:
    for relative in (Path(".env"), Path("skills/ppt-master/.env")):
        source_path = source / relative
        if not source_path.is_file():
            continue
        destination_path = destination / relative
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)


def _replace_checkout(target: Path, prepared: Path) -> None:
    if not target.exists():
        os.replace(prepared, target)
        return
    backup = target.with_name(f".ppt-master.{uuid.uuid4().hex}.backup")
    os.replace(target, backup)
    try:
        os.replace(prepared, target)
    except BaseException:
        os.replace(backup, target)
        raise
    _remove_tree(backup)


def _python_executable(explicit: str | None = None) -> str:
    return explicit or os.environ.get("ARGUS_SKILL_PYTHON") or sys.executable


def _python_has_pip(python: str) -> bool:
    try:
        result = subprocess.run(
            [python, "-m", "pip", "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return result.returncode == 0


def _install_requirements(python: str, requirements: Path) -> None:
    if _python_has_pip(python):
        command = [python, "-m", "pip", "install", "-r", str(requirements)]
    else:
        uv = shutil.which("uv")
        if uv is None:
            raise RuntimeError(
                f"PPT Master dependency installation requires pip for {python} "
                "or the uv executable"
            )
        command = [
            uv,
            "pip",
            "install",
            "--python",
            python,
            "-r",
            str(requirements),
        ]
    _run(command)


def _write_manifest(
    root: Path,
    *,
    repository: str,
    revision: str,
    python_executable: str,
    dependencies_installed: bool,
) -> None:
    payload = {
        "schema": 1,
        "repository": repository,
        "revision": revision,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "python_executable": python_executable,
        "dependencies_installed": dependencies_installed,
    }
    destination = _manifest_path(root)
    temporary = destination.with_name(f".{INSTALL_MANIFEST}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def install_ppt_master(
    *,
    global_root: Path | None = None,
    repository: str = PPT_MASTER_REPOSITORY,
    revision: str = PPT_MASTER_REVISION,
    python_executable: str | None = None,
    install_dependencies: bool = True,
) -> PptMasterStatus:
    """Install the exact audited upstream revision and its Python dependencies."""
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("PPT Master installation requires git")
    target = install_root(global_root)
    target.parent.mkdir(parents=True, exist_ok=True)

    current_revision: str | None = None
    if target.exists():
        if not (target / ".git").is_dir():
            raise RuntimeError(f"refusing to replace non-git PPT Master path: {target}")
        dirty = _tracked_changes(target, git)
        if dirty:
            raise RuntimeError(f"refusing to update a modified PPT Master checkout: {target}")
        remote = _run([git, "-C", str(target), "remote", "get-url", "origin"]).stdout.strip()
        if remote != repository:
            raise RuntimeError(
                f"refusing unexpected PPT Master origin {remote!r}; expected {repository!r}"
            )
        current_revision = _git_revision(target, git)

    python = _python_executable(python_executable)
    if current_revision == revision:
        prepared = target
        _validate_checkout(prepared, revision, git)
    else:
        prepared = _prepare_checkout(
            target=target,
            repository=repository,
            revision=revision,
            git=git,
        )
        try:
            if target.exists():
                _copy_private_config(target, prepared)
            if install_dependencies:
                _install_requirements(
                    python,
                    prepared / "skills" / "ppt-master" / "requirements.txt",
                )
            _replace_checkout(target, prepared)
        finally:
            if prepared.exists() and prepared != target:
                _remove_tree(prepared)

    _validate_checkout(target, revision, git)
    if install_dependencies and current_revision == revision:
        _install_requirements(
            python,
            skill_root(global_root) / "requirements.txt",
        )
    _write_manifest(
        target,
        repository=repository,
        revision=revision,
        python_executable=python,
        dependencies_installed=install_dependencies,
    )
    return ppt_master_status(global_root=global_root, expected_revision=revision)


def ppt_master_status(
    *,
    global_root: Path | None = None,
    expected_revision: str = PPT_MASTER_REVISION,
) -> PptMasterStatus:
    target = install_root(global_root)
    root = skill_root(global_root)
    git = shutil.which("git")
    revision = _git_revision(target, git) if git else None
    manifest: dict[str, object] = {}
    manifest_path = _manifest_path(target)
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            manifest = loaded
    dependencies_installed = bool(manifest.get("dependencies_installed"))
    recorded_python = str(manifest.get("python_executable") or "")
    expected_python = _python_executable()
    same_python = bool(recorded_python) and (
        Path(recorded_python).expanduser().resolve()
        == Path(expected_python).expanduser().resolve()
    )
    if not same_python:
        dependencies_installed = False
    missing = [relative for relative in _REQUIRED_PATHS if not (target / relative).is_file()]
    installed = target.is_dir()
    dirty = _tracked_changes(target, git) if installed and git and revision else ""
    valid = installed and not missing and revision == expected_revision and not dirty
    if not installed:
        detail = "not installed"
    elif missing:
        detail = f"incomplete checkout: missing {', '.join(missing)}"
    elif revision != expected_revision:
        detail = f"revision {revision or 'unknown'}; expected {expected_revision}"
    elif dirty:
        detail = "tracked toolkit files are modified"
    elif not dependencies_installed:
        detail = "toolkit installed; dependencies not recorded for this Python"
    else:
        detail = "ready"
    return PptMasterStatus(
        installed=installed,
        root=str(target),
        skill_root=str(root),
        expected_revision=expected_revision,
        revision=revision,
        dependencies_installed=dependencies_installed,
        valid=valid,
        detail=detail,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("install")
    subparsers.add_parser("status")
    path_parser = subparsers.add_parser("path")
    path_parser.add_argument("--skill-root", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "install":
        status = install_ppt_master()
        print(json.dumps(asdict(status), indent=2, sort_keys=True))
        return 0
    if args.command == "status":
        status = ppt_master_status()
        print(json.dumps(asdict(status), indent=2, sort_keys=True))
        return 0 if status.valid and status.dependencies_installed else 1
    print(skill_root() if args.skill_root else install_root())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
