"""Cut #1 + Cut #4: supervisor idle-backoff and per-project lifecycle root.

These exercise the small, pure helpers added to ``LifeSupervisor`` via a
minimal stand-in ``self`` (the same isolation pattern the lifecycle-gate
integration tests use), so we avoid standing up a full supervisor.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus.life.project_lifecycle_io import lifecycle_path
from argus.life.supervisor import LifeSupervisor


class _LifecycleRootStub:
    def __init__(self, *, project_state_dir: Path | None, memory_root: Path) -> None:
        self.config = SimpleNamespace(project_state_dir=project_state_dir)
        self.memory = SimpleNamespace(root=memory_root)
        self._lifecycle_migrated = False

    _lifecycle_root = LifeSupervisor._lifecycle_root
    _migrate_global_lifecycle_if_needed = (
        LifeSupervisor._migrate_global_lifecycle_if_needed
    )


def test_migration_copies_global_and_retires_it(tmp_path) -> None:
    per = tmp_path / "projects" / "abc123"
    glob = tmp_path / "global"
    glob.mkdir(parents=True)
    legacy = lifecycle_path(glob)
    legacy.write_text('{"state": "writing"}', encoding="utf-8")

    stub = _LifecycleRootStub(project_state_dir=per, memory_root=glob)
    stub._migrate_global_lifecycle_if_needed(per)

    # Copied into the per-project dir...
    assert lifecycle_path(per).exists()
    assert '"writing"' in lifecycle_path(per).read_text(encoding="utf-8")
    # ...and the global file retired so future projects don't inherit it.
    assert not legacy.exists()
    assert (glob / "lifecycle.json.migrated").exists()


def test_migration_does_not_overwrite_existing_per_project(tmp_path) -> None:
    per = tmp_path / "projects" / "abc123"
    per.mkdir(parents=True)
    lifecycle_path(per).write_text('{"state": "running"}', encoding="utf-8")
    glob = tmp_path / "global"
    glob.mkdir(parents=True)
    lifecycle_path(glob).write_text('{"state": "done"}', encoding="utf-8")

    stub = _LifecycleRootStub(project_state_dir=per, memory_root=glob)
    stub._migrate_global_lifecycle_if_needed(per)
    # Existing per-project state wins; global is left as-is (not retired).
    assert '"running"' in lifecycle_path(per).read_text(encoding="utf-8")
    assert lifecycle_path(glob).exists()
