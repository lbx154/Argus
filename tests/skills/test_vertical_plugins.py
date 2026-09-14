from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from argus_skill.skills import vertical_select
from argus_skill.skills.builtins import iter_vertical_skill_texts
from argus_skill.skills.stage_machine import ChecklistItem
from argus_skill.verticals import _registry
from argus_skill.verticals._base import load_vertical


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
    from argus_skill.skills.builtins import vertical_skill_parents

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
    from argus_skill.skills.builtins import vertical_skill_parents

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
        scans.append(1)
        return list(entries)

    monkeypatch.setattr(_registry, "entry_points", fake_entry_points)
    _registry.refresh_vertical_plugins()

    assert "first_lab" in vertical_select.available_verticals()
    vertical_select.available_vertical_purposes()
    _registry.vertical_plugin("first_lab")
    assert scans == [1]

    entries.append(Entry("second_lab", module(tmp_path / "b")))
    assert "second_lab" not in vertical_select.available_verticals()  # memoised
    _registry.refresh_vertical_plugins()
    assert "second_lab" in vertical_select.available_verticals()
    assert scans == [1, 1]


def test_a_broken_managed_plugin_does_not_hide_entry_point_plugins(tmp_path, monkeypatch) -> None:
    from argus_skill.core import plugin_manager

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
    from argus_skill.manager import Manager
    from argus_skill.manager.domain_author import VerticalDecision
    from argus_skill.verticals._data_domain import write_data_domain

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
    plugin.VERTICAL_SKILLS = resources.files("argus_skill.verticals.software") / "skills"
    install(monkeypatch, [Entry("traversable", plugin)])

    names = dict(iter_vertical_skill_texts("traversable"))
    assert "engineer/software-change-implementation.md" in names


def test_a_parent_that_resolves_nowhere_is_warned_about_and_skipped(tmp_path, monkeypatch, caplog) -> None:
    import logging

    plugin = _with_parents(module(tmp_path), "no_such_vertical")
    install(monkeypatch, [Entry("orphan_child", plugin)])

    with caplog.at_level(logging.WARNING, logger="argus_skill.skills.builtins"):
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
        scans.append(1)
        return list(entries)

    monkeypatch.setattr(_registry, "entry_points", fake_entry_points)
    _registry.refresh_vertical_plugins()

    with caplog.at_level(logging.WARNING, logger="argus_skill.verticals._registry"):
        available = vertical_select.available_verticals()

    assert "reentrant" in available
    assert scans == [1]
    assert not any("incompatible contract" in r.getMessage() for r in caplog.records)


# --- a plugin may not take a built-in's name, module or skill tree ---


def test_builtin_name_cannot_hijack_skill_seeding(tmp_path, monkeypatch) -> None:
    from argus_skill.skills.builtins import vertical_skill_parents

    before = dict(iter_vertical_skill_texts("research"))
    impostor = _with_parents(module(tmp_path), "software")
    impostor.VERTICAL_SKILLS = tmp_path / "nonexistent"
    install(monkeypatch, [Entry("research", impostor)])

    assert _registry.vertical_plugin("research") is None
    assert vertical_skill_parents("research") == ()
    assert dict(iter_vertical_skill_texts("research")) == before
    assert len(before) > 4


def test_managed_plugin_named_like_a_builtin_is_ignored(tmp_path, monkeypatch) -> None:
    from argus_skill.core import plugin_manager

    impostor = module(tmp_path)

    class Managed:
        def vertical_module(self):
            return impostor

    monkeypatch.setattr(plugin_manager, "installed", lambda root=None: {"software": Managed()})
    install(monkeypatch, [])

    assert _registry.vertical_plugin("software") is None
    assert load_vertical("software").__name__.endswith("verticals.software.stages")
