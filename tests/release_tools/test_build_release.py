from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from argus.release_tools import build_release

ROOT = Path(__file__).parents[2]


def test_release_uses_the_platform_npm_launcher() -> None:
    expected = "npm.cmd" if os.name == "nt" else "npm"
    assert build_release.NPM_COMMAND == expected


def test_release_subprocesses_use_current_python_bin(monkeypatch) -> None:
    captured = {}
    interpreter = (
        r"G:\workspace\测试\argus-venv\Scripts\python.exe"
        if os.name == "nt"
        else "/opt/argus-venv/bin/python"
    )
    monkeypatch.setattr(build_release.sys, "executable", interpreter)
    monkeypatch.setenv("PATH", os.pathsep.join(("/usr/bin", "/bin")))

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        shim_dir = kwargs["env"]["PATH"].split(os.pathsep)[0]
        shim = Path(shim_dir) / ("python.cmd" if os.name == "nt" else "python")
        captured["python_target"] = shim.read_text(encoding="utf-8")

    monkeypatch.setattr(build_release.subprocess, "run", fake_run)

    build_release.run("npm", "run", "build")

    assert captured["argv"] == ("npm", "run", "build")
    assert captured["check"] is True
    if os.name == "nt":
        assert captured["python_target"] == '@"%ARGUS_RELEASE_PYTHON%" %*\n'
        assert captured["env"]["ARGUS_RELEASE_PYTHON"] == interpreter
        captured["python_target"].encode("ascii")
    else:
        assert captured["python_target"] == f'#!/bin/sh\nexec {interpreter} "$@"\n'
    assert captured["env"]["PYTHONPATH"].split(os.pathsep)[0] == str(build_release.ROOT)


@pytest.mark.skipif(os.name == "nt", reason="POSIX virtualenv launcher")
def test_release_python_keeps_a_symlinked_virtualenv_prefix(tmp_path, monkeypatch) -> None:
    environment = tmp_path / "venv with spaces"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    monkeypatch.setattr(build_release.sys, "executable", str(environment / "bin" / "python"))

    build_release.run(
        "python",
        "-c",
        "import sys; assert sys.prefix == sys.argv[1], (sys.prefix, sys.argv[1])",
        str(environment),
    )


def test_release_subprocesses_name_the_interpreter_for_frontend_scripts(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(build_release.sys, "executable", "/opt/argus-venv/bin/python")

    def fake_run(argv, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(build_release.subprocess, "run", fake_run)
    build_release.run("npm", "run", "build")
    assert captured["env"]["ARGUS_PYTHON"] == "/opt/argus-venv/bin/python"


def test_help_describes_the_build_without_running_it(monkeypatch, capsys) -> None:
    def forbidden(*_argv, **_kwargs):
        raise AssertionError("--help must not start a build")

    monkeypatch.setattr(build_release, "run", forbidden)
    with pytest.raises(SystemExit) as exc:
        build_release.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "build_release" in out
    assert "frontend/web/dist" in out


def test_unknown_option_is_rejected_without_running_a_build(monkeypatch, capsys) -> None:
    def forbidden(*_argv, **_kwargs):
        raise AssertionError("a usage error must not start a build")

    monkeypatch.setattr(build_release, "run", forbidden)
    with pytest.raises(SystemExit) as exc:
        build_release.main(["--clean"])
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_no_arguments_runs_the_full_pipeline(monkeypatch, capsys) -> None:
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        build_release, "run", lambda *argv, **kwargs: commands.append(tuple(argv)),
    )
    assert build_release.main([]) == 0
    joined = [" ".join(command) for command in commands]
    assert any("argus.release_tools.generate_manifest" in command for command in joined)
    assert any(command.endswith("run build") for command in joined)
    assert any("argus.release_tools.check_artifacts" in command for command in joined)
    assert "release ready:" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("node") is None, reason="frontend launcher needs Node.js")
def test_frontend_python_launcher_prefers_argus_python(tmp_path) -> None:
    launcher = ROOT / "frontend" / "scripts" / "python.mjs"
    env = {**os.environ, "ARGUS_PYTHON": sys.executable}
    completed = subprocess.run(
        [shutil.which("node"), str(launcher), "-c", "import sys, argus; print(sys.executable)"],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == sys.executable


@pytest.mark.skipif(shutil.which("node") is None, reason="frontend launcher needs Node.js")
def test_frontend_python_launcher_explains_a_missing_interpreter(tmp_path) -> None:
    launcher = ROOT / "frontend" / "scripts" / "python.mjs"
    node = shutil.which("node")
    empty = tmp_path / "no-python"
    empty.mkdir()
    env = {
        key: value for key, value in os.environ.items()
        if key not in {"ARGUS_PYTHON", "PATH", "VIRTUAL_ENV"}
    }
    env["PATH"] = str(empty)
    completed = subprocess.run(
        [node, str(launcher), "-m", "argus.release_tools.generate_event_types", "--check"],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=60,
        cwd=str(tmp_path),
    )
    assert completed.returncode == 1
    assert "set ARGUS_PYTHON" in completed.stderr
    assert "argus.release_tools.generate_event_types" in completed.stderr
