from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from argus_skill.tools import jacobian


def _installed_sidecar() -> Path | None:
    configured = os.environ.get(jacobian.JACOBIAN_MCP_BIN_ENV, "").strip()
    candidate = configured or shutil.which("jacobian-mcp") or ""
    if not candidate:
        return None
    path = Path(candidate).expanduser().resolve()
    return path if path.is_file() else None


SIDECAR = _installed_sidecar()
pytestmark = pytest.mark.skipif(SIDECAR is None, reason="Jacobian MCP not installed")


def test_real_cross_version_mcp_contract() -> None:
    assert SIDECAR is not None
    status = jacobian.status(executable=SIDECAR)
    assert status["transport"] == "mcp-stdio"
    assert status["server"]["name"] == "jacobian"
    assert set(status["tools"]) == {"math.find", "math.run"}
    found = jacobian.find_operations(
        "exact determinant", limit=2, executable=SIDECAR
    )
    assert found["result"]["kind"] == "discovery"
    assert found["result"]["matches"][0]["operation_id"].startswith("matrix.")
    result = jacobian.run_operation(
        "integer.compute.extended_gcd",
        {"left": "84", "right": "30"},
        executable=SIDECAR,
    )
    assert result["result"]["output"]["gcd"] == "6"


def test_real_mcp_validation_error_keeps_field_diagnostics() -> None:
    assert SIDECAR is not None
    with pytest.raises(jacobian.JacobianMcpError) as caught:
        jacobian.run_operation(
            "integer.compute.extended_gcd",
            {"left": "bad", "right": "2"},
            executable=SIDECAR,
        )
    data = caught.value.payload["data"]
    assert data["stage"] == "operation_validation"
    assert data["errors"][0]["location"] == ["left"]
