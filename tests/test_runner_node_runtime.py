"""Regression coverage for npm runner shims launched by GUI hosts."""
from __future__ import annotations

import os
from pathlib import Path

from argus.agent_cli.runner_backend import runner_child_environment


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
