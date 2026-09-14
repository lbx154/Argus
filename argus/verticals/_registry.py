"""Trusted out-of-tree verticals registered through Python entry points.

Two sources feed one registry:

* **managed plugins** installed by ``core.plugin_manager`` (the workbench
  catalog). Their activation state lives in ``registry.json`` and changes
  while the process runs (install, enable, disable, uninstall), so they are
  read afresh on every call; the plugin manager caches the loaded modules.
* **entry-point plugins** -- distributions that register
  ``argus.verticals`` entry points; the ``argus-verticals`` community
  package registers seventeen. The pre-rename group ``argus_skill.verticals``
  is read as well for one release (a name present in both groups is taken
  from the new one). A distribution cannot appear or vanish inside
  a running interpreter without a ``pip`` action, so the scan (every
  dist-info via ``importlib.metadata``, then one ``entry.load()`` and contract
  check per plugin) runs once per process and is memoised. The Manager menu
  and skill seeding call ``vertical_plugins()`` repeatedly; without the memo
  each call rescanned. ``refresh_vertical_plugins()`` forgets the scan; call
  it after installing a distribution into the running interpreter (tests do).

A plugin module may expose ``VERTICAL_SKILL_PARENTS``: the verticals whose
skill trees are seeded before its own (``kernelbench`` inherits
``kernel_engineering``'s kernel playbooks). It is validated like the rest of
the contract -- an invalid declaration means the plugin is not advertised.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from types import ModuleType
from typing import Any

log = logging.getLogger(__name__)
ENTRY_POINT_GROUP = "argus.verticals"
#: Group name before the 2026-09-14 package rename. ``argus-verticals``
#: releases published under it keep working for one release.
LEGACY_ENTRY_POINT_GROUP = "argus_skill.verticals"
VERTICAL_API_VERSION = 1
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_ENTRY_POINT_CACHE: dict[str, VerticalPlugin] | None = None
_CACHE_LOCK = threading.RLock()
_SCAN = threading.local()  # ``partial``: the dict a scan on this thread is filling


@dataclass(frozen=True)
class VerticalPlugin:
    name: str
    purpose: str
    module: ModuleType
    skills_root: Any = None
    #: Verticals whose skill trees are seeded before this one's own, in
    #: declaration order (``VERTICAL_SKILL_PARENTS`` on the plugin module).
    skill_parents: tuple[str, ...] = ()


def _skills_root(module: ModuleType) -> Any:
    """Validate ``VERTICAL_SKILLS``: absent, a path, or a Traversable-like object."""
    value = getattr(module, "VERTICAL_SKILLS", None)
    if value is None:
        return None
    if isinstance(value, (str, os.PathLike)):
        return Path(value).expanduser()
    if callable(getattr(value, "is_dir", None)) and callable(getattr(value, "iterdir", None)):
        return value
    raise ValueError(
        f"VERTICAL_SKILLS must be a path or a Traversable, got {type(value).__name__}"
    )


def _builtin_names() -> frozenset[str]:
    from . import builtin_verticals  # lazy: the package init imports the inventory

    return frozenset(builtin_verticals())


def _skill_parents(name: str, module: ModuleType) -> tuple[str, ...]:
    """Validate ``VERTICAL_SKILL_PARENTS``: absent, or a tuple/list of vertical names."""
    raw = getattr(module, "VERTICAL_SKILL_PARENTS", ())
    if raw is None:
        return ()
    if not isinstance(raw, (tuple, list)):
        raise ValueError(
            "VERTICAL_SKILL_PARENTS must be a tuple of vertical names, "
            f"got {type(raw).__name__}"
        )
    parents: list[str] = []
    for item in raw:
        parent = item.strip().lower() if isinstance(item, str) else ""
        if not _NAME.fullmatch(parent):
            raise ValueError(f"VERTICAL_SKILL_PARENTS names an invalid vertical: {item!r}")
        if parent == name:
            raise ValueError("VERTICAL_SKILL_PARENTS names the vertical itself")
        if parent not in parents:
            parents.append(parent)
    return tuple(parents)


def _plugin(name: str, module: ModuleType) -> VerticalPlugin:
    """Read the advertised fields; raises ``ValueError`` on an invalid declaration."""
    purpose = str(getattr(module, "VERTICAL_PURPOSE", "") or "").strip()
    if not purpose:
        raise ValueError("VERTICAL_PURPOSE is missing or empty")
    return VerticalPlugin(
        name=name,
        purpose=purpose,
        module=module,
        skills_root=_skills_root(module),
        skill_parents=_skill_parents(name, module),
    )


def _managed_plugins() -> dict[str, VerticalPlugin]:
    """Enabled workbench plugins, read afresh; a broken one costs only itself."""
    from ..core import plugin_manager

    try:
        installed = plugin_manager.installed()
    except Exception:  # noqa: BLE001
        log.warning("managed vertical plugin discovery failed", exc_info=True)
        return {}
    builtin = _builtin_names()
    plugins: dict[str, VerticalPlugin] = {}
    for name, plugin in installed.items():
        if name in builtin:
            log.warning("ignoring managed vertical plugin %r: the name is a built-in vertical", name)
            continue
        try:
            plugins[name] = _plugin(name, plugin.vertical_module())
        except Exception:  # noqa: BLE001
            log.warning("managed vertical plugin %r failed to load", name, exc_info=True)
    return plugins


def _entry_point_plugins(plugins: dict[str, VerticalPlugin]) -> dict[str, VerticalPlugin]:
    """Scan the entry-point group once into ``plugins``; invalid registrations are not advertised."""
    try:
        discovered = list(entry_points(group=ENTRY_POINT_GROUP))
        legacy = list(entry_points(group=LEGACY_ENTRY_POINT_GROUP))
    except Exception:  # noqa: BLE001
        log.warning("vertical entry-point discovery failed", exc_info=True)
        return plugins
    current_names = {str(entry.name or "").strip().lower() for entry in discovered}
    legacy_only = [
        entry for entry in legacy
        if str(entry.name or "").strip().lower() not in current_names
    ]
    if legacy_only:
        log.warning(
            "vertical entry points %s are registered under the pre-rename group %r; "
            "register them under %r (the old group is read for one release)",
            sorted({str(entry.name or "").strip().lower() for entry in legacy_only}),
            LEGACY_ENTRY_POINT_GROUP, ENTRY_POINT_GROUP,
        )
        discovered.extend(legacy_only)
    from ..core import plugin_manager
    from ..core.vertical_contract import vertical_contract

    try:
        managed_names = set(plugin_manager.catalog())
    except Exception:  # noqa: BLE001
        log.warning("plugin catalog is unreadable; entry points are not filtered against it",
                    exc_info=True)
        managed_names = set()
    builtin = _builtin_names()
    for entry in sorted(discovered, key=lambda row: (row.name, row.value)):
        name = str(entry.name or "").strip().lower()
        if name in managed_names:
            continue  # catalog ids are activated by the plugin manager, never by pip
        if name in builtin:
            # A built-in is served from this package alone: an entry point of
            # the same name could otherwise redirect its skill seeding.
            log.warning("ignoring vertical entry point %r: the name is a built-in vertical", name)
            continue
        if not _NAME.fullmatch(name) or name in plugins:
            log.warning("ignoring invalid or duplicate vertical entry point %r", name)
            continue
        try:
            module = entry.load()
        except Exception:  # noqa: BLE001
            log.warning("vertical plugin %r failed to load", name, exc_info=True)
            continue
        try:
            version = int(getattr(module, "ARGUS_VERTICAL_API_VERSION", 0))
            if version != VERTICAL_API_VERSION:
                raise ValueError(f"ARGUS_VERTICAL_API_VERSION {version} != {VERTICAL_API_VERSION}")
            plugin = _plugin(name, module)
            vertical_contract(name, module)
        except Exception as exc:  # noqa: BLE001 - third-party declarations fail in any shape
            log.warning("vertical plugin %r has an incompatible contract: %s", name, exc)
            continue
        plugins[name] = plugin
    return plugins


def _cached_entry_point_plugins() -> dict[str, VerticalPlugin]:
    global _ENTRY_POINT_CACHE
    with _CACHE_LOCK:
        if _ENTRY_POINT_CACHE is not None:
            return _ENTRY_POINT_CACHE
        partial = getattr(_SCAN, "partial", None)
        if partial is not None:
            # Re-entered from a plugin module that imports the registry while
            # it is being loaded: hand back what the running scan has so far
            # instead of loading the half-initialised module a second time.
            return partial
        _SCAN.partial = {}
        try:
            _ENTRY_POINT_CACHE = _entry_point_plugins(_SCAN.partial)
        finally:
            _SCAN.partial = None
        return _ENTRY_POINT_CACHE


def vertical_plugins() -> dict[str, VerticalPlugin]:
    """Valid plugins by name: managed plugins (fresh) first, then memoised entry points."""
    plugins = _managed_plugins()
    for name, plugin in _cached_entry_point_plugins().items():
        plugins.setdefault(name, plugin)
    return plugins


def vertical_plugin(name: object) -> VerticalPlugin | None:
    if not isinstance(name, str):
        return None
    return vertical_plugins().get(name.strip().lower())


def refresh_vertical_plugins() -> None:
    """Forget the entry-point scan so the next lookup re-reads ``importlib.metadata``.

    Managed plugins need no refresh: their activation is read on every call.
    """
    global _ENTRY_POINT_CACHE
    with _CACHE_LOCK:
        _ENTRY_POINT_CACHE = None


__all__ = [
    "ENTRY_POINT_GROUP",
    "LEGACY_ENTRY_POINT_GROUP",
    "VERTICAL_API_VERSION",
    "VerticalPlugin",
    "refresh_vertical_plugins",
    "vertical_plugin",
    "vertical_plugins",
]
