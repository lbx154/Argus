"""Wheel-cache hardlinks must not block real missions or weaken source guards."""
import os
from types import SimpleNamespace

import pytest

from argus_skill.apps._runtime_execute import SkillLoopExecuteMixin
from argus_skill.apps._runtime_helpers import _ExecuteState


@pytest.fixture
def packaged(tmp_path):
    cache = tmp_path / "wheel-cache"
    package = tmp_path / "site-packages"
    cache.mkdir()
    canonical = []
    for role, name in [("engineer/workflows", "chemistry-playground.md"),
                       ("reviewer", "chemistry-playground-review.md")]:
        origin = cache / name
        origin.write_text("trusted " + name)
        target = package / role / name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(origin, target)
        except OSError as exc:
            pytest.skip(f"hardlinks unavailable: {exc}")
        canonical.append(target)

    class Harness(SkillLoopExecuteMixin):
        @staticmethod
        def _canonical_playground_skill_paths():
            return tuple(canonical)

        def _set_usage_context(self, value):
            self.usage = value

        def _run_bounded_planning(self, *_args, **_kwargs):
            pass

    return Harness(), canonical, cache


def test_cache_hardlinks_are_detached_before_source_guard(packaged):
    harness, paths, cache = packaged
    original = {p.name: (cache / p.name).read_bytes() for p in paths}
    sibling = paths[0].parent / "another-workflow.md"
    os.link(cache / paths[0].name, sibling)
    (paths[0].parent / ".argus-skill-concurrent-startup").write_bytes(b"unfinished temporary copy")
    snapshots, error = harness._snapshot_playground_skill_files()
    assert not error and len(snapshots) == 3
    assert all(path.stat().st_nlink == 1 for path, _ in snapshots)
    assert all((cache / name).read_bytes() == value for name, value in original.items())
    assert harness._restore_playground_skill_files(snapshots, error) == (False, "", True)
    paths[0].write_text("changed by an agent")
    changed, _, ok = harness._restore_playground_skill_files(snapshots, error)
    assert changed and ok and paths[0].read_bytes() == original[paths[0].name]
    assert (cache / paths[0].name).read_bytes() == original[paths[0].name]


def test_hardlink_replacement_during_execution_is_still_rejected(packaged, tmp_path):
    harness, paths, _ = packaged
    snapshots, error = harness._snapshot_playground_skill_files()
    outside = tmp_path / "outside.md"
    outside.write_text("outside must remain untouched")
    paths[0].unlink()
    os.link(outside, paths[0])
    changed, reason, ok = harness._restore_playground_skill_files(snapshots, error)
    assert changed and ok and "modified protected Skill" in reason
    assert paths[0].read_bytes() == snapshots[0][1]
    assert outside.read_text() == "outside must remain untouched"


def test_symlink_preflight_is_not_misreported_as_pipeline_mutation(packaged, tmp_path):
    harness, paths, cache = packaged
    paths[0].unlink()
    try:
        paths[0].symlink_to(cache / paths[0].name)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    state = _ExecuteState()
    state.workdir = tmp_path / "work"
    state.workdir.mkdir()
    state.loop = SimpleNamespace(run=lambda *a, **kw: pytest.fail("must refuse before execution"))
    with pytest.raises(RuntimeError, match="refused before execution: protected Playground Skill") as caught:
        harness._invoke_execute_loop(state, sink=SimpleNamespace(), objective="refine crystal", original_objective="",
                                     preplanned=True, mission_id="test", usage_mission_id=None)
    assert "created formal pipeline" not in str(caught.value)
    assert list(state.workdir.iterdir()) == []
    assert harness.usage is None and harness._current_sink is None


def test_empty_pipeline_restore_is_a_noop(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    snapshot = SkillLoopExecuteMixin._snapshot_pipeline_state(work)
    assert snapshot[1:] == (False, None, "")
    assert SkillLoopExecuteMixin._restore_pipeline_state(snapshot) == (False, "", True)
    assert list(work.iterdir()) == []


def test_actual_pipeline_creation_is_detected_and_removed(tmp_path):
    snapshot = SkillLoopExecuteMixin._snapshot_pipeline_state(tmp_path)
    path = snapshot[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"current_stage":"complete"}')
    changed, reason, ok = SkillLoopExecuteMixin._restore_pipeline_state(snapshot)
    assert changed and ok and "created formal pipeline" in reason and not path.exists()


def test_formal_mission_reaches_loop_with_uv_installed_skills(packaged, tmp_path):
    harness, paths, cache = packaged
    state = _ExecuteState()
    state.workdir = tmp_path / "work"
    state.workdir.mkdir()
    seen = []

    def run(*args, **kwargs):
        seen.append(kwargs["workdir"])
        assert all(path.stat().st_nlink == 1 for path in paths)
        return SimpleNamespace(extras={}, status="done")

    state.loop = SimpleNamespace(run=run)
    harness._invoke_execute_loop(state, sink=SimpleNamespace(), objective="solve crystal", original_objective="",
                                 preplanned=True, mission_id="test", usage_mission_id=None)
    assert seen == [state.workdir] and state.outcome.status == "done"
    assert not state.playground_workflow_guarded
    assert all((cache / path.name).read_text().startswith("trusted") for path in paths)
