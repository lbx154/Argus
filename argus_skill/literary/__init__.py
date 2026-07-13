"""Shared literary-platform contract library.

This package is the shared FOUNDATION consumed by the literary verticals
(fiction_writing, classical/modern poetry, prose, literary editor). It is
DELIBERATELY NOT a vertical: it ships no ``stages`` contract, exposes no
``STAGE_ORDER``, and is never registered in
``argus_skill.skills.vertical_select.VERTICALS``. It holds only cross-vertical
CONTRACTS and their validators:

* :mod:`.task_envelope` — the creative-authoring task contract (intake);
* (later) review contract, artifact manifest, source/provenance, stage protocol.

Vertical-PRIVATE semantics (fiction's story_state, poetry's prosody, prose's
paragraph movement, each vertical's reviewer rubric) never move here — only the
protocol/lifecycle shared by two or more verticals does.
"""
from __future__ import annotations
