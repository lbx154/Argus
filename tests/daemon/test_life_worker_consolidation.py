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


def test_skips_without_a_project_workspace_or_supervisor(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(consolidation, "consolidate_knowledge", _refuse)
    sup = SimpleNamespace(_budget_global_root=lambda: tmp_path, _emit=lambda event: True)
    _maybe_consolidate(SimpleNamespace(sup=sup, runner=object()), SimpleNamespace(project_workdir=None, life_dir=tmp_path))
    _maybe_consolidate(SimpleNamespace(sup=None, runner=object()), SimpleNamespace(project_workdir=tmp_path, life_dir=tmp_path))
    _maybe_consolidate(SimpleNamespace(sup=sup, runner=None), SimpleNamespace(project_workdir=tmp_path, life_dir=tmp_path))


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


def test_passes_the_resolved_vertical_roots_and_sink_through(tmp_path: Path, monkeypatch) -> None:
    seen: list[dict] = []
    monkeypatch.setattr(consolidation, "consolidate_knowledge", lambda **kwargs: seen.append(kwargs) or {})
    workspace = tmp_path / "ws"
    monkeypatch.setattr(vertical_select, "resolve_skill_scope", lambda ws: "research" if ws == workspace else "")
    emitted: list[dict] = []
    sup = SimpleNamespace(_budget_global_root=lambda: str(tmp_path / "home"), _emit=emitted.append)
    runner = object()

    _maybe_consolidate(
        SimpleNamespace(sup=sup, runner=runner),
        SimpleNamespace(project_workdir=workspace, life_dir=str(tmp_path / "home" / "projects" / "s-1")),
    )

    assert len(seen) == 1
    call = seen[0]
    assert call["runner"] is runner and call["vertical"] == "research"
    assert call["global_root"] == tmp_path / "home"
    assert call["life_dir"] == tmp_path / "home" / "projects" / "s-1"
    assert call["emit"] is sup._emit


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


def test_real_pass_with_an_empty_host_root_is_a_quick_skip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(vertical_select, "resolve_skill_scope", lambda ws: "research")
    events: list[dict] = []
    home = tmp_path / "home"
    sup = SimpleNamespace(_budget_global_root=lambda: home, _emit=events.append)

    _maybe_consolidate(SimpleNamespace(sup=sup, runner=object()), SimpleNamespace(project_workdir=tmp_path, life_dir=home / "projects" / "s-1"))

    assert events == [] and not home.exists()
