from __future__ import annotations

import json
import subprocess
from contextlib import nullcontext
from email.message import Message

import pytest

from argus_skill.apps import package_update
from argus_skill.apps.update import UpdateError

ZIP = "https://github.com/lbx154/Argus/archive/refs/heads/main.zip"


@pytest.fixture(autouse=True)
def isolate_mock_installer_launchers(monkeypatch):
    monkeypatch.setattr(package_update, "preserve_windows_launchers", lambda **_kwargs: nullcontext())
    monkeypatch.setattr(package_update, "validate_pip_target", lambda *_args, **_kwargs: None)


def test_pip_redirect_is_rejected_before_installer(installation, monkeypatch):
    _, calls, run = installation

    def reject(*_args, **_kwargs):
        raise UpdateError("PIP_TARGET redirects pip")

    monkeypatch.setattr(package_update, "validate_pip_target", reject)
    with pytest.raises(UpdateError, match="PIP_TARGET"):
        package_update.update_installed_package(runner=run)
    assert calls == []


class Distribution:
    version = "0.1.1"

    def __init__(self, root, *, direct=None, installer="pip", repository="https://github.com/lbx154/Argus"):
        self.root = root
        self.direct = direct
        self.installer = installer
        self.metadata = Message()
        if repository:
            self.metadata["Project-URL"] = f"Repository, {repository}"

    def locate_file(self, _path):
        return self.root / "site-packages"

    def read_text(self, name):
        if name == "direct_url.json":
            return json.dumps(self.direct) if self.direct is not None else None
        if name == "INSTALLER":
            return self.installer + "\n"
        return None


@pytest.fixture
def installation(tmp_path, monkeypatch):
    prefix = tmp_path / "argus-skill"
    prefix.mkdir()
    distribution = Distribution(prefix, direct={"url": ZIP, "archive_info": {}})
    monkeypatch.setattr(package_update.sys, "prefix", str(prefix))
    monkeypatch.setattr(package_update.sys, "executable", str(prefix / "python"))
    monkeypatch.setattr(package_update.sys, "_base_executable", str(tmp_path / "base-python"))
    monkeypatch.setattr(package_update.metadata, "distribution", lambda _name: distribution)
    monkeypatch.setattr(package_update.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(package_update.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(package_update.site, "ENABLE_USER_SITE", False)
    calls = []

    def run(command, cwd, timeout):
        assert cwd == prefix
        assert timeout is None
        calls.append(list(command))
        output = str(prefix.parent) if command == ["uv", "tool", "dir"] else "installed"
        return subprocess.CompletedProcess(command, 0, output, "")

    return distribution, calls, run


def receipt(distribution, content='requirements = [{ name = "argus-skill" }]'):
    distribution.installer = "uv"
    (distribution.root / "uv-receipt.toml").write_text("[tool]\n" + content, encoding="utf-8")


def test_pip_refreshes_same_version_with_current_python_and_dependencies(installation):
    distribution, calls, run = installation
    result = package_update.update_installed_package(runner=run)
    assert calls == [[
        str(distribution.root / "python"), "-m", "pip", "install", "--upgrade",
        "--force-reinstall", "--no-cache-dir", ZIP,
    ]]
    assert "--no-deps" not in calls[0]
    assert result.before_version == result.after_version == "0.1.1"
    assert result.refreshed and result.installer == "pip"


@pytest.mark.parametrize("repository", ["lbx154/Argus", "lbx154/argus-skill", "microsoft/ArgusAgent"])
def test_archive_keeps_repository_and_branch_and_refreshes_old_hash(installation, repository):
    distribution, calls, run = installation
    source = f"https://github.com/{repository}/archive/refs/heads/feat/research-map.zip"
    distribution.direct = {"url": source + "#sha256=old", "archive_info": {"hash": "sha256=old"}}
    result = package_update.update_installed_package(runner=run)
    assert calls[0][-1] == source
    assert result.upstream == source


@pytest.mark.parametrize("revision", ["feature/research-map", "main", "v0.1.1", "a" * 40, ""])
def test_vcs_preserves_requested_revision_instead_of_old_commit(installation, revision):
    distribution, calls, run = installation
    distribution.direct = {
        "url": "https://github.com/lbx154/Argus.git",
        "vcs_info": {"vcs": "git", "requested_revision": revision, "commit_id": "b" * 40},
    }
    package_update.update_installed_package(runner=run)
    assert calls[0][-1] == "git+https://github.com/lbx154/Argus.git" + (f"@{revision}" if revision else "")


def test_vcs_keeps_subdirectory(installation):
    distribution, calls, run = installation
    distribution.direct = {
        "url": "https://github.com/lbx154/Argus.git",
        "vcs_info": {"vcs": "git", "requested_revision": "main"},
        "subdirectory": "python/pkg",
    }
    package_update.update_installed_package(runner=run)
    assert calls[0][-1].endswith("@main#subdirectory=python%2Fpkg")


@pytest.mark.parametrize("direct", [
    None,
    {"url": "file:///tmp/argus_skill-0.1.1-py3-none-any.whl"},
    {"url": "file:///tmp/argus-src", "dir_info": {}},
])
@pytest.mark.parametrize("repository", ["lbx154/Argus", "lbx154/argus-skill", "microsoft/ArgusAgent"])
def test_wheel_uses_its_trusted_repository_with_explicit_main_note(installation, direct, repository):
    distribution, calls, run = installation
    distribution.direct = direct
    distribution.metadata.replace_header("Project-URL", f"Repository, https://github.com/{repository}")
    result = package_update.update_installed_package(runner=run)
    assert calls[0][-1] == f"https://github.com/{repository}/archive/refs/heads/main.zip"
    assert "no recorded branch" in result.source_note
    assert "Repository metadata and main" in result.source_note


@pytest.mark.parametrize("source", [
    "https://github.com/attacker/Argus/archive/main.zip",
    "http://github.com/lbx154/Argus/archive/main.zip",
    "https://github.com.evil.invalid/lbx154/Argus/archive/main.zip",
    "https://user@github.com/lbx154/Argus/archive/main.zip",
    "https://github.com/lbx154/Argus/../other/archive/main.zip",
    "https://github.com/lbx154/Argus/%2e%2e/other/archive/main.zip",
    "https://github.com/lbx154/Argus/blob/main/setup.py",
    "https://example.invalid/argus_skill.whl",
    "file:///tmp/arbitrary-source.zip",
])
def test_unknown_direct_source_never_falls_back_to_repository_metadata(installation, source):
    distribution, calls, run = installation
    distribution.direct = {"url": source}
    with pytest.raises(UpdateError):
        package_update.update_installed_package(runner=run)
    assert calls == []


@pytest.mark.parametrize("repository", ["", "https://github.com/attacker/Argus"])
def test_wheel_without_trusted_repository_cannot_choose_an_arbitrary_upstream(installation, repository):
    distribution, calls, run = installation
    distribution.direct = None
    del distribution.metadata["Project-URL"]
    if repository:
        distribution.metadata["Project-URL"] = f"Repository, {repository}"
    with pytest.raises(UpdateError):
        package_update.update_installed_package(runner=run)
    assert calls == []


def test_uv_tool_upgrades_current_named_tool_using_receipt(installation):
    distribution, calls, run = installation
    receipt(distribution)
    result = package_update.update_installed_package(runner=run)
    assert calls == [
        ["uv", "tool", "dir"],
        ["uv", "tool", "upgrade", "argus-skill", "--reinstall-package", "argus-skill", "--no-cache"],
    ]
    assert result.installer == "uv tool"


def test_uv_tool_supports_older_string_receipt(installation):
    distribution, calls, run = installation
    receipt(distribution, 'requirements = ["argus-skill @ ' + ZIP + '"]')
    package_update.update_installed_package(runner=run)
    assert calls[-1][:3] == ["uv", "tool", "upgrade"]


def test_uv_tool_local_wheel_migrates_same_tool_to_its_official_main(installation):
    distribution, calls, run = installation
    receipt(distribution)
    distribution.direct = {"url": "file:///tmp/argus_skill.whl"}
    result = package_update.update_installed_package(runner=run)
    assert calls[-1] == [
        "uv", "tool", "install", "--python", str(distribution.root.parent / "base-python"),
        "--reinstall-package", "argus-skill", "--no-cache", ZIP,
    ]
    assert result.source_note


def test_uv_tool_pinned_revision_is_disclosed_and_not_migrated(installation):
    distribution, calls, run = installation
    receipt(distribution)
    distribution.direct = {
        "url": "https://github.com/lbx154/Argus.git",
        "vcs_info": {"vcs": "git", "requested_revision": "refs/tags/v0.1.1"},
    }
    result = package_update.update_installed_package(runner=run)
    assert calls[-1][:3] == ["uv", "tool", "upgrade"]
    assert "pins do not follow main" in result.source_note


def test_uv_tool_migration_preserves_primary_extras_and_additional_requirements(installation):
    distribution, calls, run = installation
    receipt(distribution, '''requirements = [
        { name = "argus-skill", path = "/tmp/argus.whl", extras = ["qr"] },
        { name = "colorama", specifier = "==0.4.6" },
        "httpx>=0.27",
    ]''')
    distribution.direct = {"url": "file:///tmp/argus_skill.whl"}
    package_update.update_installed_package(runner=run)
    assert calls[-1][-5:] == ["--with", "colorama==0.4.6", "--with", "httpx>=0.27", f"argus-skill[qr] @ {ZIP}"]
    assert "--force" not in calls[-1]
    assert calls[-1][calls[-1].index("--python") + 1].endswith("base-python")


def test_uv_tool_migration_does_not_silently_drop_custom_resolution_settings(installation):
    distribution, calls, run = installation
    receipt(distribution, 'requirements = [{name="argus-skill"}]\n[tool.options]\nprerelease="allow"')
    distribution.direct = {"url": "file:///tmp/argus_skill.whl"}
    with pytest.raises(UpdateError, match="preserve uv tool options"):
        package_update.update_installed_package(runner=run)
    assert calls == [["uv", "tool", "dir"]]


def test_editable_package_requires_source_updater(installation):
    distribution, calls, run = installation
    distribution.direct = {"url": "file:///tmp/argus-src", "dir_info": {"editable": True}}
    with pytest.raises(UpdateError, match="editable installations"):
        package_update.update_installed_package(runner=run)
    assert calls == []


def test_uv_tool_never_upgrades_a_different_environment(installation):
    distribution, calls, run = installation
    receipt(distribution)

    def wrong_root(command, cwd, timeout):
        run(command, cwd, timeout)
        return subprocess.CompletedProcess(command, 0, str(distribution.root / "other-tools"), "")

    with pytest.raises(UpdateError, match="does not match the running environment"):
        package_update.update_installed_package(runner=wrong_root)
    assert calls == [["uv", "tool", "dir"]]


@pytest.mark.parametrize("content", ['requirements = []', 'requirements = [{ name = "other" }]', 'requirements = [{ name = 123 }]', "not toml"])
def test_uv_tool_invalid_receipt_refuses_before_installation(installation, content):
    distribution, calls, run = installation
    receipt(distribution, content)
    with pytest.raises(UpdateError, match="receipt"):
        package_update.update_installed_package(runner=run)
    assert calls == []


@pytest.mark.parametrize("installer,has_pip", [("uv", True), ("uv", False), ("pip", False)])
def test_uv_pip_targets_running_interpreter_without_requiring_pip(installation, monkeypatch, installer, has_pip):
    distribution, calls, run = installation
    distribution.installer = installer
    monkeypatch.setattr(package_update.importlib.util, "find_spec", lambda _name: object() if has_pip else None)
    result = package_update.update_installed_package(runner=run)
    assert calls == [[
        "uv", "pip", "install", "--python", str(distribution.root / "python"),
        "--reinstall-package", "argus-skill", "--upgrade-package", "argus-skill", "--no-cache", ZIP,
    ]]
    assert result.installer == "uv pip"


def test_missing_installers_reports_actionable_error(installation, monkeypatch):
    _distribution, calls, run = installation
    monkeypatch.setattr(package_update.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(package_update.shutil, "which", lambda _name: None)
    with pytest.raises(UpdateError, match="no pip and uv is unavailable"):
        package_update.update_installed_package(runner=run)
    assert calls == []


def test_install_failure_is_not_reported_as_a_success(installation):
    _distribution, _calls, run = installation

    def failing(command, cwd, timeout):
        run(command, cwd, timeout)
        return subprocess.CompletedProcess(command, 1, "", "dependency resolution failed")

    with pytest.raises(UpdateError, match="dependency resolution failed"):
        package_update.update_installed_package(runner=failing)


def test_user_install_stays_in_user_site(installation, monkeypatch):
    distribution, calls, run = installation
    user_site = distribution.root.parent / "user-site"
    monkeypatch.setattr(distribution, "locate_file", lambda _path: user_site)
    monkeypatch.setattr(package_update.site, "getusersitepackages", lambda: str(user_site))
    monkeypatch.setattr(package_update.site, "ENABLE_USER_SITE", True)
    launcher_options = []
    monkeypatch.setattr(package_update, "preserve_windows_launchers", lambda **kwargs: launcher_options.append(kwargs) or nullcontext())
    package_update.update_installed_package(runner=run)
    assert "--user" in calls[0]
    assert launcher_options[0]["scripts_directory"] == package_update.Path(package_update.sysconfig.get_path(
        "scripts", scheme=package_update.sysconfig.get_preferred_scheme("user"),
    ))


def test_package_from_other_environment_is_not_overwritten(installation, monkeypatch):
    distribution, calls, run = installation
    monkeypatch.setattr(distribution, "locate_file", lambda _path: distribution.root.parent / "other-env")
    with pytest.raises(UpdateError, match="outside the current Python environment"):
        package_update.update_installed_package(runner=run)
    assert calls == []
