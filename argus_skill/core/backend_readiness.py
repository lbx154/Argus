"""Shared backend/auth readiness contract for setup, doctor, and startup."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..agent_cli.runner_backend import normalize_runner_backend, resolve_runner_bin
from .knob_store import read_persisted_knobs, write_persisted_knobs

AUTH_MODE_KNOB = "ARGUS_SKILL_BACKEND_AUTH_MODE"
AUTH_MODE_SUBSCRIPTION = "subscription_cli"
AUTH_MODE_MODEL_API = "model_api"
ALLOW_PRERELEASE_ENV = "ARGUS_SKILL_ALLOW_BACKEND_PRERELEASE"

CODEX_MIN_VERSION = (0, 128, 0)
CODEX_RECOMMENDED_VERSION = "0.144.5"
DEFAULT_MODEL_API_ROUTES = ("engineer", "reviewer", "text")

SETUP_EXIT_USAGE = 2
SETUP_EXIT_NOT_READY = 3
SETUP_EXIT_PERSISTENCE = 4

_SUPPORTED_BACKENDS = frozenset({"codex", "copilot", "claude", "opencode"})
_VERSION_RE = re.compile(
    r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?:[-+]([0-9A-Za-z.-]+))?"
)
_AUTH_COMMANDS: dict[str, tuple[str, ...]] = {
    "codex": ("login", "status"),
    "claude": ("auth", "status"),
    "opencode": ("auth", "list"),
}
_INSTALL_COMMANDS = {
    "codex": "npm install -g @openai/codex@latest",
    "copilot": "npm install -g @github/copilot",
    "claude": "npm install -g @anthropic-ai/claude-code",
    "opencode": "curl -fsSL https://opencode.ai/install | bash",
}
_LOGIN_COMMANDS = {
    "codex": "codex login",
    "copilot": "copilot login",
    "claude": "claude auth login",
    "opencode": "opencode auth login",
}


@dataclass(frozen=True)
class BackendProfile:
    backend: str
    auth_mode: str
    backend_source: str
    auth_mode_source: str

    @property
    def config_source(self) -> str:
        return (
            f"backend={self.backend_source}; "
            f"auth_mode={self.auth_mode_source}"
        )


@dataclass(frozen=True)
class ReadinessProblem:
    capability: str
    detail: str
    remediation: str


@dataclass
class BackendReadiness:
    profile: BackendProfile
    executable: str = ""
    version: str = ""
    auth_checked: bool = False
    vault_path: str = ""
    problems: list[ReadinessProblem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def remediation(self) -> str:
        return self.problems[0].remediation if self.problems else ""


def _clean_auth_mode(raw: str | None) -> str:
    value = str(raw or "").strip().lower().replace("-", "_")
    if value in {"cli", "subscription", "subscription_cli"}:
        return AUTH_MODE_SUBSCRIPTION
    if value in {"api", "vault", "model_api"}:
        return AUTH_MODE_MODEL_API
    return ""


def resolve_backend_profile(
    backend: str | None = None,
    auth_mode: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> BackendProfile:
    env_map = env if env is not None else os.environ
    persisted = read_persisted_knobs()

    explicit_backend = str(backend or "").strip()
    if explicit_backend:
        backend_value, backend_source = explicit_backend, "argument"
    else:
        backend_value, backend_source = "", "default"
        for name in ("ARGUS_SKILL_RUNNER_BACKEND", "ARGUS_SKILL_LIFE_BACKEND"):
            value = str(env_map.get(name) or "").strip()
            if value:
                backend_value, backend_source = value, f"env:{name}"
                break
        if not backend_value:
            for name in ("ARGUS_SKILL_RUNNER_BACKEND", "ARGUS_SKILL_LIFE_BACKEND"):
                value = str(persisted.get(name) or "").strip()
                if value:
                    backend_value, backend_source = value, f"persisted:{name}"
                    break
    raw_backend = str(backend_value or "codex").strip().lower()
    normalized_backend = (
        normalize_runner_backend(raw_backend)
        if raw_backend in _SUPPORTED_BACKENDS or raw_backend == "opencod"
        else raw_backend
    )

    explicit_auth = str(auth_mode or "").strip()
    if explicit_auth:
        auth_value, auth_source = explicit_auth, "argument"
    else:
        auth_value = str(env_map.get(AUTH_MODE_KNOB) or "").strip()
        auth_source = f"env:{AUTH_MODE_KNOB}" if auth_value else "default"
        if not auth_value:
            auth_value = str(persisted.get(AUTH_MODE_KNOB) or "").strip()
            if auth_value:
                auth_source = f"persisted:{AUTH_MODE_KNOB}"
    normalized_auth = _clean_auth_mode(auth_value) or AUTH_MODE_SUBSCRIPTION
    return BackendProfile(
        backend=normalized_backend,
        auth_mode=normalized_auth,
        backend_source=backend_source,
        auth_mode_source=auth_source,
    )


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _run_text(
    command: Sequence[str],
    *,
    timeout_s: float,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        check=False,
    )


def _extract_version(text: str) -> tuple[str, tuple[int, int, int], str] | None:
    match = _VERSION_RE.search(text)
    if match is None:
        return None
    version = match.group(0)
    return (
        version,
        (int(match.group(1)), int(match.group(2)), int(match.group(3))),
        str(match.group(4) or ""),
    )


def _probe_copilot_auth(executable: str, timeout_s: float) -> tuple[bool, str]:
    """Create an ACP session without sending a prompt or spending model tokens."""
    try:
        from ..agent_cli.copilot_acp import CopilotAcpClient

        client = CopilotAcpClient(executable, lean=True)
        try:
            client._ensure_started()
            client._new_session(str(Path.cwd()))
        finally:
            client.close()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def _probe_cli_auth(
    backend: str,
    executable: str,
    *,
    timeout_s: float,
) -> tuple[bool, str]:
    if backend == "copilot":
        return _probe_copilot_auth(executable, timeout_s)
    suffix = _AUTH_COMMANDS.get(backend)
    if suffix is None:
        return False, f"no read-only authentication probe is defined for {backend}"
    try:
        result = _run_text((executable, *suffix), timeout_s=timeout_s)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if result.returncode == 0:
        return True, ""
    detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
    return False, detail[:300]


def _check_model_api_routes(
    report: BackendReadiness,
    *,
    required_routes: Iterable[str],
    probe_vault: bool,
    timeout_s: float,
) -> None:
    from ..tools.capability_vault import default_vault_path, load_model_api_route

    required = tuple(dict.fromkeys(str(route).strip() for route in required_routes if route))
    report.vault_path = str(default_vault_path())
    for name in required:
        route = load_model_api_route(name)
        if route is None or not route.usable:
            report.problems.append(
                ReadinessProblem(
                    capability=f"model_api:{name}",
                    detail=(
                        f"required route is not configured "
                        f"(vault={report.vault_path})"
                    ),
                    remediation=(
                        "run `argus --setup --backend codex --auth-mode model_api` "
                        f"or inspect `argus --model-api-status`; vault override: "
                        "ARGUS_SKILL_CAPABILITY_VAULT"
                    ),
                )
            )
    if report.problems or not required or not probe_vault:
        return

    from .vault_preflight import check_routes

    try:
        vault_report = check_routes(
            required=required,
            optional=(),
            timeout_s=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001
        report.problems.append(
            ReadinessProblem(
                capability="model_api:probe",
                detail=f"readiness probe failed: {type(exc).__name__}: {exc}",
                remediation=(
                    "retry `argus --doctor`; verify network access and the configured "
                    "model API routes"
                ),
            )
        )
        return
    for check in vault_report.required_failures:
        report.problems.append(
            ReadinessProblem(
                capability=f"model_api:{check.name}",
                detail=(
                    f"route unreachable"
                    f"{f' (HTTP {check.http_status})' if check.http_status else ''}: "
                    f"{check.error or 'unknown error'}"
                ),
                remediation=(
                    "run `argus --model-api-status`, then fix the route or rerun "
                    "`argus --setup --backend codex --auth-mode model_api`"
                ),
            )
        )


def check_backend_readiness(
    backend: str | None = None,
    auth_mode: str | None = None,
    *,
    runner_bin: str | None = None,
    probe_auth: bool = True,
    probe_vault: bool = False,
    required_routes: Iterable[str] = DEFAULT_MODEL_API_ROUTES,
    allow_prerelease: bool | None = None,
    timeout_s: float = 8.0,
    env: Mapping[str, str] | None = None,
) -> BackendReadiness:
    env_map = env if env is not None else os.environ
    profile = resolve_backend_profile(backend, auth_mode, env=env_map)
    report = BackendReadiness(profile=profile)
    if profile.backend not in _SUPPORTED_BACKENDS:
        report.problems.append(
            ReadinessProblem(
                "backend",
                f"unsupported backend {profile.backend!r}",
                "choose one of: codex, copilot, claude, opencode",
            )
        )
        return report
    if profile.auth_mode == AUTH_MODE_MODEL_API and profile.backend != "codex":
        report.problems.append(
            ReadinessProblem(
                "auth_mode",
                f"{profile.auth_mode} is only supported with the codex backend",
                f"use `--auth-mode {AUTH_MODE_SUBSCRIPTION}` for {profile.backend}",
            )
        )
        return report

    configured_bin = str(
        runner_bin or env_map.get("ARGUS_SKILL_RUNNER_BIN") or ""
    ).strip()
    executable = resolve_runner_bin(profile.backend, configured_bin or None)
    if executable is None:
        report.problems.append(
            ReadinessProblem(
                "backend executable",
                f"`{profile.backend}` was not found on PATH",
                (
                    f"{_INSTALL_COMMANDS[profile.backend]}, or set "
                    "ARGUS_SKILL_RUNNER_BIN"
                ),
            )
        )
        return report
    report.executable = executable

    try:
        version_result = _run_text((executable, "--version"), timeout_s=timeout_s)
    except (OSError, subprocess.SubprocessError) as exc:
        report.problems.append(
            ReadinessProblem(
                "backend version",
                f"version check failed: {type(exc).__name__}: {exc}",
                f"reinstall with `{_INSTALL_COMMANDS[profile.backend]}`",
            )
        )
        return report
    rendered_version = (version_result.stdout or version_result.stderr).strip()
    parsed = _extract_version(rendered_version)
    if version_result.returncode != 0 or parsed is None:
        report.problems.append(
            ReadinessProblem(
                "backend version",
                (
                    f"`{profile.backend} --version` was not usable "
                    f"(exit={version_result.returncode}, output={rendered_version[:160]!r})"
                ),
                f"reinstall with `{_INSTALL_COMMANDS[profile.backend]}`",
            )
        )
        return report
    report.version, version_tuple, prerelease = parsed
    if profile.backend == "codex":
        allow_pre = (
            _truthy(env_map.get(ALLOW_PRERELEASE_ENV))
            if allow_prerelease is None
            else bool(allow_prerelease)
        )
        if prerelease and not allow_pre:
            report.problems.append(
                ReadinessProblem(
                    "backend version",
                    f"Codex {report.version} is a prerelease and is not enabled",
                    (
                        "install stable Codex with `npm install -g @openai/codex@latest`, "
                        f"or explicitly set {ALLOW_PRERELEASE_ENV}=1"
                    ),
                )
            )
        elif version_tuple < CODEX_MIN_VERSION:
            report.problems.append(
                ReadinessProblem(
                    "backend version",
                    (
                        f"Codex {report.version} is below the supported stable floor "
                        ">=0.128.0"
                    ),
                    "upgrade with `npm install -g @openai/codex@latest`",
                )
            )
        elif report.version != CODEX_RECOMMENDED_VERSION:
            report.warnings.append(
                f"tested recommendation is Codex {CODEX_RECOMMENDED_VERSION}; "
                f"detected {report.version}"
            )
    if report.problems:
        return report

    if profile.auth_mode == AUTH_MODE_MODEL_API:
        _check_model_api_routes(
            report,
            required_routes=required_routes,
            probe_vault=probe_vault,
            timeout_s=timeout_s,
        )
    elif probe_auth:
        report.auth_checked = True
        ok, detail = _probe_cli_auth(
            profile.backend,
            executable,
            timeout_s=timeout_s,
        )
        if not ok:
            report.problems.append(
                ReadinessProblem(
                    "authentication",
                    f"{profile.backend} authentication is not usable: {detail}",
                    f"run `{_LOGIN_COMMANDS[profile.backend]}`, then `argus --doctor`",
                )
            )
    return report


def persist_validated_profile(report: BackendReadiness) -> bool:
    if not report.ok:
        return False
    return write_persisted_knobs(
        {
            "ARGUS_SKILL_RUNNER_BACKEND": report.profile.backend,
            AUTH_MODE_KNOB: report.profile.auth_mode,
            "ARGUS_SKILL_BACKEND_VALIDATED_VERSION": report.version,
        }
    )


def format_backend_readiness(report: BackendReadiness) -> str:
    lines = [
        "argus backend readiness",
        (
            f"  backend={report.profile.backend} "
            f"auth_mode={report.profile.auth_mode}"
        ),
        f"  configuration source: {report.profile.config_source}",
    ]
    if report.executable:
        lines.append(f"  executable: {report.executable}")
    if report.version:
        lines.append(f"  version: {report.version}")
    if report.vault_path:
        lines.append(f"  capability vault: {report.vault_path}")
    for warning in report.warnings:
        lines.append(f"  warning: {warning}")
    if report.ok:
        auth = "checked" if report.auth_checked else "configuration validated"
        lines.append(f"  ready: yes ({auth})")
    else:
        lines.append("  ready: no")
        for problem in report.problems:
            lines.append(f"  failed capability: {problem.capability}")
            lines.append(f"    {problem.detail}")
            lines.append(f"    fix: {problem.remediation}")
    return "\n".join(lines)


def profile_json(report: BackendReadiness) -> str:
    return json.dumps(
        {
            "backend": report.profile.backend,
            "auth_mode": report.profile.auth_mode,
            "config_source": report.profile.config_source,
            "executable": report.executable,
            "version": report.version,
            "ok": report.ok,
            "problems": [
                {
                    "capability": problem.capability,
                    "detail": problem.detail,
                    "remediation": problem.remediation,
                }
                for problem in report.problems
            ],
            "warnings": report.warnings,
        },
        indent=2,
        sort_keys=True,
    )
