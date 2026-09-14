import os
import shutil
import subprocess

import pytest


def test_minimal_child_environment_bootstraps_node_without_user_configuration(platform_process_env):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node executable is required for this process-bootstrap check")
    assert set(platform_process_env) <= {"SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP"}
    if os.name == "nt":
        assert platform_process_env["SYSTEMROOT"]
    result = subprocess.run(
        [node, "-e", "process.stdout.write('bootstrap-ok')"],
        env={**platform_process_env, "PATH": os.defpath},
        capture_output=True, text=True, check=True, timeout=15,
    )
    assert result.stdout == "bootstrap-ok"
