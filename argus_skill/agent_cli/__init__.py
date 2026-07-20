"""Vendored low-level codex/claude/copilot/opencode CLI driver.

Only three modules survive from the historical upstream ArgusBot
``agent_cli`` package: :mod:`agent_cli_runner`, :mod:`runner_backend`
and :mod:`models`. Together they form the minimal CLI driver that
``argus_skill.adapters.agent_cli_backend`` uses to run one engineer turn.

The rest of the legacy standalone autoloop stack (orchestrator, telegram
/ feishu daemons, a second reviewer/planner, dashboards, …) has been
removed — the live ``argus-skill`` product supersedes it with
``argus_skill.life`` / ``argus_skill.engineer`` / ``argus_skill.planner``.

This package intentionally performs **no** eager submodule imports so that
``import argus_skill.agent_cli.agent_cli_runner`` stays cheap and free of
legacy side effects. See ``_VENDORED.md`` / ``LICENSE`` for provenance.
"""
from __future__ import annotations

__all__: list[str] = []
