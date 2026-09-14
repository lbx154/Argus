"""Domain-specific adapters on top of the domain-agnostic Argus runtime.

Layer: domain

Seven verticals are built in (one directory each, ``<name>/stages.py``
implementing ``VerticalContract``); everything else is discovered at runtime
through the ``argus_skill.verticals`` entry-point group (see ``_registry``),
which is how the ``argus-verticals`` community package contributes its
seventeen. The framework-owned bridge modules (``_base``, ``_registry``,
``_data_domain``, ``research_bridge``, ``metric_evidence``,
``optimization_base``, ``path_evidence``) are the public seam an external
vertical imports; nothing outside this package names a vertical directly.

The canonical built-in inventory and Manager-facing purpose descriptions live
in :mod:`argus_skill.skills.vertical_select`. Keep package documentation free of
a second handwritten inventory so registration and documentation cannot drift.
"""
from __future__ import annotations


def builtin_verticals() -> tuple[str, ...]:
    """Return the canonical built-in inventory without creating an import cycle."""
    from ..skills.vertical_select import VERTICALS

    return VERTICALS


__all__ = ["builtin_verticals"]
