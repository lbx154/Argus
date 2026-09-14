"""Kernel of the Argus runtime: the vocabulary every other package speaks.

Layer: kernel

Belongs here: models, ports, event catalog + schemas, contracts
(vertical/research/project), paths, OS primitives (file_lock, process_stop,
daemon_lock, windows_job, ...). Also present today and marked as extraction
candidates (see docs/LAYOUT.md): accounting (usage, cost_control, metrics,
pricing, token_usage, provider_quota, cost_events), backend config/readiness
(knobs, knob_store, role_config, backend_readiness, config_snapshot), the
cockpit read model (mission_view, log_view), operator stores
(operator_context, operator_decision, operator_messages, operator_presence),
paper/venue policy (venue_review, manuscript_*).

Does not belong here (and where it goes): anything that needs a role, the
supervisor, a vertical, a Skill library or a delivery surface. Every layer
above imports ``core`` at module level, so an upward import from here is an
import cycle waiting for the next reorder. Today's module-level upward edges
out of ``core`` (into ``agent_cli``, ``life`` and the package root) are pinned
by name in tests/test_architecture_invariants.py
(``MODULE_LEVEL_UPWARD_ALLOWLIST``) and are removed by phase 1 of
docs/LAYOUT.md; ``core/usage.py`` -> ``provider_integrations.copilot_usage``
is the one expected to survive until the accounting tier leaves.
"""
