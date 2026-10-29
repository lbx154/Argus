from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from argus.skills import vertical_select
from argus.skills.builtins import iter_vertical_skill_texts
from argus.skills.stage_machine import ChecklistItem
from argus.verticals import _registry
from argus.verticals._base import load_vertical


@pytest.fixture(autouse=True)
def clear_registry():
    _registry.refresh_vertical_plugins()
    yield
    _registry.refresh_vertical_plugins()


class Entry:
    def __init__(self, name: str, module: ModuleType | None) -> None:
        self.name = name
        self.value = f"plugin.{name}"
        self.module = module

    def load(self):
        if self.module is None:
            raise ImportError("broken plugin")
        return self.module


def module(tmp_path: Path, *, version: int = 1, purpose: str = "Plugin work") -> ModuleType:
    result = ModuleType("plugin.stages")
    result.ARGUS_VERTICAL_API_VERSION = version
    result.VERTICAL_PURPOSE = purpose
    result.CHECKLIST_STAGE_ORDER = ("work", "deliver")
    result.CHECKLIST_ITEMS = {
        "work": (ChecklistItem("work.output", "Work output exists", "work artifact"),),
        "deliver": (
            ChecklistItem("deliver.output", "Delivery is complete", "delivery artifact"),
        ),
    }
    result.completion_gate = "none"
    skills = tmp_path / "skills" / "engineer"
    skills.mkdir(parents=True)
    (skills / "plugin.md").write_text("---\nname: Plugin\ndescription: Plugin\n---\n", encoding="utf-8")
    result.VERTICAL_SKILLS = skills.parent
    return result


def install(monkeypatch, entries) -> None:
    monkeypatch.setattr(_registry, "entry_points", lambda group: list(entries))
    _registry.refresh_vertical_plugins()


def test_invalid_plugins_are_not_advertised(tmp_path, monkeypatch) -> None:
    install(monkeypatch, [
        Entry("bad/name", module(tmp_path / "a")),
        Entry("old_api", module(tmp_path / "b", version=2)),
        Entry("no_purpose", module(tmp_path / "c", purpose="")),
        Entry("broken", None),
    ])

    available = vertical_select.available_verticals()
    assert all(name not in available for name in ("bad/name", "old_api", "no_purpose", "broken"))


# --- VERTICAL_SKILL_PARENTS: a plugin declares whose skills seed before its own ---


def _with_parents(mod: ModuleType, *parents: object) -> ModuleType:
    mod.VERTICAL_SKILL_PARENTS = parents if len(parents) != 1 or isinstance(parents[0], str) else parents[0]
    return mod


def test_plugin_parent_may_itself_be_a_plugin(tmp_path, monkeypatch) -> None:
    parent = module(tmp_path / "parent", purpose="Generic optimize")
    (tmp_path / "parent" / "skills" / "engineer" / "parent.md").write_text(
        "---\nname: Parent\ndescription: Parent\n---\n", encoding="utf-8"
    )
    child = _with_parents(module(tmp_path / "child", purpose="Specific optimize"), "generic")
    install(monkeypatch, [Entry("generic", parent), Entry("specific", child)])

    names = [name for name, _ in iter_vertical_skill_texts("specific")]

    assert names == ["engineer/parent.md", "engineer/plugin.md"]
    assert dict(iter_vertical_skill_texts("specific"))["engineer/plugin.md"] == (
        (tmp_path / "child" / "skills" / "engineer" / "plugin.md").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    "parents",
    ["kernel_engineering", ("",), ("Bad Name",), (7,), ("self_ref",)],
    ids=["bare-string", "empty-name", "invalid-name", "non-string", "names-itself"],
)
def test_invalid_skill_parents_reject_the_plugin(tmp_path, monkeypatch, parents) -> None:
    plugin = module(tmp_path)
    plugin.VERTICAL_SKILL_PARENTS = parents
    install(monkeypatch, [Entry("self_ref", plugin)])

    assert "self_ref" not in vertical_select.available_verticals()
    assert _registry.vertical_plugin("self_ref") is None


# --- memoisation: one scan per process, refresh really rescans ---


def test_a_broken_managed_plugin_does_not_hide_entry_point_plugins(tmp_path, monkeypatch) -> None:
    from argus.core import plugin_manager

    class Broken:
        def vertical_module(self):
            raise RuntimeError("managed plugin exploded")

    monkeypatch.setattr(plugin_manager, "installed", lambda root=None: {"crystalpilot": Broken()})
    install(monkeypatch, [Entry("external_lab", module(tmp_path))])

    available = vertical_select.available_verticals()
    assert "external_lab" in available and "crystalpilot" not in available


# --- the Manager classifies an installed plugin vertical as a vertical ---


# --- registry hardening: third-party declarations fail in any shape ---


@pytest.mark.parametrize("skills", [42, object(), ["a"]], ids=["int", "object", "list"])
def test_an_invalid_vertical_skills_declaration_is_rejected_at_registration(tmp_path, monkeypatch, skills) -> None:
    plugin = module(tmp_path)
    plugin.VERTICAL_SKILLS = skills
    install(monkeypatch, [Entry("odd_skills", plugin)])

    assert "odd_skills" not in vertical_select.available_verticals()


def test_a_parent_that_resolves_nowhere_is_warned_about_and_skipped(tmp_path, monkeypatch, caplog) -> None:
    import logging

    plugin = _with_parents(module(tmp_path), "no_such_vertical")
    install(monkeypatch, [Entry("orphan_child", plugin)])

    with caplog.at_level(logging.WARNING, logger="argus.skills.builtins"):
        names = [name for name, _ in iter_vertical_skill_texts("orphan_child")]

    assert names == ["engineer/plugin.md"]
    assert any("no_such_vertical" in record.getMessage() for record in caplog.records)


def test_a_plugin_that_reads_the_registry_while_loading_does_not_trigger_a_second_scan(
    tmp_path, monkeypatch, caplog,
) -> None:
    import logging

    scans: list[int] = []
    plugin = module(tmp_path)

    class ReentrantEntry(Entry):
        def load(self):
            # A plugin module whose import touches the registry (for example
            # through ``available_verticals()``) re-enters the scan.
            vertical_select.available_verticals()
            return plugin

    entries = [ReentrantEntry("reentrant", plugin)]

    def fake_entry_points(group):
        scans.append(group)
        return list(entries)

    monkeypatch.setattr(_registry, "entry_points", fake_entry_points)
    _registry.refresh_vertical_plugins()

    with caplog.at_level(logging.WARNING, logger="argus.verticals._registry"):
        available = vertical_select.available_verticals()

    assert "reentrant" in available
    assert scans == [_registry.ENTRY_POINT_GROUP]
    assert not any("incompatible contract" in r.getMessage() for r in caplog.records)


# --- a plugin may not take a built-in's name, module or skill tree ---


def test_builtin_name_cannot_hijack_skill_seeding(tmp_path, monkeypatch) -> None:
    from argus.skills.builtins import vertical_skill_parents

    before = dict(iter_vertical_skill_texts("research"))
    impostor = _with_parents(module(tmp_path), "software")
    impostor.VERTICAL_SKILLS = tmp_path / "nonexistent"
    install(monkeypatch, [Entry("research", impostor)])

    assert _registry.vertical_plugin("research") is None
    assert vertical_skill_parents("research") == ()
    assert dict(iter_vertical_skill_texts("research")) == before
    assert len(before) > 4


def test_managed_plugin_named_like_a_builtin_is_ignored(tmp_path, monkeypatch) -> None:
    from argus.core import plugin_manager

    impostor = module(tmp_path)

    class Managed:
        def vertical_module(self):
            return impostor

    monkeypatch.setattr(plugin_manager, "installed", lambda root=None: {"software": Managed()})
    install(monkeypatch, [])

    assert _registry.vertical_plugin("software") is None
    assert load_vertical("software").__name__.endswith("verticals.software.stages")


# --- the Vertical Store: a third source, read from registry.json, no dist-info ---


@pytest.fixture
def store_release(tmp_path, monkeypatch):
    from argus.verticals import store
    from tests.verticals import fake_release as fake

    monkeypatch.delenv(store.HOST_ROOT_ENV, raising=False)
    monkeypatch.delenv("ARGUS_TRIAL_HARNESS", raising=False)
    catalog = fake.build_release(tmp_path / "dist", [
        fake.spec("store_lab", purpose_zh="商店垂直"),
        fake.spec("store_child", requires=("store_lab",), parents=("store_lab",)),
    ])
    monkeypatch.setenv(store.CATALOG_ENV, str(catalog))
    fake.hide_installed_community_package(monkeypatch)
    install(monkeypatch, [])
    yield store
    _registry.refresh_vertical_plugins()
    store._purge_modules(["argus_verticals"])


def test_a_pip_installed_copy_wins_over_the_store_and_is_reported_as_a_package(store_release, tmp_path, monkeypatch) -> None:

    store = store_release
    store.install("store_lab", wait=True)
    pip_copy = module(tmp_path / "pip", purpose="Registered by pip")
    install(monkeypatch, [Entry("store_lab", pip_copy)])

    plugins = _registry.vertical_plugins()
    assert plugins["store_lab"].module is pip_copy and plugins["store_lab"].origin == "entry_point"
    assert load_vertical("store_lab") is pip_copy
    row = next(r for r in store.rows() if r["name"] == "store_lab")
    assert row["kind"] == "package" and row["installed_version"] == "0.1.0" and row["actions"] == ["uninstall"]
    # The store dir is appended to a real pip package's search path when one exists.
    fake_pkg = ModuleType("argus_verticals")
    fake_pkg.__path__ = [str(tmp_path / "site" / "argus_verticals")]
    monkeypatch.setitem(sys.modules, "argus_verticals", fake_pkg)
    store.ensure_importable(store.package_root())
    assert fake_pkg.__path__ == [str(tmp_path / "site" / "argus_verticals"), str(store.package_root())]


def test_a_broken_store_entry_costs_only_itself(store_release) -> None:
    store = store_release
    store.install("store_child", wait=True)
    (store.package_root() / "store_lab" / "stages.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    store._purge_modules(["argus_verticals"])
    _registry.refresh_vertical_plugins()

    available = vertical_select.available_verticals()
    assert "store_child" in available and "store_lab" not in available


def test_a_store_entry_named_like_a_builtin_is_ignored(store_release) -> None:
    import json

    store = store_release
    store.install("store_lab", wait=True)
    path = store.registry_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["verticals"]["software"] = dict(data["verticals"]["store_lab"], module="argus_verticals.store_lab.stages")
    path.write_text(json.dumps(data), encoding="utf-8")

    assert _registry.vertical_plugin("software") is None
    assert load_vertical("software").__name__.endswith("verticals.software.stages")
