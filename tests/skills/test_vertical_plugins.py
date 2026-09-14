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
    impostor = module(tmp_path)
    install(monkeypatch, [Entry("research", impostor)])

    assert vertical_select.available_verticals().count("research") == 1
    assert load_vertical("research") is not impostor
    assert load_vertical("research").__name__.endswith("verticals.research.stages")


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

    install(monkeypatch, [Entry("external_lab", module(tmp_path / "plugin"))])
    project = tmp_path / "project"
    decision = VerticalDecision(
        choice="existing", vertical="external_lab", execution_task="do the plugin's work",
    )

    division = Manager(project_root=project).commit_vertical_decision("do the work", decision)

    assert division.vertical == "external_lab"
    assert division.learned_vertical_status == ""  # a data domain would carry its status
    assert division.stages == ["work", "deliver"]
    assert Manager._kind_for("external_lab") == "custom"
    assert not (project / "research" / "DOMAINS").exists()
