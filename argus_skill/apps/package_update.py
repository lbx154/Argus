"""Refresh an installed Argus package in the environment that is running it."""

from __future__ import annotations

import importlib.metadata as metadata
import importlib.util
import json
import re
import shutil
import site
import sys
import sysconfig
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urlsplit, urlunsplit

from .update_install import validate_pip_target
from .update_launcher import preserve_windows_launchers

PACKAGE = "argus-skill"
_REPOSITORIES = {"lbx154/argus", "lbx154/argus-skill", "microsoft/argusagent"}


@dataclass(frozen=True)
class PackageUpdateResult:
    root: Path
    upstream: str
    installer: str
    before_version: str
    after_version: str
    source_note: str = ""
    refreshed: bool = True


def _repository(url: str) -> tuple[str, str]:
    """Validate a distribution URL without accepting lookalike hosts or repos."""
    from .update import UpdateError

    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise UpdateError("installed package has an invalid source URL") from exc
    pieces = unquote(parsed.path).strip("/").split("/")
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or len(pieces) < 2
        or any(part in {"", ".", ".."} or "\\" in part for part in pieces)
        or any(ord(char) < 32 for char in url)
    ):
        raise UpdateError("package source must use an official Argus GitHub HTTPS repository")
    name = "/".join(pieces[:2]).removesuffix(".git")
    if name.lower() not in _REPOSITORIES:
        raise UpdateError("package source is not an official Argus repository")
    return f"https://github.com/{name}", "/".join(pieces[2:])


def _metadata_repository(distribution) -> str:
    from .update import UpdateError

    for entry in distribution.metadata.get_all("Project-URL", []):
        label, separator, url = entry.partition(",")
        if separator and label.strip().lower() == "repository":
            repository, path = _repository(url.strip())
            if not path:
                return repository
    raise UpdateError(
        "this package has no trusted remote source or official Repository metadata; "
        "reinstall it from the intended Argus repository before updating"
    )


def _package_source(distribution) -> tuple[str, str, bool]:
    from .update import UpdateError

    raw = distribution.read_text("direct_url.json")
    if not raw:
        repository = _metadata_repository(distribution)
        return (
            f"{repository}/archive/refs/heads/main.zip",
            "The installed wheel has no recorded branch; using its Repository metadata and main.",
            True,
        )
    try:
        direct = json.loads(raw)
        url = direct["url"]
        if not isinstance(url, str):
            raise ValueError("URL is not a string")
        parsed = urlsplit(url)
    except (ValueError, KeyError, TypeError) as exc:
        raise UpdateError("installed package has invalid direct_url.json metadata") from exc
    directory = direct.get("dir_info")
    if directory is not None:
        if not isinstance(directory, dict) or parsed.scheme != "file":
            raise UpdateError("installed package has invalid local directory metadata")
        if directory.get("editable"):
            raise UpdateError("editable installations must be updated from their source checkout")
        repository = _metadata_repository(distribution)
        return (
            f"{repository}/archive/refs/heads/main.zip",
            "The installed local package has no recorded branch; using its Repository metadata and main.",
            True,
        )
    if parsed.scheme == "file" and parsed.path.lower().endswith(".whl"):
        repository = _metadata_repository(distribution)
        return (
            f"{repository}/archive/refs/heads/main.zip",
            "The local wheel has no recorded branch; using its Repository metadata and main.",
            True,
        )
    repository, path = _repository(url)
    vcs = direct.get("vcs_info")
    if vcs is not None:
        if not isinstance(vcs, dict) or vcs.get("vcs") != "git" or path:
            raise UpdateError("unsupported VCS package source; expected an official Git repository")
        revision = vcs.get("requested_revision", "")
        if not isinstance(revision, str) or any(ord(char) < 32 for char in revision):
            raise UpdateError("installed package has an invalid requested VCS revision")
        source = f"git+{repository}.git"
        if revision:
            source += "@" + quote(revision, safe="/")
        subdirectory = direct.get("subdirectory")
        if subdirectory:
            if not isinstance(subdirectory, str):
                raise UpdateError("installed package has an invalid VCS subdirectory")
            source += "#" + urlencode({"subdirectory": subdirectory})
        note = (
            f"Preserving requested revision {revision!r}; tags and commit pins do not follow main."
            if revision and revision not in {"main", "refs/heads/main"} else ""
        )
        return source, note, False
    if path.lower().endswith(".whl"):
        return (
            f"{repository}/archive/refs/heads/main.zip",
            "The release wheel has no recorded branch; using its repository and main.",
            True,
        )
    if not path.startswith("archive/") or not path.endswith((".zip", ".tar.gz")):
        raise UpdateError("unsupported package source; expected an official GitHub source archive")
    # An archive hash records the previous download, not the branch's next revision.
    # Keeping it would reject a correctly refreshed moving-branch archive.
    note = (
        "Preserving the recorded archive reference; tags and commit pins do not follow main."
        if not path.startswith("archive/refs/heads/") else ""
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")), note, False


def _uv_tool_receipt(prefix: Path, installer: str) -> dict | None:
    from .update import UpdateError

    path = prefix / "uv-receipt.toml"
    if not path.is_file():
        return None
    try:
        receipt = tomllib.loads(path.read_text(encoding="utf-8"))
        requirements = receipt["tool"]["requirements"]
        first = requirements[0]
        name = first.get("name", "") if isinstance(first, dict) else re.split(r"[ @<>=!~\[]", first)[0]
        normalized = re.sub(r"[-_.]+", "-", name).lower()
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise UpdateError("could not identify the current uv tool environment from its receipt") from exc
    if installer != "uv" or normalized != PACKAGE or prefix.name.lower() != PACKAGE:
        raise UpdateError("the current uv tool receipt does not identify an Argus tool environment")
    return receipt["tool"]


def _uv_requirement(value) -> str:
    """Preserve receipt add-ons when replacing a local wheel's source."""
    from .update import UpdateError

    if isinstance(value, str):
        return value
    if not isinstance(value, dict) or set(value) - {
        "name", "extras", "specifier", "url", "path", "marker", "editable",
    } or value.get("editable"):
        raise UpdateError("cannot preserve this uv tool add-on source automatically")
    name = value.get("name", "")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise UpdateError("uv tool receipt has an invalid add-on name")
    extras = value.get("extras", [])
    if not isinstance(extras, list) or any(
        not isinstance(extra, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", extra)
        for extra in extras
    ):
        raise UpdateError("uv tool receipt has invalid package extras")
    if extras:
        name += "[" + ",".join(extras) + "]"
    if "url" in value:
        name += " @ " + value["url"]
    elif "path" in value:
        path = Path(value["path"])
        if not path.is_absolute():
            raise UpdateError("cannot preserve a relative uv tool add-on path automatically")
        name += " @ " + path.as_uri()
    else:
        name += value.get("specifier", "")
    if value.get("marker"):
        name += " ; " + value["marker"]
    return name


def _uv_migration_arguments(receipt: dict, source: str) -> list[str]:
    from .update import UpdateError

    # `tool install` replaces these settings rather than inheriting them. Refuse
    # an unsupported migration before touching an environment with custom rules.
    for setting in ("constraints", "overrides", "excludes", "build-constraint-dependencies", "options"):
        if receipt.get(setting):
            raise UpdateError(f"cannot automatically preserve uv tool {setting} during source migration")
    primary, *additions = receipt["requirements"]
    if isinstance(primary, dict):
        target = _uv_requirement({"name": PACKAGE, "extras": primary.get("extras", [])})
    else:
        match = re.match(r"^[A-Za-z0-9._-]+(?:\[[^\]]*\])?", primary)
        if not match:
            raise UpdateError("uv tool receipt has an invalid primary requirement")
        target = match[0]
    arguments = []
    for addition in additions:
        arguments.extend(["--with", _uv_requirement(addition)])
    arguments.append(source if target == PACKAGE else f"{target} @ {source}")
    return arguments


def update_installed_package(*, runner=None) -> PackageUpdateResult:
    """Reinstall even when the package version is unchanged, resolving dependencies."""
    from .update import UpdateError, _checked, _run_command

    run = runner or _run_command
    try:
        distribution = metadata.distribution(PACKAGE)
    except metadata.PackageNotFoundError as exc:
        raise UpdateError("no installed argus-skill package metadata was found") from exc
    prefix = Path(sys.prefix).resolve()
    installed_root = Path(distribution.locate_file("")).resolve()
    user_site = Path(site.getusersitepackages()).resolve()
    user_install = bool(site.ENABLE_USER_SITE and installed_root.is_relative_to(user_site))
    if not installed_root.is_relative_to(prefix) and not user_install:
        raise UpdateError(
            "Argus is loaded from outside the current Python environment; "
            "run its own installed command before updating"
        )
    source, note, replace_tool_source = _package_source(distribution)
    installer = (distribution.read_text("INSTALLER") or "").strip().lower()
    before = distribution.version
    tool_receipt = _uv_tool_receipt(prefix, installer)
    if tool_receipt is not None:
        uv = shutil.which("uv")
        if not uv:
            raise UpdateError("uv is required to upgrade this uv tool installation")
        tools_root = Path(_checked(run, [uv, "tool", "dir"], cwd=prefix)).resolve()
        if tools_root / PACKAGE != prefix:
            raise UpdateError(
                "uv tool dir does not match the running environment; "
                f"set UV_TOOL_DIR to {prefix.parent} before updating"
            )
        if replace_tool_source:
            # Without --force, uv reuses this environment when its Python
            # matches. --force would delete the running interpreter on Windows.
            command = [
                uv, "tool", "install", "--python",
                getattr(sys, "_base_executable", sys.executable),
                "--reinstall-package", PACKAGE, "--no-cache",
                *_uv_migration_arguments(tool_receipt, source),
            ]
        else:
            command = [uv, "tool", "upgrade", PACKAGE, "--reinstall-package", PACKAGE, "--no-cache"]
        channel = "uv tool"
    elif installer != "uv" and importlib.util.find_spec("pip") is not None:
        validate_pip_target(sys.executable, prefix, run, user_install=user_install)
        command = [
            sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall",
            "--no-cache-dir", source,
        ]
        if user_install:
            command.insert(-1, "--user")
        channel = "pip"
    else:
        if user_install:
            raise UpdateError("pip is required to update this user-site installation in place")
        uv = shutil.which("uv")
        if not uv:
            raise UpdateError(
                "this Python environment has no pip and uv is unavailable; "
                "install pip in this environment or restore uv before updating"
            )
        command = [
            uv, "pip", "install", "--python", sys.executable,
            "--reinstall-package", PACKAGE, "--upgrade-package", PACKAGE, "--no-cache", source,
        ]
        channel = "uv pip"
    scripts_directory = (
        Path(sysconfig.get_path("scripts", scheme=sysconfig.get_preferred_scheme("user")))
        if user_install else None
    )
    try:
        with preserve_windows_launchers(scripts_directory=scripts_directory):
            _checked(run, command, cwd=prefix)
    except OSError as exc:
        raise UpdateError(f"could not replace installed launchers: {exc}") from exc
    importlib.invalidate_caches()
    try:
        after = metadata.version(PACKAGE)
    except metadata.PackageNotFoundError as exc:
        raise UpdateError("installer completed but argus-skill metadata is missing") from exc
    return PackageUpdateResult(prefix, source, channel, before, after, note)
