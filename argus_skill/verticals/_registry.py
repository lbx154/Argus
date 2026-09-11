"""Trusted out-of-tree verticals registered through Python entry points."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from types import ModuleType
from typing import Any

log = logging.getLogger(__name__)
ENTRY_POINT_GROUP = "argus_skill.verticals"
VERTICAL_API_VERSION = 1
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


@dataclass(frozen=True)
class VerticalPlugin:
    name: str
    purpose: str
    module: ModuleType
    skills_root: Any = None


def _skills_root(module: ModuleType) -> Any:
    value = getattr(module, "VERTICAL_SKILLS", None)
    if value is None:
        return None
    if isinstance(value, (str, Path)):
        return Path(value).expanduser()
    return value


def vertical_plugins() -> dict[str, VerticalPlugin]:
    """Load valid plugins once. Invalid registrations are not advertised."""
    try:
        discovered = entry_points(group=ENTRY_POINT_GROUP)
    except Exception:  # noqa: BLE001
        log.warning("vertical entry-point discovery failed", exc_info=True)
        return {}
    from ..core import plugin_manager
    plugins: dict[str, VerticalPlugin] = {}
    for name, plugin in plugin_manager.installed().items():
        module = plugin.vertical_module()
        plugins[name] = VerticalPlugin(name, module.VERTICAL_PURPOSE, module, _skills_root(module))
    managed_names = set(plugin_manager.catalog())
    for entry in sorted(discovered, key=lambda row: (row.name, row.value)):
        name = str(entry.name or "").strip().lower()
        if name in managed_names:
            continue
        if not _NAME.fullmatch(name) or name in plugins:
            log.warning("ignoring invalid or duplicate vertical entry point %r", name)
            continue
        try:
            module = entry.load()
            version = int(getattr(module, "ARGUS_VERTICAL_API_VERSION", 0))
            purpose = str(getattr(module, "VERTICAL_PURPOSE", "") or "").strip()
        except Exception:  # noqa: BLE001
            log.warning("vertical plugin %r failed to load", name, exc_info=True)
            continue
        if version != VERTICAL_API_VERSION or not purpose:
            log.warning("vertical plugin %r has an incompatible contract", name)
            continue
        try:
            from ..core.vertical_contract import vertical_contract

            vertical_contract(name, module)
        except ValueError as exc:
            log.warning("vertical plugin %r has an incompatible contract: %s", name, exc)
            continue
        plugins[name] = VerticalPlugin(
            name=name,
            purpose=purpose,
            module=module,
            skills_root=_skills_root(module),
        )
    return plugins


def vertical_plugin(name: object) -> VerticalPlugin | None:
    if not isinstance(name, str):
        return None
    return vertical_plugins().get(name.strip().lower())


def refresh_vertical_plugins() -> None:
    pass  # Managed activation is read on every discovery; installed modules are cached.


__all__ = [
    "ENTRY_POINT_GROUP",
    "VERTICAL_API_VERSION",
    "VerticalPlugin",
    "refresh_vertical_plugins",
    "vertical_plugin",
    "vertical_plugins",
]
