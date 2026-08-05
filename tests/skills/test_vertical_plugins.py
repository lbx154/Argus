from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from argus_skill.skills import vertical_select
from argus_skill.skills.builtins import iter_vertical_skill_texts
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
