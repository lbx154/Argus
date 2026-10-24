from __future__ import annotations

import ast
from pathlib import Path


def test_application_code_has_no_direct_backend_run_exec_bypass() -> None:
    package = Path(__file__).parents[2] / "argus"
    allowed = {
        package / "adapters" / "agent_cli_backend" / "_exec.py",
        package / "adapters" / "agent_cli_backend" / "_exec_spawn.py",
        package / "core" / "run_gateway.py",
    }
    violations = []
    for path in package.rglob("*.py"):
        if path in allowed or "agent_cli" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run_exec"
            ):
                violations.append(f"{path.relative_to(package)}:{node.lineno}")
    assert violations == []
