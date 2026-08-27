"""Cross-platform, read-only Doctor inventory and blockage classification."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import DoctorFinding, DoctorReport


@dataclass(frozen=True)
class DoctorContext:
    global_root: Path
    project_root: Path
    checkout: Path | None = None
    python_executable: Path = Path(sys.executable)
    web_host: str = "127.0.0.1"
    web_port: int = 8799
    desktop_user_data: Path | None = None
    install_mode: str = "source"
    backend: str | None = None
    auth_mode: str | None = None
    allow_prerelease: bool = False

    def fingerprint_payload(self) -> dict[str, str | int]:
        return {
            "global_root": str(self.global_root.expanduser().resolve()),
            "project_root": str(self.project_root.expanduser().resolve()),
            "checkout": str(self.checkout.expanduser().resolve()) if self.checkout else "",
            "python_executable": str(self.python_executable.expanduser().resolve()),
            "web_host": self.web_host,
            "web_port": int(self.web_port),
            "platform": platform.system(),
            "install_mode": self.install_mode,
            "machine": platform.machine(),
        }

    @property
    def target_fingerprint(self) -> str:
        payload = json.dumps(self.fingerprint_payload(), sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def _finding(
    code: str,
    scope: str,
    ok: bool,
    status: str,
    detail: str,
    *,
    severity: str = "info",
    evidence: dict[str, Any] | None = None,
    actions: tuple[str, ...] = (),
    recommendation: str = "",
) -> DoctorFinding:
    return DoctorFinding(
        code=code,
        scope=scope,  # type: ignore[arg-type]
        severity=("info" if ok else severity),  # type: ignore[arg-type]
        ok=ok,
        status=status,
        detail=detail,
        evidence=evidence or {},
        repair_action_ids=actions if not ok else (),
        recommendation="" if ok else recommendation,
    )


def _command_version(name: str, *, timeout: float = 3.0) -> tuple[bool, str, str]:
    executable = shutil.which(name)
    if not executable:
        return False, "", f"{name} was not found on PATH"
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, executable, f"{type(exc).__name__}: {exc}"
    lines = (completed.stdout or completed.stderr or "").strip().splitlines()
    detail = lines[0] if lines else f"exit {completed.returncode}"
    return completed.returncode == 0, executable, detail


def _probe_web(host: str, port: int) -> tuple[str, dict[str, Any]]:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            pass
    except OSError:
        return "stopped", {}
    try:
        request = Request(f"http://{host}:{port}/api/meta")
        with urlopen(request, timeout=1.5) as response:  # noqa: S310 - loopback/operator target
            payload = json.load(response)
        if payload.get("service") == "argus-skill-webapi":
            return "compatible", payload
        return "foreign_http", payload if isinstance(payload, dict) else {}
    except HTTPError as exc:
        # Desktop protects loopback with a bearer token. A 401 still proves an
        # Argus-shaped HTTP listener only when the protocol header is present.
        protocol = str(exc.headers.get("x-argus-protocol") or "")
        return ("protected_argus" if protocol.startswith("argus.webapi/") else "foreign_http"), {
            "http_status": exc.code,
            "protocol": protocol,
        }
    except (URLError, OSError, ValueError, json.JSONDecodeError):
        return "occupied_unresponsive", {}


def _checkout_finding(context: DoctorContext) -> list[DoctorFinding]:
    checkout = context.checkout
    if checkout is None:
        packaged = context.install_mode in {"wheel", "frozen"}
        return [_finding(
            "ARGUS-INSTALL-001", "install", packaged,
            f"{context.install_mode}_install" if packaged else "checkout_unknown",
            (
                f"Argus {context.install_mode} installation at {context.python_executable}"
                if packaged else "Argus source checkout could not be identified"
            ),
            severity="error", actions=("refresh_path_memory",),
            recommendation="pass --root or run the standalone argus-doctor from the checkout",
            evidence={"install_mode": context.install_mode},
        )]
    root = checkout.expanduser().resolve()
    valid = (root / "pyproject.toml").is_file() and (root / "argus_skill").is_dir()
    findings = [_finding(
        "ARGUS-INSTALL-001", "install", valid,
        "source_checkout" if valid else "broken_checkout",
        str(root) if valid else f"missing pyproject.toml or argus_skill under {root}",
        severity="critical", actions=("refresh_path_memory",),
        recommendation="restore a complete checkout before applying runtime repairs",
        evidence={"checkout": str(root)},
    )]
    if not valid:
        return findings

    manifest = root / "argus_skill" / "release_manifest.json"
    web = root / "frontend" / "web" / "dist" / "index.html"
    tui = root / "frontend" / "tui" / "bundle" / "argus.mjs"
    missing = [str(path.relative_to(root)) for path in (manifest, web, tui) if not path.is_file()]
    findings.append(_finding(
        "ARGUS-ASSET-001", "install", not missing,
        "assets_ready" if not missing else "assets_missing",
        "release manifest and Web/TUI assets are present" if not missing else f"missing: {', '.join(missing)}",
        severity="error", actions=("rebuild_release_assets",),
        recommendation="rebuild release assets with the checkout interpreter",
        evidence={"missing": missing},
    ))

    git_ok, git_bin, git_detail = _command_version("git")
    branch = ""
    dirty = False
    if git_ok:
        try:
            branch_run = subprocess.run(
                [git_bin, "-C", str(root), "branch", "--show-current"],
                check=False, capture_output=True, text=True, encoding="utf-8", timeout=3,
            )
            status_run = subprocess.run(
                [git_bin, "-C", str(root), "status", "--porcelain"],
                check=False, capture_output=True, text=True, encoding="utf-8", timeout=3,
            )
            branch = branch_run.stdout.strip()
            dirty = bool(status_run.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    findings.append(_finding(
        "ARGUS-GIT-001", "update", git_ok,
        "git_ready" if git_ok else "git_missing", git_detail,
        severity="error", recommendation="install Git and add it to PATH",
        evidence={"executable": git_bin, "branch": branch, "dirty": dirty},
    ))
    return findings


def _runtime_findings(context: DoctorContext) -> list[DoctorFinding]:
    python_path = context.python_executable.expanduser()
    python_ok = python_path.is_file() and sys.version_info >= (3, 11)
    findings = [_finding(
        "ARGUS-PYTHON-001", "cli", python_ok,
        "python_ready" if python_ok else "python_invalid",
        f"{platform.python_version()} at {python_path}",
        severity="critical", actions=("install_editable",),
        recommendation="use Python 3.11+ and reinstall Argus into the intended environment",
        evidence={"executable": str(python_path), "version": platform.python_version()},
    )]
    if context.install_mode == "frozen" or getattr(sys, "frozen", False):
        findings.append(_finding(
            "ARGUS-NODE-001",
            "cli",
            True,
            "bundled_runtime",
            "Node.js is bundled or not required by the frozen Desktop runtime",
        ))
        return findings
    node_ok, node_bin, node_detail = _command_version("node")
    match = re.search(r"v?(\d+)(?:\.(\d+))?", node_detail)
    node_version = (
        (int(match.group(1)), int(match.group(2) or 0))
        if match else (0, 0)
    )
    node_supported = node_ok and node_version >= (22, 12)
    findings.append(_finding(
        "ARGUS-NODE-001", "cli", node_supported,
        "node_ready" if node_supported else "node_missing_or_unsupported", node_detail,
        severity="error", recommendation="install Node.js 22.12+ and add it to PATH",
        evidence={"executable": node_bin, "version": node_detail},
    ))
    return findings


def _desktop_finding(context: DoctorContext) -> DoctorFinding:
    if getattr(sys, "frozen", False):
        return _finding(
            "ARGUS-DESKTOP-001", "desktop", True, "frozen_runtime",
            "running from a frozen Argus distribution",
            evidence={"executable": sys.executable},
        )
    if context.checkout is None:
        return _finding(
            "ARGUS-DESKTOP-001", "desktop", True, "not_inspectable",
            "no source checkout was selected; Desktop source dependencies were not inspected",
        )
    desktop = context.checkout / "desktop"
    package = desktop / "package.json"
    if not package.is_file():
        return _finding(
            "ARGUS-DESKTOP-001", "desktop", True, "not_present",
            "this checkout has no Desktop source package",
        )
    candidates = [
        desktop / "node_modules" / "electron" / "path.txt",
        desktop / "node_modules" / "electron" / "dist" / "electron.exe",
    ]
    ready = all(path.is_file() for path in candidates) if os.name == "nt" else candidates[0].is_file()
    return _finding(
        "ARGUS-DESKTOP-001", "desktop", ready,
        "electron_ready" if ready else "electron_binary_missing",
        "Electron development runtime is installed" if ready else "Electron package exists without an installed runtime binary",
        severity="warning", actions=("install_electron_binary",),
        recommendation="run the registered Electron install action from desktop/",
        evidence={"desktop": str(desktop)},
    )


def _desktop_log_finding(context: DoctorContext) -> DoctorFinding:
    user_data = context.desktop_user_data
    if user_data is None:
        return _finding(
            "ARGUS-DESKTOP-LOG-001", "desktop", True, "not_configured",
            "Desktop user-data path is not configured",
        )
    log_path = user_data / "logs" / "desktop.log"
    if not log_path.is_file():
        return _finding(
            "ARGUS-DESKTOP-LOG-001", "desktop", True, "no_log",
            f"no Desktop log at {log_path}",
        )
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
    except OSError as exc:
        return _finding(
            "ARGUS-DESKTOP-LOG-001", "desktop", False, "log_unreadable",
            f"{type(exc).__name__}: {exc}", severity="warning",
            recommendation="export Desktop diagnostics and inspect file permissions",
        )
    error_markers = (
        "backend error:", "backend failed", "failed to start", "startup timed out",
        "electron uninstall", "uncaught main-process error",
    )
    last_error = max(
        (index for index, line in enumerate(lines) if any(marker in line.casefold() for marker in error_markers)),
        default=-1,
    )
    last_ready = max(
        (index for index, line in enumerate(lines) if "backend ready:" in line.casefold()),
        default=-1,
    )
    unresolved = last_error > last_ready
    return _finding(
        "ARGUS-DESKTOP-LOG-001", "desktop", not unresolved,
        "recent_startup_error" if unresolved else "log_healthy",
        (
            lines[last_error][-500:] if unresolved
            else f"Desktop log has no unresolved startup error: {log_path}"
        ),
        severity="error",
        recommendation="run standalone argus-doctor, then retry Desktop or export diagnostics",
        evidence={"path": str(log_path), "last_error_after_ready": unresolved},
    )


def _daemon_findings(context: DoctorContext) -> list[DoctorFinding]:
    project_root = context.project_root.expanduser()
    try:
        from ..daemon.state import read_daemon_status
        status = read_daemon_status(project_root)
    except Exception as exc:  # noqa: BLE001 - Doctor must remain fail-soft
        return [_finding(
            "ARGUS-DAEMON-001", "daemon", False, "status_unreadable",
            f"{type(exc).__name__}: {exc}", severity="error",
            recommendation="inspect daemon sidecars and run Doctor again",
        )]
    if status.alive:
        state = str(status.health_state or "unknown")
        stalled = bool(status.stalled)
        drain_path = project_root / "daemon.drain-request.json"
        draining = drain_path.exists()
        drain_age = 0.0
        if draining:
            try:
                drain_payload = json.loads(drain_path.read_text(encoding="utf-8"))
                drain_age = max(0.0, time.time() - float(drain_payload.get("requested_at") or 0.0))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                try:
                    drain_age = max(0.0, time.time() - drain_path.stat().st_mtime)
                except OSError:
                    drain_age = 0.0
        semantic = (
            "draining" if draining
            else "stalled" if stalled else state
        )
        return [_finding(
            "ARGUS-DAEMON-001", "daemon", not stalled,
            semantic, f"daemon pid {status.pid} is alive; health={semantic}",
            severity="error",
            actions=(),
            recommendation="interrupt the verified owned daemon after reviewing its latest boot log",
            evidence={
                "pid": status.pid,
                "started_at_iso": status.started_at_iso,
                "health_state": state,
                "seconds_since_progress": status.seconds_since_progress,
                "draining": draining,
                "drain_age_seconds": drain_age,
            },
        )]
    pid_path = project_root / "daemon.pid"
    stale_pid = None
    try:
        stale_pid = int(pid_path.read_text(encoding="ascii").strip()) if pid_path.exists() else None
    except (OSError, ValueError):
        stale_pid = None
    findings = [_finding(
        "ARGUS-DAEMON-001", "daemon", True, "stopped",
        "no daemon is running",
    )]
    if pid_path.exists():
        findings.append(_finding(
            "ARGUS-STATE-001", "daemon", False, "stale_pid",
            f"daemon.pid remains for non-running pid {stale_pid or 'unknown'}",
            severity="error", actions=("remove_verified_stale_daemon_pid",),
            recommendation="remove only after lock and liveness revalidation",
            evidence={"pid": stale_pid, "path": str(pid_path)},
        ))
    control_files = [
        name for name in ("daemon.stop-request.json", "daemon.drain-request.json")
        if (project_root / name).exists()
    ]
    if control_files:
        findings.append(_finding(
            "ARGUS-STATE-002", "daemon", False, "stale_control_request",
            f"stopped daemon retains control files: {', '.join(control_files)}",
            severity="warning", actions=("remove_dead_daemon_control_files",),
            recommendation="remove requests only after confirming no daemon owns them",
            evidence={"files": control_files},
        ))
    return findings


def render_full_report(report: DoctorReport) -> str:
    lines = ["argus doctor — cross-platform diagnostics", ""]
    for finding in report.findings:
        glyph = "✓" if finding.ok else "!" if finding.severity == "warning" else "✗"
        lines.append(
            f"{glyph} {finding.code} [{finding.scope}/{finding.status}] {finding.detail}"
        )
        if finding.recommendation:
            lines.append(f"    fix: {finding.recommendation}")
        if finding.repair_action_ids:
            lines.append(
                f"    repair: {', '.join(finding.repair_action_ids)}"
            )
    lines.extend([
        "",
        "all blocking checks passed" if report.ok else "blocking issues found",
    ])
    return "\n".join(lines)


def run_full_doctor(
    context: DoctorContext,
    *,
    include_backend: bool = True,
    probe_auth: bool = False,
) -> DoctorReport:
    """Return a complete read-only report. No directory is created here."""
    findings: list[DoctorFinding] = [
        _finding(
            "ARGUS-HOST-001", "host", True, "supported_host",
            f"{platform.system()} {platform.release()} {platform.machine()}",
            evidence={
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
            },
        )
    ]
    findings.extend(_checkout_finding(context))
    findings.extend(_runtime_findings(context))

    special_prompt = context.global_root / "special_prompts" / "10-house-rules.md"
    findings.append(_finding(
        "ARGUS-CONFIG-001", "install", special_prompt.is_file(),
        "house_rules_ready" if special_prompt.is_file() else "house_rules_missing",
        str(special_prompt), severity="error", actions=("create_house_rules",),
        recommendation="review and create machine-specific operator house rules",
    ))

    web_status, web_meta = _probe_web(context.web_host, context.web_port)
    web_ok = web_status in {"stopped", "compatible", "protected_argus"}
    findings.append(_finding(
        "ARGUS-WEB-001", "web", web_ok, web_status,
        f"{context.web_host}:{context.web_port} is {web_status}",
        severity="error",
        recommendation="choose a free port or stop only a verified owned listener",
        evidence={
            "host": context.web_host,
            "port": context.web_port,
            "service": web_meta.get("service"),
            "runtime": web_meta.get("runtime", {}),
        },
    ))
    findings.append(_desktop_finding(context))
    findings.append(_desktop_log_finding(context))
    findings.extend(_daemon_findings(context))

    memory_path = context.global_root / "repairs" / "path-memory.json"
    findings.append(_finding(
        "ARGUS-PATH-001", "install", memory_path.is_file(),
        "path_memory_ready" if memory_path.is_file() else "path_memory_missing",
        str(memory_path), severity="warning", actions=("refresh_path_memory",),
        recommendation="record canonical runtime paths for future Doctor runs",
    ))

    if include_backend:
        try:
            from ..webapi.diagnostics import run_diagnostics
            legacy = run_diagnostics(
                context.project_root,
                global_root=context.global_root,
                backend=context.backend,
                auth_mode=context.auth_mode,
                probe_auth=probe_auth,
                allow_prerelease=context.allow_prerelease,
            )
            for check in legacy:
                if check.name not in {"daemon", "lock sanity", "empty session"}:
                    code = {
                        "backend preflight": "ARGUS-BACKEND-001",
                        "model API capability": "ARGUS-BACKEND-002",
                    }.get(check.name, "ARGUS-BACKEND-003")
                    findings.append(_finding(
                        code, "backend", bool(check.ok),
                        "ready" if check.ok else "not_ready", check.detail,
                        severity="error", recommendation=check.fix,
                    ))
        except Exception as exc:  # noqa: BLE001
            findings.append(_finding(
                "ARGUS-BACKEND-001", "backend", False, "probe_failed",
                f"{type(exc).__name__}: {exc}", severity="error",
                recommendation="run bootstrap Doctor and inspect backend installation",
            ))

    return DoctorReport(
        schema_version=1,
        target_fingerprint=context.target_fingerprint,
        generated_at=datetime.now(timezone.utc).isoformat(),
        findings=tuple(findings),
    )
