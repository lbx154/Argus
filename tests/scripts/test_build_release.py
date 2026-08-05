from __future__ import annotations

import os

from scripts import build_release


def test_release_frontend_subprocesses_use_current_python_bin(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(build_release.sys, "executable", "/opt/argus-venv/bin/python")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)

    monkeypatch.setattr(build_release.subprocess, "run", fake_run)

    build_release.run("npm", "run", "build")

    assert captured["argv"] == ("npm", "run", "build")
    assert captured["check"] is True
    assert captured["env"]["PATH"].split(os.pathsep)[0] == "/opt/argus-venv/bin"
