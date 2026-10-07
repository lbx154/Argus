from types import ModuleType, SimpleNamespace

import pytest

from argus.roles.prompts.manager import (
    build_fast_vertical_decision_prompt,
    build_vertical_decision_prompt,
)
from argus.skills import vertical_select
from argus.verticals import _registry, store


@pytest.mark.parametrize("raw", [None, "hardware/chip", ["hardware"], ["Hardware", "chip"], ["hardware", "other"], ["hardware", "chip", "sta", "extra"]])
def test_invalid_routing_metadata_is_rejected(raw):
    with pytest.raises(ValueError, match="routing_path"):
        _registry.validate_routing_path("chip", raw)


def test_paths_preserve_flat_identity_without_knowledge_inheritance(monkeypatch):
    module = ModuleType("chip.stages")
    module.VERTICAL_PURPOSE = "chip design"
    module.VERTICAL_ROUTING_PATH = ("hardware", "chip")
    plugin = _registry._plugin("chip", module)
    assert plugin.skill_parents == ()
    monkeypatch.setattr(_registry, "vertical_plugins", lambda: {"chip": plugin})
    assert vertical_select.available_vertical_routing_paths() == {"chip": ("hardware", "chip")}
    assert "chip" in vertical_select.available_verticals()


@pytest.mark.parametrize("build", [build_fast_vertical_decision_prompt, build_vertical_decision_prompt])
def test_both_routing_paths_group_specialists_and_preserve_legacy_candidates(build):
    prompt = build(
        "inspect timing",
        verticals_with_purpose={"chip": "integration", "chip_sta": "timing constraints", "software": "software"},
        vertical_routing_paths={"chip": ("hardware", "chip"), "chip_sta": ("hardware", "chip", "sta")},
    )
    assert prompt.count("### hardware / chip") == 1
    assert "`chip_sta` [specialty: sta]: timing constraints" in prompt
    assert "### Other capabilities\n  - `software`: software" in prompt
    assert "Category headings are not selectable" in prompt
    assert "automatic cross-vertical" in prompt


def test_compact_menu_retains_scopes_without_repeating_profile_prose(monkeypatch):
    from argus.verticals import _base

    contract = SimpleNamespace(
        workflow_profiles={"verify": SimpleNamespace(purpose="Long purpose", stages=("test",))},
        workflow_stage_requirements={"test": ()},
    )
    monkeypatch.setattr(_registry, "vertical_plugins", lambda: {})
    monkeypatch.setattr(_base, "load_vertical_contract", lambda name: contract)
    full = vertical_select.available_vertical_purposes()
    compact = vertical_select.available_vertical_purposes(compact=True)
    for name in compact:
        assert "Long purpose" in full[name] and "Long purpose" not in compact[name]
        assert "verify (test)" in compact[name]
        assert "test (+none)" in compact[name]


def test_package_store_row_keeps_routing_path_without_a_catalog(monkeypatch, tmp_path):
    plugin = _registry.VerticalPlugin("chip", "design", ModuleType("chip"), routing_path=("hardware", "chip"))
    monkeypatch.setattr(_registry, "vertical_plugins", lambda: {"chip": plugin})
    rows = store.rows(tmp_path, catalog=None)
    assert next(row for row in rows if row["name"] == "chip")["routing_path"] == ["hardware", "chip"]
