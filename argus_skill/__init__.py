"""``argus_skill``: the pre-rename import name of the :mod:`argus` package.

The package was renamed ``argus_skill`` -> ``argus`` on 2026-09-14. This shim
keeps the old spelling importable for one release so that seeded Skill copies
(``python -m argus_skill.tools.subagent ...``), systemd units, container images
and third-party code written against ``argus_skill`` keep working while they
are migrated.

It does not copy anything. A :data:`sys.meta_path` finder at index 0 answers
every ``argus_skill[.x]`` lookup with the already-imported ``argus[.x]`` module
object, so both names are the *same* module: ``monkeypatch.setattr`` through
either spelling patches the one runtime, ``isinstance`` checks agree and there
is never a second copy of a module-level singleton. ``sys.modules["argus_skill"]``
is ``argus`` itself.

Persisted names are not part of this rename and keep their spelling:
``ARGUS_SKILL_*`` environment variables and knobs, ``~/.argus-skill``, the
``argus-skill-webapi`` wire id, and the other on-disk markers listed in the
README's "Renamed: argus-skill -> argus" section.
"""
from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import sys
import warnings

LEGACY_NAME = "argus_skill"
CANONICAL_NAME = "argus"


def canonical_module_name(name: str) -> str | None:
    """Map ``argus_skill[.x]`` to ``argus[.x]``; ``None`` for any other name."""
    if name == LEGACY_NAME:
        return CANONICAL_NAME
    if name.startswith(LEGACY_NAME + "."):
        return CANONICAL_NAME + name[len(LEGACY_NAME):]
    return None


class _AliasLoader:
    """Loader whose ``create_module`` hands back the canonical module object.

    ``importlib`` stores the returned object under the legacy name in
    ``sys.modules`` and then calls :meth:`exec_module`, which only undoes the
    one attribute ``module_from_spec`` overwrites unconditionally
    (``__spec__``), so the canonical module keeps its own identity.
    """

    def __init__(self, target: str, target_spec: importlib.machinery.ModuleSpec) -> None:
        self._target = target
        self._target_spec = target_spec
        self._canonical_spec: importlib.machinery.ModuleSpec | None = None

    def create_module(self, spec):
        module = importlib.import_module(self._target)
        self._canonical_spec = getattr(module, "__spec__", None)
        return module

    def exec_module(self, module) -> None:
        if self._canonical_spec is not None:
            module.__spec__ = self._canonical_spec

    # ``runpy`` (``python -m argus_skill.tools.subagent``) needs a code object.
    def get_code(self, fullname: str):
        loader = self._target_spec.loader
        get_code = getattr(loader, "get_code", None)
        return get_code(self._target) if callable(get_code) else None

    def get_source(self, fullname: str):
        loader = self._target_spec.loader
        get_source = getattr(loader, "get_source", None)
        return get_source(self._target) if callable(get_source) else None

    def is_package(self, fullname: str) -> bool:
        return self._target_spec.submodule_search_locations is not None


class ArgusSkillAliasFinder:
    """``sys.meta_path`` entry aliasing ``argus_skill[.x]`` to ``argus[.x]``.

    It must stay at index 0: behind ``PathFinder`` the parent's ``__path__``
    (which is ``argus.__path__``) would load a second copy of every submodule
    under the legacy name.
    """

    @staticmethod
    def find_spec(fullname: str, path=None, target=None):
        canonical = canonical_module_name(fullname)
        if canonical is None:
            return None
        try:
            target_spec = importlib.util.find_spec(canonical)
        except (ImportError, AttributeError, ValueError):
            return None
        if target_spec is None:
            return None
        spec = importlib.machinery.ModuleSpec(
            fullname,
            _AliasLoader(canonical, target_spec),
            origin=target_spec.origin,
            is_package=target_spec.submodule_search_locations is not None,
        )
        if target_spec.submodule_search_locations is not None:
            spec.submodule_search_locations = list(target_spec.submodule_search_locations)
        return spec


def _install() -> None:
    if not any(isinstance(finder, ArgusSkillAliasFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, ArgusSkillAliasFinder())
    canonical = importlib.import_module(CANONICAL_NAME)
    # Replace this shim module in ``sys.modules`` so ``import argus_skill`` and
    # ``import argus`` return the same object (importlib re-reads sys.modules
    # after executing a package __init__).
    sys.modules[LEGACY_NAME] = canonical


warnings.warn(
    "the 'argus_skill' package was renamed to 'argus'; the 'argus_skill' spelling is "
    "an alias kept for one release. Import 'argus' and run 'python -m argus ...' instead.",
    DeprecationWarning,
    stacklevel=2,
)
_install()
