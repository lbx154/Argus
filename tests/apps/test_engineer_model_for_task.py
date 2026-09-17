"""One mission may run on the model its vertical's route names; a hiccup keeps the default."""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from argus.apps import _runtime_execute as mod
from argus.verticals import _base


def _vertical(route: str | object):
    def model_route_for_task(text: str) -> str:
        if isinstance(route, Exception):
            raise route
        return str(route)

    return types.SimpleNamespace(model_route_for_task=model_route_for_task)


def test_a_routed_task_runs_on_the_route_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_base, "load_vertical", lambda name, project_root=None: _vertical("figure"))
    monkeypatch.setenv("ARGUS_SKILL_FIGURE_MODEL", "sees-pictures")
    assert mod._engineer_model_for_task("engineer-model", "research", "draw the method figure", tmp_path) == "sees-pictures"


def test_without_a_route_or_a_knob_the_engineer_model_stays(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ARGUS_SKILL_FIGURE_MODEL", raising=False)
    monkeypatch.setattr("argus.core.knob_store.read_persisted_knobs", lambda: {})
    monkeypatch.setattr(_base, "load_vertical", lambda name, project_root=None: _vertical("figure"))
    assert mod._engineer_model_for_task("engineer-model", "research", "draw the method figure", tmp_path) == "engineer-model"
    monkeypatch.setattr(_base, "load_vertical", lambda name, project_root=None: _vertical(""))
    monkeypatch.setenv("ARGUS_SKILL_FIGURE_MODEL", "sees-pictures")
    assert mod._engineer_model_for_task("engineer-model", "research", "write the results section", tmp_path) == "engineer-model"
    assert mod._engineer_model_for_task("engineer-model", "", "draw the method figure", tmp_path) == "engineer-model"
    assert mod._engineer_model_for_task("engineer-model", "research", "", tmp_path) == "engineer-model"


def test_a_failing_hook_or_vertical_keeps_the_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_FIGURE_MODEL", "sees-pictures")
    monkeypatch.setattr(_base, "load_vertical", lambda name, project_root=None: _vertical(RuntimeError("boom")))
    assert mod._engineer_model_for_task("engineer-model", "research", "draw the method figure", tmp_path) == "engineer-model"

    def missing(name, project_root=None):
        raise LookupError(name)

    monkeypatch.setattr(_base, "load_vertical", missing)
    assert mod._engineer_model_for_task("engineer-model", "nowhere", "draw the method figure", tmp_path) == "engineer-model"
