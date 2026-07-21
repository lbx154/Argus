"""Physics research gates package.

Each gate is a deterministic *artifact* verifier (it never performs web search or
full-text reading — those are the agent's stage actions). A gate emits a
machine-readable failure list and repair context via
``argus_skill.skills.research_gates`` and is driven by the applicable capabilities
from ``argus_skill.skills.capability_registry``.

Phase 1 ships the Literature Positioning gate. Theory / Numerical / Novelty /
Paper-Type gates are added in later phases behind the same interface.
"""
