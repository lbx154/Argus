"""Meta-level control layer — saturation detection → enforced regime-jump.

Background (operator diagnosis, this workstream — the
``fix/planner-livelock-echo-chamber`` branch): the search loop surfaces a live
NO-VERDICT "search altitude" block (``verticals/nanochat/stages.py:
search_altitude_context``) that already shows the planner the live floor,
distance-to-target, and ``Consecutive attempts since the FLOOR last improved``.
On the live nanochat-b200 mission that counter reached 63 — yet the agent kept
proposing adjacent local levers on the SAME backbone. Pure visibility was
ignored for 63 rounds.

This package adds the missing escalation, designed to stay on the right side of
the operator's #1 rule (*research judgment belongs to the agent; the harness is
a domain-agnostic dumb pipe*) by splitting the concern three ways:

  * DETECT  (harness, dumb counters): ``saturation.analyze`` — ``frozen_rounds``
    and a diversity descriptor, both pure facts re-surfaced from the agent's
    OWN recorded attempts. No prose-grep, no code edit-distance.
  * JUDGE   (planner LLM): when the harness convenes a JUMP turn, the planner
    decides whether the basin is really saturated and which regime to jump to,
    emitting a structured ``meta_decision`` — ``meta_prompter.parse_meta_decision``.
  * ENFORCE (harness): ``flow_controller`` rebuilds the planner context (drops
    the local trajectory), and ``ledger`` persists the agent's OWN declared
    forbidden directions in a never-cleared record that is re-injected every
    round so the next candidate cannot silently re-anchor on the dead regime.

The mechanism is modelled on STAR-PólyaMath's Meta-Strategist (counter-based
stall detection → binding, agent-authored ``Forbidden_Directions``) and on the
QD / FunSearch / AlphaEvolve diversity line (a coverage descriptor + "parent +
top-k diverse inspirations" framing), with the EMNLP-2025 negative result in
mind: dumping all prior attempts into context is *worse* than ignoring them, so
the jump context is a BOUNDED ledger + top-k inspirations, never a full-history
dump.

Every entry point is fail-soft: any error degrades to "no meta intervention"
(``mode="exploit"``, empty block) so the planner loop never breaks on this.
"""
from __future__ import annotations

from .config import MetaConfig, load_meta_config
from .flow_controller import FlowDecision, decide, record_decision
from .saturation import SaturationSignal, analyze

__all__ = [
    "MetaConfig",
    "load_meta_config",
    "SaturationSignal",
    "analyze",
    "FlowDecision",
    "decide",
    "record_decision",
]
