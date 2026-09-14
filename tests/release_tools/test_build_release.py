from __future__ import annotations

import os
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
