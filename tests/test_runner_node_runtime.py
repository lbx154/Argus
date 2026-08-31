"""Regression coverage for npm runner shims launched by GUI hosts."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from argus_skill.agent_cli.runner_backend import runner_child_environment


def _node_path(env: dict[str, str]) -> list[Path]:
    return [Path(entry) for entry in env["PATH"].split(os.pathsep) if entry]


def test_npm_batch_runner_gets_node_from_nvm_settings_when_path_is_stale(
    tmp_path: Path,
) -> None:
    runner = tmp_path / "npm" / "codex.cmd"
    runner.parent.mkdir()
    runner.write_text("@echo off\nnode ignored.js\n", encoding="utf-8")
    node_dir = tmp_path / "nodejs"
    node_dir.mkdir()
    (node_dir / "node.exe").write_bytes(b"test-node")
    local_app_data = tmp_path / "AppData" / "Local"
    nvm = local_app_data / "nvm"
    nvm.mkdir(parents=True)
    (nvm / "settings.txt").write_text(
        f"root: {tmp_path / 'nvm'}\npath: {node_dir}\n",
        encoding="utf-8",
    )
    env = {
        "PATH": str(tmp_path / "no-node-on-path"),
        "LOCALAPPDATA": str(local_app_data),
        "APPDATA": str(tmp_path / "AppData" / "Roaming"),
        "USERPROFILE": str(tmp_path),
    }

    patched = runner_child_environment(str(runner), env=env)

    assert patched is not None
    assert _node_path(patched)[0] == node_dir
    assert _node_path(patched)[1] == Path(env["PATH"])


def test_npm_batch_runner_keeps_a_path_that_already_has_node(tmp_path: Path) -> None:
    runner = tmp_path / "npm" / "codex.cmd"
    runner.parent.mkdir()
    runner.touch()
    node_dir = tmp_path / "nodejs"
    node_dir.mkdir()
    (node_dir / "node.exe").touch()

    assert runner_child_environment(
        str(runner),
        env={"PATH": str(node_dir)},
    ) is None


def test_native_runner_never_rewrites_the_environment(tmp_path: Path) -> None:
    runner = tmp_path / "codex.exe"
    runner.touch()

    assert runner_child_environment(
        str(runner),
        env={"PATH": str(tmp_path / "nothing")},
    ) is None


def test_backend_readiness_probes_npm_runner_with_repaired_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Doctor must validate the same Node path a real turn will inherit."""
    from argus_skill.core import backend_readiness

    runner = tmp_path / "npm" / "codex.cmd"
    runner.parent.mkdir()
    runner.touch()
    node_dir = tmp_path / "nodejs"
    node_dir.mkdir()
    (node_dir / "node.exe").touch()
    local_app_data = tmp_path / "AppData" / "Local"
    nvm = local_app_data / "nvm"
    nvm.mkdir(parents=True)
    (nvm / "settings.txt").write_text(f"path: {node_dir}\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path / "no-node-on-path"))
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("NVM_HOME", raising=False)
    monkeypatch.delenv("NVM_SYMLINK", raising=False)
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    seen: dict[str, object] = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(command, 0, stdout="codex-cli 0.144.5", stderr="")

    monkeypatch.setattr(backend_readiness.subprocess, "run", fake_run)
    backend_readiness._run_text((str(runner), "--version"), timeout_s=1)

    assert seen["command"] == [str(runner), "--version"]
    assert isinstance(seen["env"], dict)
    assert _node_path(seen["env"])[0] == node_dir
