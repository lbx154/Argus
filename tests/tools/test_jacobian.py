from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.tools import jacobian


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
    assert str(binary.resolve()) in note
    assert "argus_skill.tools.jacobian find" in note
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


def test_invalid_operation_id_is_rejected_before_sidecar_start(tmp_path: Path) -> None:
    with pytest.raises(jacobian.JacobianAdapterError, match="invalid"):
        jacobian.run_operation("../../shell", {}, executable=_binary(tmp_path))
