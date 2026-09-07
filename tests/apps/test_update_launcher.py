from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from argus_skill.apps import update_launcher


@pytest.fixture
def scripts(tmp_path, monkeypatch):
    monkeypatch.setattr(update_launcher, "_IS_WINDOWS", True)
    monkeypatch.setattr(update_launcher, "_launcher_directories", lambda: {tmp_path})
    return tmp_path


def test_installation_replaces_all_argus_launchers_and_cleans_old_backups(scripts):
    for name in update_launcher._LAUNCHER_NAMES:
        (scripts / f"{name}.exe").write_bytes(b"old")
    stale = scripts / ".argus.exe.argus-update-previous.bak"
    stale.write_bytes(b"older")
    unrelated = scripts / "other.exe"
    unrelated.write_bytes(b"untouched")

    with update_launcher.preserve_windows_launchers():
        assert not stale.exists()
        for name in update_launcher._LAUNCHER_NAMES:
            path = scripts / f"{name}.exe"
            assert not path.exists()
            path.write_bytes(b"new")

    assert not list(scripts.glob("*.bak"))
    assert unrelated.read_bytes() == b"untouched"
    for name in update_launcher._LAUNCHER_NAMES:
        assert (scripts / f"{name}.exe").read_bytes() == b"new"


def test_failed_installer_restores_original_launchers(scripts):
    launcher = scripts / "argus.exe"
    launcher.write_bytes(b"original")

    with pytest.raises(RuntimeError, match="installer failed"):
        with update_launcher.preserve_windows_launchers():
            launcher.write_bytes(b"partial replacement")
            raise RuntimeError("installer failed")

    assert launcher.read_bytes() == b"original"
    assert not list(scripts.glob("*.bak"))


def test_restoration_failure_preserves_the_original_installer_exception(scripts, monkeypatch):
    launcher = scripts / "argus.exe"
    launcher.write_bytes(b"original")
    failure = RuntimeError("original installer failure")
    monkeypatch.setattr(
        Path, "replace", lambda *_args: (_ for _ in ()).throw(OSError("recovery failed")),
    )

    with pytest.raises(RuntimeError, match="original installer failure") as caught:
        with update_launcher.preserve_windows_launchers():
            raise failure

    assert caught.value is failure
    assert "recovery failed" in caught.value.__notes__[0]


def test_user_install_does_not_touch_the_system_scripts_directory(scripts, monkeypatch):
    user = scripts / "user-scripts"
    system = scripts / "system-scripts"
    user.mkdir()
    system.mkdir()
    (user / "argus.exe").write_bytes(b"user")
    (system / "argus.exe").write_bytes(b"system")
    monkeypatch.setattr(update_launcher, "_launcher_directories", lambda: {system})
    monkeypatch.setattr(update_launcher, "_invoked_launcher_directory", lambda: user)
    rename = Path.rename

    def refuse_system(path, target):
        if path.parent == system:
            raise PermissionError("system installation is not writable")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", refuse_system)
    with update_launcher.preserve_windows_launchers(scripts_directory=user):
        assert (system / "argus.exe").read_bytes() == b"system"
        (user / "argus.exe").write_bytes(b"updated user")
    assert (user / "argus.exe").read_bytes() == b"updated user"


def test_failure_moving_a_launcher_restores_already_moved_files(scripts, monkeypatch):
    first = scripts / "argus.exe"
    second = scripts / "argus-skill.exe"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    rename = Path.rename

    def fail_second(path, target):
        if path == second:
            raise PermissionError("move refused")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_second)
    with pytest.raises(PermissionError, match="move refused"):
        with update_launcher.preserve_windows_launchers():
            pytest.fail("installer must not start after a failed move")

    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"


def test_installer_that_leaves_launcher_unchanged_does_not_remove_command(scripts):
    launcher = scripts / "argus.exe"
    launcher.write_bytes(b"existing")
    with update_launcher.preserve_windows_launchers():
        pass
    assert launcher.read_bytes() == b"existing"


def test_non_windows_install_does_not_move_launchers(scripts, monkeypatch):
    monkeypatch.setattr(update_launcher, "_IS_WINDOWS", False)
    launcher = scripts / "argus.exe"
    launcher.write_bytes(b"existing")
    with update_launcher.preserve_windows_launchers():
        assert launcher.read_bytes() == b"existing"


def test_frozen_desktop_executable_is_not_moved(scripts, monkeypatch):
    monkeypatch.setattr(update_launcher.sys, "frozen", True, raising=False)
    launcher = scripts / "argus.exe"
    launcher.write_bytes(b"desktop executable")
    with update_launcher.preserve_windows_launchers():
        assert launcher.read_bytes() == b"desktop executable"


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows executable lock")
@pytest.mark.parametrize("succeeds", [True, False])
def test_running_windows_exe_can_update_synchronously_and_restore_on_failure(
    tmp_path, succeeds,
):
    from pip._vendor.distlib.scripts import ScriptMaker

    # Create a real pip-style console .exe. A regular file mock cannot reveal
    # Windows' inability to delete a running executable.
    module = tmp_path / "launcher_probe.py"
    module.write_text(
        "from pathlib import Path\n"
        "from argus_skill.apps import update_launcher\n"
        "from importlib.metadata import version\n"
        "def main():\n"
        "    version('argus-skill')  # caches the running .exe's appended ZIP\n"
        "    root = Path(__file__).parent\n"
        "    launcher = root / 'argus.exe'\n"
        "    original = launcher.read_bytes()\n"
        "    update_launcher._launcher_directories = lambda: {root}\n"
        "    try:\n"
        "        with update_launcher.preserve_windows_launchers():\n"
        "            assert not launcher.exists()\n"
        "            launcher.write_bytes(b'new launcher')\n"
        + ("            pass\n" if succeeds else "            raise RuntimeError('install failed')\n")
        + "    except RuntimeError:\n"
        "        assert launcher.read_bytes() == original\n"
        "        assert not list(root.glob('*.bak'))\n"
        "        print('restored')\n"
        "        return 0\n"
        "    assert launcher.read_bytes() == b'new launcher'\n"
        "    assert len(list(root.glob('*.bak'))) == 1\n"
        "    print('updated synchronously')\n"
        "    return 0\n",
        encoding="utf-8",
    )
    maker = ScriptMaker(None, str(tmp_path))
    maker.variants = {""}
    maker.make("argus = launcher_probe:main")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((str(tmp_path), str(Path(__file__).resolve().parents[2])))

    result = subprocess.run(
        [str(tmp_path / "argus.exe")], env=env, text=True, encoding="utf-8",
        errors="replace", capture_output=True, timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert ("updated synchronously" if succeeds else "restored") in result.stdout
    # Once the wrapper process exits, its retained image can be cleaned up.
    update_launcher._cleanup_backups(tmp_path / "argus.exe")
    assert not list(tmp_path.glob("*.bak"))
