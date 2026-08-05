from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import argus_skill.daemon.self_maintenance as self_maintenance_mod
from argus_skill.daemon.self_maintenance import (
    DaemonSelfMaintenance,
    read_self_maintenance_snapshot,
)
from argus_skill.life.memory import BacklogItem, LifeMemory


def _init_repo(path: Path, branch: str = "main") -> None:
    """Initialise a named branch on Git versions predating ``git init -b``."""
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "checkout", "-b", branch],
        cwd=path,
        check=True,
        capture_output=True,
    )


class _Manager:
    def __init__(
        self,
        action: str = "repair",
        *,
        affected_paths: tuple[str, ...] | None = None,
    ) -> None:
        self.action = action
        self.affected_paths = affected_paths or (
            "argus_skill/life/supervisor/_planning_cycle.py",
            "tests/life/test_planner_dag_enqueue.py",
        )
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
            affected_paths=self.affected_paths,
        )


def test_read_self_maintenance_snapshot_is_typed_and_fail_soft(
    tmp_path: Path,
) -> None:
    assert read_self_maintenance_snapshot(tmp_path) is None
    path = tmp_path / "self-maintenance" / "state.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        (
            '{"phase":"canary_running","maintenance_available":true,'
            '"updated_at":12.5,"last_audit_at":10.0,"pr_url":"",'
            '"publication_status":"unavailable",'
            '"publication_error":"no push permission"}'
        ),
        encoding="utf-8",
    )

    snapshot = read_self_maintenance_snapshot(tmp_path)

    assert snapshot is not None
    assert snapshot.phase == "canary_running"
    assert snapshot.maintenance_available is True
    assert snapshot.updated_at == 12.5
    assert snapshot.last_audit_at == 10.0
    assert snapshot.publication_status == "unavailable"
    assert snapshot.publication_error == "no push permission"


def test_frontend_dependency_links_are_temporary(tmp_path: Path) -> None:
    source = tmp_path / "source"
    worktree = tmp_path / "worktree"
    for relative in (
        Path("frontend/web/node_modules"),
        Path("frontend/tui/node_modules"),
    ):
        dependency_dir = source / relative
        dependency_dir.mkdir(parents=True)
        (dependency_dir / "marker").write_text("installed\n", encoding="utf-8")

    with self_maintenance_mod._frontend_dependency_links(source, worktree):
        for relative in (
            Path("frontend/web/node_modules"),
            Path("frontend/tui/node_modules"),
        ):
            target = worktree / relative
            assert target.is_symlink()
            assert (target / "marker").read_text(encoding="utf-8") == "installed\n"

    assert not (worktree / "frontend/web/node_modules").exists()
    assert not (worktree / "frontend/tui/node_modules").exists()


def _controller(tmp_path: Path, manager: _Manager) -> DaemonSelfMaintenance:
    memory = LifeMemory.open(tmp_path / "life")
    memory.init()
    project = tmp_path / "project"
    project.mkdir()
    framework = tmp_path / "framework"
    framework.mkdir()
    for relative in (
        Path("frontend/web/node_modules"),
        Path("frontend/tui/node_modules"),
    ):
        (framework / relative).mkdir(parents=True)
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
    _init_repo(repo)
    subprocess.run(["git", "config", "user.name", "seed"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "seed@example.com"],
        cwd=repo,
        check=True,
    )
    (repo / "argus_skill").mkdir()
    (repo / "scripts").mkdir()
    (repo / "frontend" / "core" / "src").mkdir(parents=True)
    (repo / "frontend" / "tui" / "bundle").mkdir(parents=True)
    (repo / "frontend" / "web" / "dist" / "assets").mkdir(parents=True)
    (repo / ".gitignore").write_text(
        "node_modules/\n*.pyc\n",
        encoding="utf-8",
    )
    (repo / "argus_skill" / "base.py").write_text("BASE = 1\n", encoding="utf-8")
    (repo / "scripts" / "generate_release_manifest.py").write_text(
        "import pathlib, subprocess, sys\n"
        "root = pathlib.Path(__file__).resolve().parents[1]\n"
        "tracked = subprocess.check_output(['git', 'ls-files'], cwd=root, text=True)\n"
        "expected = 'new-feature\\n' if 'argus_skill/new_feature.py' in tracked else 'base\\n'\n"
        "manifest = root / 'argus_skill' / 'release_manifest.json'\n"
        "generated = root / 'frontend/core/src/release.generated.ts'\n"
        "if '--check' in sys.argv:\n"
        "    valid = manifest.read_text() == expected and generated.read_text() == expected\n"
        "    raise SystemExit(0 if valid else 2)\n"
        "if '--prepare-build' not in sys.argv:\n"
        "    raise SystemExit(2)\n"
        "manifest.write_text(expected)\n"
        "generated.write_text(expected)\n",
        encoding="utf-8",
    )
    (repo / "scripts" / "build_release.py").write_text(
        "import pathlib, subprocess, sys\n"
        "root = pathlib.Path(__file__).resolve().parents[1]\n"
        "subprocess.run([\n"
        "    sys.executable,\n"
        "    'scripts/generate_release_manifest.py',\n"
        "    '--prepare-build',\n"
        "], cwd=root, check=True)\n"
        "release = (root / 'argus_skill/release_manifest.json').read_text()\n"
        "(root / 'frontend/tui/bundle/argus.mjs').write_text(release)\n"
        "(root / 'frontend/web/dist/assets/index.js').write_text(release)\n"
        "(root / 'frontend/web/dist/index.html').write_text(\n"
        "    '<script src=\"/assets/index.js\"></script>\\n'\n"
        ")\n",
        encoding="utf-8",
    )
    (repo / "argus_skill" / "release_manifest.json").write_text(
        "base\n",
        encoding="utf-8",
    )
    (repo / "frontend" / "core" / "src" / "release.generated.ts").write_text(
        "base\n",
        encoding="utf-8",
    )
    (repo / "frontend" / "tui" / "bundle" / "argus.mjs").write_text(
        "base\n",
        encoding="utf-8",
    )
    (repo / "frontend" / "web" / "dist" / "assets" / "index.js").write_text(
        "base\n",
        encoding="utf-8",
    )
    (repo / "frontend" / "web" / "dist" / "index.html").write_text(
        '<script src="/assets/index.js"></script>\n',
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
    _init_repo(worktree)
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


def test_unmerged_local_repair_blocks_a_new_maintenance_audit(
    tmp_path: Path,
) -> None:
    manager = _Manager()
    controller = _controller(tmp_path, manager)
    controller._write_state(
        phase="local_active",
        publication_status="awaiting_approval",
        commit="a" * 40,
    )
    controller.observe({
        "type": "life.planner.error",
        "ts": 10.0,
        "error": "another failure",
    })

    assert controller.audit_if_due(daemon_state={}) == ""
    assert manager.calls == 0


@pytest.mark.parametrize("status", ["paused_operator", "failed"])
def test_operator_question_repair_blocks_a_new_maintenance_audit(
    tmp_path: Path,
    status: str,
) -> None:
    manager = _Manager()
    controller = _controller(tmp_path, manager)
    item = controller.memory.backlog.add(BacklogItem.new(
        title="Repair framework",
        objective="Repair the framework.",
        tags=["framework_maintenance"],
    ))
    controller.memory.backlog.update(
        item.id,
        status=status,
        pending_question="May this boundary change proceed?",
    )
    controller._write_state(
        phase="review_rejected",
        active_item_id=item.id,
        event_audit_pending=True,
    )

    assert controller.audit_if_due(daemon_state={}) == ""
    assert manager.calls == 0


def test_unsafe_manager_paths_fail_visibly_and_can_be_corrected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = _Manager(affected_paths=("/tmp/argus-skill (planner module)",))
    controller = _controller(tmp_path, manager)
    events: list[dict[str, object]] = []
    controller.on_event = events.append
    controller.observe({
        "type": "life.planner.error",
        "ts": 10.0,
        "error": "schema failure",
    })

    assert controller.audit_if_due(daemon_state={}) == ""

    state = controller._state()
    failed_incident = state["last_incident_id"]
    assert state["phase"] == "preparation_failed"
    assert state["error"] == "Manager returned unsafe affected paths"
    assert events[-1]["type"] == "manager.self_maintenance.preparation_failed"
    assert events[-1]["affected_paths"] == [
        "/tmp/argus-skill (planner module)"
    ]

    manager.affected_paths = ("argus_skill/life/supervisor/_core.py",)
    controller._write_state(event_audit_pending=True)
    retried_incidents: list[str] = []

    def fail_after_retry(incident_id: str):
        retried_incidents.append(incident_id)
        raise ValueError("stop after retry")

    monkeypatch.setattr(
        controller,
        "_prepare_worktree",
        fail_after_retry,
    )

    assert controller.audit_if_due(daemon_state={}) == ""
    assert retried_incidents
    assert retried_incidents[0] != failed_incident


def test_transient_preparation_failure_retries_the_same_incident(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller = _controller(tmp_path, _Manager())
    controller.observe({
        "type": "life.planner.error",
        "ts": 10.0,
        "error": "schema failure",
    })
    attempts: list[str] = []

    def timeout(incident_id: str):
        attempts.append(incident_id)
        raise subprocess.TimeoutExpired(["git", "fetch"], 120)

    monkeypatch.setattr(controller, "_prepare_worktree", timeout)

    assert controller.audit_if_due(daemon_state={}) == ""
    controller._write_state(event_audit_pending=True)
    assert controller.audit_if_due(daemon_state={}) == ""

    assert len(attempts) == 2
    assert attempts[0] == attempts[1]


def test_wiki_hook_warning_triggers_manager_audit(tmp_path: Path) -> None:
    controller = _controller(tmp_path, _Manager(action="no_action"))

    controller.observe({
        "type": "wiki.hook.warning",
        "ts": 10.0,
        "operation": "rebuild_indexes",
        "error": "ValueError: invalid timestamp",
    })

    state = controller._state()
    assert state["event_audit_pending"] is True
    [observation] = state["observations"]
    assert observation["type"] == "wiki.hook.warning"
    assert observation["details"]["operation"] == "rebuild_indexes"


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


def test_private_worktree_uses_local_main_when_fetch_times_out(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "framework"
    _init_repo(repo)
    subprocess.run(["git", "config", "user.name", "seed"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "seed@example.com"],
        cwd=repo,
        check=True,
    )
    (repo / "README").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True)
    main_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "checkout", "-b", "running-vertical"],
        cwd=repo,
        check=True,
    )
    (repo / "README").write_text("vertical\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "vertical"], cwd=repo, check=True)

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
    real_run = self_maintenance_mod._run

    def timeout_fetch(args, **kwargs):
        if args[:3] == ["git", "fetch", "origin"]:
            raise subprocess.TimeoutExpired(args, 120)
        return real_run(args, **kwargs)

    monkeypatch.setattr(self_maintenance_mod, "_run", timeout_fetch)
    worktree, branch = controller._prepare_worktree("incident123")

    assert branch.startswith("argus-self/")
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == main_revision
    assert (worktree / "README").read_text(encoding="utf-8") == "seed\n"
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


def test_private_worktree_prefers_fetched_origin_main(tmp_path: Path) -> None:
    upstream = tmp_path / "upstream"
    subprocess.run(["git", "init", "--bare", str(upstream)], check=True)
    repo = tmp_path / "framework"
    _init_repo(repo)
    subprocess.run(["git", "config", "user.name", "seed"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "seed@example.com"],
        cwd=repo,
        check=True,
    )
    (repo / "README").write_text("main\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", str(upstream)],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "checkout", "-b", "running-vertical"],
        cwd=repo,
        check=True,
    )
    (repo / "README").write_text("vertical\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "vertical"], cwd=repo, check=True)
    origin_main = subprocess.run(
        ["git", "rev-parse", "origin/main"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

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

    worktree, _branch = controller._prepare_worktree("incident456")

    assert subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == origin_main


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


def test_restarted_canary_gets_a_fresh_stability_window(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller = _controller(tmp_path, _Manager())
    commit = "1" * 40
    controller._write_state(
        phase="canary_running",
        canary_source_root=str(controller.framework_root),
        commit=commit,
        canary_started_at=1.0,
        canary_pid=123,
    )
    monkeypatch.setattr(self_maintenance_mod.time, "time", lambda: 500.0)

    assert controller.mark_canary_started(
        loaded_source_root=controller.framework_root,
        revision=commit[:12],
    )
    state = controller._state()
    assert state["canary_started_at"] == 500.0
    assert state["canary_pid"] == self_maintenance_mod.os.getpid()


def test_canary_publication_uses_current_authorized_github_identity(
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
        if args[:3] == [
            "/usr/bin/gh",
            "api",
            "repos/lbx154/argus-skill",
        ]:
            stdout = "true\n"
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

    # Publication now needs the operator's approval (2026-07-26). This test is
    # about *how* an approved fix is published — which identity, which commands
    # — so grant the approval and keep asserting the same thing.
    assert controller.approve_publication(reviewed_commit) == ""

    url = controller.publish_after_canary(
        summary={"stopped_by": "planner_retry", "planning_cycles": 1}
    )

    assert url.endswith("/pull/1")
    assert not any("merge" in arg for call in calls for arg in call)
    push = next(call for call in calls if "push" in call)
    assert any("gh auth git-credential" in arg for arg in push)
    assert controller._state()["phase"] == "pr_open"


def test_canary_remains_local_without_github_account(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller = _controller(tmp_path, _Manager())
    repo = controller.framework_root
    reviewed_commit = "e" * 40
    controller._write_state(
        phase="canary_running",
        canary_source_root=str(repo),
        worktree=str(repo),
        branch="argus-self/session/incident",
        incident_id="incident",
        commit=reviewed_commit,
    )

    def fake_run(args, *, cwd, timeout=60.0, check=True):
        stdout = (
            reviewed_commit + "\n"
            if args[:3] == ["git", "rev-parse", "HEAD"]
            else ""
        )
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(self_maintenance_mod, "_run", fake_run)
    monkeypatch.setattr(self_maintenance_mod.shutil, "which", lambda _name: None)

    result = controller.publish_after_canary(
        summary={"stopped_by": "planner_retry", "planning_cycles": 1}
    )

    state = controller._state()
    assert result == reviewed_commit
    assert state["phase"] == "local_active"
    assert state["publication_status"] == "unavailable"
    assert "GitHub CLI is unavailable" in state["publication_error"]


def test_canary_remains_local_without_repository_permission(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller = _controller(tmp_path, _Manager())
    repo = controller.framework_root
    reviewed_commit = "f" * 40
    controller._write_state(
        phase="canary_running",
        canary_source_root=str(repo),
        worktree=str(repo),
        branch="argus-self/session/incident",
        incident_id="incident",
        commit=reviewed_commit,
    )
    calls: list[list[str]] = []

    def fake_run(args, *, cwd, timeout=60.0, check=True):
        calls.append(list(args))
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            stdout = reviewed_commit + "\n"
        elif args[:4] == ["git", "remote", "get-url", "origin"]:
            stdout = "https://github.com/lbx154/argus-skill.git\n"
        elif args[:3] == [
            "/usr/bin/gh",
            "api",
            "repos/lbx154/argus-skill",
        ]:
            stdout = "false\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(self_maintenance_mod, "_run", fake_run)
    monkeypatch.setattr(
        self_maintenance_mod.shutil,
        "which",
        lambda _name: "/usr/bin/gh",
    )

    result = controller.publish_after_canary(
        summary={"stopped_by": "planner_retry", "planning_cycles": 1}
    )

    state = controller._state()
    assert result == reviewed_commit
    assert state["phase"] == "local_active"
    assert state["publication_status"] == "unavailable"
    assert "no push permission" in state["publication_error"]
    assert not any("push" in call for call in calls)


def test_legacy_publication_failure_migrates_to_local_active(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller = _controller(tmp_path, _Manager())
    repo = controller.framework_root
    reviewed_commit = "a" * 40
    controller._write_state(
        phase="publication_failed",
        canary_source_root=str(repo),
        worktree=str(repo),
        branch="argus-self/session/incident",
        incident_id="incident",
        commit=reviewed_commit,
        error="legacy push permission denied",
    )

    def fake_run(args, *, cwd, timeout=60.0, check=True):
        stdout = (
            reviewed_commit + "\n"
            if args[:3] == ["git", "rev-parse", "HEAD"]
            else ""
        )
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(self_maintenance_mod, "_run", fake_run)
    monkeypatch.setattr(self_maintenance_mod.shutil, "which", lambda _name: None)

    result = controller.publish_after_canary(summary={})

    state = controller._state()
    assert result == reviewed_commit
    assert state["phase"] == "local_active"
    assert state["publication_status"] == "unavailable"
    assert state["error"] == ""


def test_closed_upstream_pr_requests_durable_local_rollback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller = _controller(tmp_path, _Manager())
    worktree = controller.framework_root
    prior = tmp_path / "prior-argus"
    prior.mkdir()
    controller._write_state(
        phase="pr_open",
        worktree=str(worktree),
        canary_source_root=str(worktree),
        pr_url="https://github.com/example/argus-skill/pull/7",
        old_source_root=str(prior),
    )

    monkeypatch.setattr(
        self_maintenance_mod.shutil,
        "which",
        lambda _name: "/usr/bin/gh",
    )
    monkeypatch.setattr(
        self_maintenance_mod,
        "_run",
        lambda args, **_kwargs: subprocess.CompletedProcess(
            args,
            0,
            "CLOSED\n",
            "",
        ),
    )

    result = controller.reconcile_pull_request()

    state = controller._state()
    assert result == f"rollback:{prior}"
    assert state["phase"] == "pr_closed"
    assert state["publication_status"] == "closed"
    assert state["error"] == ""
    assert controller.failed_start_rollback_candidate(
        loaded_source_root=worktree,
    ) == prior

    controller.mark_handoff_failed("standby did not start")
    state = controller._state()
    assert state["phase"] == "pr_closed"
    assert state["handoff_error"] == "standby did not start"
    assert controller.failed_start_rollback_candidate(
        loaded_source_root=worktree,
    ) == prior
    assert controller.failed_start_rollback_candidate(
        loaded_source_root=prior,
    ) is None
    state = controller._state()
    assert state["phase"] == "rolled_back"
    assert state["handoff_error"] == ""


def test_legacy_closed_publication_state_migrates_to_rollback(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path, _Manager())
    candidate = controller.framework_root
    prior = tmp_path / "prior-legacy"
    prior.mkdir()
    controller._write_state(
        phase="local_active",
        publication_status="closed",
        canary_source_root=str(candidate),
        old_source_root=str(prior),
    )

    assert controller.failed_start_rollback_candidate(
        loaded_source_root=candidate,
    ) == prior
    assert controller._state()["phase"] == "pr_closed"


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


def test_upstream_adoption_requires_verified_merged_pr(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller = _controller(tmp_path, _Manager())

    def fake_run(args, *, cwd, timeout=60.0, check=True):
        if args[:4] == ["git", "remote", "get-url", "origin"]:
            stdout = "https://github.com/lbx154/argus-skill.git\n"
        else:
            stdout = json.dumps([{
                "number": 42,
                "html_url": "https://github.com/lbx154/argus-skill/pull/42",
                "title": "reviewed repair",
                "body": "Reviewer evidence",
                "merged_at": "2026-07-21T00:00:00Z",
                "merged_by": {"login": "lbx154"},
            }])
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(self_maintenance_mod, "_run", fake_run)
    monkeypatch.setattr(
        self_maintenance_mod.shutil,
        "which",
        lambda _name: "/usr/bin/gh",
    )

    evidence = controller._merged_pr_evidence("a" * 40)

    assert evidence is not None
    assert evidence["number"] == 42
    assert evidence["merged_by"] == "lbx154"


def test_direct_main_commit_is_not_adoption_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    controller = _controller(tmp_path, _Manager())

    def fake_run(args, *, cwd, timeout=60.0, check=True):
        stdout = (
            "https://github.com/lbx154/argus-skill.git\n"
            if args[:4] == ["git", "remote", "get-url", "origin"]
            else "[]"
        )
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(self_maintenance_mod, "_run", fake_run)
    monkeypatch.setattr(
        self_maintenance_mod.shutil,
        "which",
        lambda _name: "/usr/bin/gh",
    )

    assert controller._merged_pr_evidence("b" * 40) is None


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


def test_stable_idle_canary_is_accepted_locally(
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
        incident_id="incident",
        commit=reviewed_commit,
        canary_started_at=(
            time.time()
            - self_maintenance_mod._IDLE_CANARY_STABILITY_SECONDS
            - 1
        ),
    )

    def fake_run(args, *, cwd, timeout=60.0, check=True):
        stdout = (
            reviewed_commit + "\n"
            if args[:3] == ["git", "rev-parse", "HEAD"]
            else ""
        )
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(self_maintenance_mod, "_run", fake_run)
    monkeypatch.setattr(self_maintenance_mod.shutil, "which", lambda _name: None)

    result = controller.publish_after_canary(
        summary={"stopped_by": "backlog_empty", "results": []}
    )

    assert result == reviewed_commit
    assert controller._state()["phase"] == "local_active"


def test_recent_idle_canary_waits_for_stability(tmp_path: Path) -> None:
    controller = _controller(tmp_path, _Manager())
    controller._write_state(
        phase="canary_running",
        canary_source_root=str(controller.framework_root),
        worktree=str(controller.framework_root),
        branch="argus-self/session/incident",
        commit="d" * 40,
        canary_started_at=time.time(),
    )

    assert controller.publish_after_canary(
        summary={"stopped_by": "backlog_empty", "results": []}
    ) == ""
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
        phase="local_active",
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


def test_failed_source_resume_preserves_publication_state(
    tmp_path: Path,
) -> None:
    manager = _Manager()
    controller = _controller(tmp_path, manager)
    reviewed, commit = _publication_repo(tmp_path)
    controller._write_state(
        phase="local_active",
        publication_status="awaiting_approval",
        canary_source_root=str(reviewed),
        commit=commit,
    )

    controller.mark_handoff_failed("persisted runtime did not reach standby")

    state = controller._state()
    assert state["phase"] == "local_active"
    assert state["publication_status"] == "awaiting_approval"
    assert state["handoff_error"] == "persisted runtime did not reach standby"
    controller._write_state(event_audit_pending=True)
    assert controller.audit_if_due(daemon_state={}) == ""
    assert manager.calls == 0

    assert controller.source_resume_candidate(
        loaded_source_root=reviewed,
    ) is None
    assert controller._state()["handoff_error"] == ""


def test_dirty_resume_candidate_is_rejected_before_its_validator_runs(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path, _Manager())
    candidate, commit = _publication_repo(tmp_path)
    marker = tmp_path / "untrusted-validator-ran"
    validator = candidate / "scripts" / "generate_release_manifest.py"
    validator.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
        encoding="utf-8",
    )
    controller._write_state(
        phase="local_active",
        canary_source_root=str(candidate),
        commit=commit,
        handoff_error="resume failed",
    )

    assert controller.source_resume_candidate(
        loaded_source_root=candidate,
    ) is None
    assert not marker.exists()
    assert controller._state()["handoff_error"] == "resume failed"


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


def test_reviewed_operator_continuation_retains_maintenance_ownership(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path, _Manager())
    repo, base = _publication_repo(tmp_path)
    (repo / "argus_skill" / "new_feature.py").write_text(
        "FEATURE = True\n",
        encoding="utf-8",
    )
    blocked = controller.memory.backlog.add(BacklogItem.new(
        title="Repair framework",
        objective="Repair the framework.",
        tags=["framework_maintenance"],
        execution_workdir=str(repo),
    ))
    blocked.status = "paused_operator"
    blocked.pending_question = "May this repair proceed?"
    controller.memory.backlog.update(
        blocked.id,
        status="paused_operator",
        pending_question=blocked.pending_question,
    )
    _original, continuation = controller.memory.backlog.continue_with_operator_reply(
        blocked.id,
        "Proceed without expanding scope.",
        manager_decision="Proceed in the authorized worktree.",
    )
    assert continuation is not None
    controller._write_state(
        phase="queued",
        active_item_id=blocked.id,
        incident_id="incident",
        worktree=str(repo),
        base_revision=base,
        affected_paths=["argus_skill/new_feature.py"],
    )

    assert controller.prepare_reviewed_change({
        "item_id": continuation.id,
        "status": "done",
        "success": True,
        "review_status": "done",
    }) == repo
    assert controller._state()["active_item_id"] == continuation.id


def test_manager_directory_path_does_not_authorize_descendants(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path, _Manager())
    repo, base = _publication_repo(tmp_path)
    (repo / "argus_skill" / "base.py").write_text("BASE = 2\n", encoding="utf-8")
    controller._write_state(
        phase="queued",
        active_item_id="maintenance-prefix",
        incident_id="incident",
        worktree=str(repo),
        base_revision=base,
        affected_paths=["argus_skill"],
    )

    assert controller.prepare_reviewed_change({
        "item_id": "maintenance-prefix",
        "status": "done",
        "success": True,
        "review_status": "done",
    }) is None
    assert "argus_skill/base.py" in controller._state()["error"]


def test_generated_output_symlink_is_rejected_before_build(tmp_path: Path) -> None:
    controller = _controller(tmp_path, _Manager())
    repo, base = _publication_repo(tmp_path)
    (repo / "argus_skill" / "new_feature.py").write_text(
        "FEATURE = True\n",
        encoding="utf-8",
    )
    outside = tmp_path / "outside-manifest"
    outside.write_text("sentinel\n", encoding="utf-8")
    manifest = repo / "argus_skill" / "release_manifest.json"
    manifest.unlink()
    manifest.symlink_to(outside)
    controller._write_state(
        phase="queued",
        active_item_id="maintenance-symlink",
        incident_id="incident",
        worktree=str(repo),
        base_revision=base,
        affected_paths=["argus_skill/new_feature.py"],
    )

    assert controller.prepare_reviewed_change({
        "item_id": "maintenance-symlink",
        "status": "done",
        "success": True,
        "review_status": "done",
    }) is None
    assert outside.read_text(encoding="utf-8") == "sentinel\n"
    assert "unsafe generated output path" in controller._state()["error"]


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


def test_publication_stages_new_files_and_preserves_repository_identity(
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
    assert author == "seed <seed@example.com>"
    subprocess.run(
        [sys.executable, "scripts/generate_release_manifest.py", "--check"],
        cwd=repo,
        check=True,
    )


def test_publication_cleans_ignored_worktree_artifacts(tmp_path: Path) -> None:
    controller = _controller(tmp_path, _Manager())
    repo, base = _publication_repo(tmp_path)
    (repo / "argus_skill" / "new_feature.py").write_text(
        "FEATURE = True\n",
        encoding="utf-8",
    )
    malicious = repo / "frontend" / "web" / "node_modules"
    malicious.mkdir(parents=True)
    (malicious / "vite").write_text("malicious\n", encoding="utf-8")
    bytecode = repo / "scripts" / "sitecustomize.pyc"
    bytecode.write_bytes(b"untrusted bytecode")
    controller._write_state(
        phase="queued",
        active_item_id="maintenance-dependencies",
        incident_id="incident",
        worktree=str(repo),
        base_revision=base,
        affected_paths=["argus_skill/new_feature.py"],
    )

    result = controller.prepare_reviewed_change({
        "item_id": "maintenance-dependencies",
        "status": "done",
        "success": True,
        "review_status": "done",
    })
    assert result == repo, controller._state()
    assert not malicious.exists()
    assert not bytecode.exists()


def test_next_repair_reuses_persisted_trusted_dependencies(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path, _Manager())
    dependency_root = controller.framework_root
    current_canary = tmp_path / "current-canary"
    current_canary.mkdir()
    controller.framework_root = current_canary
    repo, base = _publication_repo(tmp_path)
    (repo / "argus_skill" / "new_feature.py").write_text(
        "FEATURE = True\n",
        encoding="utf-8",
    )
    controller._write_state(
        phase="queued",
        active_item_id="maintenance-second-repair",
        incident_id="incident",
        worktree=str(repo),
        base_revision=base,
        dependency_root=str(dependency_root),
        affected_paths=["argus_skill/new_feature.py"],
    )

    assert controller.prepare_reviewed_change({
        "item_id": "maintenance-second-repair",
        "status": "done",
        "success": True,
        "review_status": "done",
    }) == repo


def test_publication_removes_role_local_runtime_artifacts(tmp_path: Path) -> None:
    controller = _controller(tmp_path, _Manager())
    repo, base = _publication_repo(tmp_path)
    (repo / "argus_skill" / "new_feature.py").write_text(
        "FEATURE = True\n",
        encoding="utf-8",
    )
    (repo / ".autors" / "maintenance" / "wiki").mkdir(parents=True)
    (repo / ".autors" / "maintenance" / "wiki" / "state.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    runtime = repo / ".argus-self-maintenance-runtime" / "copilot-home"
    runtime.mkdir(parents=True)
    (runtime / "session.json").write_text("{}\n", encoding="utf-8")
    controller._write_state(
        phase="queued",
        active_item_id="maintenance-runtime",
        incident_id="incident",
        worktree=str(repo),
        base_revision=base,
        affected_paths=["argus_skill/new_feature.py"],
    )

    assert controller.prepare_reviewed_change({
        "item_id": "maintenance-runtime",
        "status": "done",
        "success": True,
        "review_status": "done",
    }) == repo
    assert not (repo / ".autors").exists()
    assert not (repo / ".argus-self-maintenance-runtime").exists()
    committed = subprocess.run(
        ["git", "show", "--pretty=", "--name-only", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert ".autors" not in committed
    assert ".argus-self-maintenance-runtime" not in committed


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
