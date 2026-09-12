"""Ephemeral Copilot MCP configuration for one host-bound advisor capability."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping

TOOL_NAME = "argus_advisor-consult_advisor"


@contextmanager
def advisor_mcp_args(environment: Mapping[str, str]) -> Iterator[list[str]]:
    # The bearer is a capability, so it belongs in a private temporary file,
    # never inline argv, provider prompts, or permanent Copilot configuration.
    with tempfile.TemporaryDirectory(prefix="argus-advisor-mcp-") as directory:
        path = Path(directory) / "mcp.json"
        config = {"mcpServers": {"argus_advisor": {
            "type": "local", "command": sys.executable,
            "args": ["-m", "argus_skill.tools.advisor", "mcp"],
            "env": {**environment, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
            "tools": ["consult_advisor"],
        }}}
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, allow_nan=False)
        yield ["--additional-mcp-config", "@" + str(path)]


__all__ = ["TOOL_NAME", "advisor_mcp_args"]
