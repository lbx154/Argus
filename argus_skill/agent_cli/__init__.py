"""Low-level codex/claude/copilot/cursor/opencode/pi/grok/dsh CLI driver.

The stable public surface is :mod:`agent_cli_runner`, :mod:`runner_backend`,
and :mod:`models`. Private modules split command construction, process
control, event parsing, prompt delivery, ACP routing, and recovery behind
that surface.

This package intentionally performs **no** eager submodule imports so that
``import argus_skill.agent_cli.agent_cli_runner`` stays cheap. See
``_VENDORED.md`` / ``LICENSE`` for provenance.
"""
from __future__ import annotations

__all__: list[str] = []
