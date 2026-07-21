from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import argus_skill.daemon.self_maintenance as self_maintenance_mod
from argus_skill.daemon.self_maintenance import DaemonSelfMaintenance
from argus_skill.life.memory import LifeMemory


class _Manager:
    def __init__(self, action: str = "repair") -> None:
        self.action = action
        self.calls = 0

    def decide_self_maintenance(self, observations, **_kwargs):
        self.calls += 1
        if self.action == "no_action":
            return SimpleNamespace(action="no_action")
        if self.action == "adopt":
            update = next(
                row
                for row in observations
                if row["type"] == "framework.update_available"
            )
            return SimpleNamespace(
                action="adopt",
                reason="merged change fits this daemon",
                acceptance_check="clean supervisor pass",
                evidence_ids=(update["id"],),
            )
        return SimpleNamespace(
            action="repair",
            problem="planner error repeated",
            reason="the structured planner error is reproducible",
            title="Repair planner error",
            objective="Fix the planner error without broad refactoring.",
            acceptance_check="pytest -q tests/life/test_planner_dag_enqueue.py",
            evidence_ids=(observations[-1]["id"],),
            affected_paths=(
                "argus_skill/life/supervisor/_planning_cycle.py",
                "tests/life/test_planner_dag_enqueue.py",
            ),
        )


def _controller(tmp_path: Path, manager: _Manager) -> DaemonSelfMaintenance:
    memory = LifeMemory.open(tmp_path / "life")
    memory.init()
    project = tmp_path / "project"
    project.mkdir()
    framework = tmp_path / "framework"
    framework.mkdir()
    controller = DaemonSelfMaintenance(
        life_dir=memory.root,
        framework_root=framework,
        project_workdir=project,
        manager=manager,
        memory=memory,
    )
    controller._write_state(
        maintenance_available=True,
        isolation_checked_at=time.time(),
    )
    return controller


def _publication_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "publication-repo"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "config", "user.name", "seed"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "seed@example.com"],
        cwd=repo,
        check=True,
    )
    (repo / "argus_skill").mkdir()
    (repo / "scripts").mkdir()
    (repo / "argus_skill" / "base.py").write_text("BASE = 1\n", encoding="utf-8")
    (repo / "scripts" / "generate_release_manifest.py").write_text(
        "import pathlib, subprocess, sys\n"
        "root = pathlib.Path(__file__).resolve().parents[1]\n"
        "tracked = subprocess.check_output(['git', 'ls-files'], cwd=root, text=True)\n"
        "expected = 'new-feature\\n' if 'argus_skill/new_feature.py' in tracked else 'base\\n'\n"
        "manifest = root / 'argus_skill' / 'release_manifest.json'\n"
        "if '--check' in sys.argv:\n"
        "    raise SystemExit(0 if manifest.read_text() == expected else 2)\n"
        "manifest.write_text(expected)\n",
        encoding="utf-8",
    )
    (repo / "argus_skill" / "release_manifest.json").write_text(
        "base\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repo, base


def test_manager_queues_private_reviewed_repair_from_real_event(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = _Manager()
    controller = _controller(tmp_path, manager)
    worktree = tmp_path / "private-framework"
    subprocess.run(["git", "init", "-b", "main", str(worktree)], check=True)
    subprocess.run(["git", "config", "user.name", "seed"], cwd=worktree, check=True)
    subprocess.run(
        ["git", "config", "user.email", "seed@example.com"],
        cwd=worktree,
        check=True,
    )
    (worktree / "README").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=worktree, check=True)
    monkeypatch.setattr(
        controller,
        "_prepare_worktree",
        lambda _incident_id: (worktree, "argus-self/session/incident"),
    )
    controller.observe({
        "type": "life.planner.error",
        "ts": 10.0,
        "error": "schema failure",
    })

    item_id = controller.audit_if_due(daemon_state={"stopped_by": "planner_error"})

    assert item_id
    assert manager.calls == 1
    [item] = controller.memory.backlog.all()
    assert item.execution_workdir == str(worktree)
    assert "framework_maintenance" in item.tags
    assert "review:required" in item.tags
    assert "Do not perform unrelated cleanup" in item.objective


def test_manager_no_action_never_creates_make_work(tmp_path: Path) -> None:
    manager = _Manager(action="no_action")
    controller = _controller(tmp_path, manager)
    controller.observe({
        "type": "life.planner.error",
        "ts": 10.0,
        "error": "one transient failure",
    })

    assert controller.audit_if_due(daemon_state={}) == ""
    assert controller.memory.backlog.all() == []


def test_budget_block_prevents_manager_maintenance_call(tmp_path: Path) -> None:
    manager = _Manager()
    controller = _controller(tmp_path, manager)
    controller.observe({
        "type": "life.planner.error",
        "ts": 10.0,
        "error": "schema failure",
    })

    assert controller.audit_if_due(
        daemon_state={"budget_allowed": False}
    ) == ""
    assert manager.calls == 0


def test_missing_isolation_prevents_manager_maintenance_call(tmp_path: Path) -> None:
    manager = _Manager()
    controller = _controller(tmp_path, manager)
    controller._write_state(
        maintenance_available=False,
        isolation_checked_at=time.time(),
    )
    controller.observe({
        "type": "life.planner.error",
        "ts": 10.0,
        "error": "schema failure",
    })

    assert controller.audit_if_due(daemon_state={}) == ""
    assert manager.calls == 0


def test_private_worktree_does_not_overwrite_shared_git_identity(
    tmp_path: Path,
) -> None:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True)
    repo = tmp_path / "framework"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "config", "user.name", "seed"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "seed@example.com"],
        cwd=repo,
        check=True,
    )
    (repo / "README").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=repo, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=repo, check=True)

    memory = LifeMemory.open(tmp_path / "life")
    memory.init()
    project = tmp_path / "project"
    project.mkdir()
    controller = DaemonSelfMaintenance(
        life_dir=memory.root,
        framework_root=repo,
        project_workdir=project,
        manager=_Manager(),
        memory=memory,
    )
    worktree, branch = controller._prepare_worktree("incident123")

    assert branch.startswith("argus-self/")
    assert subprocess.run(
        ["git", "config", "user.name"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == "seed"
    assert subprocess.run(
        ["git", "config", "user.email"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == "seed@example.com"


def test_canary_revision_accepts_runtime_short_sha(tmp_path: Path) -> None:
    controller = _controller(tmp_path, _Manager())
    controller._write_state(
        phase="handoff_requested",
        canary_source_root=str(controller.framework_root),
        commit="1234567890abcdef1234567890abcdef12345678",
    )

    assert controller.mark_canary_started(
        loaded_source_root=controller.framework_root,
        revision="1234567890ab",
    )
    assert controller._state()["phase"] == "canary_running"


def test_canary_revision_mismatch_requires_startup_rollback(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path, _Manager())
    prior = tmp_path / "prior"
    prior.mkdir()
    controller._write_state(
        phase="handoff_requested",
        canary_source_root=str(controller.framework_root),
        old_source_root=str(prior),
        commit="1" * 40,
    )

    assert not controller.mark_canary_started(
        loaded_source_root=controller.framework_root,
        revision="2" * 12,
    )
    assert controller.failed_start_rollback_candidate(
        loaded_source_root=controller.framework_root,
    ) == prior


def test_canary_publication_requires_lbx154_and_never_merges(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller = _controller(tmp_path, _Manager())
    repo = controller.framework_root
    reviewed_commit = "d" * 40
    controller._write_state(
        phase="canary_running",
        canary_source_root=str(repo),
        worktree=str(repo),
        branch="argus-self/session/incident",
        problem="observed planner failure",
        acceptance_check="pytest -q",
        incident_id="incident",
        commit=reviewed_commit,
    )
    calls: list[list[str]] = []

    def fake_run(args, *, cwd, timeout=60.0, check=True):
        calls.append(list(args))
        if args[:3] == ["/usr/bin/gh", "api", "user"]:
            stdout = "lbx154\n"
        elif args[:3] == ["/usr/bin/gh", "pr", "list"]:
            stdout = "\n"
        elif args[:3] == ["/usr/bin/gh", "pr", "create"]:
            stdout = "https://github.com/lbx154/argus-skill/pull/1\n"
        elif args[:3] == ["git", "rev-parse", "HEAD"]:
            stdout = reviewed_commit + "\n"
        elif args[:4] == ["git", "remote", "get-url", "origin"]:
            stdout = "https://github.com/lbx154/argus-skill.git\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(self_maintenance_mod, "_run", fake_run)
    monkeypatch.setattr(
        self_maintenance_mod.shutil,
        "which",
        lambda _name: "/usr/bin/gh",
    )

    url = controller.publish_after_canary(
        summary={"stopped_by": "planner_retry", "planning_cycles": 1}
    )

    assert url.endswith("/pull/1")
    assert not any("merge" in arg for call in calls for arg in call)
    push = next(call for call in calls if "push" in call)
    assert any("gh auth git-credential" in arg for arg in push)
    assert controller._state()["phase"] == "pr_open"


def test_each_manager_can_adopt_merged_main_in_its_own_canary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller = _controller(tmp_path, _Manager(action="adopt"))
    candidate = "a" * 40
    update = {
        "id": "update-1",
        "type": "framework.update_available",
        "ts": 10.0,
        "details": {
            "candidate_revision": candidate,
            "source": "human-merged origin/main",
        },
    }
    controller._append_observation(update)
    adoption = tmp_path / "adoption"
    adoption.mkdir()
    monkeypatch.setattr(controller, "_observe_upstream_update", lambda: None)
    monkeypatch.setattr(
        controller,
        "_prepare_adoption_worktree",
        lambda _candidate: adoption,
    )

    action = controller.audit_if_due(daemon_state={"stopped_by": "planner_retry"})

    assert action == f"adopt:{adoption}"
    assert controller._state()["canary_kind"] == "adoption"
    assert controller.mark_canary_started(
        loaded_source_root=adoption,
        revision=candidate[:12],
    )
    controller.framework_root = adoption.resolve()
    assert controller.publish_after_canary(
        summary={"stopped_by": "planner_retry", "planning_cycles": 1}
    ) == candidate
    assert controller._state()["phase"] == "adopted"


def test_failed_canary_requests_prior_source_rollback(tmp_path: Path) -> None:
    controller = _controller(tmp_path, _Manager())
    controller._write_state(
        phase="canary_running",
        canary_source_root=str(controller.framework_root),
        old_source_root="/prior/argus",
        canary_kind="repair",
    )

    result = controller.publish_after_canary(
        summary={"stopped_by": "supervisor_error"}
    )

    assert result == "rollback:/prior/argus"
    assert controller._state()["phase"] == "canary_failed"


def test_paused_or_failed_result_is_not_positive_canary_health(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path, _Manager())
    controller._write_state(
        phase="canary_running",
        canary_source_root=str(controller.framework_root),
        old_source_root="/prior/argus",
        canary_kind="adoption",
        commit="c" * 40,
    )

    assert controller.publish_after_canary(summary={
        "stopped_by": "paused_budget",
        "results": [{"status": "paused_budget", "success": False}],
    }) == ""
    assert controller._state()["phase"] == "canary_running"


def test_normal_restart_restores_persisted_self_managed_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller = _controller(tmp_path, _Manager())
    candidate = tmp_path / "persisted-canary"
    candidate.mkdir()
    commit = "b" * 40
    controller._write_state(
        phase="adopted",
        canary_source_root=str(candidate),
        commit=commit,
    )

    def fake_run(args, *, cwd, timeout=60.0, check=True):
        stdout = commit if args[:3] == ["git", "rev-parse", "HEAD"] else ""
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(self_maintenance_mod, "_run", fake_run)

    assert controller.source_resume_candidate(
        loaded_source_root=controller.framework_root,
    ) == candidate


def test_publication_rejects_staged_path_outside_manager_authority(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path, _Manager())
    repo, base = _publication_repo(tmp_path)
    (repo / "forbidden.py").write_text("NO = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "forbidden.py"], cwd=repo, check=True)
    controller._write_state(
        phase="queued",
        active_item_id="maintenance-1",
        incident_id="incident",
        worktree=str(repo),
        base_revision=base,
        affected_paths=["argus_skill/base.py"],
    )

    assert controller.prepare_reviewed_change({
        "item_id": "maintenance-1",
        "status": "done",
        "success": True,
        "review_status": "done",
    }) is None
    assert "outside Manager authorization" in controller._state()["error"]


def test_publication_rejects_rename_from_unauthorized_source(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path, _Manager())
    repo, base = _publication_repo(tmp_path)
    (repo / "argus_skill" / "base.py").rename(
        repo / "argus_skill" / "allowed.py"
    )
    controller._write_state(
        phase="queued",
        active_item_id="maintenance-rename",
        incident_id="incident",
        worktree=str(repo),
        base_revision=base,
        affected_paths=["argus_skill/allowed.py"],
    )

    assert controller.prepare_reviewed_change({
        "item_id": "maintenance-rename",
        "status": "done",
        "success": True,
        "review_status": "done",
    }) is None
    assert "argus_skill/base.py" in controller._state()["error"]


def test_publication_stages_new_files_before_release_manifest_and_uses_lbx154(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path, _Manager())
    repo, base = _publication_repo(tmp_path)
    (repo / "argus_skill" / "new_feature.py").write_text(
        "FEATURE = True\n",
        encoding="utf-8",
    )
    controller._write_state(
        phase="queued",
        active_item_id="maintenance-2",
        incident_id="incident",
        worktree=str(repo),
        base_revision=base,
        affected_paths=["argus_skill/new_feature.py"],
    )

    assert controller.prepare_reviewed_change({
        "item_id": "maintenance-2",
        "status": "done",
        "success": True,
        "review_status": "done",
    }) == repo
    author = subprocess.run(
        ["git", "show", "-s", "--format=%an <%ae>", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert author == "lbx154 <lbxhaixing154@sjtu.edu.cn>"
    subprocess.run(
        [sys.executable, "scripts/generate_release_manifest.py", "--check"],
        cwd=repo,
        check=True,
    )


def test_prune_keeps_active_and_rollback_worktrees_only(tmp_path: Path) -> None:
    repo, _base = _publication_repo(tmp_path)
    memory = LifeMemory.open(tmp_path / "life-prune")
    memory.init()
    project = tmp_path / "project-prune"
    project.mkdir()
    controller = DaemonSelfMaintenance(
        life_dir=memory.root,
        framework_root=repo,
        project_workdir=project,
        manager=_Manager(),
        memory=memory,
    )
    active = controller.root / "worktrees" / "active"
    obsolete = controller.root / "worktrees" / "obsolete"
    active.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "worktree", "add", "-b", "argus-self/active", str(active), "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "worktree",
            "add",
            "-b",
            "argus-self/obsolete",
            str(obsolete),
            "HEAD",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    controller._write_state(
        phase="adopted",
        canary_source_root=str(active),
        worktree=str(active),
        old_source_root=str(repo),
    )

    removed = controller.prune_obsolete_worktrees()

    assert str(obsolete) in removed
    assert active.is_dir()
    assert not obsolete.exists()
