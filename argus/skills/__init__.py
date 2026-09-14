"""The Skill library: agent-readable Markdown Skills in layered stores.

Layer: capabilities

A Skill is a markdown document with two frontmatter fields (``name``,
``description``) in the layered project/vertical/global store (``store.py``,
``layered.py``); the runtime hands agents library paths and never parses the
body. Also present today, pending phases 2-3 (see docs/LAYOUT.md): the stage
machine and vertical selection (``stage_machine``, ``vertical_select``,
``checklist_store``), the RL research gates (``run_contract``,
``rl_training_*``, ``anti_mediocrity``, ``evidence_chain``), and the SkillLoop
mixins (``loop_*``).

Does not belong here (and where it goes): pipeline state and vertical
selection -> ``pipeline/`` (phase 3); the research gates ->
``verticals/research`` (phase 2); the loop mixins -> ``mission_runner/``
(phase 5). Until they move, importing this package's stage machine from a
kernel or provider module would drag vertical selection into every backend
import, so those layers must keep importing ``core`` only.
"""
