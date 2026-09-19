"""Cold CLI session identity negotiation (ACP owns its own session protocol)."""
from __future__ import annotations

import re
import subprocess
import uuid
from dataclasses import replace

from ._process_control import background_subprocess_kwargs


class CopilotSessionCompatibilityError(RuntimeError):
    """Known refusal before any provider process or metered work is started."""


class CopilotSessionArgumentsError(ValueError):
    """Conflicting identity selectors refused before provider dispatch."""


def supports_session_id(agent_bin: str, *, env: dict[str, str] | None = None) -> bool:
    # Probe the selected loader, not the outer npm package version. No model
    # request or permission flags; failure is explicit compatibility refusal.
    try:
        result = subprocess.run([agent_bin, "--help"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                timeout=10, check=False, env=env,
                                **background_subprocess_kwargs())
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(re.search(
        r"(?m)^\s*--session-id(?:\s|=)", result.stdout))


def prepare_session(runner, options, resume_thread_id):
    callback = getattr(options, "_bind_provider_session", None)
    if callback is None:
        # Low-level embedders without an accounting context retain their API.
        return options, resume_thread_id
    args = [*runner.default_extra_args, *(options.extra_args or [])]
    if any(arg.split("=", 1)[0] in {"--session-id", "--resume", "--continue"}
           for arg in args):
        raise CopilotSessionArgumentsError("Copilot identity flags must not be supplied in extra_args")
    if resume_thread_id:
        identity = resume_thread_id
    else:
        executable = runner._resolve_executable(runner.agent_bin)
        if not supports_session_id(executable, env=runner._child_env(options, executable=executable)):
            raise CopilotSessionCompatibilityError("Copilot compatibility: --session-id support required for durable accounting; no call dispatched")
        identity = str(uuid.uuid4())
    # The caller rechecks interruption after the potentially slow probe, then
    # commits the binding. Negotiation alone must not record a dispatch.
    return replace(options, _provider_session_id=identity), identity
