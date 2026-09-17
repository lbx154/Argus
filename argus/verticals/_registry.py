"""Trusted out-of-tree verticals: managed plugins, the Vertical Store, entry points.

Three sources feed one registry:

* **managed plugins** installed by ``core.plugin_manager`` (the workbench
  catalog). Their activation state lives in ``registry.json`` and changes
  while the process runs (install, enable, disable, uninstall), so they are
  read afresh on every call; the plugin manager caches the loaded modules.
* **store verticals** installed by :mod:`argus.verticals.store` one directory
  at a time under ``<store root>/argus_verticals/<name>/``. The store's
  ``registry.json`` names each enabled entry's module; the scan is memoised
  and re-run whenever that file changes on disk. The store makes
  ``argus_verticals`` importable itself (appended to a pip-installed copy's
  ``__path__`` -- the pip copy wins for a duplicate name -- or as a synthetic
  namespace package), so nothing here reads dist-info and the frozen desktop
  discovers store verticals exactly like a source checkout.
* **entry-point plugins** -- distributions that register
  ``argus.verticals`` entry points; the ``argus-verticals`` community
  package registers seventeen. The pre-rename group ``argus_skill.verticals``
  is read as well for one release (a name present in both groups is taken
  from the new one). A distribution cannot appear or vanish inside
  a running interpreter without a ``pip`` action, so the scan (every
  dist-info via ``importlib.metadata``, then one ``entry.load()`` and contract
  check per plugin) runs once per process and is memoised. The Manager menu
  and skill seeding call ``vertical_plugins()`` repeatedly; without the memo
  each call rescanned. ``refresh_vertical_plugins()`` forgets both scans; call
  it after installing a distribution into the running interpreter (tests do).

Precedence for one name: managed plugin, then entry point, then store.

A plugin module may expose ``VERTICAL_SKILL_PARENTS``: the verticals whose
skill trees are seeded before its own (``kernelbench`` inherits
``kernel_engineering``'s kernel playbooks). It is validated like the rest of
the contract -- an invalid declaration means the plugin is not advertised.
"""
from __future__ import annotations

import dataclasses
import importlib
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
ORIGINS: tuple[str, ...] = ("managed", "store", "entry_point")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_ENTRY_POINT_CACHE: dict[str, VerticalPlugin] | None = None
_STORE_CACHE: tuple[Any, dict[str, VerticalPlugin]] | None = None
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
    #: Which source advertised the plugin: ``"managed"`` (workbench plugin),
    #: ``"store"`` (Vertical Store directory) or ``"entry_point"`` (pip).
    origin: str = "entry_point"


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


def _plugin(name: str, module: ModuleType, *, origin: str = "entry_point") -> VerticalPlugin:
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
        origin=origin,
    )


def _validated_plugin(name: str, module: ModuleType, *, origin: str) -> VerticalPlugin:
    """The full third-party check: API version, advertised fields, then the contract."""
    from ..core.vertical_contract import vertical_contract

    version = int(getattr(module, "ARGUS_VERTICAL_API_VERSION", 0))
    if version != VERTICAL_API_VERSION:
        raise ValueError(f"ARGUS_VERTICAL_API_VERSION {version} != {VERTICAL_API_VERSION}")
    plugin = _plugin(name, module, origin=origin)
    vertical_contract(name, module)
    location = getattr(module, "__file__", None)
    if plugin.skills_root is None and origin == "store" and isinstance(location, str):
        # A store directory carries its skills next to stages.py; a vertical
        # that declares nothing still seeds them from there.
        default = Path(location).resolve().parent / "skills"
        if default.is_dir():
            plugin = dataclasses.replace(plugin, skills_root=default)
    return plugin


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
            plugins[name] = _plugin(name, plugin.vertical_module(), origin="managed")
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
            plugin = _validated_plugin(name, module, origin="entry_point")
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


def _store_plugins(taken: frozenset[str]) -> dict[str, VerticalPlugin]:
    """Enabled Vertical Store entries; rescanned when ``registry.json`` changes.

    ``taken`` are the names an earlier source already provides (a pip copy of
    the same vertical wins, as does a managed plugin); the store skips them.
    """
    global _STORE_CACHE
    from . import store

    try:
        signature = store.registry_signature()
    except Exception:  # noqa: BLE001 - an unresolvable home means no store
        log.warning("vertical store root could not be resolved", exc_info=True)
        return {}
    if signature is None:
        return {}
    key = (signature, taken)
    with _CACHE_LOCK:
        if _STORE_CACHE is not None and _STORE_CACHE[0] == key:
            return _STORE_CACHE[1]
    plugins: dict[str, VerticalPlugin] = {}

    def remember(result: dict[str, VerticalPlugin]) -> dict[str, VerticalPlugin]:
        # A failure is memoised too: the warning is logged once per registry
        # state, not once per Manager menu or skill-seeding call.
        global _STORE_CACHE
        with _CACHE_LOCK:
            _STORE_CACHE = (key, result)
        return result

    try:
        entries = store.enabled_entries()
    except Exception:  # noqa: BLE001
        log.warning("vertical store registry is unreadable", exc_info=True)
        return remember({})
    if entries:
        try:
            store.ensure_importable(store.package_root())
        except Exception:  # noqa: BLE001
            log.warning("the vertical store package could not be made importable", exc_info=True)
            return remember({})
        builtin = _builtin_names()
        for name, entry in sorted(entries.items()):
            if name in taken:
                continue
            if name in builtin:
                log.warning("ignoring store vertical %r: the name is a built-in vertical", name)
                continue
            module_name = str(entry.get("module") or f"{store.PACKAGE}.{name}.stages")
            if not store.valid_module_name(module_name):
                log.warning("ignoring store vertical %r: invalid module %r", name, module_name)
                continue
            try:
                module = importlib.import_module(module_name)
            except Exception:  # noqa: BLE001
                log.warning("store vertical %r failed to import", name, exc_info=True)
                continue
            try:
                plugin = _validated_plugin(name, module, origin="store")
            except Exception as exc:  # noqa: BLE001
                log.warning("store vertical %r has an incompatible contract: %s", name, exc)
                continue
            plugins[name] = plugin
    return remember(plugins)


def vertical_plugins() -> dict[str, VerticalPlugin]:
    """Valid plugins by name: managed (fresh), then memoised entry points, then the store."""
    plugins = _managed_plugins()
    for name, plugin in _cached_entry_point_plugins().items():
        plugins.setdefault(name, plugin)
    for name, plugin in _store_plugins(frozenset(plugins)).items():
        plugins.setdefault(name, plugin)
    return plugins


def vertical_plugin(name: object) -> VerticalPlugin | None:
    if not isinstance(name, str):
        return None
    return vertical_plugins().get(name.strip().lower())


def refresh_vertical_plugins() -> None:
    """Forget the entry-point and store scans so the next lookup re-reads them.

    Managed plugins need no refresh: their activation is read on every call.
    """
    global _ENTRY_POINT_CACHE, _STORE_CACHE
    with _CACHE_LOCK:
        _ENTRY_POINT_CACHE = None
        _STORE_CACHE = None


__all__ = [
    "ENTRY_POINT_GROUP",
    "LEGACY_ENTRY_POINT_GROUP",
    "ORIGINS",
    "VERTICAL_API_VERSION",
    "VerticalPlugin",
    "refresh_vertical_plugins",
    "vertical_plugin",
    "vertical_plugins",
]
