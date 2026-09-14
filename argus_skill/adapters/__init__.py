"""Concrete ``RunnerBackend`` implementations behind the kernel's ports.

Layer: providers

Belongs here: ``agent_cli_backend`` (the real backend: a thin adapter over
``agent_cli.AgentCliRunner`` that drives the codex/claude/copilot/cursor/
opencode/pi/grok/dsh CLIs), ``memory_backend`` (the deterministic in-memory
backend for tests, smoke runs and CI) and ``stream_progress`` (agent CLI
stream-json lines -> ``engineer.progress`` events). Everything here
implements or feeds a ``core.ports`` protocol. At module level the package
imports only ``core`` and ``provider_integrations``; ``agent_cli`` is resolved
at call time through ``agent_cli_backend/_runtime.py`` so importing the
backend never pulls in the subprocess driver, and three call-time edges into
``trial`` and ``tools`` (``agent_cli_backend/_exec.py``, ``_exec_finalize.py``,
``_core.py``) are pinned in tests/test_architecture_invariants.py
(``FUNCTION_BODY_UPWARD_ALLOWLIST``) and shrink from there.

Does not belong here (and where it goes): backend discovery and process
control (``agent_cli``), provider quota/telemetry (``provider_integrations``),
and the round loop that consumes a backend (``engineer``, ``loop``). A
backend that imports a role or a vertical can no longer be swapped for
``MemoryBackend`` in tests, which is the whole point of the port.
"""
