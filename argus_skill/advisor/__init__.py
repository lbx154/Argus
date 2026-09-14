"""Independent, evidence-bound advice requested by an Argus role.

Layer: providers

An outbound side-channel to an external advisor model (Copilot, MCP, Pi
extension) with receipts and evidence, requested by a role and never
adjudicating. It imports ``core``, ``agent_cli`` and ``adapters``; its
call-time imports of ``life`` and ``trial`` are pinned as upward edges in
tests/test_architecture_invariants.py."""

from .config import AdvisorConfig, AdvisorConfigError, load_advisor_config, save_advisor_config

__all__ = ["AdvisorConfig", "AdvisorConfigError", "load_advisor_config", "save_advisor_config"]
