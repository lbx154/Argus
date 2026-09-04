from __future__ import annotations

import os
import tomllib
from pathlib import Path

from argus_skill.release_tools import build_release


def test_sdist_carries_frontends_required_by_the_wheel() -> None:
    project = tomllib.loads(
        (build_release.ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    targets = project["tool"]["hatch"]["build"]["targets"]

    assert "frontend/web/dist/**" in targets["sdist"]["include"]
    assert targets["wheel"]["force-include"] == {
        "argus_doctor.py": "argus_doctor.py",
        "frontend/tui/bundle/argus.mjs": (
            "argus_skill/_frontend/tui/bundle/argus.mjs"
        ),
        "frontend/web/dist": "argus_skill/_frontend/web/dist",
    }


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
        captured["python_target"] = (
            shim.read_text(encoding="utf-8")
            if os.name == "nt"
            else str(shim.resolve())
        )

    monkeypatch.setattr(build_release.subprocess, "run", fake_run)

    build_release.run("npm", "run", "build")

    assert captured["argv"] == ("npm", "run", "build")
    assert captured["check"] is True
    if os.name == "nt":
        assert captured["python_target"] == '@"%ARGUS_RELEASE_PYTHON%" %*\n'
        assert captured["env"]["ARGUS_RELEASE_PYTHON"] == interpreter
        captured["python_target"].encode("ascii")
    else:
        assert captured["python_target"] == interpreter
    assert captured["env"]["PYTHONPATH"].split(os.pathsep)[0] == str(build_release.ROOT)
