"""Legacy runners receive current memory at their sole public call boundary."""
from types import SimpleNamespace

import pytest

from argus.core.operator_context import append_directive, append_revoke
from argus.life.memory import BacklogItem, MemoryBundle
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.life.supervisor._mission_execution_helpers import _MissionRunState


def test_legacy_runner_refreshes_after_mission_preparation(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    page = workspace / ".autors/project/wiki/pages/quartz.md"
    page.parent.mkdir(parents=True)
    (page.parent.parent / "INDEX.md").write_text("# Wiki")
    page.write_text("quartz obsolete finding")
    memory = MemoryBundle.for_cwd(workspace, global_root=tmp_path / "tenant", fingerprint="project")
    append_directive(memory.project_root, "OLD_KNOWLEDGE", expected_revision=0)
    captured = []

    class LegacyRunner:
        def execute(self, *, objective, sink, prelude_context, scope):
            captured.append(prelude_context)
            return SimpleNamespace(success=True)

    supervisor = LifeSupervisor(
        memory=memory, runner=LegacyRunner(), sink=SimpleNamespace(handle_event=lambda _event: None),
        config=LifeSupervisorConfig(project_worktree=workspace, runtime_context="STATIC_RUNTIME_POLICY"),
    )
    item = memory.backlog.add(BacklogItem.new(title="quartz", objective="verify quartz"))

    def prepare(item, prelude, workdir, vertical_root):
        # The runtime may spend time preparing a vertical/worktree/context packet
        # after _build_mission_prelude and before execute is finally called.
        page.unlink()
        append_revoke(memory.project_root, 1, reason="withdrawn", expected_revision=1)
        append_directive(memory.project_root, "CURRENT_KNOWLEDGE", lifetime="once", expected_revision=2)
        state = _MissionRunState(item)
        state.prelude = "VERTICAL_POLICY\n" + prelude
        state.vertical_root = vertical_root
        state.execution_workdir = workdir
        state.cost_sink = supervisor.sink
        return state

    monkeypatch.setattr(supervisor, "_prepare_mission_context", prepare)
    monkeypatch.setattr(
        "argus.life.supervisor._mission_execution.ensure_manager_decision",
        lambda _host, item, *_args, **_kwargs: item,
    )
    # Stop after the actual execute boundary; settlement is outside this test.
    class ReachedBoundary(Exception):
        pass

    def stop_after_invoke(state):
        assert state.exc_str is None
        raise ReachedBoundary

    monkeypatch.setattr(supervisor, "_derive_basic_outcome_fields", stop_after_invoke)
    with pytest.raises(ReachedBoundary):
        supervisor._run_one(item)
    assert len(captured) == 1
    assert "CURRENT_KNOWLEDGE" in captured[0]
    assert "OLD_KNOWLEDGE" not in captured[0]
    assert str(page) not in captured[0]
    assert "STATIC_RUNTIME_POLICY" in captured[0] and "VERTICAL_POLICY" in captured[0]
    following = _MissionRunState(BacklogItem.new(title="next", objective="verify quartz"))
    following.cost_sink = supervisor.sink
    following.vertical_root = workspace
    supervisor._invoke_mission_runner(following)
    assert following.exc_str is None and len(captured) == 2
    assert "CURRENT_KNOWLEDGE" not in captured[1]
