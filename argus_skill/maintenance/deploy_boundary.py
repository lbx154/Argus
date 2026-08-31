"""The single deployment boundary for an independently reviewed Argus change."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..release_tools.repository_parity import (
    changed_paths as parity_changed_paths,
)
from ..release_tools.repository_parity import unexpected_differences

_PROCESS = object()
_NPM = "npm.cmd" if os.name == "nt" else "npm"
_PYTHON = (("python", "-m", "pytest", "-q"),)
_FRONTEND = (
    (_NPM, "--prefix", "frontend/web", "ci"),
    (_NPM, "--prefix", "frontend/tui", "ci"),
    (_NPM, "--prefix", "frontend/web", "run", "typecheck"),
    (_NPM, "--prefix", "frontend/web", "test"),
    (_NPM, "--prefix", "frontend/tui", "test"),
)
_DESKTOP = (
    (_NPM, "--prefix", "desktop", "ci"),
    ("python", "-m", "ruff", "check", "desktop", "tests/desktop"),
    ("python", "-m", "pytest", "-q", "tests/desktop/test_frozen_runtime.py"),
    (
        "python", "-m", "pytest", "--collect-only", "-q",
        "tests/core/test_file_lock.py", "tests/core/test_daemon_lock.py",
        "tests/daemon/test_life_worker.py", "tests/manager/test_live_view.py",
        "tests/manager/test_manager_session.py", "tests/test_manager_session_lock.py",
        "tests/test_engineer_sandbox.py",
    ),
    (
        "python", "-m", "pytest", "-q",
        "tests/apps/test_cli_parser.py", "tests/apps/test_tui_launcher.py",
        "tests/daemon/test_health.py", "tests/daemon/test_process_workspace.py",
        "tests/daemon/test_spawn_admission_portable.py",
        "tests/daemon/test_spawn_helper.py",
        "tests/daemon/test_windows_daemon_control.py",
        "tests/daemon/test_windows_terminal_process.py",
        "tests/core/test_agent_probe.py", "tests/core/test_backend_readiness.py",
        "tests/core/test_cheap_route_models.py",
        "tests/maintenance/test_repair_pipeline.py",
        "tests/maintenance/test_doctor_advisor.py",
        "tests/test_agent_cli_backend.py", "tests/test_bootstrap_doctor.py",
        "tests/test_doctor.py", "tests/test_install_documentation.py",
        "tests/tools/test_setup_readiness.py", "tests/webapi/test_commands_m1.py",
        "tests/webapi/test_server_m0.py", "tests/webapi/test_workspace_v2.py",
    ),
    (_NPM, "--prefix", "desktop", "run", "typecheck"),
    (_NPM, "--prefix", "desktop", "run", "test:identity"),
    ("pwsh", "-NoProfile", "-File", "desktop/scripts/build-backend.ps1", "-SkipInstall"),
    (_NPM, "--prefix", "desktop", "run", "build"),
    (
        _NPM, "--prefix", "desktop", "exec", "electron-builder", "--",
        "--win", "--dir", "--publish", "never",
        "--config.win.signAndEditExecutable=false",
    ),
)


@dataclass(frozen=True)
class ReviewedChange:
    repository: Path
    public_base: str
    reviewed_candidate: str
    reviewer_verdict: str
    acceptance_command: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    mission_id: str
    receipt_dir: Path
    origin_remote: str = "origin"
    private_remote: str = "private"


@dataclass
class DeploymentApproval:
    input_digest: str
    decision_id: str
    _process: object = field(repr=False)
    _used: bool = field(default=False, repr=False)


def _run(
    argv: Sequence[str], *, cwd: Path, input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(cwd), env.get("PYTHONPATH"))))
    command = [sys.executable if value == "python" else value for value in argv]
    return subprocess.run(
        command, cwd=cwd, env=env, input=input_text, text=True,
        capture_output=True, check=check,
    )


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(("git", *args), cwd=repo, check=check)


def _revision(repo: Path, value: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{value}^{{commit}}").stdout.strip()


def _remote_url(repo: Path, remote: str, *, push: bool = False) -> str:
    args = ["remote", "get-url", "--all"]
    if push:
        args.append("--push")
    urls = _git(repo, *args, remote).stdout.splitlines()
    if len(urls) != 1:
        raise ValueError(f"remote {remote!r} must resolve to exactly one URL")
    return urls[0]


def _commands(paths: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    commands: list[tuple[str, ...]] = []
    if any(path.startswith(("argus_skill/", "tests/")) or path in {
        "argus_doctor.py", "pyproject.toml",
    } for path in paths):
        commands.extend(_PYTHON)
    if any(path.startswith("frontend/") for path in paths):
        commands.extend(_FRONTEND)
    if any(path.startswith("desktop/") for path in paths):
        commands.extend(_DESKTOP)
    return tuple(commands or _PYTHON)


def _resolved_input(change: ReviewedChange) -> dict[str, Any]:
    repo = change.repository.resolve()
    base = _revision(repo, change.public_base)
    candidate = _revision(repo, change.reviewed_candidate)
    _git(repo, "merge-base", "--is-ancestor", base, candidate)
    paths = tuple(_git(
        repo, "diff", "--name-only", "--no-renames", base, candidate, "--",
    ).stdout.splitlines())
    commands = _commands(paths)
    origin = (_remote_url(repo, change.origin_remote),
              _remote_url(repo, change.origin_remote, push=True))
    private = (_remote_url(repo, change.private_remote),
               _remote_url(repo, change.private_remote, push=True))
    payload = {
        "acceptance_command": change.acceptance_command,
        "evidence_refs": change.evidence_refs,
        "mission_id": change.mission_id,
        "public_base": base,
        "reviewed_candidate": candidate,
        "reviewer_verdict": change.reviewer_verdict,
        "test_commands": commands,
        "target_remotes": {"origin": origin, "private": private},
    }
    return {
        "repo": repo, "base": base, "candidate": candidate, "paths": paths,
        "commands": commands, "origin": origin, "private": private,
        "payload": payload,
    }


def deployment_input_digest(change: ReviewedChange) -> str:
    """Return the internal identity stored with an ordinary decision card."""
    payload = _resolved_input(change)["payload"]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def approve_reviewed_change(
    change: ReviewedChange, operator_decision: Mapping[str, Any],
) -> DeploymentApproval:
    """Turn one resolved ordinary decision card into an in-process approval."""
    frozen_digest = str(operator_decision.get("input_digest") or "")
    if not (
        operator_decision.get("status") == "resolved"
        and operator_decision.get("selected_option") == "adopt"
        and operator_decision.get("item_id") == change.mission_id
        and frozen_digest
    ):
        raise ValueError("operator decision does not approve this reviewed input")
    return DeploymentApproval(
        frozen_digest, str(operator_decision.get("id") or ""), _PROCESS,
    )


def _failures(root: Path, commands: Sequence[Sequence[str]]) -> set[str]:
    failures: set[str] = set()
    for command in commands:
        result = _run(command, cwd=root, check=False)
        if not result.returncode:
            continue
        label = " ".join(command)
        output = (result.stdout + result.stderr).replace(str(root), ".")
        output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
        tests = re.findall(
            r"(?m)^(?:FAILED|ERROR)(?:\s+collecting)?\s+([^\s]+)", output,
        )
        tests.extend(re.findall(r"(?m)^\s*FAIL\s+([^\r\n]+)", output))
        if tests:
            failures.update(f"{label}: {test}" for test in tests)
            continue
        diagnostics = {
            line.strip()
            for line in output.splitlines()
            if re.search(r"\b(?:error|fail(?:ed|ure)?)\b", line, re.IGNORECASE)
        }
        failures.update(f"{label}: {line}" for line in diagnostics)
        if not diagnostics:
            failures.add(f"{label}: exit {result.returncode}")
    return failures


def _release(root: Path) -> dict[str, Any] | None:
    for frontend in ("frontend/web", "frontend/tui"):
        if (root / frontend / "package-lock.json").is_file() and _run(
            (_NPM, "--prefix", frontend, "ci"), cwd=root, check=False,
        ).returncode:
            return None
    script = root / "argus_skill/release_tools/build_release.py"
    if _run((sys.executable, str(script)), cwd=root, check=False).returncode:
        return None
    probe = _run((
        sys.executable, "-c",
        "import json; from argus_skill.release import release_identity; "
        "print(json.dumps(release_identity('.')))",
    ), cwd=root, check=False)
    try:
        identity = json.loads(probe.stdout) if probe.returncode == 0 else {}
        return identity if identity["release_matches_source"] is True else None
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _commit(root: Path, message: str) -> str:
    if _git(root, "status", "--porcelain").stdout.strip():
        _git(root, "add", "--all")
        _git(
            root, "-c", "user.name=Argus Runtime",
            "-c", "user.email=argus-runtime@localhost",
            "commit", "-m", message,
        )
    return _revision(root, "HEAD")


def _remote_head(repo: Path, url: str, branch: str) -> str:
    result = _git(
        repo, "ls-remote", "--heads", url, f"refs/heads/{branch}", check=False,
    )
    if result.returncode:
        raise RuntimeError("remote reference query failed")
    return result.stdout.partition("\t")[0].strip()


def _fetch_branch(repo: Path, url: str, branch: str) -> str:
    result = _git(
        repo, "fetch", "--no-tags", url, f"refs/heads/{branch}", check=False,
    )
    if result.returncode:
        raise RuntimeError("remote branch fetch failed")
    return _revision(repo, "FETCH_HEAD")


def _same_tree(repo: Path, left: str, right: str) -> bool:
    return _git(repo, "rev-parse", f"{left}^{{tree}}").stdout == _git(
        repo, "rev-parse", f"{right}^{{tree}}",
    ).stdout


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return not _git(
        repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False,
    ).returncode


def _receipt(
    change: ReviewedChange, facts: dict[str, Any], stamp: str,
) -> dict[str, Any]:
    facts["finished_at"] = datetime.now(timezone.utc).isoformat()
    encoded = json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()
    facts["run_digest"] = hashlib.sha256(encoded).hexdigest()
    change.receipt_dir.mkdir(parents=True, exist_ok=True)
    (change.receipt_dir / f"deployment-{stamp}.json").write_text(
        json.dumps(facts, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    return facts


def deploy_reviewed_change(
    run_input: ReviewedChange, approval: DeploymentApproval,
) -> dict[str, Any]:
    """Test, rebuild and publish once, then return one ADOPT/REJECT receipt."""
    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    try:
        resolved = _resolved_input(run_input)
        encoded = json.dumps(
            resolved["payload"], sort_keys=True, separators=(",", ":"),
        ).encode()
        identity = hashlib.sha256(encoded).hexdigest()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        facts = {
            "started_at": started.isoformat(), "input_digest": "",
            "decision_id": approval.decision_id, "mission_id": run_input.mission_id,
            "approval_matches_input": False, "reviewer_verdict_done": False,
            "baseline_failures": [], "candidate_failures": [],
            "failure_subset": False, "acceptance_passed": False,
            "release_matches_source": False, "both_publication_routes_complete": False,
            "partial_publication": False, "failure_stage": "input",
            "failure_reason": type(exc).__name__, "verdict": "REJECT",
            "daemon_roll_permitted": False,
        }
        approval._used = True
        return _receipt(run_input, facts, stamp)

    approved = (
        approval._process is _PROCESS and not approval._used
        and approval.input_digest == identity
    )
    approval._used = True
    facts: dict[str, Any] = {
        "started_at": started.isoformat(), "input_digest": identity,
        "decision_id": approval.decision_id, "mission_id": run_input.mission_id,
        "reviewed_candidate": resolved["candidate"],
        "public_base": resolved["base"], "changed_paths": list(resolved["paths"]),
        "test_commands": [list(command) for command in resolved["commands"]],
        "approval_matches_input": approved,
        "reviewer_verdict_done": run_input.reviewer_verdict == "done",
        "baseline_failures": [], "candidate_failures": [],
        "failure_subset": False, "acceptance_reproduced": False,
        "acceptance_passed": False, "release_matches_source": False,
        "repository_parity_verified": False,
        "public_sync_published": False, "public_main_updated": False,
        "private_sync_published": False, "private_main_updated": False,
        "both_publication_routes_complete": False, "partial_publication": False,
        "release_id": "", "adopted_public_ref": "", "adopted_private_ref": "",
        "runtime_source_root": "", "failure_stage": "", "failure_reason": "",
    }
    if not approved or run_input.reviewer_verdict != "done":
        facts["failure_stage"] = "approval" if not approved else "review"
        facts["failure_reason"] = (
            "approval does not match deployment input"
            if not approved else "reviewer has not completed the change"
        )
        facts.update(verdict="REJECT", daemon_roll_permitted=False)
        return _receipt(run_input, facts, stamp)

    repo = resolved["repo"]
    sync_date = started.date().isoformat()
    public_sync = f"sync-public-{sync_date}"
    private_sync = f"sync-private-{sync_date}"
    runtime_root = run_input.receipt_dir / "deployed-runtimes" / stamp
    run_input.receipt_dir.mkdir(parents=True, exist_ok=True)
    worktrees: list[Path] = []
    keep_runtime = False
    stage = "remote_state"
    with tempfile.TemporaryDirectory(prefix="argus-deploy-") as temporary:
        base_root = Path(temporary) / "base"
        candidate_root = Path(temporary) / "candidate"
        private_root = Path(temporary) / "private"
        try:
            origin_fetch, origin_push = resolved["origin"]
            private_fetch, private_push = resolved["private"]
            public_main = _fetch_branch(repo, origin_fetch, "main")
            private_main = _fetch_branch(repo, private_fetch, "main")
            existing_public_sync = _remote_head(repo, origin_push, public_sync)
            existing_private_sync = _remote_head(repo, private_push, private_sync)
            if existing_public_sync:
                existing_public_sync = _fetch_branch(repo, origin_push, public_sync)
            if existing_private_sync:
                existing_private_sync = _fetch_branch(repo, private_push, private_sync)
            recovering_public = public_main != resolved["base"]
            if recovering_public and not _is_ancestor(
                repo, resolved["candidate"], public_main,
            ):
                raise RuntimeError("public main no longer matches the reviewed deployment")
            if recovering_public:
                facts["public_main_updated"] = True
                facts["adopted_public_ref"] = public_main
                facts["partial_publication"] = True
            private_already_published = bool(
                recovering_public
                and not unexpected_differences(parity_changed_paths(
                    repo, private_ref=private_main, public_ref=public_main,
                ))
            )
            if private_already_published:
                facts["private_main_updated"] = True
                facts["adopted_private_ref"] = private_main

            stage = "verification"
            for path, revision in (
                (base_root, resolved["base"]),
                (candidate_root, resolved["candidate"]),
            ):
                _git(repo, "worktree", "add", "--detach", str(path), revision)
                worktrees.append(path)
            baseline = _failures(base_root, resolved["commands"])
            candidate_failures = _failures(candidate_root, resolved["commands"])
            facts["baseline_failures"] = sorted(baseline)
            facts["candidate_failures"] = sorted(candidate_failures)
            facts["failure_subset"] = candidate_failures <= baseline
            if not facts["failure_subset"]:
                raise RuntimeError("candidate introduces test failures")

            stage = "acceptance"
            base_acceptance = _run(
                run_input.acceptance_command, cwd=base_root, check=False,
            ).returncode
            candidate_acceptance = _run(
                run_input.acceptance_command, cwd=candidate_root, check=False,
            ).returncode
            facts["acceptance_reproduced"] = bool(
                base_acceptance and not candidate_acceptance
            )
            facts["acceptance_passed"] = facts["acceptance_reproduced"]
            if not facts["acceptance_passed"]:
                raise RuntimeError("acceptance did not turn from failing to passing")

            stage = "public_release"
            runtime_root.parent.mkdir(parents=True, exist_ok=True)
            _git(
                repo, "worktree", "add", "--detach", str(runtime_root),
                resolved["candidate"],
            )
            worktrees.append(runtime_root)
            public_release = _release(runtime_root)
            if public_release is None:
                raise RuntimeError("public release build rejected")
            built_public = _commit(runtime_root, "Build reviewed Argus release")
            selected_public = built_public
            if recovering_public:
                if not _same_tree(repo, public_main, built_public):
                    raise RuntimeError(
                        "public main no longer matches the reviewed deployment"
                    )
                selected_public = public_main
            elif existing_public_sync:
                if _same_tree(repo, existing_public_sync, built_public):
                    selected_public = existing_public_sync
                elif not _is_ancestor(repo, existing_public_sync, built_public):
                    raise RuntimeError("existing public sync differs from this release")
            if not _is_ancestor(repo, resolved["candidate"], selected_public):
                raise RuntimeError("public release does not descend from the reviewed change")
            if selected_public != built_public:
                _git(
                    repo, "worktree", "remove", "--force", str(runtime_root),
                )
                _git(
                    repo, "worktree", "add", "--detach", str(runtime_root),
                    selected_public,
                )

            stage = "private_release"
            _git(repo, "worktree", "add", "--detach", str(private_root), private_main)
            worktrees.append(private_root)
            if not (
                private_already_published
                or recovering_public and private_main == existing_private_sync
            ):
                patch = _git(
                    repo, "diff", "--binary", resolved["base"],
                    resolved["candidate"],
                ).stdout
                _run(("git", "apply", "--index", "-"), cwd=private_root, input_text=patch)
                _commit(private_root, "Import reviewed Argus change")
            private_release = _release(private_root)
            if private_release is None:
                raise RuntimeError("private release build rejected")
            built_private = _commit(private_root, "Build private Argus release")
            selected_private = built_private
            if existing_private_sync:
                if _same_tree(repo, existing_private_sync, built_private):
                    selected_private = existing_private_sync
                elif not _is_ancestor(repo, existing_private_sync, built_private):
                    raise RuntimeError("existing private sync differs from this release")

            facts["release_id"] = str(public_release.get("release_id") or "")
            facts["release_matches_source"] = True
            facts["public_sync_branch"] = public_sync
            facts["private_sync_branch"] = private_sync

            stage = "parity"
            differences = unexpected_differences(parity_changed_paths(
                repo, private_ref=selected_private, public_ref=selected_public,
            ))
            if differences:
                raise RuntimeError("private and public product trees differ")
            facts["repository_parity_verified"] = True

            stage = "public_sync"
            if selected_public != existing_public_sync:
                _git(
                    repo, "push", origin_push,
                    f"{selected_public}:refs/heads/{public_sync}",
                )
            facts["public_sync_published"] = True

            stage = "private_sync"
            if selected_private != existing_private_sync:
                _git(
                    repo, "push", private_push,
                    f"{selected_private}:refs/heads/{private_sync}",
                )
            facts["private_sync_published"] = True

            stage = "public_main"
            if public_main != selected_public:
                _git(
                    repo, "merge-base", "--is-ancestor", public_main, selected_public,
                )
                _git(repo, "push", origin_push, f"{selected_public}:refs/heads/main")
            facts["public_main_updated"] = True
            facts["adopted_public_ref"] = selected_public

            stage = "private_main"
            if private_main != selected_private:
                _git(
                    repo, "merge-base", "--is-ancestor", private_main, selected_private,
                )
                _git(repo, "push", private_push, f"{selected_private}:refs/heads/main")
            facts["private_main_updated"] = True
            facts["adopted_private_ref"] = selected_private
            facts["runtime_source_root"] = str(runtime_root.resolve())
            keep_runtime = True
        except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
            facts["failure_stage"] = stage
            facts["failure_reason"] = (
                str(exc) if isinstance(exc, RuntimeError)
                else f"command exited with status {getattr(exc, 'returncode', 1)}"
            )
        finally:
            for path in reversed(worktrees):
                if keep_runtime and path == runtime_root:
                    continue
                _git(repo, "worktree", "remove", "--force", str(path), check=False)

    routes_complete = all(facts[key] for key in (
        "public_sync_published", "private_sync_published",
        "public_main_updated", "private_main_updated",
    ))
    facts["both_publication_routes_complete"] = routes_complete
    published = any(facts[key] for key in (
        "public_sync_published", "private_sync_published",
        "public_main_updated", "private_main_updated",
    ))
    facts["partial_publication"] = published and not routes_complete
    adopt = all((
        approved, facts["reviewer_verdict_done"], facts["failure_subset"],
        facts["acceptance_passed"], facts["release_matches_source"],
        routes_complete,
    ))
    facts.update(verdict="ADOPT" if adopt else "REJECT", daemon_roll_permitted=adopt)
    if not adopt and keep_runtime:
        _git(repo, "worktree", "remove", "--force", str(runtime_root), check=False)
        facts["runtime_source_root"] = ""
    return _receipt(run_input, facts, stamp)


__all__ = [
    "DeploymentApproval",
    "ReviewedChange",
    "approve_reviewed_change",
    "deploy_reviewed_change",
    "deployment_input_digest",
]
