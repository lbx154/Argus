"""Domain-overlay registry and optional-hook accessors.

A workflow vertical owns stage order, completion, and evidence lifecycle. A domain
overlay may add role context, checklist floors, and matchable Skills, but it never
replaces the workflow contract.
"""

from __future__ import annotations

import importlib
from types import ModuleType

from ..skills.stage_machine import ChecklistItem

BUILTIN_DOMAINS: tuple[str, ...] = ("chemistry",)

DOMAIN_PURPOSES: dict[str, str] = {
    "chemistry": (
        "chemistry research across molecular properties and activity, reactions "
        "and synthesis, cheminformatics, quantum chemistry, computational screening, "
        "closed-loop optimization, and authorized instrument-backed experiments"
    ),
}


class UnknownDomainError(ValueError):
    """Raised when a persisted or requested domain is not built in."""


def _normalize_domain(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def require_domain(value: object) -> str:
    """Return a built-in domain slug or raise."""
    domain = _normalize_domain(value)
    if domain not in BUILTIN_DOMAINS:
        raise UnknownDomainError(
            f"{value!r} is not a known domain (built-ins: {', '.join(BUILTIN_DOMAINS)})"
        )
    return domain


def load_domain(value: object) -> ModuleType:
    """Load one built-in domain overlay, failing loudly on a broken package."""
    domain = require_domain(value)
    return importlib.import_module(f"argus_skill.domains.{domain}.overlay")


def domain_role_banner(mod: ModuleType, role: str) -> str:
    """Return concise role context from a domain overlay."""
    fn = getattr(mod, "role_banner", None)
    if not callable(fn):
        return ""
    result = fn(role)
    return result if isinstance(result, str) else ""


def domain_checklist_items(
    mod: ModuleType,
) -> dict[str, tuple[ChecklistItem, ...]]:
    """Return immutable per-stage checklist additions."""
    raw = getattr(mod, "CHECKLIST_ITEMS", None)
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, tuple[ChecklistItem, ...]] = {}
    for stage, items in raw.items():
        stage_name = str(stage or "").strip().lower()
        if not stage_name or not isinstance(items, (list, tuple)):
            continue
        normalized[stage_name] = tuple(
            item for item in items if isinstance(item, ChecklistItem)
        )
    return normalized


__all__ = [
    "BUILTIN_DOMAINS",
    "DOMAIN_PURPOSES",
    "UnknownDomainError",
    "domain_checklist_items",
    "domain_role_banner",
    "load_domain",
    "require_domain",
]
