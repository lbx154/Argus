from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import argus_skill.daemon.self_maintenance as maintenance_mod
from argus_skill.daemon.self_maintenance import DaemonSelfMaintenance
from argus_skill.life.memory import LifeMemory
from tests.daemon.test_self_maintenance import (
    _commit_repo,
    _init_repo,
    _Manager,
    _publication_repo,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _wheel_controller(
    tmp_path: Path,
    manager: _Manager | None = None,
) -> tuple[DaemonSelfMaintenance, Path]:
    memory = LifeMemory.open(tmp_path / "life")
    memory.init()
    runtime = tmp_path / "site-packages" / "argus_skill"
    runtime.mkdir(parents=True)
    (runtime / "runtime.py").write_text("wheel\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    controller = DaemonSelfMaintenance(
        life_dir=memory.root,
        framework_root=runtime,
        project_workdir=project,
        manager=manager or _Manager(),
        memory=memory,
    )
    assert controller.preflight_isolation(force=True) is False
    assert controller._state()["access_mode"] == "full"
    return controller, runtime


def _official_with_locks(tmp_path: Path) -> tuple[Path, str]:
    repo, _base = _publication_repo(tmp_path)
    lock = (
        '{"name":"argus-test","version":"1.0.0","lockfileVersion":3,'
        '"requires":true,"packages":{"":{"name":"argus-test","version":"1.0.0"}}}\n'
    )
    for relative in (Path("frontend/web"), Path("frontend/tui")):
        root = repo / relative
        root.mkdir(parents=True, exist_ok=True)
        (root / "package.json").write_text(
            '{"name":"argus-test","version":"1.0.0"}\n',
            encoding="utf-8",
        )
        (root / "package-lock.json").write_text(lock, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add frontend locks")
    return repo, _git(repo, "rev-parse", "HEAD")


def _packaged_clone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    with_locks: bool = False,
) -> tuple[DaemonSelfMaintenance, Path, Path, str, str]:
    upstream, base = (
        _official_with_locks(tmp_path)
        if with_locks
        else _publication_repo(tmp_path)
    )
    monkeypatch.setattr(maintenance_mod, "PUBLIC_REPOSITORY", str(upstream))
    controller, runtime = _wheel_controller(tmp_path)
    checkout, branch = controller._prepare_packaged_repair_clone("incident123")
    return controller, runtime, checkout, branch, base


def test_standalone_clone_is_exact_official_and_disables_checkout_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    sentinel = tmp_path / "hook-ran"
    (hooks / "post-checkout").write_text(
        f"#!/bin/sh\necho ran > {sentinel}\n",
        encoding="utf-8",
    )
    (hooks / "post-checkout").chmod(0o755)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(hooks))

    controller, runtime, checkout, branch, base = _packaged_clone(
        tmp_path,
        monkeypatch,
    )

    assert checkout == controller.root / "repairs" / "incident123"
    assert _git(checkout, "rev-parse", "--show-toplevel") == str(checkout)
    assert _git(checkout, "config", "--get", "remote.origin.url") == str(
        tmp_path / "publication-repo"
    )
    assert _git(checkout, "branch", "--show-current") == branch
    assert _git(checkout, "rev-parse", "HEAD") == base
    assert _git(checkout, "rev-parse", "origin/main") == base
    assert _git(checkout, "status", "--porcelain") == ""
    assert not (controller.root / "repairs" / ".clone").exists()
    assert not sentinel.exists()
    assert (runtime / "runtime.py").read_text(encoding="utf-8") == "wheel\n"


def test_interrupted_clone_removes_only_staging_then_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream, _base = _publication_repo(tmp_path)
    monkeypatch.setattr(maintenance_mod, "PUBLIC_REPOSITORY", str(upstream))
    controller, _runtime = _wheel_controller(tmp_path)
    real_run = maintenance_mod._run

    def interrupt(args, **kwargs):
        if "clone" in args:
            Path(args[-1]).mkdir()
            (Path(args[-1]) / "partial").write_text("partial\n", encoding="utf-8")
            raise subprocess.TimeoutExpired(args, 120)
        return real_run(args, **kwargs)

    monkeypatch.setattr(maintenance_mod, "_run", interrupt)
    with pytest.raises(subprocess.TimeoutExpired):
        controller._prepare_packaged_repair_clone("incident123")
    assert not (controller.root / "repairs" / ".clone").exists()
    assert not (controller.root / "repairs" / "incident123").exists()

    monkeypatch.setattr(maintenance_mod, "_run", real_run)
    checkout, _branch = controller._prepare_packaged_repair_clone("incident123")
    assert checkout.is_dir()


def test_offline_clone_keeps_repair_pending_for_scheduled_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _Manager()
    controller, _runtime = _wheel_controller(tmp_path, manager)
    ready = tmp_path / "ready"
    _init_repo(ready)
    _commit_repo(ready)
    attempts: list[str] = []

    def prepare(incident_id: str):
        attempts.append(incident_id)
        if len(attempts) == 1:
            raise subprocess.TimeoutExpired(["git", "clone"], 120)
        return ready, "argus-self/session/incident"

    monkeypatch.setattr(controller, "_prepare_packaged_repair_clone", prepare)
    controller.observe({"type": "life.planner.error", "error": "wheel defect"})

    assert controller.audit_if_due(daemon_state={}) == ""
    state = controller._state()
    assert state["phase"] == "repair_pending"
    assert not state.get("adjudicated_observation_ids")

    controller._write_state(last_audit_at=0.0)
    assert controller.audit_if_due(daemon_state={})
    assert attempts == [attempts[0], attempts[0]]
    assert controller._state()["phase"] == "queued"


def test_preparing_clone_retries_after_crash_before_backlog_item(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream, _base = _publication_repo(tmp_path)
    monkeypatch.setattr(maintenance_mod, "PUBLIC_REPOSITORY", str(upstream))
    controller, _runtime = _wheel_controller(tmp_path, _Manager())
    controller.observe({"type": "life.planner.error", "error": "queue crash"})
    real_add = controller.memory.backlog.add
    monkeypatch.setattr(
        controller.memory.backlog,
        "add",
        lambda _item: (_ for _ in ()).throw(RuntimeError("crash before item")),
    )

    with pytest.raises(RuntimeError, match="before item"):
        controller.audit_if_due(daemon_state={})
    preparing = controller._state()
    prepared = Path(preparing["worktree"])
    assert preparing["phase"] == "preparing"
    assert prepared.is_dir()
    assert controller.memory.backlog.all() == []
    assert not preparing.get("adjudicated_observation_ids")

    monkeypatch.setattr(controller.memory.backlog, "add", real_add)
    item_id = controller.audit_if_due(daemon_state={})
    assert item_id
    assert controller._state()["phase"] == "queued"
    assert Path(controller._state()["worktree"]) == prepared
    assert len(controller.memory.backlog.all()) == 1


def test_preparing_state_adopts_item_after_crash_before_queued_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream, _base = _publication_repo(tmp_path)
    monkeypatch.setattr(maintenance_mod, "PUBLIC_REPOSITORY", str(upstream))
    manager = _Manager()
    controller, _runtime = _wheel_controller(tmp_path, manager)
    controller.observe({"type": "life.planner.error", "error": "state crash"})
    real_write = controller._write_state
    crashed = False

    def write(**updates):
        nonlocal crashed
        if updates.get("phase") == "queued" and not crashed:
            crashed = True
            raise RuntimeError("crash after item")
        return real_write(**updates)

    monkeypatch.setattr(controller, "_write_state", write)
    with pytest.raises(RuntimeError, match="after item"):
        controller.audit_if_due(daemon_state={})
    [item] = controller.memory.backlog.all()
    assert controller._state()["phase"] == "preparing"

    monkeypatch.setattr(controller, "_write_state", real_write)
    assert controller.audit_if_due(daemon_state={}) == item.id
    assert controller._state()["active_item_id"] == item.id
    assert controller._state()["phase"] == "queued"
    assert len(controller.memory.backlog.all()) == 1
    assert manager.calls == 1


def test_frozen_runtime_keeps_read_only_release_update_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(maintenance_mod.sys, "frozen", True, raising=False)
    manager = _Manager()
    controller, _runtime = _wheel_controller(tmp_path, manager)
    monkeypatch.setattr(
        controller,
        "_prepare_packaged_repair_clone",
        lambda _incident: pytest.fail("frozen runtime must not clone"),
    )
    controller.observe({"type": "life.planner.error", "error": "bundled defect"})

    assert controller.audit_if_due(daemon_state={}) == ""
    state = controller._state()
    assert state["phase"] == "release_update_required"
    assert state["maintenance_mode"] == "release_update"
    assert state.get("adjudicated_observation_ids")
    assert controller.memory.backlog.all() == []
    assert manager.kwargs[-1]["read_only"] is True


def test_packaged_review_rejects_missing_reviewer_and_out_of_scope_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _runtime, checkout, branch, base = _packaged_clone(
        tmp_path,
        monkeypatch,
    )
    controller._write_state(
        phase="queued",
        active_item_id="repair",
        incident_id="incident123",
        worktree=str(checkout),
        branch=branch,
        base_revision=base,
        affected_paths=["argus_skill/base.py"],
        repair_mode="packaged_clone",
    )
    rejected = {
        "item_id": "repair",
        "status": "done",
        "success": True,
        "review_status": "failed",
    }
    assert controller.prepare_reviewed_change(rejected) is None
    assert controller._state()["phase"] == "review_rejected"

    (checkout / "forbidden.py").write_text("FORBIDDEN = True\n", encoding="utf-8")
    accepted = {**rejected, "review_status": "done"}
    assert controller.prepare_reviewed_change(accepted) is None
    assert "outside Manager authorization" in controller._state()["error"]
    assert _git(checkout, "rev-parse", "HEAD") == base


def test_packaged_review_rejects_authorized_python_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    require_symlink_support,
) -> None:
    controller, _runtime, checkout, branch, base = _packaged_clone(
        tmp_path,
        monkeypatch,
    )
    outside = tmp_path / "outside.py"
    outside.write_text("VALUE = 1\n", encoding="utf-8")
    changed = checkout / "argus_skill" / "authorized.py"
    changed.symlink_to(outside)
    controller._write_state(
        phase="queued",
        active_item_id="repair",
        incident_id="incident123",
        worktree=str(checkout),
        branch=branch,
        base_revision=base,
        affected_paths=["argus_skill/authorized.py"],
        repair_mode="packaged_clone",
    )

    assert controller.prepare_reviewed_change({
        "item_id": "repair",
        "status": "done",
        "success": True,
        "review_status": "done",
    }) is None
    assert "symbolic link" in controller._state()["error"]
    assert outside.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_symlink_parent_is_rejected_by_changed_path_guard(
    tmp_path: Path,
    require_symlink_support,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_repo(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "linked").symlink_to(outside, target_is_directory=True)

    error = maintenance_mod._repair_symlink_error(
        repo,
        {"linked/authorized.py"},
    )

    assert "through symbolic link" in error


def test_reviewed_release_reinstalls_dependencies_and_requests_wheel_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, runtime, checkout, branch, base = _packaged_clone(
        tmp_path,
        monkeypatch,
        with_locks=True,
    )
    changed = checkout / "frontend" / "web" / "src" / "repair.ts"
    changed.parent.mkdir(parents=True)
    changed.write_text("export const repaired = true;\n", encoding="utf-8")
    installed = tmp_path / "installed"
    (installed / "argus_skill" / "release_tools").mkdir(parents=True)
    (installed / "argus_skill" / "__init__.py").write_text("", encoding="utf-8")
    (installed / "argus_skill" / "release_tools" / "__init__.py").write_text(
        "raise RuntimeError('installed wheel tooling loaded')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(installed))
    hook_sentinel = tmp_path / "commit-hook-ran"
    pre_commit = checkout / ".git" / "hooks" / "pre-commit"
    pre_commit.write_text(
        f"#!/bin/sh\necho ran > {hook_sentinel}\n",
        encoding="utf-8",
    )
    pre_commit.chmod(0o755)
    controller._write_state(
        phase="queued",
        active_item_id="repair",
        incident_id="incident123",
        worktree=str(checkout),
        branch=branch,
        base_revision=base,
        affected_paths=["frontend/web/src/repair.ts"],
        repair_mode="packaged_clone",
    )
    real_run = maintenance_mod._run
    real_which = maintenance_mod.shutil.which
    npm_roots: list[Path] = []
    python_envs: list[dict[str, str]] = []

    def run(args, **kwargs):
        if args == ["/trusted/npm", "ci"]:
            npm_roots.append(kwargs["cwd"])
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == maintenance_mod.sys.executable:
            python_envs.append(kwargs["env"])
        return real_run(args, **kwargs)

    monkeypatch.setattr(maintenance_mod, "_run", run)
    monkeypatch.setattr(
        maintenance_mod.shutil,
        "which",
        lambda name: "/trusted/npm" if name == "npm" else real_which(name),
    )
    monkeypatch.setattr(
        maintenance_mod,
        "release_identity",
        lambda _root: {"release_id": "wheel-release-old"},
    )
    assert npm_roots == []

    candidate = controller.prepare_reviewed_change({
        "item_id": "repair",
        "status": "done",
        "success": True,
        "review_status": "done",
    })

    assert candidate == checkout
    assert npm_roots == [
        checkout / "frontend" / "web",
        checkout / "frontend" / "tui",
    ]
    assert python_envs
    assert all(
        env["PYTHONPATH"].split(os.pathsep, 1)[0] == str(checkout)
        for env in python_envs
    )
    state = controller._state()
    assert state["phase"] == "handoff_requested"
    assert state["old_source_root"] == str(runtime.resolve())
    assert state["old_source_release_id"] == "wheel-release-old"
    assert state["canary_source_root"] == str(checkout)
    assert state["release_artifacts_built"] is True
    assert _git(checkout, "rev-parse", "HEAD^") == base
    assert _git(checkout, "status", "--porcelain") == ""
    assert not hook_sentinel.exists()
    assert (runtime / "runtime.py").read_text(encoding="utf-8") == "wheel\n"


def test_packaged_restart_requires_exact_clone_and_canary_rolls_back_to_wheel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, runtime, checkout, branch, base = _packaged_clone(
        tmp_path,
        monkeypatch,
    )
    (checkout / "argus_skill" / "base.py").write_text("BASE = 2\n", encoding="utf-8")
    _git(checkout, "add", "-A")
    _git(
        checkout,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.com",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-m",
        "reviewed",
    )
    commit = _git(checkout, "rev-parse", "HEAD")
    controller._write_state(
        phase="handoff_requested",
        repair_mode="packaged_clone",
        worktree=str(checkout),
        canary_source_root=str(checkout),
        old_source_root=str(runtime),
        branch=branch,
        base_revision=base,
        commit=commit,
        release_artifacts_built=False,
    )

    assert controller.source_resume_candidate(loaded_source_root=runtime) == checkout
    (checkout / "tampered").write_text("dirty\n", encoding="utf-8")
    assert controller.source_resume_candidate(loaded_source_root=runtime) is None
    (checkout / "tampered").unlink()

    controller.framework_root = checkout.resolve()
    controller._write_state(phase="canary_running", canary_kind="repair")
    assert controller.publish_after_canary(
        summary={"stopped_by": "supervisor_error"}
    ) == f"rollback:{runtime}"


def test_packaged_canary_failure_survives_preflight_until_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, runtime, checkout, branch, base = _packaged_clone(
        tmp_path,
        monkeypatch,
    )
    controller.framework_root = checkout.resolve()
    controller._write_state(
        phase="canary_failed",
        repair_mode="packaged_clone",
        worktree=str(checkout),
        canary_source_root=str(checkout),
        old_source_root=str(runtime),
        branch=branch,
        base_revision=base,
        commit=base,
    )

    assert controller.preflight_isolation(force=True) is False
    assert controller._state()["phase"] == "canary_failed"
    assert controller.failed_start_rollback_candidate(
        loaded_source_root=checkout,
    ) == runtime


def test_packaged_python_integrity_error_becomes_canary_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, runtime, checkout, branch, base = _packaged_clone(
        tmp_path,
        monkeypatch,
    )
    controller._write_state(
        phase="handoff_requested",
        repair_mode="packaged_clone",
        worktree=str(checkout),
        canary_source_root=str(checkout),
        old_source_root=str(runtime),
        branch=branch,
        base_revision=base,
        commit=base,
        release_artifacts_built=True,
    )
    monkeypatch.setattr(
        maintenance_mod,
        "_packaged_python_env",
        lambda _root: (_ for _ in ()).throw(ValueError("wrong Python root")),
    )

    assert not controller.mark_canary_started(
        loaded_source_root=checkout,
        revision=base[:12],
    )
    assert controller._state()["phase"] == "canary_failed"
    assert controller.failed_start_rollback_candidate(
        loaded_source_root=checkout,
    ) == runtime


@pytest.mark.parametrize("rewrite", ["insteadOf", "pushInsteadOf"])
def test_packaged_publication_rejects_applicable_url_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rewrite: str,
) -> None:
    controller, _runtime, checkout, branch, base = _packaged_clone(
        tmp_path,
        monkeypatch,
    )
    _git(
        checkout,
        "config",
        f"url.https://evil.example/.{rewrite}",
        str(tmp_path / "publication-repo"),
    )

    error = controller._validate_packaged_repair_clone(
        checkout,
        branch=branch,
        base_revision=base,
        head_revision=base,
        clean=True,
    )
    assert "unsafe local Git config" in error


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("core.fsmonitor", "/malicious/fsmonitor"),
        ("gpg.program", "/malicious/gpg"),
        ("filter.inject.clean", "/malicious/filter"),
    ],
)
def test_packaged_validation_rejects_executable_local_git_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
) -> None:
    controller, _runtime, checkout, branch, base = _packaged_clone(
        tmp_path,
        monkeypatch,
    )
    _git(checkout, "config", "--local", key, value)

    error = controller._validate_packaged_repair_clone(
        checkout,
        branch=branch,
        base_revision=base,
        head_revision=base,
        clean=True,
    )

    assert key.casefold() in error


@pytest.mark.parametrize(
    "flag",
    ["--assume-unchanged", "--skip-worktree"],
)
def test_packaged_validation_rejects_concealed_index_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
) -> None:
    controller, _runtime, checkout, branch, base = _packaged_clone(
        tmp_path,
        monkeypatch,
    )
    path = "argus_skill/base.py"
    _git(checkout, "update-index", flag, path)
    (checkout / path).write_text("BASE = 999\n", encoding="utf-8")
    controller._write_state(
        phase="queued",
        active_item_id="repair",
        incident_id="incident123",
        worktree=str(checkout),
        branch=branch,
        base_revision=base,
        affected_paths=[path],
        repair_mode="packaged_clone",
    )

    candidate = controller.prepare_reviewed_change({
        "item_id": "repair",
        "status": "done",
        "success": True,
        "review_status": "done",
    })

    assert candidate is None
    assert "concealed index entry" in controller._state()["error"]
    assert path in controller._state()["error"]
    assert _git(checkout, "rev-parse", "HEAD") == base


def test_packaged_validation_rejects_external_hardlink_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _runtime, checkout, branch, base = _packaged_clone(
        tmp_path,
        monkeypatch,
    )
    path = "argus_skill/base.py"
    external = tmp_path / "external.py"
    os.link(checkout / path, external)
    (checkout / path).write_text("BASE = 999\n", encoding="utf-8")
    assert external.read_text(encoding="utf-8") == "BASE = 999\n"
    controller._write_state(
        phase="queued",
        active_item_id="repair",
        incident_id="incident123",
        worktree=str(checkout),
        branch=branch,
        base_revision=base,
        affected_paths=[path],
        repair_mode="packaged_clone",
    )

    candidate = controller.prepare_reviewed_change({
        "item_id": "repair",
        "status": "done",
        "success": True,
        "review_status": "done",
    })

    assert candidate is None
    assert "hard-linked tracked file" in controller._state()["error"]
    assert path in controller._state()["error"]
    assert _git(checkout, "rev-parse", "HEAD") == base


def test_packaged_push_uses_literal_official_url_and_disables_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _runtime = _wheel_controller(tmp_path)
    worktree = tmp_path / "repair"
    worktree.mkdir()
    calls: list[list[str]] = []
    monkeypatch.setattr(maintenance_mod, "PUBLIC_REPOSITORY", "https://official/Argus.git")
    monkeypatch.setattr(
        controller,
        "_validate_packaged_repair_clone",
        lambda *_args, **_kwargs: "",
    )

    def run(args, **_kwargs):
        calls.append(list(args))
        stdout = "https://example.invalid/pr/1\n" if args[1:3] == ["pr", "list"] else ""
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(maintenance_mod, "_run", run)
    controller._publish_reviewed_change(
        state={"repair_mode": "packaged_clone", "base_revision": "a" * 40},
        worktree=worktree,
        branch="argus-self/session/incident",
        reviewed_commit="b" * 40,
        target=maintenance_mod._PublicationTarget(
            gh="/usr/bin/gh",
            slug="lbx154/Argus",
        ),
    )

    push = next(call for call in calls if "push" in call)
    assert "core.hooksPath=/dev/null" in push
    assert push[push.index("push") + 1] == "https://official/Argus.git"


@pytest.mark.parametrize(
    ("pr_state", "phase", "action"),
    [("MERGED", "idle", "MERGED"), ("CLOSED", "pr_closed", "rollback:")],
)
def test_packaged_pr_terminal_state_never_creates_adoption_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pr_state: str,
    phase: str,
    action: str,
) -> None:
    manager = _Manager()
    controller, runtime = _wheel_controller(tmp_path, manager)
    worktree = tmp_path / "repair"
    worktree.mkdir()
    controller._write_state(
        phase="pr_open",
        repair_mode="packaged_clone",
        worktree=str(worktree),
        pr_url="https://example.invalid/pr/1",
        old_source_root=str(runtime),
    )
    monkeypatch.setattr(maintenance_mod.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(
        maintenance_mod,
        "_run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, pr_state, ""),
    )

    result = controller.reconcile_pull_request()
    assert controller._state()["phase"] == phase
    if action == "MERGED":
        assert result == action
    else:
        assert result.startswith(action)
        assert controller.failed_start_rollback_candidate(
            loaded_source_root=runtime,
        ) is None
        assert controller._state()["phase"] == "idle"
        assert controller._state()["last_repair_result"] == "closed"
    controller.audit_if_due(daemon_state={})
    assert manager.calls == 0
    assert not (controller.root / "adoptions").exists()


def test_merged_packaged_incident_resets_and_queues_fresh_clone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, runtime, checkout, _branch, base = _packaged_clone(
        tmp_path,
        monkeypatch,
    )
    controller.framework_root = checkout.resolve()
    controller._write_state(
        phase="pr_open",
        repair_mode="packaged_clone",
        incident_id="incident123",
        incident_evidence_ids=[],
        worktree=str(checkout),
        canary_source_root=str(checkout),
        branch="argus-self/session/incident123",
        base_revision=base,
        commit=base,
        pr_url="https://example.invalid/pr/1",
    )
    real_run = maintenance_mod._run
    real_which = maintenance_mod.shutil.which
    monkeypatch.setattr(maintenance_mod.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(
        maintenance_mod,
        "_run",
        lambda args, **kwargs: (
            subprocess.CompletedProcess(args, 0, "MERGED", "")
            if args[:3] == ["/usr/bin/gh", "pr", "view"]
            else real_run(args, **kwargs)
        ),
    )

    assert controller.reconcile_pull_request() == "MERGED"
    finalized = controller._state()
    assert finalized["phase"] == "idle"
    assert finalized["repair_mode"] == ""
    assert finalized["worktree"] == ""
    assert finalized["active_source_root"] == str(checkout)
    assert finalized["active_source_revision"] == base
    assert finalized["pr_url"].endswith("/pr/1")
    assert finalized["last_repair_commit"] == base
    assert finalized["last_repair_result"] == "merged"

    restarted = DaemonSelfMaintenance(
        life_dir=controller.life_dir,
        framework_root=runtime,
        project_workdir=controller.project_workdir,
        manager=controller.manager,
        memory=controller.memory,
    )
    assert restarted.source_resume_candidate(loaded_source_root=runtime) == checkout
    superseded, _ = restarted._prepare_packaged_repair_clone("superseded")
    assert str(superseded.resolve()) in restarted.prune_obsolete_worktrees()
    assert checkout.is_dir()

    upstream = tmp_path / "publication-repo"
    (upstream / "official-next.py").write_text("NEXT = True\n", encoding="utf-8")
    _git(upstream, "add", "official-next.py")
    _git(upstream, "commit", "-m", "merged official revision")
    next_base = _git(upstream, "rev-parse", "HEAD")
    monkeypatch.setattr(maintenance_mod, "_run", real_run)
    monkeypatch.setattr(maintenance_mod.shutil, "which", real_which)
    controller.observe({"type": "life.planner.error", "error": "new defect"})
    assert controller.audit_if_due(daemon_state={})
    queued = controller._state()
    assert queued["repair_mode"] == "packaged_clone"
    assert Path(queued["worktree"]) != checkout
    assert Path(queued["worktree"]).parent == controller.root / "repairs"
    assert _git(Path(queued["worktree"]), "rev-parse", "HEAD") == next_base


@pytest.mark.parametrize(
    ("loaded_release_id", "expected_resume"),
    [
        ("wheel-release-old", True),
        ("wheel-release-new", False),
    ],
)
def test_active_clone_resume_requires_same_running_wheel_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    loaded_release_id: str,
    expected_resume: bool,
) -> None:
    controller, runtime, checkout, _branch, revision = _packaged_clone(
        tmp_path,
        monkeypatch,
    )
    controller._write_state(
        phase="idle",
        repair_mode="",
        worktree="",
        active_source_root=str(checkout),
        active_source_revision=revision,
        active_base_release_id="wheel-release-old",
        last_repair_result="merged",
    )
    monkeypatch.setattr(
        maintenance_mod,
        "release_identity",
        lambda _root: {"release_id": loaded_release_id},
    )
    restarted = DaemonSelfMaintenance(
        life_dir=controller.life_dir,
        framework_root=runtime,
        project_workdir=controller.project_workdir,
        manager=controller.manager,
        memory=controller.memory,
    )

    candidate = restarted.source_resume_candidate(loaded_source_root=runtime)

    assert (candidate == checkout) is expected_resume
    state = restarted._state()
    if expected_resume:
        assert state["active_source_root"] == str(checkout)
        assert state["active_source_revision"] == revision
    else:
        assert state["active_source_root"] == ""
        assert state["active_source_revision"] == ""
        assert state["active_base_release_id"] == ""


def test_active_clone_restart_rejects_post_review_hardlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, runtime, checkout, _branch, revision = _packaged_clone(
        tmp_path,
        monkeypatch,
    )
    controller._write_state(
        phase="idle",
        repair_mode="",
        worktree="",
        active_source_root=str(checkout),
        active_source_revision=revision,
        active_base_release_id="wheel-release-old",
        last_repair_result="merged",
    )
    os.link(checkout / "argus_skill" / "base.py", tmp_path / "external.py")
    assert _git(checkout, "status", "--porcelain") == ""
    monkeypatch.setattr(
        maintenance_mod,
        "release_identity",
        lambda _root: {"release_id": "wheel-release-old"},
    )
    restarted = DaemonSelfMaintenance(
        life_dir=controller.life_dir,
        framework_root=runtime,
        project_workdir=controller.project_workdir,
        manager=controller.manager,
        memory=controller.memory,
    )

    assert restarted.source_resume_candidate(loaded_source_root=runtime) is None
    assert not restarted._standalone_clone_is_valid(checkout, revision)


def test_prune_removes_clean_superseded_clone_and_preserves_active_unsafe_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    require_symlink_support,
) -> None:
    controller, runtime, active, branch, base = _packaged_clone(
        tmp_path,
        monkeypatch,
    )
    superseded, _ = controller._prepare_packaged_repair_clone("incident456")
    dirty, _ = controller._prepare_packaged_repair_clone("incident789")
    (dirty / "dirty.txt").write_text("keep\n", encoding="utf-8")
    outside = tmp_path / "outside-repair"
    outside.mkdir()
    linked = controller.root / "repairs" / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    controller._write_state(
        phase="pr_open",
        repair_mode="packaged_clone",
        worktree=str(active),
        canary_source_root=str(active),
        old_source_root=str(runtime),
        branch=branch,
        base_revision=base,
    )

    removed = controller.prune_obsolete_worktrees()

    assert str(superseded.resolve()) in removed
    assert not superseded.exists()
    assert active.is_dir()
    assert dirty.is_dir()
    assert linked.is_symlink()


def test_prune_preserves_preparing_clone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _runtime, preparing, branch, base = _packaged_clone(
        tmp_path,
        monkeypatch,
    )
    superseded, _ = controller._prepare_packaged_repair_clone("superseded")
    controller._write_state(
        phase="preparing",
        repair_mode="packaged_clone",
        worktree=str(preparing),
        branch=branch,
        base_revision=base,
    )

    removed = controller.prune_obsolete_worktrees()

    assert preparing.is_dir()
    assert str(superseded.resolve()) in removed
