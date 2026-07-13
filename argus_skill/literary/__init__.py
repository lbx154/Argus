"""Shared literary-platform contract library.

This package is the shared FOUNDATION consumed by the literary verticals
(fiction_writing, classical/modern poetry, prose, literary editor). It is
DELIBERATELY NOT a vertical: it ships no ``stages`` contract, exposes no
``STAGE_ORDER``, and is never registered in
``argus_skill.skills.vertical_select.VERTICALS``. It holds only cross-vertical
CONTRACTS and their validators:

* :mod:`.task_envelope` — the creative-authoring task contract (intake);
* :mod:`.review_contract` — the structured reviewer finding/verdict contract;
* :mod:`.artifact_manifest` — the creative-artifact version & lineage contract;
* :mod:`.source_registry` — the rights/provenance catalog of usable sources;
* :mod:`.provenance` — the per-mission source-usage ledger + rights cross-check;
* :mod:`.stage_protocol` — the minimal shared stage interface (extracted from the
  real verticals; each vertical keeps its own stage flow).

Vertical-PRIVATE semantics (fiction's story_state, poetry's prosody, prose's
paragraph movement, each vertical's reviewer rubric) never move here — only the
protocol/lifecycle shared by two or more verticals does.
"""
from __future__ import annotations
