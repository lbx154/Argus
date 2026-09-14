"""Delivery surfaces of the argus-skill product: the CLI and what it launches.

Layer: delivery

Belongs here: the argparse CLI (``apps/cli``), the TUI launcher
(``tui_launcher``), the updater (``update*``, ``package_update``), and
``--watch``. Also present today, pending phase 5 (see docs/LAYOUT.md): the
mission runtime (``_runtime*.py``, ``_self_reply.py``) shared by the daemon,
the teammate runner and the Manager front door, plus the operator inbox and
life actions (``_inbox.py``, ``_life_actions.py``, phase 4 -> ``life/``).

Does not belong here (and where it goes): anything a lower layer has to
import. ``daemon`` and ``life`` reach into ``apps._runtime`` /
``apps._inbox`` / ``apps._life_actions`` today; those edges are pinned in
tests/test_architecture_invariants.py and shrink only when the runtime moves
to ``mission_runner/`` and the inbox to ``life/``. Adding a new one means a
process-layer package now fails to import whenever a CLI module does.
"""
