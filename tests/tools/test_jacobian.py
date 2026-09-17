from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from argus.tools import jacobian


def _caller(calls: list[tuple]) -> jacobian.McpCaller:
    def call(tool, arguments, timeout, executable):
        calls.append((tool, arguments, timeout, executable))
        return {
            "schema_version": 1,
            "transport": "mcp-stdio",
            "protocol_version": "2025-11-25",
            "server": {"name": "jacobian", "version": "0.14.0"},
            "tools": ["math.find", "math.run"],
            "tool": tool,
            "request": arguments,
            "result": {"kind": "test"},
        }

    return call


def _binary(tmp_path: Path) -> Path:
    path = tmp_path / "jacobian-mcp"
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_resolve_jacobian_mcp_honors_explicit_path(tmp_path: Path) -> None:
    binary = _binary(tmp_path)
    assert jacobian.resolve_jacobian_mcp_executable(
        {jacobian.JACOBIAN_MCP_BIN_ENV: str(binary)}
    ) == binary.resolve()


def test_capability_note_names_the_mcp_bridge(tmp_path: Path, monkeypatch) -> None:
    binary = _binary(tmp_path)
    monkeypatch.setenv(jacobian.JACOBIAN_MCP_BIN_ENV, str(binary))
    note = jacobian.jacobian_capability_note()
    source_interpreter = Path(jacobian.__file__).resolve().parents[2] / ".venv/bin/python"
    assert str(binary.resolve()) in note
    interpreter = source_interpreter if source_interpreter.is_file() else Path(sys.executable)
    assert str(interpreter) in note
    assert "argus.tools.jacobian find" in note
    assert "import Jacobian" not in note


def test_find_delegates_to_official_math_find_contract(tmp_path: Path) -> None:
    calls: list[tuple] = []
    binary = _binary(tmp_path)
    jacobian.find_operations(
        "exact determinant",
        domain="matrix",
        limit=3,
        executable=binary,
        caller=_caller(calls),
    )
    assert calls == [
        (
            "math.find",
            {
                "request": {
                    "op": "search",
                    "query": "exact determinant",
                    "limit": 3,
                    "domain": "matrix",
                }
            },
            120,
            binary,
        )
    ]


def test_run_preserves_exact_payload_for_math_run(tmp_path: Path) -> None:
    calls: list[tuple] = []
    binary = _binary(tmp_path)
    payload = {"left": "84", "right": "30"}
    jacobian.run_operation(
        "integer.compute.extended_gcd",
        payload,
        executable=binary,
        caller=_caller(calls),
    )
    assert calls[0][0] == "math.run"
    assert calls[0][1] == {
        "operation_id": "integer.compute.extended_gcd",
        "payload": payload,
    }


def test_safe_environment_drops_unrelated_credentials() -> None:
    env = jacobian._safe_env(
        {
            "HOME": "/home/test",
            "PATH": "/bin",
            "OPENAI_API_KEY": "secret",
            "ARGUS_SKILL_TELEGRAM_BOT_TOKEN": "secret",
        }
    )
    assert env["HOME"] == "/home/test"
    assert env["PATH"] == "/bin"
    assert "OPENAI_API_KEY" not in env
    assert "ARGUS_SKILL_TELEGRAM_BOT_TOKEN" not in env


def test_payload_file_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "payload.json"
    target.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(jacobian.JacobianAdapterError, match="non-symlink"):
        jacobian._payload_file(str(link))


def test_payload_file_rejects_non_json_extension(tmp_path: Path) -> None:
    payload = tmp_path / "result.csv"
    payload.write_text("{}")
    with pytest.raises(jacobian.JacobianAdapterError, match="dedicated .json"):
        jacobian._payload_file(str(payload))


def test_invalid_operation_id_is_rejected_before_sidecar_start(tmp_path: Path) -> None:
    with pytest.raises(jacobian.JacobianAdapterError, match="invalid"):
        jacobian.run_operation("../../shell", {}, executable=_binary(tmp_path))


def test_payload_file_rejects_invalid_encoding(tmp_path: Path) -> None:
    payload = tmp_path / "payload.json"
    payload.write_bytes(b"\xff")
    with pytest.raises(jacobian.JacobianAdapterError, match="not valid JSON"):
        jacobian._payload_file(str(payload))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable fixture")
def test_stdio_sidecar_smoke_preserves_contract_and_isolates_credentials(
    tmp_path: Path, monkeypatch,
) -> None:
    binary = tmp_path / "jacobian-mcp"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import math, os\n"
        "from mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('jacobian-test')\n"
        "@server.tool(name='math.find')\n"
        "def find(request: dict) -> dict:\n"
        "    return {'kind': 'discovery', 'request': request}\n"
        "@server.tool(name='math.run')\n"
        "def run(operation_id: str, payload: dict) -> dict:\n"
        "    if operation_id != 'integer.compute.gcd':\n"
        "        raise ValueError('unsupported operation')\n"
        "    return {'output': {'gcd': str(math.gcd(int(payload['left']), int(payload['right'])))},\n"
        "            'credential_forwarded': 'OPENAI_API_KEY' in os.environ}\n"
        "server.run()\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-credential")

    status = jacobian.status(executable=binary)
    assert status["transport"] == "mcp-stdio"
    assert status["server"]["name"] == "jacobian-test"
    assert status["tools"] == ["math.find", "math.run"]
    found = jacobian.find_operations("exact gcd", executable=binary)
    assert found["result"]["request"] == {"op": "search", "query": "exact gcd", "limit": 5}
    result = jacobian.run_operation(
        "integer.compute.gcd", {"left": "84", "right": "30"}, executable=binary,
    )
    assert result["result"] == {"output": {"gcd": "6"}, "credential_forwarded": False}
    with pytest.raises(jacobian.JacobianMcpError) as caught:
        jacobian.run_operation("unknown.operation", {}, executable=binary)
    assert "unsupported operation" in str(caught.value.payload["content"])

    monkeypatch.setenv(jacobian.JACOBIAN_MCP_BIN_ENV, str(binary))
    payload = tmp_path / "payload.json"
    payload.write_text('{"left": "84", "right": "30"}', encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable, "-m", "argus.tools.jacobian", "run",
            "--operation", "integer.compute.gcd", "--payload-file", str(payload),
        ],
        check=True, capture_output=True, text=True, timeout=30,
    )
    output = json.loads(completed.stdout)
    assert output["result"] == {"output": {"gcd": "6"}, "credential_forwarded": False}
