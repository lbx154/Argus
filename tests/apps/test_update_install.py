from __future__ import annotations

import subprocess

import pytest

from argus.apps.update import UpdateError
from argus.apps.update_install import validate_pip_target


@pytest.fixture(autouse=True)
def isolated_pip_environment(monkeypatch):
    for name in ("PIP_TARGET", "PIP_PREFIX", "PIP_USER"):
        monkeypatch.delenv(name, raising=False)


def _runner(stdout="", *, returncode=0, stderr=""):
    calls = []

    def run(command, cwd, timeout):
        calls.append((list(command), cwd, timeout))
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    return run, calls


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_user_environment_requires_matching_installation(tmp_path, monkeypatch, value):
    monkeypatch.setenv("PIP_USER", value)
    run, calls = _runner()
    with pytest.raises(UpdateError, match="PIP_USER"):
        validate_pip_target("python", tmp_path, run)
    assert calls == []
    validate_pip_target("python", tmp_path, run, user_install=True)


@pytest.mark.parametrize("key", ["global.user", "install.user", ":env:.user"])
def test_user_config_requires_matching_installation(tmp_path, key):
    run, _calls = _runner(f"{key}='true'\n")
    with pytest.raises(UpdateError, match="user configuration"):
        validate_pip_target("python", tmp_path, run)
    validate_pip_target("python", tmp_path, run, user_install=True)


def test_empty_target_and_prefix_settings_are_not_redirections(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_TARGET", "")
    monkeypatch.setenv("PIP_PREFIX", "")
    run, _calls = _runner("global.target=''\ninstall.prefix=''\n")
    validate_pip_target("python", tmp_path, run)


def test_whitespace_target_is_still_a_directory_redirection(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_TARGET", "   ")
    run, _calls = _runner()
    with pytest.raises(UpdateError, match="PIP_TARGET"):
        validate_pip_target("python", tmp_path, run)
    monkeypatch.delenv("PIP_TARGET")
    run, _calls = _runner("global.target='   '\n")
    with pytest.raises(UpdateError, match="global.target"):
        validate_pip_target("python", tmp_path, run)


def test_other_commands_config_does_not_change_install_target(tmp_path):
    run, _calls = _runner("download.target='downloads'\ncustom-secret-section.target='ignored'\n")
    validate_pip_target("python", tmp_path, run)


def test_invalid_user_boolean_has_safe_diagnostic(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_USER", "sensitive-invalid-value")
    run, _calls = _runner()
    with pytest.raises(UpdateError, match="invalid boolean") as caught:
        validate_pip_target("python", tmp_path, run)
    assert "sensitive-invalid-value" not in str(caught.value)


def test_config_read_failure_never_exposes_captured_credentials(tmp_path):
    run, _calls = _runner(
        "global.index-url='https://user:secret@example.invalid/simple'",
        returncode=1, stderr="invalid config contains another-secret",
    )
    with pytest.raises(UpdateError, match="could not inspect pip configuration safely") as caught:
        validate_pip_target("python", tmp_path, run)
    assert "secret" not in str(caught.value)
    assert "example.invalid" not in str(caught.value)
