"""The compiled first-party SHELX bridge preserves real Windows process semantics."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from argus_skill.core.platon_windows import launcher_path
from argus_skill.core.plugin_runtime import clean_env

pytestmark = pytest.mark.skipif(
    os.name != "nt" or not launcher_path().is_file(),
    reason="Requires the built Windows native adapter (build-native-tools.ps1)",
)


@pytest.mark.parametrize("alias,variable", [
    ("axl.exe", "ARGUS_PLATON_FORWARD_SHELXL"),
    ("axt.exe", "ARGUS_PLATON_FORWARD_SHELXT"),
])
def test_tool_forwarder_preserves_unicode_arguments_cwd_path_and_exit_code(tmp_path, alias, variable):
    helper = tmp_path / alias
    shutil.copyfile(launcher_path(), helper)
    work = tmp_path / "晶体 文件 & workspace"
    work.mkdir()
    script = work / "inspect.ps1"
    script.write_text(
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
        "[pscustomobject]@{Arguments=@($args); Cwd=(Get-Location).Path; "
        "PathUnchanged=($env:PATH -eq $env:EXPECTED_PATH)} | ConvertTo-Json -Compress\n"
        "exit 73\n",
        encoding="ascii",
    )
    system = Path(os.environ["SYSTEMROOT"]) / "System32"
    shell = system / "WindowsPowerShell/v1.0/powershell.exe"
    path = str(system) + ";C:\\synthetic-tools"
    env = {**clean_env(), variable: str(shell), "PATH": path, "EXPECTED_PATH": path}
    arguments = ["D:/晶体数据/a & b.cif", "one argument with spaces", "literal%value^&", "-z2"]
    result = subprocess.run(
        [helper, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script, *arguments],
        cwd=work, env=env, capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 73
    record = json.loads(result.stdout.decode("utf-8-sig"))
    assert record["Arguments"] == arguments  # No PLATON-only +00 added to SHELX.
    assert Path(record["Cwd"]) == work
    assert record["PathUnchanged"] is True


def test_forwarder_without_a_private_target_fails_instead_of_running_platon(tmp_path):
    helper = tmp_path / "axl.exe"
    shutil.copyfile(launcher_path(), helper)
    env = clean_env()
    result = subprocess.run([helper], cwd=tmp_path, env=env, capture_output=True,
                            timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode != 0
    assert b"Missing private SHELX bridge target" in result.stderr
