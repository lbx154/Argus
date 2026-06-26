"""End-of-mission skill tidy-up entry point (the Manager as "janitor").

When a mission completes, the project layer of the skill library
(``~/.argus-skill/projects/<fp>/skills/``) holds the playbooks the reviewer
distilled while working it. This module is the single entry point that has the
Manager review those distilled skills and route each one to where it belongs in
the shared library:

* a CROSS-DOMAIN capability → the global layer (``promote_to_global``);
* a capability specific to the mission's vertical → that vertical's runtime
  layer (``promote_to_vertical``);
* anything too project-specific or unplaceable → left in the project layer.

It constructs the three-layer :class:`LayeredSkillStore` bound to the project's
active vertical (seeding the runtime vertical layer from the version-controlled
``verticals/<v>/skills/`` source first) and delegates the per-skill judgement to
:meth:`Manager.tidy_project_skills`. Fully fail-soft: any setup error returns
zero counts without raising, so mission completion is never blocked by tidy-up.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_ZERO = {"promoted_global": 0, "promoted_vertical": 0, "stayed": 0, "errors": 0}


def tidy_after_mission(
    project_root: Path | str,
    runner: Any,
    *,
    on_event: Any = None,
) -> dict[str, int]:
    """Route a finished project's distilled skills to global / vertical / stay.

    Returns counts ``{"promoted_global", "promoted_vertical", "stayed",
    "errors"}``. Never raises — a setup failure logs and returns zeros.
    """
    try:
        from ..core import paths as cp
        from ..core.project import project_fingerprint
        from ..skills.builtins import seed_vertical_layer
        from ..skills.layered import LayeredSkillStore
        from ..skills.vertical_select import resolve_vertical
        from ._core import Manager

        root = Path(project_root)
        fingerprint = project_fingerprint(root).fingerprint
        vertical = resolve_vertical(root)

        # The runtime vertical middle layer — present only for a non-research
        # vertical, and seeded from its version-controlled source first so the
        # layer exists and is populated before the Manager files skills into it.
        vertical_dir = None
        if vertical and vertical != "research":
            try:
                seed_vertical_layer(vertical)
                vertical_dir = cp.skills_vertical_root(vertical)
            except Exception:  # noqa: BLE001 — degrade to no vertical layer
                log.warning(
                    "tidy_after_mission: failed to seed vertical layer for %r",
                    vertical,
                    exc_info=True,
                )
                vertical_dir = None

        layered = LayeredSkillStore(
            project_dir=cp.project_skills_root(fingerprint),
            vertical_dir=vertical_dir,
            global_dir=cp.skills_global_root(),
            runner=runner,
        )
        manager = Manager(root, runner)
        return manager.tidy_project_skills(
            layered, active_vertical=vertical, on_event=on_event
        )
    except Exception:  # noqa: BLE001 — tidy-up must never block mission completion
        log.warning("tidy_after_mission: setup failed; skipping tidy", exc_info=True)
        return dict(_ZERO)


__all__ = ["tidy_after_mission"]
