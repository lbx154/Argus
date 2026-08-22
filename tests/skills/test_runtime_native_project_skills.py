from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from argus_skill.apps._runtime_construction import _RunnerConstructionMixin
from argus_skill.apps._runtime_execute import SkillLoopExecuteMixin
from argus_skill.apps._runtime_supervisor import run_life_supervisor
from argus_skill.daemon.config import LifeWorkerConfig
from argus_skill.daemon.life_worker import LifeWorker
from argus_skill.life.supervisor import LifeSupervisor
from argus_skill.life.supervisor._planning_cycle_helpers import _PlanCycleState
from argus_skill.skills.missions import (
    EngineerMission,
    ManagerMission,
    PlannerMission,
    ReviewerMission,
)


class _CapturedLoop:
    def __init__(self, **kwargs) -> None:
        self.skill_store = kwargs["skill_store"]


class _ExecuteHarness(SkillLoopExecuteMixin):
    def __init__(self, args) -> None:
        self._args = args
        self._backend = object()
        self._SkillLoop = _CapturedLoop
        self.refreshed_workdir: Path | None = None

    def _refresh_manager_skill_store(self, args, *, workdir=None) -> None:
        assert args is self._args
        self.refreshed_workdir = workdir


class _RefreshHarness:
    _build_manager_skill_store = _RunnerConstructionMixin._build_manager_skill_store
    _refresh_manager_skill_store = _RunnerConstructionMixin._refresh_manager_skill_store

    def __init__(self, args) -> None:
        self._args = args
        self.manager = SimpleNamespace(_session=None)
        self.planner_backend = object()
        self.backend = object()
        self._manager_skill_store = self._build_manager_skill_store(args)
        self.refresh_workdirs: list[Path | None] = []

    def _refresh_manager_skill_store(self, args, *, workdir=None) -> None:
        self.refresh_workdirs.append(workdir)
        _RunnerConstructionMixin._refresh_manager_skill_store(
            self,
            args,
            workdir=workdir,
        )


def _args(tmp_path: Path, configured_workdir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        skills_dir=str(tmp_path / "global-skills"),
        project_state_dir=str(tmp_path / "state"),
        workdir=str(configured_workdir),
    )


def _write_native_skill(workdir: Path) -> Path:
    root = workdir / ".agents" / "skills"
    skill = root / "local-runtime" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: local-runtime\ndescription: Runtime-native test Skill.\n---\n\n"
        "PRIVATE RUNTIME BODY\n",
        encoding="utf-8",
    )
    return root.resolve()


def test_runtime_uses_canonical_execution_workdir_for_all_role_libraries(
    tmp_path: Path,
) -> None:
    configured_workdir = tmp_path / "configured"
    execution_workdir = tmp_path / "canonical-execution"
    native_root = _write_native_skill(execution_workdir)
    args = _args(tmp_path, configured_workdir)

    manager_store = _RunnerConstructionMixin._build_manager_skill_store(
        object(),
        args,
        workdir=execution_workdir,
    )
    for mission_type in (
        PlannerMission,
        ManagerMission,
        EngineerMission,
        ReviewerMission,
    ):
        assert mission_type(manager_store).libraries().native_paths[0] == native_root

    harness = _ExecuteHarness(args)
    state = SimpleNamespace(
        workdir=execution_workdir,
        config=SimpleNamespace(active_vertical=""),
    )
    harness._build_execute_skill_store_and_loop(
        state,
        sink=SimpleNamespace(handle_event=lambda event: None),
    )

    assert harness.refreshed_workdir == execution_workdir
    assert state.loop.skill_store.library_roots()[0] == native_root
    assert not (configured_workdir / ".agents").exists()


def test_runtime_does_not_create_a_missing_agents_directory(tmp_path: Path) -> None:
    workdir = tmp_path / "repo"
    args = _args(tmp_path, workdir)

    store = _RunnerConstructionMixin._build_manager_skill_store(object(), args)

    assert not (workdir / ".agents").exists()
    assert store.library_roots()[0] == (tmp_path / "state" / "skills").resolve()


def test_in_process_life_planner_receives_refreshed_project_skills(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus_skill.apps import _runtime_supervisor

    workdir = tmp_path / "project"
    workdir.mkdir()
    args = _args(tmp_path, tmp_path / "stale-workdir")
    runner = _RefreshHarness(args)
    native_root = _write_native_skill(workdir)
    captured = {}

    class _Supervisor:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def run(self):
            return {"stopped_by": "test"}

    monkeypatch.setattr(_runtime_supervisor, "LifeSupervisor", _Supervisor)

    result = run_life_supervisor(
        mem=SimpleNamespace(
            root=tmp_path / "state",
            global_root=tmp_path / "global",
        ),
        runner=runner,
        engineer_model="memory",
        reviewer_model="memory",
        once=True,
        max_missions=1,
        global_daily_cap_usd=0,
        project_worktree=workdir,
        artifact_root=tmp_path / "state",
        quiet=True,
    )

    assert result == {"stopped_by": "test"}
    assert captured["skill_store"] is runner._manager_skill_store
    assert captured["skill_store"].native_project_roots() == [native_root]
    assert runner.refresh_workdirs == [workdir]


def test_daemon_boot_life_planner_receives_refreshed_project_skills(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    life_dir = tmp_path / "state"
    args = _args(tmp_path, workdir)
    runner = _RefreshHarness(args)
    native_root = _write_native_skill(workdir)
    captured = {}

    class _Supervisor:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)
            self._vertical_resolved = False

    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.LifeSupervisor",
        _Supervisor,
    )
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=life_dir,
            global_root=tmp_path / "global",
            project_fingerprint="native-skills",
            project_workdir=workdir,
            backend="memory",
            mission_width=1,
        )
    )
    rf_state = SimpleNamespace(
        cfg=worker.config,
        runtime_root=life_dir,
        mem=object(),
        sink=object(),
        runner=runner,
        init_continuous=False,
        init_objective="",
        continuous_provider=lambda: (False, "", True),
        manager_handoff_resolved=False,
    )

    worker._rf_build_supervisor(rf_state)

    assert captured["skill_store"] is runner._manager_skill_store
    assert captured["skill_store"].native_project_roots() == [native_root]
    assert runner.refresh_workdirs == [workdir]


def test_life_planner_cycle_rebuilds_skills_for_adopted_worktree(
    tmp_path,
    monkeypatch,
) -> None:
    from argus_skill.core.campaign_workdir import adopt_campaign_workdir

    base = tmp_path / "base"
    adopted = base / "adopted"
    base.mkdir()
    adopted.mkdir()
    subprocess.run(["git", "init", "-q", str(adopted)], check=True)
    stale_native_root = _write_native_skill(base)
    adopted_native_root = _write_native_skill(adopted)
    state_root = tmp_path / "state"
    args = _args(tmp_path, base)
    runner = _RefreshHarness(args)
    stale_store = runner._manager_skill_store
    adopt_campaign_workdir(
        state_root=state_root,
        base_root=base,
        current_root=base,
        requested="adopted",
    )
    captured = {}

    class _Planner:
        def __init__(self, _runner, *, skill_store, **_kwargs) -> None:
            captured["skill_store"] = skill_store

        def plan_next(self, **_kwargs):
            return object()

    monkeypatch.setattr("argus_skill.planner.Planner", _Planner)
    supervisor = LifeSupervisor.__new__(LifeSupervisor)
    supervisor.runner = runner
    supervisor.skill_store = stale_store
    supervisor.memory = SimpleNamespace(root=state_root, project_worktree=base)
    supervisor.planner_runner = object()
    supervisor.config = SimpleNamespace(
        continuous_objective="continue",
        role_skill_maintenance_enabled=True,
        project_worktree=base,
    )
    supervisor.sink = object()
    supervisor._planning_cycles = 1
    supervisor._render_journal_for_planner = lambda: ""
    supervisor._planner_runtime_with_idle_note = lambda: ""
    supervisor._recent_subagent_family_failures = lambda: {}
    supervisor._stuck_subagent_families_note = lambda _failures: ""
    supervisor._manager_intent_prompt_block = lambda *_args: ""
    supervisor._planner_authorization_prompt_block = lambda: ""
    supervisor._planner_config = lambda: None
    state = _PlanCycleState(None)

    assert supervisor._pc_invoke_planner(state) is None
    assert runner.refresh_workdirs == [adopted]
    assert supervisor.skill_store is runner._manager_skill_store
    assert captured["skill_store"] is runner._manager_skill_store
    assert captured["skill_store"].native_project_roots() == [adopted_native_root]
    assert stale_store.native_project_roots() == [stale_native_root]


def test_daemon_restart_refreshes_primary_and_helper_planner_skills(
    tmp_path,
    monkeypatch,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    native_root = _write_native_skill(workdir)
    life_dir = tmp_path / "state"
    args = _args(tmp_path, workdir)
    primary_runner = _RefreshHarness(args)
    helper_runners = []
    captured = []

    class _Supervisor:
        def __init__(self, **kwargs) -> None:
            captured.append(kwargs)
            self.config = kwargs["config"]
            self._vertical_resolved = False

    def build_helper(_args):
        runner = _RefreshHarness(args)
        helper_runners.append(runner)
        return runner

    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.LifeSupervisor",
        _Supervisor,
    )
    monkeypatch.setattr(
        "argus_skill.apps._runtime.build_life_runner",
        build_helper,
    )
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=life_dir,
            global_root=tmp_path / "global",
            project_fingerprint="native-skills-restart",
            project_workdir=workdir,
            backend="codex",
            mission_width=2,
        )
    )
    rf_state = SimpleNamespace(
        cfg=worker.config,
        runtime_root=life_dir,
        mem=object(),
        sink=object(),
        runner=primary_runner,
        init_continuous=False,
        init_objective="",
        continuous_provider=lambda: (False, "", True),
        manager_handoff_resolved=False,
    )

    worker._rf_build_supervisor(rf_state)
    worker._rf_build_supervisor(rf_state)

    assert primary_runner.refresh_workdirs == [workdir, workdir]
    assert len(helper_runners) == 2
    assert all(runner.refresh_workdirs == [workdir] for runner in helper_runners)
    assert len(captured) == 4
    assert all(
        row["skill_store"].native_project_roots() == [native_root]
        for row in captured
    )
