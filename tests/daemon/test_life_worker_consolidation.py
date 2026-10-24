"""The drain loop's consolidation hook: quiet when there is nothing to do, never raising."""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import argus.life.consolidation as consolidation
import argus.skills.vertical_select as vertical_select
from argus.daemon._life_worker_run import _maybe_consolidate


def _refuse(**_kwargs):
    raise AssertionError("consolidate_knowledge must not be called")


def test_skips_when_learning_is_off(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(consolidation, "consolidate_knowledge", _refuse)
    monkeypatch.setattr(vertical_select, "resolve_skill_scope", lambda ws: "research")
    monkeypatch.setenv("ARGUS_SKILL_LEARNING", "0")
    sup = SimpleNamespace(_budget_global_root=lambda: tmp_path, _emit=lambda event: True)
    _maybe_consolidate(SimpleNamespace(sup=sup, runner=object()), SimpleNamespace(project_workdir=tmp_path, life_dir=tmp_path))


def test_skips_quietly_when_the_vertical_is_undecided_or_unreadable(tmp_path: Path, monkeypatch, caplog) -> None:
    monkeypatch.setattr(consolidation, "consolidate_knowledge", _refuse)
    sup = SimpleNamespace(_budget_global_root=lambda: tmp_path, _emit=lambda event: True)
    config = SimpleNamespace(project_workdir=tmp_path / "ws", life_dir=tmp_path / "life")
    caplog.set_level(logging.INFO)

    monkeypatch.setattr(vertical_select, "resolve_skill_scope", lambda workspace: "")
    _maybe_consolidate(SimpleNamespace(sup=sup, runner=object()), config)

    def unreadable(workspace):
        raise ValueError("state file is not valid")

    monkeypatch.setattr(vertical_select, "resolve_skill_scope", unreadable)
    _maybe_consolidate(SimpleNamespace(sup=sup, runner=object()), config)
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_falls_back_to_the_memory_global_root(tmp_path: Path, monkeypatch) -> None:
    seen: list[dict] = []
    monkeypatch.setattr(consolidation, "consolidate_knowledge", lambda **kwargs: seen.append(kwargs) or {})
    monkeypatch.setattr(vertical_select, "resolve_skill_scope", lambda ws: "software")
    sup = SimpleNamespace(_emit=lambda event: True)  # no _budget_global_root on this supervisor
    state = SimpleNamespace(sup=sup, runner=object(), mem=SimpleNamespace(global_root=tmp_path / "home"))

    _maybe_consolidate(state, SimpleNamespace(project_workdir=tmp_path, life_dir=tmp_path / "life"))
    assert seen and seen[0]["global_root"] == tmp_path / "home" and seen[0]["vertical"] == "software"

    seen.clear()
    state = SimpleNamespace(sup=sup, runner=object(), mem=SimpleNamespace(global_root=None))
    _maybe_consolidate(state, SimpleNamespace(project_workdir=tmp_path, life_dir=tmp_path / "life"))
    assert seen == [], "no usable host root means nothing to consolidate"


def test_a_failure_inside_consolidation_is_logged_not_raised(tmp_path: Path, monkeypatch, caplog) -> None:
    def explode(**_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(consolidation, "consolidate_knowledge", explode)
    monkeypatch.setattr(vertical_select, "resolve_skill_scope", lambda ws: "research")
    sup = SimpleNamespace(_budget_global_root=lambda: tmp_path, _emit=lambda event: True)
    caplog.set_level(logging.ERROR)

    _maybe_consolidate(SimpleNamespace(sup=sup, runner=object()), SimpleNamespace(project_workdir=tmp_path, life_dir=tmp_path))

    assert any("knowledge consolidation failed" in record.getMessage() for record in caplog.records)
