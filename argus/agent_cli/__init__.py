"""Low-level codex/claude/copilot/cursor/opencode/pi/grok/dsh CLI driver.

Layer: providers

The stable public surface is :mod:`agent_cli_runner`, :mod:`runner_backend`,
and :mod:`models`. Private modules split command construction, process
control, event parsing, prompt delivery, ACP routing, and recovery behind
that surface.

This package intentionally performs **no** eager submodule imports so that
``import argus.agent_cli.agent_cli_runner`` stays cheap. The driver
originated in ArgusBot and is maintained in-tree; the MIT ``LICENSE`` in this
directory (ArgusBot contributors) covers it.
"""
from __future__ import annotations

__all__: list[str] = []
