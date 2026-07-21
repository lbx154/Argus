"""Vendored low-level codex/claude/copilot/opencode CLI driver.

``AgentCliRunner`` and ``RunnerOptions`` are the two public names callers
import from this module (``argus_skill.adapters.agent_cli_backend`` wraps
``AgentCliRunner`` behind the ``RunnerBackend`` port). Their actual behavior —
argv construction, the sandbox gate, prompt delivery, event parsing, OpenCode
recovery, process-group termination, and the ``run_exec`` phases — lives in
the small internal mixin modules imported below; this file only owns
construction, the small ACP-scope instance state, and the composed class.

``import os`` / ``import subprocess`` stay here (even though this module no
longer calls them directly) because several tests patch
``agent_cli_runner.os`` / ``agent_cli_runner.subprocess`` by name; since
Python modules are singletons, patching the attribute on this shared module
object also affects the mixins in ``_run_exec.py`` / ``_opencode_recovery.py``
/ ``_process_control.py`` that call through it.
"""
from __future__ import annotations

import os  # noqa: F401 -- re-exported so tests can patch `agent_cli_runner.os`
import subprocess  # noqa: F401 -- re-exported so tests can patch `agent_cli_runner.subprocess`
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from ._acp_routing import AcpRoutingMixin

# Re-exported: ``tests/agent_cli/test_incomplete_turn_error.py`` and
# ``tests/test_run_exec_stream_callback.py`` import these two names directly
# from ``agent_cli_runner`` rather than from ``_env``.
from ._env import _incomplete_turn_error, _turn_wall_clock_seconds  # noqa: F401
from ._event_consumers import EventConsumerMixin
from ._opencode_recovery import OpenCodeRecoveryMixin
from ._process_control import ProcessControlMixin
from ._prompt_delivery import PromptDeliveryMixin
from ._run_exec import RunExecMixin
from ._sandbox_commands import CommandBuilderMixin
from .models import InactivitySnapshot
from .runner_backend import (
    BACKEND_COPILOT,  # noqa: F401 -- re-exported: tests import it from here
    DEFAULT_RUNNER_BACKEND,
    RunnerBackend,
    default_runner_bin,
    resolve_runner_bin,
)

EventCallback = Callable[[str, str], None]
InactivityDecision = Literal["continue", "restart"]
InactivityCallback = Callable[[InactivitySnapshot], InactivityDecision]
ExternalInterruptProvider = Callable[[], str | None]


@dataclass
class RunnerOptions:
    model: str | None = None
    reasoning_effort: str | None = None
    dangerous_yolo: bool = False
    full_auto: bool = False
    # Codex sandbox policy. When set (e.g. "workspace-write"), the codex command
    # is built with ``-s <mode> -C <working_dir> --add-dir <add_dirs>`` so writes
    # are confined to the workspace + add_dirs, and the child env is scrubbed of
    # push-capable VCS credentials with PYTHONSAFEPATH=1 — INSTEAD of
    # ``--dangerously-bypass-approvals-and-sandbox``. None = legacy behaviour
    # (dangerous_yolo / full_auto flags), so existing callers are unaffected.
    sandbox_mode: str | None = None
    isolate_workdir: bool = False
    skip_git_repo_check: bool = False
    # Enable codex's native live web_search tool (``-c web_search="live"``).
    live_search: bool = False
    extra_args: list[str] | None = None
    working_dir: str | None = None
    output_schema_path: str | None = None
    watchdog_soft_idle_seconds: int | None = None
    watchdog_hard_idle_seconds: int | None = None
    inactivity_callback: InactivityCallback | None = None
    external_interrupt_reason_provider: ExternalInterruptProvider | None = None
    add_dirs: list[str] | None = None
    plugin_dirs: list[str] | None = None
    file_specs: list[str] | None = None
    worktree_name: str | None = None
    # Fired with each NEW assistant message block the instant it lands on stdout
    # (see ``run_exec``). Opt-in — default ``None`` leaves every existing caller
    # (the whole daemon) byte-for-byte unchanged; only the Manager chat
    # front-door sets it, to stream the reply live.
    on_agent_message: Callable[[str], None] | None = None


class AgentCliRunner(
    AcpRoutingMixin,
    RunExecMixin,
    CommandBuilderMixin,
    PromptDeliveryMixin,
    EventConsumerMixin,
    OpenCodeRecoveryMixin,
    ProcessControlMixin,
):
    """Drives one codex/claude/copilot/opencode CLI turn.

    This class itself only owns construction and ACP-scope state; every other
    behavior comes from the mixins above (each documented in its own module):

    - ``_acp_routing.AcpRoutingMixin``: warm ``copilot --acp`` fast path.
    - ``_run_exec.RunExecMixin``: the public ``run_exec`` entry point, split
      into its start-gate / spawn / stream / finalize phases.
    - ``_sandbox_commands.CommandBuilderMixin``: per-backend argv construction
      and the codex sandbox policy gate.
    - ``_prompt_delivery.PromptDeliveryMixin``: stdin prompt delivery and the
      embedded output-schema contract for backends without ``--output-schema``.
    - ``_event_consumers.EventConsumerMixin``: per-backend JSON event parsing.
    - ``_opencode_recovery.OpenCodeRecoveryMixin``: truncated-stream recovery
      for OpenCode.
    - ``_process_control.ProcessControlMixin``: emit/executable-resolution/
      process-group termination helpers.
    """

    def __init__(
        self,
        agent_bin: str | None = None,
        *,
        backend: RunnerBackend = DEFAULT_RUNNER_BACKEND,
        event_callback: EventCallback | None = None,
        default_extra_args: list[str] | None = None,
        before_exec: Callable[[], None] | None = None,
    ) -> None:
        self.backend = backend
        requested_bin = agent_bin or default_runner_bin(backend)
        self.agent_bin = (
            resolve_runner_bin(backend, requested_bin)
            or str(Path(requested_bin).expanduser())
        )
        self.event_callback = event_callback
        self.default_extra_args = list(default_extra_args or [])
        self.before_exec = before_exec
        self._acp_scope = f"runner:{id(self):x}"
