"""Real LLM backend: thin adapter over the bundled ``AgentCliRunner``.

Argus's loop is deliberately backend-agnostic — it talks to a
``RunnerBackend`` (Protocol) defined in ``argus.core.ports``. The
deterministic ``MemoryBackend`` is fine for tests, but for *real* runs we
need to drive the actual codex / claude / copilot / cursor / opencode / pi / grok / dsh CLI.

``argus.agent_cli.agent_cli_runner.AgentCliRunner`` is a
battle-tested subprocess wrapper that handles JSON event streams, idle
watchdogs, claude/copilot dialects, and cross-platform stdin quirks. This
package *wraps* it rather than re-implementing that logic, translating
Argus's ``RunnerOptions``/``RunnerResult`` to and from the bundled
runner's own shapes. Field names are mostly 1:1, so only the slim subset
argus needs gets carried across the boundary.

Token usage: a codex call is the requests codex recorded in its session
files, its subagents' included (``provider_integrations.codex_usage``);
other CLIs report usage in their event streams. A count no source
reported stays missing (the loop never branches on token counts).

Split into small internal modules by responsibility so no single file
mixes concerns:

  * ``_options`` — Codex CLI arg/model-selection parsing and normalization.
  * ``_io_log``  — per-call JSONL event logging and raw stream batching.
  * ``_result``  — stop-kind classification and result/usage normalization.
  * ``_exec``    — provider-execution orchestration (one call's
    admission/spawn/finalize flow).
  * ``_core``    — the public ``AgentCliBackend`` facade class.

Only the names re-exported below are meant to be imported from outside
this package; everything else is a private implementation detail.
"""
from __future__ import annotations

from ._core import AgentCliBackend, build_agent_cli_backend_from_env
from ._io_log import _needed_for_live_progress
from ._options import (
    resolve_codex_execution_model,
    resolve_pricing_model,
)
from ._result import (
    _raw_backend_stop_kind,
    looks_like_auth_failure,
)

__all__ = [
    "AgentCliBackend",
    "build_agent_cli_backend_from_env",
    "resolve_pricing_model",
    "resolve_codex_execution_model",
    "looks_like_auth_failure",
    "_raw_backend_stop_kind",
    "_needed_for_live_progress",
]
