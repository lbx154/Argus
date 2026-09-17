"""A task route may name its own model; everything else keeps the engineer's.

The figure route exists so an operator can point figure work at an
image-capable model without switching the whole Engineer. Unset, ``auto`` or
an unknown route change nothing.
"""
from __future__ import annotations

import pytest

from argus.core import knob_store, knobs


def test_the_figure_knob_is_registered_and_cockpit_editable() -> None:
    knob = {k.name: k for k in knobs.KNOBS}["ARGUS_SKILL_FIGURE_MODEL"]
    assert knob.default == "auto" and knob.group == "models" and knob.cockpit
    assert "ARGUS_SKILL_FIGURE_MODEL" in knobs.cockpit_editable_names()


@pytest.fixture
def persisted(monkeypatch: pytest.MonkeyPatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(knob_store, "read_persisted_knobs", lambda: dict(store))
    return store


def test_environment_wins_then_the_persisted_cockpit_value(persisted: dict[str, str]) -> None:
    assert knobs.resolve_task_route_model("figure", fallback="engineer-model", env={"ARGUS_SKILL_FIGURE_MODEL": "sees-pictures"}) == "sees-pictures"
    persisted["ARGUS_SKILL_FIGURE_MODEL"] = "cockpit-choice"
    assert knobs.resolve_task_route_model("figure", fallback="engineer-model", env={}) == "cockpit-choice"
    assert knobs.resolve_task_route_model("figure", fallback="engineer-model", env={"ARGUS_SKILL_FIGURE_MODEL": "env-choice"}) == "env-choice"


def test_auto_unset_and_unknown_routes_keep_the_engineer_model(persisted: dict[str, str]) -> None:
    assert knobs.resolve_task_route_model("figure", fallback="engineer-model", env={}) == "engineer-model"
    assert knobs.resolve_task_route_model("figure", fallback="engineer-model", env={"ARGUS_SKILL_FIGURE_MODEL": "auto"}) == "engineer-model"
    persisted["ARGUS_SKILL_FIGURE_MODEL"] = "inherit"
    assert knobs.resolve_task_route_model("figure", fallback="engineer-model", env={}) == "engineer-model"
    assert knobs.resolve_task_route_model("poetry", fallback="engineer-model", env={"ARGUS_SKILL_POETRY_MODEL": "x"}) == "engineer-model"
    assert knobs.resolve_task_route_model("", fallback="engineer-model", env={}) == "engineer-model"
