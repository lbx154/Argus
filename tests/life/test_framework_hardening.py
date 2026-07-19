"""Cut #1 + Cut #4: supervisor idle-backoff and per-project lifecycle root.

These exercise the small, pure helpers added to ``LifeSupervisor`` via a
minimal stand-in ``self`` (the same isolation pattern the lifecycle-gate
integration tests use), so we avoid standing up a full supervisor.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus_skill.life.project_lifecycle_io import lifecycle_path
from argus_skill.life.supervisor import (
    _IDLE_BACKOFF_BASE_SECONDS,
    _IDLE_BACKOFF_CAP_SECONDS,
    LifeSupervisor,
)


class _BackoffStub:
    def __init__(self) -> None:
        self._consecutive_idle_planner_cycles = 0
        self._suggested_sleep_s = 0.0

    _idle_backoff_seconds = LifeSupervisor._idle_backoff_seconds
    _reset_idle_backoff = LifeSupervisor._reset_idle_backoff
    _enter_idle_backoff = LifeSupervisor._enter_idle_backoff


def test_idle_backoff_is_exponential_and_capped() -> None:
    s = _BackoffStub()
    first = s._enter_idle_backoff()
    assert first == _IDLE_BACKOFF_BASE_SECONDS
    second = s._enter_idle_backoff()
    assert second == _IDLE_BACKOFF_BASE_SECONDS * 2
    # Drive it far past the cap.
    for _ in range(20):
        last = s._enter_idle_backoff()
    assert last == _IDLE_BACKOFF_CAP_SECONDS


def test_idle_backoff_handles_an_extreme_persisted_cycle_count() -> None:
    s = _BackoffStub()
    s._consecutive_idle_planner_cycles = 10**6

    assert s._idle_backoff_seconds() == _IDLE_BACKOFF_CAP_SECONDS


def test_reset_clears_backoff() -> None:
    s = _BackoffStub()
    s._enter_idle_backoff()
    s._enter_idle_backoff()
    s._reset_idle_backoff()
    assert s._consecutive_idle_planner_cycles == 0
    assert s._suggested_sleep_s == 0.0
    # After reset the next entry starts again at base.
    assert s._enter_idle_backoff() == _IDLE_BACKOFF_BASE_SECONDS


class _LifecycleRootStub:
    def __init__(self, *, telemetry_dir: Path | None, memory_root: Path) -> None:
        self.config = SimpleNamespace(telemetry_dir=telemetry_dir)
        self.memory = SimpleNamespace(root=memory_root)
        self._lifecycle_migrated = False

    _lifecycle_root = LifeSupervisor._lifecycle_root
    _migrate_global_lifecycle_if_needed = (
        LifeSupervisor._migrate_global_lifecycle_if_needed
    )


def test_lifecycle_root_prefers_per_project_telemetry_dir(tmp_path) -> None:
    per = tmp_path / "projects" / "abc123"
    glob = tmp_path / "global"
    stub = _LifecycleRootStub(telemetry_dir=per, memory_root=glob)
    assert stub._lifecycle_root() == per


def test_lifecycle_root_falls_back_to_global_when_no_telemetry_dir(tmp_path) -> None:
    glob = tmp_path / "global"
    stub = _LifecycleRootStub(telemetry_dir=None, memory_root=glob)
    assert stub._lifecycle_root() == glob


def test_migration_copies_global_and_retires_it(tmp_path) -> None:
    per = tmp_path / "projects" / "abc123"
    glob = tmp_path / "global"
    glob.mkdir(parents=True)
    legacy = lifecycle_path(glob)
    legacy.write_text('{"state": "writing"}', encoding="utf-8")

    stub = _LifecycleRootStub(telemetry_dir=per, memory_root=glob)
    stub._migrate_global_lifecycle_if_needed(per)

    # Copied into the per-project dir...
    assert lifecycle_path(per).exists()
    assert '"writing"' in lifecycle_path(per).read_text(encoding="utf-8")
    # ...and the global file retired so future projects don't inherit it.
    assert not legacy.exists()
    assert (glob / "lifecycle.json.migrated").exists()


def test_migration_noop_when_no_telemetry_dir(tmp_path) -> None:
    glob = tmp_path / "global"
    glob.mkdir(parents=True)
    legacy = lifecycle_path(glob)
    legacy.write_text('{"state": "writing"}', encoding="utf-8")

    stub = _LifecycleRootStub(telemetry_dir=None, memory_root=glob)
    stub._migrate_global_lifecycle_if_needed(glob)
    # Legacy/global file is untouched in the non-per-project regime.
    assert legacy.exists()


def test_migration_does_not_overwrite_existing_per_project(tmp_path) -> None:
    per = tmp_path / "projects" / "abc123"
    per.mkdir(parents=True)
    lifecycle_path(per).write_text('{"state": "running"}', encoding="utf-8")
    glob = tmp_path / "global"
    glob.mkdir(parents=True)
    lifecycle_path(glob).write_text('{"state": "done"}', encoding="utf-8")

    stub = _LifecycleRootStub(telemetry_dir=per, memory_root=glob)
    stub._migrate_global_lifecycle_if_needed(per)
    # Existing per-project state wins; global is left as-is (not retired).
    assert '"running"' in lifecycle_path(per).read_text(encoding="utf-8")
    assert lifecycle_path(glob).exists()
