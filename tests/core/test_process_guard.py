"""Reject invalid guard configurations without launching any provider process."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from argus.agent_cli.process_guard import CONTRACT, configuration


def config(tmp_path):
    return {"protocol": CONTRACT["protocol"], "version": CONTRACT["version"],
            "command": [sys.executable, "-c", "raise AssertionError('must not execute')"],
            "cwd": str(tmp_path), "env": {}, "input": "prompt", "wall_ms": 1000, "grace_ms": 100}


@pytest.mark.parametrize("key,value", [
    ("version", True), ("version", 999), ("command", []), ("command", [None]),
    ("command", ["command\0suffix"]), ("cwd", "relative"), ("env", {"SECRET": None}),
    ("env", {"bad=key": "value"}), ("input", {}), ("wall_ms", False), ("grace_ms", 0),
])
def test_invalid_configuration_is_rejected(tmp_path, key, value):
    raw = json.dumps({**config(tmp_path), key: value}).encode() + b"\n"
    with pytest.raises(ValueError):
        configuration(raw)


def test_partial_configuration_cannot_start_after_owner_eof(tmp_path):
    with pytest.raises(ValueError):
        configuration(json.dumps(config(tmp_path)).encode())


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_guard_refuses_to_share_the_launchers_process_group(tmp_path):
    result = subprocess.run([sys.executable, "-m", "argus.agent_cli.process_guard"],
                            input=json.dumps(config(tmp_path)) + "\n", text=True, capture_output=True,
                            cwd=Path(__file__).resolve().parents[2], timeout=5, check=True)
    response = json.loads(result.stdout)
    assert response["type"] == "exit" and response["code"] is None
    assert "own POSIX session" in response["error"]
