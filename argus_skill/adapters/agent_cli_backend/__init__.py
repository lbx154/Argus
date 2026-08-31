"""Real LLM backend: thin adapter over the bundled ``AgentCliRunner``.

argus-skill's loop is deliberately backend-agnostic — it talks to a
``RunnerBackend`` (Protocol) defined in ``argus_skill.core.ports``. The
deterministic ``MemoryBackend`` is fine for tests, but for *real* runs we
need to drive the actual codex / claude / copilot / cursor / opencode / pi / grok / dsh CLI.

``argus_skill.agent_cli.agent_cli_runner.AgentCliRunner`` is a
battle-tested subprocess wrapper that handles JSON event streams, idle
watchdogs, claude/copilot dialects, and cross-platform stdin quirks. This
package *wraps* it rather than re-implementing that logic, translating
argus-skill's ``RunnerOptions``/``RunnerResult`` to and from the bundled
runner's own shapes. Field names are mostly 1:1, so only the slim subset
argus-skill needs gets carried across the boundary.

Token usage is best-effort — codex's JSON event stream emits
``token_count.input_tokens`` / ``output_tokens`` in some events; we sum
them across the run when present. When unavailable we leave them at 0
(the loop never branches on token counts).

Split into small internal modules by responsibility so no single file
mixes concerns:

  * ``_runtime`` — loads the bundled ``argus_skill.agent_cli`` runtime.
  * ``_options`` — Codex CLI arg/model-selection parsing and normalization.
  * ``_io_log``  — per-call JSONL event logging and raw stream batching.
  * ``_result``  — stop-kind classification and result/usage normalization.
  * ``_exec``    — provider-execution orchestration (one call's
    cost-reservation/quota/spawn/finalize flow).
  * ``_core``    — the public ``AgentCliBackend`` facade class.

Only the names re-exported below are meant to be imported from outside
this package; everything else is a private implementation detail.
"""
from __future__ import annotations

from ._core import AgentCliBackend, build_agent_cli_backend_from_env
from ._io_log import _needed_for_live_progress
from ._options import (
    _strip_legacy_codex_profile_args,
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
    "_strip_legacy_codex_profile_args",
    "resolve_pricing_model",
    "resolve_codex_execution_model",
    "looks_like_auth_failure",
    "_raw_backend_stop_kind",
    "_needed_for_live_progress",
]
