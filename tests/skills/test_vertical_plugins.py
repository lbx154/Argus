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


def test_valid_plugin_is_selectable_loadable_and_seeds_skills(tmp_path, monkeypatch) -> None:
    plugin = module(tmp_path)
    install(monkeypatch, [Entry("external_lab", plugin)])

    assert "external_lab" in vertical_select.available_verticals()
    assert vertical_select.available_vertical_purposes()["external_lab"] == "Plugin work"
    assert load_vertical("external_lab") is plugin
    assert dict(iter_vertical_skill_texts("external_lab"))["engineer/plugin.md"].startswith("---")


def test_invalid_plugins_are_not_advertised(tmp_path, monkeypatch) -> None:
    install(monkeypatch, [
        Entry("bad/name", module(tmp_path / "a")),
        Entry("old_api", module(tmp_path / "b", version=2)),
        Entry("no_purpose", module(tmp_path / "c", purpose="")),
        Entry("broken", None),
    ])

    available = vertical_select.available_verticals()
    assert all(name not in available for name in ("bad/name", "old_api", "no_purpose", "broken"))


def test_builtin_name_cannot_be_replaced(tmp_path, monkeypatch) -> None:
    from argus.skills.builtins import vertical_skill_parents

    before = dict(iter_vertical_skill_texts("research"))
    impostor = module(tmp_path)
    impostor.VERTICAL_SKILL_PARENTS = ("software",)
    install(monkeypatch, [Entry("research", impostor)])

    assert vertical_select.available_verticals().count("research") == 1
    assert load_vertical("research") is not impostor
    assert load_vertical("research").__name__.endswith("verticals.research.stages")
    assert _registry.vertical_plugin("research") is None
    assert vertical_skill_parents("research") == ()
    assert dict(iter_vertical_skill_texts("research")) == before


# --- VERTICAL_SKILL_PARENTS: a plugin declares whose skills seed before its own ---


def _with_parents(mod: ModuleType, *parents: object) -> ModuleType:
    mod.VERTICAL_SKILL_PARENTS = parents if len(parents) != 1 or isinstance(parents[0], str) else parents[0]
    return mod


def test_plugin_inherits_a_builtin_parents_skills_first(tmp_path, monkeypatch) -> None:
    from argus.skills.builtins import vertical_skill_parents

    plugin = _with_parents(module(tmp_path), "kernel_engineering")
    install(monkeypatch, [Entry("bench", plugin)])

    assert _registry.vertical_plugin("bench").skill_parents == ("kernel_engineering",)
    assert vertical_skill_parents("bench") == ("kernel_engineering",)
    names = [name for name, _ in iter_vertical_skill_texts("bench")]
    kernel = [name for name, _ in iter_vertical_skill_texts("kernel_engineering")]
    assert kernel and names[: len(kernel)] == kernel
    assert names[len(kernel):] == ["engineer/plugin.md"]


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


def test_missing_skill_parents_means_none(tmp_path, monkeypatch) -> None:
    install(monkeypatch, [Entry("plain", module(tmp_path))])

    assert _registry.vertical_plugin("plain").skill_parents == ()


# --- memoisation: one scan per process, refresh really rescans ---


def test_entry_points_are_scanned_once_until_refreshed(tmp_path, monkeypatch) -> None:
    scans: list[int] = []
    entries = [Entry("first_lab", module(tmp_path / "a"))]

    def fake_entry_points(group):
        scans.append(group)
        return list(entries)

    monkeypatch.setattr(_registry, "entry_points", fake_entry_points)
    _registry.refresh_vertical_plugins()

    # One scan reads the current group and the pre-rename group, once each.
    one_scan = [_registry.ENTRY_POINT_GROUP, _registry.LEGACY_ENTRY_POINT_GROUP]
    assert "first_lab" in vertical_select.available_verticals()
    vertical_select.available_vertical_purposes()
    _registry.vertical_plugin("first_lab")
    assert scans == one_scan

    entries.append(Entry("second_lab", module(tmp_path / "b")))
    assert "second_lab" not in vertical_select.available_verticals()  # memoised
    _registry.refresh_vertical_plugins()
    assert "second_lab" in vertical_select.available_verticals()
    assert scans == one_scan * 2


def test_pre_rename_entry_point_group_is_read_and_loses_to_the_new_group(tmp_path, monkeypatch, caplog) -> None:
    """``argus-verticals`` releases registered under ``argus_skill.verticals`` keep working.

    A name present in both groups comes from the new group; a name only in the
    old group is registered and warned about once, by name, so the maintainer
    of the distribution knows what to change.
    """
    import logging

    current = module(tmp_path / "current", purpose="Registered under the current group")
    legacy_only = module(tmp_path / "legacy", purpose="Registered under the old group only")
    legacy_duplicate = module(tmp_path / "duplicate", purpose="Old-group copy of a current plugin")
    groups = {
        _registry.ENTRY_POINT_GROUP: [Entry("shared_lab", current)],
        _registry.LEGACY_ENTRY_POINT_GROUP: [
            Entry("shared_lab", legacy_duplicate), Entry("old_lab", legacy_only),
        ],
    }
    monkeypatch.setattr(_registry, "entry_points", lambda group: list(groups[group]))
    _registry.refresh_vertical_plugins()

    with caplog.at_level(logging.WARNING, logger="argus.verticals._registry"):
        plugins = _registry.vertical_plugins()

    assert plugins["shared_lab"].module is current
    assert plugins["old_lab"].module is legacy_only
    legacy_warnings = [r.getMessage() for r in caplog.records if "pre-rename group" in r.getMessage()]
    assert len(legacy_warnings) == 1
    assert "old_lab" in legacy_warnings[0] and "shared_lab" not in legacy_warnings[0]
    assert _registry.LEGACY_ENTRY_POINT_GROUP in legacy_warnings[0]
    assert not any("duplicate" in r.getMessage() for r in caplog.records)


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


def test_manager_treats_an_installed_plugin_as_a_vertical_not_a_data_domain(
    tmp_path, monkeypatch
) -> None:
    from argus.manager import Manager
    from argus.manager.domain_author import VerticalDecision
    from argus.verticals._data_domain import write_data_domain

    install(monkeypatch, [Entry("external_lab", module(tmp_path / "plugin"))])
    project = tmp_path / "project"
    # A same-named project data domain exists too: with the old built-in-only
    # check the plugin name was "not a built-in", its status was read off this
    # file and the domain's stages would have been planned.
    write_data_domain(
        project, "external_lab", stages=["draft", "ship"],
        checklist_stage_order=["draft", "ship"], created_by="manager",
    )
    decision = VerticalDecision(
        choice="existing", vertical="external_lab", execution_task="do the plugin's work",
    )

    division = Manager(project_root=project).commit_vertical_decision("do the work", decision)

    assert division.vertical == "external_lab"
    assert division.learned_vertical_status == ""  # the plugin wins; no domain status is read
    assert division.stages == ["work", "deliver"]  # the plugin's stages, not the domain's
    assert Manager._kind_for("external_lab") == "custom"


# --- registry hardening: third-party declarations fail in any shape ---


def test_a_contract_that_raises_a_type_error_is_skipped_not_raised(tmp_path, monkeypatch) -> None:
    broken = module(tmp_path)
    broken.CHECKLIST_STAGE_ORDER = 5  # escapes as TypeError from inside the contract
    install(monkeypatch, [Entry("broken_order", broken), Entry("fine", module(tmp_path / "b"))])

    available = vertical_select.available_verticals()

    assert "broken_order" not in available and "fine" in available


@pytest.mark.parametrize("skills", [42, object(), ["a"]], ids=["int", "object", "list"])
def test_an_invalid_vertical_skills_declaration_is_rejected_at_registration(tmp_path, monkeypatch, skills) -> None:
    plugin = module(tmp_path)
    plugin.VERTICAL_SKILLS = skills
    install(monkeypatch, [Entry("odd_skills", plugin)])

    assert "odd_skills" not in vertical_select.available_verticals()


def test_a_traversable_vertical_skills_is_accepted(tmp_path, monkeypatch) -> None:
    from importlib import resources

    plugin = module(tmp_path)
    plugin.VERTICAL_SKILLS = resources.files("argus.verticals.software") / "skills"
    install(monkeypatch, [Entry("traversable", plugin)])

    names = dict(iter_vertical_skill_texts("traversable"))
    assert "engineer/software-change-implementation.md" in names


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
    assert scans == [_registry.ENTRY_POINT_GROUP, _registry.LEGACY_ENTRY_POINT_GROUP]
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
    install(monkeypatch, [])
    yield store
    _registry.refresh_vertical_plugins()
    store._purge_modules(["argus_verticals"])


def test_store_verticals_are_discovered_with_origin_store(store_release) -> None:
    store = store_release
    store.install("store_child", wait=True)

    plugins = _registry.vertical_plugins()
    assert plugins["store_lab"].origin == "store" and plugins["store_child"].origin == "store"
    assert plugins["store_child"].skill_parents == ("store_lab",)
    assert plugins["store_child"].skills_root == store.package_root() / "store_child" / "skills"
    assert load_vertical("store_child") is plugins["store_child"].module
    assert "store_child" in vertical_select.available_verticals()
    assert [n for n, _ in iter_vertical_skill_texts("store_child")] == ["engineer/store_lab.md", "engineer/store_child.md"]
    import sys

    assert sys.modules["argus_verticals"].__path__ == [str(store.package_root())]


def test_disabled_store_entries_are_hidden_and_the_registry_mtime_triggers_a_rescan(store_release) -> None:
    import json

    store = store_release
    store.install("store_lab", wait=True)
    assert "store_lab" in vertical_select.available_verticals()
    # Edit registry.json behind the store's back: no refresh call, only the file changes.
    path = store.registry_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["verticals"]["store_lab"]["enabled"] = False
    path.write_text(json.dumps(data), encoding="utf-8")
    assert "store_lab" not in vertical_select.available_verticals()
    assert _registry.vertical_plugin("store_lab") is None
    data["verticals"]["store_lab"]["enabled"] = True
    path.write_text(json.dumps(data), encoding="utf-8")
    assert "store_lab" in vertical_select.available_verticals()


def test_store_scan_is_memoised_until_the_registry_changes(store_release, monkeypatch) -> None:
    store = store_release
    store.install("store_lab", wait=True)
    reads: list[int] = []
    original = store.enabled_entries

    def counting(root=None):
        reads.append(1)
        return original(root)

    monkeypatch.setattr(store, "enabled_entries", counting)
    _registry.refresh_vertical_plugins()
    vertical_select.available_verticals()
    vertical_select.available_vertical_purposes()
    _registry.vertical_plugin("store_lab")
    assert len(reads) == 1
    store.disable("store_lab")
    vertical_select.available_verticals()
    assert len(reads) == 2


def test_a_pip_installed_copy_wins_over_the_store_and_is_reported_as_a_package(store_release, tmp_path, monkeypatch) -> None:
    import sys

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


def test_store_discovery_works_in_the_frozen_desktop(store_release, monkeypatch) -> None:
    """No dist-info, no entry points: the frozen bundle still finds store directories."""
    store = store_release
    store.install("store_lab", wait=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(_registry, "entry_points", lambda group: [])
    _registry.refresh_vertical_plugins()
    store._purge_modules(["argus_verticals"])

    assert "store_lab" in vertical_select.available_verticals()
    plugin = _registry.vertical_plugin("store_lab")
    assert plugin.origin == "store"
    assert Path(plugin.module.__file__).resolve().is_relative_to(store.package_root().resolve())


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
