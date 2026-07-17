from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from argus_skill.daemon.state import read_continuous_state, write_continuous_config
from argus_skill.manager import front_door
from argus_skill.manager._core import (
    clear_manager_pipeline_yield,
    manager_pipeline_yield_requested,
    request_manager_pipeline_yield,
)


def test_pipeline_yield_marker_tracks_live_request(tmp_path) -> None:
    token = request_manager_pipeline_yield(tmp_path)

    assert manager_pipeline_yield_requested(tmp_path) is True
    assert clear_manager_pipeline_yield(tmp_path, token) is True
    assert manager_pipeline_yield_requested(tmp_path) is False


def test_pipeline_yield_marker_clears_dead_request(tmp_path, monkeypatch) -> None:
    request_manager_pipeline_yield(tmp_path)

    def dead_process(_pid, _signal):
        raise ProcessLookupError

    monkeypatch.setattr("argus_skill.manager._core.os.kill", dead_process)

    assert manager_pipeline_yield_requested(tmp_path) is False
    assert not (tmp_path / ".manager_pipeline_yield.json").exists()


def test_continuous_handoff_requests_boundary_yield(tmp_path, monkeypatch) -> None:
    life_dir = tmp_path / "life"
    workdir = tmp_path / "work"
    life_dir.mkdir()
    workdir.mkdir()
    write_continuous_config(life_dir, enabled=True, objective="old objective")
    memory = SimpleNamespace(root=life_dir, project_root=life_dir)

    class Manager:
        project_root = workdir

        @contextmanager
        def pipeline_lock(self):
            assert manager_pipeline_yield_requested(life_dir) is True
            yield

    class Prepared:
        mem = memory
        body = "new objective"
        manager = Manager()
        execution_task = "manager-authored theorem objective"

        @staticmethod
        def commit(*, acquire_lock=True):
            assert acquire_lock is False
            return SimpleNamespace(
                vertical="math",
                kind="research",
                stages=["scope", "solve", "review"],
                workflow_mode="staged",
                headline=lambda: "math theorem objective",
            )

        @staticmethod
        def completed(_division, *, continuous_generation=None):
            assert continuous_generation == 2

        @staticmethod
        def failed(_exc):
            raise AssertionError("handoff should not fail")

        @staticmethod
        def superseded():
            raise AssertionError("handoff should not be superseded")

    monkeypatch.setattr(
        front_door,
        "prepare_manager_execution_task",
        lambda *_args, **_kwargs: Prepared(),
    )

    result = front_door.manager_continuous_handoff(
        memory,
        "new objective",
        {},
    )

    assert result == "manager-authored theorem objective"
    state = read_continuous_state(life_dir)
    assert state.enabled is True
    assert state.objective == "manager-authored theorem objective"
    assert not (life_dir / ".manager_pipeline_yield.json").exists()
