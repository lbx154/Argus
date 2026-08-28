"""Persisted repair plans and a closed registry of executable actions."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .doctor import DoctorContext, run_full_doctor
from .models import DoctorFinding, RepairAction, RepairPlanRef, RepairResult

_PLAN_SCHEMA_VERSION = 1
_HISTORY_SCHEMA_VERSION = 1
_SECRET_PATTERNS = (
    re.compile(r"(?i)(?:gh[pousr]_|sk-|token=)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(?:api[_-]?key|password|secret)\s*[:=]\s*[^\s,;]+"),
)


def _redact(value: str) -> str:
    text = value.replace(str(Path.home()), "~")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _sanitize_audit(value: Any) -> Any:
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, dict):
        return {str(key): _sanitize_audit(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_audit(item) for item in value]
    return value


def repairs_root(global_root: Path) -> Path:
    return global_root.expanduser().resolve() / "repairs"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _append_history(global_root: Path, event: dict[str, Any]) -> None:
    path = repairs_root(global_root) / "history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _sanitize_audit({
        "schema_version": _HISTORY_SCHEMA_VERSION,
        "ts": time.time(),
        **event,
    })
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass


def _path_memory_payload(context: DoctorContext) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "checkout": str(context.checkout.expanduser().resolve()) if context.checkout else "",
        "python_executable": str(context.python_executable.expanduser().resolve()),
        "global_root": str(context.global_root.expanduser().resolve()),
        "project_root": str(context.project_root.expanduser().resolve()),
        "desktop_user_data": (
            str(context.desktop_user_data.expanduser().resolve())
            if context.desktop_user_data else ""
        ),
        "web_host": context.web_host,
        "web_port": int(context.web_port),
        "platform": os.name,
        "install_mode": context.install_mode,
    }


def write_path_memory(context: DoctorContext) -> Path:
    path = repairs_root(context.global_root) / "path-memory.json"
    _atomic_json(path, _path_memory_payload(context))
    return path


def read_path_memory(global_root: Path) -> dict[str, Any]:
    path = repairs_root(global_root) / "path-memory.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _target_for_action(context: DoctorContext, action_id: str) -> str:
    targets = {
        "refresh_path_memory": str(repairs_root(context.global_root) / "path-memory.json"),
        "ensure_runtime_dirs": str(repairs_root(context.global_root)),
        "remove_verified_stale_daemon_pid": str(context.project_root / "daemon.pid"),
        "remove_dead_daemon_control_files": str(context.project_root),
        "stop_owned_stuck_daemon": str(context.project_root),
        "install_editable": str(context.checkout or ""),
        "install_electron_binary": str((context.checkout / "desktop") if context.checkout else ""),
        "rebuild_release_assets": str(context.checkout or ""),
    }
    return targets.get(action_id, action_id)


def _action_spec(context: DoctorContext, action_id: str) -> RepairAction:
    specs: dict[str, tuple[str, str, tuple[str, ...]]] = {
        "refresh_path_memory": ("core", "safe", ("ARGUS-PATH-001",)),
        "ensure_runtime_dirs": ("core", "safe", ()),
        "remove_verified_stale_daemon_pid": ("daemon", "safe", ("ARGUS-STATE-001",)),
        "remove_dead_daemon_control_files": ("daemon", "safe", ("ARGUS-STATE-002",)),
        "stop_owned_stuck_daemon": ("daemon", "consent", ("ARGUS-DAEMON-001",)),
        "install_editable": ("python", "consent", ("ARGUS-PYTHON-001",)),
        "install_electron_binary": ("desktop", "consent", ("ARGUS-DESKTOP-001",)),
        "rebuild_release_assets": ("release", "consent", ("ARGUS-ASSET-001",)),
    }
    provider, risk, verify = specs.get(action_id, ("manual", "manual", ()))
    return RepairAction(
        id=action_id,
        provider=provider,
        risk=risk,  # type: ignore[arg-type]
        target=_target_for_action(context, action_id),
        precondition={"target_fingerprint": context.target_fingerprint},
        verify_codes=verify,
    )


def create_plan(
    context: DoctorContext,
    findings: list[DoctorFinding] | tuple[DoctorFinding, ...],
) -> RepairPlanRef:
    root = repairs_root(context.global_root)
    now = datetime.now(timezone.utc)
    unique = uuid.uuid4().hex[:8]
    plan_id = f"rp-{now.strftime('%Y%m%dT%H%M%SZ')}-{unique}"
    action_ids: list[str] = []
    evidence_by_action: dict[str, dict[str, Any]] = {}
    for finding in findings:
        for action_id in finding.repair_action_ids:
            if action_id not in action_ids:
                action_ids.append(action_id)
            evidence_by_action.setdefault(action_id, {}).update(finding.evidence)
    # Remembering canonical paths is always safe and makes the next diagnosis
    # deterministic, but do not duplicate an explicitly requested action.
    if "refresh_path_memory" not in action_ids:
        action_ids.append("refresh_path_memory")
    action_rows: list[RepairAction] = []
    for action_id in action_ids:
        action = _action_spec(context, action_id)
        if action_id == "stop_owned_stuck_daemon":
            evidence = evidence_by_action.get(action_id, {})
            action = RepairAction(
                id=action.id,
                provider=action.provider,
                risk=action.risk,
                target=action.target,
                precondition={
                    **action.precondition,
                    "pid": evidence.get("pid"),
                    "started_at_iso": evidence.get("started_at_iso"),
                },
                verify_codes=action.verify_codes,
            )
        action_rows.append(action)
    actions = tuple(action_rows)
    payload = {
        "schema_version": _PLAN_SCHEMA_VERSION,
        "plan_id": plan_id,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "target_fingerprint": context.target_fingerprint,
        "target": context.fingerprint_payload(),
        "status": "planned",
        "findings": [_sanitize_audit(finding.to_jsonable()) for finding in findings],
        "actions": [action.to_jsonable() for action in actions],
        "outcomes": [],
        "verification": {},
    }
    path = root / "plans" / f"{plan_id}.json"
    _atomic_json(path, payload)
    _append_history(context.global_root, {"type": "repair.plan.created", "plan_id": plan_id})
    return RepairPlanRef(plan_id=plan_id, path=path, status="planned", actions=actions)


def _load_plan(global_root: Path, plan_id: str) -> tuple[Path, dict[str, Any]]:
    if not re.fullmatch(r"rp-[A-Za-z0-9TZ-]+", plan_id):
        raise ValueError("invalid repair plan id")
    path = repairs_root(global_root) / "plans" / f"{plan_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"repair plan not found: {plan_id}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != _PLAN_SCHEMA_VERSION:
        raise ValueError(f"unsupported or malformed repair plan: {plan_id}")
    return path, payload


def _safe_remove_stale_pid(context: DoctorContext) -> dict[str, Any]:
    from ..core import daemon_lock

    path = context.project_root / "daemon.pid"
    pid = daemon_lock.read_daemon_pid(path)
    if pid is None and not path.exists():
        return {"status": "not_needed"}
    if pid is not None and daemon_lock.is_pid_running(pid):
        return {"status": "skipped_live", "pid": pid}
    try:
        lock = daemon_lock.acquire_global_daemon_lock(pid_path=path)
    except daemon_lock.DaemonAlreadyRunning as exc:
        return {"status": "skipped_live", "pid": exc.pid}
    lock.release()
    return {"status": "applied", "previous_pid": pid}


def _safe_remove_control_files(context: DoctorContext) -> dict[str, Any]:
    from ..daemon.state import read_daemon_status

    status = read_daemon_status(context.project_root)
    if status.alive:
        return {"status": "skipped_live", "pid": status.pid}
    removed: list[str] = []
    for name in ("daemon.stop-request.json", "daemon.drain-request.json"):
        path = context.project_root / name
        try:
            path.unlink()
            removed.append(name)
        except FileNotFoundError:
            pass
    return {"status": "applied" if removed else "not_needed", "removed": removed}


def _run_registered_command(
    argv: list[str], *, cwd: Path, timeout: float | None
) -> dict[str, Any]:
    completed = subprocess.run(
        argv,
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    detail = _redact((completed.stderr or completed.stdout or "").strip()[-4_000:])
    return {
        "status": "applied" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "detail": detail,
    }


def _apply_registered_action(
    context: DoctorContext,
    action: RepairAction,
    *,
    confirmed: bool,
) -> dict[str, Any]:
    if action.risk == "manual":
        return {"status": "manual_required"}
    if action.risk == "consent" and not confirmed:
        return {"status": "consent_required"}
    if action.id == "refresh_path_memory":
        return {"status": "applied", "path": str(write_path_memory(context))}
    if action.id == "ensure_runtime_dirs":
        for path in (
            context.global_root,
            context.global_root / "projects",
            context.global_root / "special_prompts",
            repairs_root(context.global_root),
        ):
            path.mkdir(parents=True, exist_ok=True)
        return {"status": "applied"}
    if action.id == "remove_verified_stale_daemon_pid":
        return _safe_remove_stale_pid(context)
    if action.id == "remove_dead_daemon_control_files":
        return _safe_remove_control_files(context)
    if action.id == "stop_owned_stuck_daemon":
        from ..daemon.state import read_daemon_status, stop_daemon

        status = read_daemon_status(context.project_root)
        expected_pid = int((action.precondition or {}).get("pid") or 0)
        expected_started = str((action.precondition or {}).get("started_at_iso") or "")
        if not status.alive:
            return {"status": "not_needed"}
        if expected_pid and status.pid != expected_pid:
            return {"status": "skipped_changed_target", "pid": status.pid}
        if expected_started and status.started_at_iso != expected_started:
            return {"status": "skipped_changed_target", "pid": status.pid}
        rc = stop_daemon(context.project_root, timeout=15.0, force=False)
        return {
            "status": "applied" if rc in {0, 1} else "failed",
            "returncode": rc,
            "detail": "owned daemon interrupted" if rc in {0, 1} else "daemon did not stop within 15 seconds",
        }
    if context.checkout is None:
        return {"status": "failed", "detail": "source checkout is unavailable"}
    checkout = context.checkout.resolve()
    if action.id == "install_editable":
        return _run_registered_command(
            [str(context.python_executable), "-m", "pip", "install", "-e", str(checkout)],
            cwd=checkout,
            timeout=None,
        )
    if action.id == "install_electron_binary":
        desktop = checkout / "desktop"
        node = shutil_which("node")
        if not node or not (desktop / "package.json").is_file():
            return {"status": "failed", "detail": "Node or desktop/package.json is unavailable"}
        return _run_registered_command(
            [node, "-e", "require('electron')"],
            cwd=desktop,
            timeout=None,
        )
    if action.id == "rebuild_release_assets":
        git = shutil_which("git")
        if not git:
            return {"status": "failed", "detail": "Git is required to attribute rebuilt files"}
        before = subprocess.run(
            [git, "-C", str(checkout), "status", "--porcelain"],
            check=False, capture_output=True, text=True, encoding="utf-8",
        )
        if before.returncode != 0 or before.stdout.strip():
            return {
                "status": "failed",
                "detail": "checkout must be clean before a repository repair action",
            }
        result = _run_registered_command(
            [str(context.python_executable), "-m", "argus_skill.release_tools.build_release"],
            cwd=checkout,
            timeout=None,
        )
        after = subprocess.run(
            [git, "-C", str(checkout), "status", "--porcelain"],
            check=False, capture_output=True, text=True, encoding="utf-8",
        )
        result["changed_paths"] = sorted(
            line[3:].strip().replace("\\", "/")
            for line in after.stdout.splitlines()
            if len(line) >= 4
        )
        result["repository_changes"] = bool(result["changed_paths"])
        return result
    return {"status": "manual_required", "detail": "unregistered action"}


def shutil_which(name: str) -> str | None:
    # Kept as a seam for tests and provider-specific path memory.
    import shutil

    return shutil.which(name)


def apply_plan(
    context: DoctorContext,
    plan_id: str,
    *,
    safe_only: bool = False,
    confirmed: bool = False,
) -> RepairResult:
    path, payload = _load_plan(context.global_root, plan_id)
    if payload.get("target_fingerprint") != context.target_fingerprint:
        raise RuntimeError("repair target fingerprint changed; create a new plan")
    if payload.get("status") == "completed":
        return RepairResult(
            plan_id=plan_id,
            status="already_applied",
            actions=tuple(payload.get("outcomes") or ()),
            verification=dict(payload.get("verification") or {}),
        )

    previous_outcomes = {
        str(item.get("id") or ""): dict(item)
        for item in payload.get("outcomes") or []
        if isinstance(item, dict)
    }
    if payload.get("status") == "running":
        _append_history(context.global_root, {
            "type": "repair.plan.reconciled",
            "plan_id": plan_id,
            "previous_status": "running",
            "status": "interrupted",
        })
        payload["status"] = "interrupted"
    payload["status"] = "running"
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_json(path, payload)
    outcomes: list[dict[str, Any]] = []
    for raw in payload.get("actions") or []:
        action = RepairAction(
            id=str(raw.get("id") or ""),
            provider=str(raw.get("provider") or "manual"),
            risk=str(raw.get("risk") or "manual"),  # type: ignore[arg-type]
            target=str(raw.get("target") or ""),
            precondition=dict(raw.get("precondition") or {}),
            verify_codes=tuple(raw.get("verify_codes") or ()),
        )
        previous = previous_outcomes.get(action.id, {})
        if previous.get("status") in {"applied", "not_needed", "already_applied"}:
            outcome = {"id": action.id, "status": "already_applied"}
        elif action.precondition.get("target_fingerprint") != context.target_fingerprint:
            outcome = {"id": action.id, "status": "skipped_changed_target"}
        elif safe_only and action.risk != "safe":
            outcome = {"id": action.id, "status": "consent_required"}
        else:
            try:
                outcome = {"id": action.id, **_apply_registered_action(
                    context,
                    action,
                    confirmed=confirmed or action.risk == "safe",
                )}
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                outcome = {
                    "id": action.id,
                    "status": "failed",
                    "detail": _redact(f"{type(exc).__name__}: {exc}"),
                }
        outcomes.append(outcome)
        _append_history(context.global_root, {
            "type": "repair.action.finished",
            "plan_id": plan_id,
            "action": outcome,
        })

    verification = run_full_doctor(context, include_backend=False).to_jsonable()
    verification_by_code = {
        str(item.get("code") or ""): item
        for item in verification.get("findings") or []
    }
    outcome_by_id = {str(item.get("id") or ""): item for item in outcomes}
    verification_failures: list[str] = []
    for raw in payload.get("actions") or []:
        outcome = outcome_by_id.get(str(raw.get("id") or ""), {})
        if outcome.get("status") not in {"applied", "not_needed", "already_applied"}:
            continue
        for code in raw.get("verify_codes") or []:
            finding = verification_by_code.get(str(code))
            if finding is not None and not finding.get("ok"):
                verification_failures.append(str(code))
    failed = (
        any(item.get("status") in {"failed", "skipped_changed_target"} for item in outcomes)
        or bool(verification_failures)
    )
    unresolved = any(item.get("status") in {"consent_required", "manual_required", "skipped_live"} for item in outcomes)
    status = "failed" if failed else "partial" if unresolved else "completed"
    payload.update({
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "outcomes": outcomes,
        "verification": verification,
        "verification_failures": sorted(set(verification_failures)),
    })
    _atomic_json(path, payload)
    _append_history(context.global_root, {
        "type": "repair.plan.finished",
        "plan_id": plan_id,
        "status": status,
    })
    return RepairResult(
        plan_id=plan_id,
        status=status,
        actions=tuple(outcomes),
        verification=verification,
    )


def submit_pr(
    context: DoctorContext,
    plan_id: str,
    *,
    confirmed: bool,
) -> str:
    """Publish a previously repaired and committed branch with explicit consent."""
    if not confirmed:
        raise PermissionError("submit-pr requires --yes explicit authorization")
    if context.checkout is None:
        raise RuntimeError("source checkout is unavailable")
    checkout = context.checkout.resolve()
    _path, payload = _load_plan(context.global_root, plan_id)
    changed_paths = sorted({
        str(path)
        for outcome in payload.get("outcomes") or []
        for path in outcome.get("changed_paths") or []
    })
    if not changed_paths:
        raise RuntimeError("plan has no registered repository repair changes")
    git = shutil_which("git")
    gh = shutil_which("gh")
    if not git or not gh:
        raise RuntimeError("git and authenticated GitHub CLI are required")

    def git_text(*args: str) -> str:
        completed = subprocess.run(
            [git, "-C", str(checkout), *args],
            check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout).strip())
        return completed.stdout.strip()

    branch = git_text("branch", "--show-current")
    if not branch or branch in {"main", "master"}:
        raise RuntimeError("submit-pr requires a non-main repair branch")
    if git_text("status", "--porcelain"):
        raise RuntimeError("commit and review registered repair changes before submit-pr")
    try:
        ahead = int(git_text("rev-list", "--count", "origin/main..HEAD") or "0")
    except ValueError:
        ahead = 0
    if ahead <= 0:
        raise RuntimeError("repair branch has no committed changes ahead of origin/main")
    auth = subprocess.run(
        [gh, "auth", "status"], check=False, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=10,
    )
    if auth.returncode != 0:
        raise RuntimeError("GitHub CLI is not authenticated")
    report = prepare_pr_report(context, plan_id)
    title = f"fix(doctor): apply verified repair {plan_id}"
    created = subprocess.run(
        [
            gh, "pr", "create", "--base", "main", "--head", branch,
            "--title", title, "--body-file", str(report),
        ],
        cwd=str(checkout), check=False, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if created.returncode != 0:
        raise RuntimeError((created.stderr or created.stdout).strip())
    url = created.stdout.strip().splitlines()[-1]
    _append_history(context.global_root, {
        "type": "repair.pr.submitted",
        "plan_id": plan_id,
        "branch": branch,
        "url": url,
        "changed_paths": changed_paths,
    })
    return url


def prepare_pr_report(context: DoctorContext, plan_id: str) -> Path:
    _path, payload = _load_plan(context.global_root, plan_id)
    report = repairs_root(context.global_root) / "reports" / f"{plan_id}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Argus Doctor repair report: {plan_id}",
        "",
        "## Target class",
        "",
        f"- Platform: {os.name}",
        f"- Checkout: {_redact(str(context.checkout or 'unknown'))}",
        f"- Status: {payload.get('status', 'planned')}",
        "",
        "## Findings",
        "",
    ]
    for finding in payload.get("findings") or []:
        lines.append(
            f"- `{finding.get('code', 'ARGUS-UNKNOWN')}` "
            f"[{finding.get('status', 'unknown')}]: {_redact(str(finding.get('detail') or ''))}"
        )
    lines.extend(["", "## Registered actions", ""])
    outcomes = {str(item.get("id")): item for item in payload.get("outcomes") or []}
    for action in payload.get("actions") or []:
        action_id = str(action.get("id") or "")
        outcome = outcomes.get(action_id, {})
        lines.append(
            f"- `{action_id}` ({action.get('risk', 'manual')}): "
            f"{outcome.get('status', 'planned')}"
        )
    changed_paths = sorted({
        str(path)
        for outcome in payload.get("outcomes") or []
        for path in outcome.get("changed_paths") or []
    })
    lines.extend(["", "## Candidate changed paths", ""])
    if changed_paths:
        lines.extend(f"- `{_redact(path)}`" for path in changed_paths)
    else:
        lines.append("- None (environment-only repair)")
    lines.extend([
        "",
        "## Verification",
        "",
        f"- Doctor verification present: {bool(payload.get('verification'))}",
        "- This report was prepared locally and was not published automatically.",
        "",
    ])
    report.write_text(_redact("\n".join(lines)), encoding="utf-8")
    _append_history(context.global_root, {
        "type": "repair.pr_report.prepared",
        "plan_id": plan_id,
        "path": str(report),
    })
    return report
