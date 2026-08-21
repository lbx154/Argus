"""Shared backend/auth readiness contract for setup, doctor, and startup."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..agent_cli.runner_backend import (
    SUPPORTED_BACKENDS,
    normalize_runner_backend,
    resolve_runner_bin,
)
from .knob_store import read_persisted_knobs, write_persisted_knobs
from .knobs import resolve_runner_bin_setting

AUTH_MODE_KNOB = "ARGUS_SKILL_BACKEND_AUTH_MODE"
AUTH_MODE_SUBSCRIPTION = "subscription_cli"
AUTH_MODE_MODEL_API = "model_api"
ALLOW_PRERELEASE_ENV = "ARGUS_SKILL_ALLOW_BACKEND_PRERELEASE"

CODEX_MIN_VERSION = (0, 128, 0)
CODEX_RECOMMENDED_VERSION = "0.144.5"
PI_MIN_VERSION = (0, 83, 0)
DEFAULT_MODEL_API_ROUTES = ("engineer", "reviewer", "text")
DEFAULT_READINESS_TIMEOUT_S = 30.0

SETUP_EXIT_USAGE = 2
SETUP_EXIT_NOT_READY = 3
SETUP_EXIT_PERSISTENCE = 4

_SUPPORTED_BACKENDS = frozenset(SUPPORTED_BACKENDS)
_VERSION_RE = re.compile(
    r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?:[-+]([0-9A-Za-z.-]+))?"
)
_AUTH_COMMANDS: dict[str, tuple[str, ...]] = {
    "codex": ("login", "status"),
    "claude": ("auth", "status"),
    "opencode": ("auth", "list"),
    # qodercli exits non-zero from --list-models when unauthenticated, so it
    # doubles as a read-only auth probe.
    "qoder": ("--list-models",),
    # dsh exposes no read-only auth-status command that does not cost a model
    # call; the probe below short-circuits on DEEPSEEK_API_KEY instead.
    "dsh": (),
}
_INSTALL_COMMANDS = {
    "codex": "npm install -g @openai/codex@latest",
    "copilot": "npm install -g @github/copilot",
    "claude": "npm install -g @anthropic-ai/claude-code",
    "opencode": "curl -fsSL https://opencode.ai/install | bash",
    "pi": "npm install -g --ignore-scripts @earendil-works/pi-coding-agent",
    "grok": "curl -fsSL https://x.ai/cli/install.sh | bash",
    "qoder": "npm install -g @qoder-ai/qodercli",
    "dsh": "npm install -g @deepseek-ai/dsh",
}
_LOGIN_COMMANDS = {
    "codex": "codex login",
    "copilot": "copilot login",
    "claude": "claude auth login",
    "opencode": "opencode auth login",
    "pi": "pi, then /login",
    "grok": "grok login",
    "qoder": "qodercli login",
    "dsh": "export DEEPSEEK_API_KEY=<key> in the launching environment (or set it on the dsh web Models page)",
}


def backend_install_command(
    backend: str,
    *,
    platform_name: str | None = None,
) -> str:
    """Return one platform-appropriate official installation hint."""
    platform_name = os.name if platform_name is None else platform_name
    if platform_name != "nt":
        return _INSTALL_COMMANDS[backend]
    windows = {
        "copilot": "npm.cmd install -g @github/copilot",
        "codex": "npm.cmd install -g @openai/codex@latest",
        "claude": "npm.cmd install -g @anthropic-ai/claude-code",
        "pi": "npm.cmd install -g --ignore-scripts @earendil-works/pi-coding-agent",
        "opencode": "choose a Windows installer at https://opencode.ai/docs/#windows",
        "grok": "use the official Windows instructions at https://x.ai/cli",
        "qoder": "npm.cmd install -g @qoder-ai/qodercli",
        "dsh": "npm.cmd install -g @deepseek-ai/dsh",
    }
    return windows[backend]


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


def _run_backend_version(
    backend: str,
    executable: str,
    *,
    timeout_s: float,
) -> subprocess.CompletedProcess[str]:
    command: tuple[str, ...] = (executable, "--version")
    if backend == "pi" and sys.platform == "darwin" and Path("/usr/bin/script").is_file():
        command = ("/usr/bin/script", "-q", "/dev/null", *command)
    return _run_text(command, timeout_s=timeout_s)


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


def _parse_pi_model_catalog(stdout: str) -> dict[str, set[str]]:
    """Parse ``pi --list-models`` into ``{provider: {model_id, ...}}``.

    Pi prints one ``provider model context max-out thinking images`` row per
    AUTHENTICATED model, so the parsed table is exactly the set of selectors
    that can actually be bought right now — which is what readiness must judge.
    """
    catalog: dict[str, set[str]] = {}
    for line in stdout.splitlines():
        row = line.strip()
        if not row or set(row) <= {"-", " "}:
            continue
        fields = row.split()
        if len(fields) < 2:
            continue
        provider = fields[0]
        if provider.casefold() in {"provider", "warning:", "error:"}:
            continue
        catalog.setdefault(provider, set()).add(fields[1])
    return catalog


def _probe_pi_catalog(
    executable: str, timeout_s: float
) -> tuple[dict[str, set[str]], str]:
    """Read Pi's authenticated model catalog without spending a model turn."""
    try:
        result = _run_text((executable, "--list-models"), timeout_s=timeout_s)
    except (OSError, subprocess.SubprocessError) as exc:
        return {}, f"{type(exc).__name__}: {exc}"
    catalog = _parse_pi_model_catalog(result.stdout)
    if result.returncode == 0 and catalog:
        return catalog, ""
    detail = (result.stderr or result.stdout or "no authenticated Pi models").strip()
    return {}, detail[:300]


#: Roles whose configured model Argus will hand to the backend verbatim.
_MODEL_ROLES: tuple[tuple[str, str], ...] = (
    ("manager", "ARGUS_SKILL_MANAGER_MODEL"),
    ("planner", "ARGUS_SKILL_PLAN_MODEL"),
    ("engineer", "ARGUS_SKILL_ENGINEER_MODEL"),
    ("reviewer", "ARGUS_SKILL_REVIEWER_MODEL"),
)

#: Model-id prefixes that identify the vendor catalog an id was minted for.
#: Deliberately incomplete: an id matching nothing here is simply unknown, and
#: an unknown id never raises a complaint (a private gateway may serve any
#: name it likes). Only a POSITIVE match on the WRONG catalog is actionable.
_MODEL_CATALOG_PREFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("openai", ("gpt-", "gpt5", "o1-", "o3-", "o4-", "codex-")),
    ("anthropic", ("claude-",)),
    ("xai", ("grok-",)),
    ("deepseek", ("deepseek-",)),
)

#: Backends whose CLI serves exactly ONE vendor catalog, so a model id from a
#: different catalog cannot resolve. Deliberately excluded:
#: ``copilot`` (GitHub resells several vendors, Anthropic ids included),
#: ``qoder`` (its own catalog; read it with ``qodercli --list-models``),
#: ``pi`` / ``opencode`` (provider-agnostic fronts — ``_check_pi_model_routing``
#: judges Pi against its real authenticated catalog instead), and ``dsh``
#: (Argus sends ``provider/model`` through the ``ARGUS_DSH_*`` overlay, so the
#: bare id here is not the whole selector and judging it would misfire).
_BACKEND_MODEL_CATALOG: dict[str, str] = {
    "codex": "openai",
    "claude": "anthropic",
    "grok": "xai",
}

#: Model Argus adopts for a backend when the operator never chose one.
#: Populated only where the id is verified against a real CLI; a backend absent
#: from this table keeps the shared default and relies on
#: :func:`_check_backend_model_catalog` to say so out loud.
_BACKEND_DEFAULT_MODELS: dict[str, str] = {
    "claude": "claude-opus-5",
}


def _model_catalog(model: str) -> str:
    """Vendor catalog a model id belongs to, or ``""`` when unrecognized."""
    lowered = str(model or "").strip().lower()
    for catalog, prefixes in _MODEL_CATALOG_PREFIXES:
        if lowered.startswith(prefixes):
            return catalog
    return ""


def _explicit_model_selection(
    role_env: str,
    *,
    env: Mapping[str, str],
    persisted: Mapping[str, str],
) -> str:
    """The model id the OPERATOR chose for a role, or ``""`` when they never did.

    Resolving with an empty default makes the distinction structural: only the
    ``env`` and ``persisted`` layers can return a non-empty value, so anything
    non-empty here was deliberately configured by a human.
    """
    from .knobs import resolve_knob

    for name in (role_env, "ARGUS_SKILL_MODEL"):
        if not name:
            continue
        chosen = resolve_knob(name, "", env=env, persisted=persisted).value.strip()
        if chosen:
            return chosen
    return ""


def default_model_for_backend(
    backend: str,
    *,
    env: Mapping[str, str] | None = None,
    persisted: Mapping[str, str] | None = None,
) -> str:
    """Model id setup should adopt for ``backend``, or ``""`` to leave it alone.

    Returns a value only when BOTH hold: the backend has a verified default,
    and the operator has not chosen a model themselves. An explicit choice is
    never second-guessed, so re-running setup cannot silently retune a machine
    that was already configured by hand.
    """
    normalized = normalize_runner_backend(backend)
    adopted = _BACKEND_DEFAULT_MODELS.get(normalized, "")
    if not adopted:
        return ""
    env_map = env if env is not None else os.environ
    persisted_map = persisted if persisted is not None else read_persisted_knobs()
    for _route, role_env in _MODEL_ROLES:
        if _explicit_model_selection(role_env, env=env_map, persisted=persisted_map):
            return ""
    return adopted


def _check_backend_model_catalog(
    report: BackendReadiness,
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    """Verify the model ids Argus will send belong to the backend's own catalog.

    The gap this closes: readiness validated that the CLI existed, ran, and was
    authenticated — never that the model selector was one that CLI could serve.
    Argus's shared default (``gpt-5.5``) is an OpenAI-catalog id, so
    ``argus --setup --backend claude`` produced a machine that passed every
    check and then failed EVERY call with "There's an issue with the selected
    model (gpt-5.5)". Worse, the Manager front door reports any non-zero
    backend exit as "Manager could not classify this message", so the operator
    never saw the model name at all.

    Severity splits on WHO chose the id, matching the Pi precedent above. An
    id nobody chose is a hard problem: it is Argus's own default landing on a
    catalog that cannot serve it, and the failure is deterministic. An id the
    operator set by hand is a warning: they may be pointing the CLI at a
    private gateway that really does serve it, and a false red doctor is worse
    than an unheeded warning.

    中文：原先只校验 CLI 是否存在/已登录，从不校验下发的 model id 是否属于该
    后端的目录。默认值 ``gpt-5.5`` 是 OpenAI 目录的 id，于是
    ``--setup --backend claude`` 全绿通过、每次调用必失败。这里按「谁选的」
    分级：默认值选错 = 硬失败；操作者显式设置 = 仅告警（可能接了私有网关）。
    """
    from .knobs import resolve_role_model

    expected = _BACKEND_MODEL_CATALOG.get(report.profile.backend)
    if expected is None:
        return
    env_map = env if env is not None else os.environ
    persisted_map = read_persisted_knobs()
    # Roles usually share one model id; speak once per distinct selector.
    seen: set[str] = set()
    for route, role_env in _MODEL_ROLES:
        model = str(
            resolve_role_model(route, role_env=role_env, env=env_map) or ""
        ).strip()
        if not model or model in seen:
            continue
        seen.add(model)
        actual = _model_catalog(model)
        if not actual or actual == expected:
            continue
        chosen = _explicit_model_selection(
            role_env, env=env_map, persisted=persisted_map
        )
        if chosen:
            report.warnings.append(
                f"the {route} model {model!r} looks like an {actual} id but "
                f"the {report.profile.backend} CLI serves the {expected} "
                f"catalog; keep it only if that CLI is pointed at a gateway "
                f"that carries it"
            )
            continue
        adopted = _BACKEND_DEFAULT_MODELS.get(report.profile.backend, "")
        fix = (
            f"re-run `argus --setup --backend {report.profile.backend}` to "
            f"adopt {adopted}, or set ARGUS_SKILL_MODEL to a model that CLI "
            f"serves"
            if adopted
            else (
                f"set ARGUS_SKILL_MODEL (or {role_env}) to a model the "
                f"{report.profile.backend} CLI serves, then re-run "
                f"`argus --doctor`"
            )
        )
        report.problems.append(
            ReadinessProblem(
                "model selector",
                (
                    f"the {route} model resolves to {model!r}, an {actual} "
                    f"catalog id, but {report.profile.backend} serves the "
                    f"{expected} catalog; no model is configured, so this is "
                    f"Argus's shared default and every call will fail"
                ),
                fix,
            )
        )
        return


def _check_pi_model_routing(
    report: BackendReadiness,
    catalog: dict[str, set[str]],
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    """Verify the selectors Argus will actually send resolve against Pi's catalog.

    ``pi --list-models`` exiting non-empty only proves SOME provider is
    authenticated. Readiness used to stop there, so a deployment whose provider
    prefix named a catalog it had no key for reported ``ready: yes`` and then
    failed every single call with ``No API key found for <provider>`` — the
    exact shape of the hardcoded-``github-copilot`` bug. Judge the effective
    selector, not the CLI's general health.

    Severity is deliberately split. An unauthenticated PROVIDER is a hard
    problem: the failure is deterministic and the message Pi returns is exactly
    the one above. An unmatched MODEL is only a warning, because Pi's
    ``--model`` takes fuzzy patterns as well as exact ids, so an id missing
    from the table can still resolve — a false red doctor would be worse than
    an unheeded warning.

    中文：``--list-models`` 成功只说明「有某个 provider 已认证」。这里校验 Argus
    真正下发的 provider/model：provider 未认证 = 硬失败（必然报 No API key）；
    model 对不上只给 warning（Pi 的 --model 支持模糊匹配，避免误报）。
    """
    from .knobs import resolve_knob, resolve_role_model

    env_map = env if env is not None else os.environ
    providers = sorted(catalog)
    configured = resolve_knob(
        "ARGUS_SKILL_PI_PROVIDER", "", env=env_map
    ).value.strip().strip("/")
    if configured and configured not in catalog:
        report.problems.append(
            ReadinessProblem(
                "pi provider",
                (
                    f"ARGUS_SKILL_PI_PROVIDER={configured!r} is not an "
                    f"authenticated Pi provider; `pi --list-models` offers: "
                    f"{', '.join(providers)}"
                ),
                (
                    "set ARGUS_SKILL_PI_PROVIDER to one of the providers above "
                    "(or unset it to let Pi resolve bare model ids itself), "
                    "then re-run `argus --doctor`"
                ),
            )
        )
        return

    # Roles usually share one model id; warn once per distinct selector rather
    # than four times over.
    seen: set[str] = set()
    for route, role_env in _MODEL_ROLES:
        model = str(
            resolve_role_model(route, role_env=role_env, env=env_map) or ""
        ).strip()
        if not model or model in seen:
            continue
        seen.add(model)
        provider, separator, model_id = model.partition("/")
        if not separator:
            provider, model_id = configured, model
        if provider:
            if model_id not in catalog.get(provider, set()):
                report.warnings.append(
                    f"the {route} model {model_id!r} is not listed for the Pi "
                    f"provider {provider!r}; check `pi --list-models` or change "
                    f"it with {role_env}"
                )
            continue
        carriers = sorted(
            name for name, models in catalog.items() if model_id in models
        )
        if not carriers:
            report.warnings.append(
                f"no authenticated Pi provider lists the {route} model "
                f"{model_id!r} (providers: {', '.join(providers)}); check "
                f"`pi --list-models` or set {role_env} / ARGUS_SKILL_MODEL"
            )
        elif len(carriers) > 1:
            report.warnings.append(
                f"the {route} model {model_id!r} exists on more than one "
                f"authenticated Pi provider ({', '.join(carriers)}); Pi picks "
                f"one — set ARGUS_SKILL_PI_PROVIDER to choose deliberately"
            )


def _probe_copilot_auth(executable: str, timeout_s: float) -> tuple[bool, str]:
    """Create an ACP session without sending a prompt or spending model tokens."""
    from ..agent_cli.copilot_acp import CopilotAcpClient

    last_exc: Exception | None = None
    for attempt in range(2):
        client = CopilotAcpClient(executable, lean=True, startup_timeout_s=timeout_s)
        try:
            client._ensure_started()
            client._new_session(str(Path.cwd()))
            return True, ""
        except RuntimeError as exc:
            last_exc = exc
            if attempt or not str(exc).startswith("acp initialize failed:"):
                break
        finally:
            client.close()
    assert last_exc is not None
    return False, f"{type(last_exc).__name__}: {last_exc}"


def _probe_cli_auth(
    backend: str,
    executable: str,
    *,
    timeout_s: float,
) -> tuple[bool, str]:
    if backend == "copilot":
        return _probe_copilot_auth(executable, timeout_s)
    if backend == "pi":
        _catalog, detail = _probe_pi_catalog(executable, timeout_s)
        return (bool(_catalog), detail)
    if backend == "grok":
        if str(os.environ.get("XAI_API_KEY") or "").strip():
            return True, ""
        grok_home = Path(
            str(os.environ.get("GROK_HOME") or Path.home() / ".grok")
        ).expanduser()
        auth_file = grok_home / "auth.json"
        try:
            if auth_file.is_file() and auth_file.stat().st_size > 2:
                return True, ""
        except OSError as exc:
            return False, f"{type(exc).__name__}: {exc}"
        return False, (
            "no XAI_API_KEY or cached Grok login was found; "
            "Grok Build does not expose a read-only auth-status command"
        )
    if backend == "qoder":
        # A PAT is the headless path; otherwise fall through to the generic
        # `qodercli --list-models` probe below, which reports login state.
        if str(os.environ.get("QODER_PERSONAL_ACCESS_TOKEN") or "").strip():
            return True, ""
    if backend == "dsh":
        # dsh has no read-only auth probe: the headless profile rejects an
        # unauthenticated boot with MISSING_CREDENTIAL. Treat an exported
        # key as ready and otherwise report the remediation directly. dsh's
        # layered env loader (process > cwd .env > $DSH_HOME/.env) also
        # admits DEEPSEEK_API_KEY from $DSH_HOME/.env, so scan that file too
        # rather than misreporting a working deployment as unauthenticated.
        if str(os.environ.get("DEEPSEEK_API_KEY") or "").strip():
            return True, ""
        dsh_home = Path(
            str(os.environ.get("DSH_HOME") or Path.home() / ".dsh")
        ).expanduser()
        env_file = dsh_home / ".env"
        try:
            if env_file.is_file():
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    name, sep, raw = line.strip().partition("=")
                    if sep and name.strip() == "DEEPSEEK_API_KEY" and raw.strip():
                        return True, ""
        except OSError:
            pass
        return False, (
            "no DEEPSEEK_API_KEY was found in the environment or "
            f"{env_file}; export it or set it through the dsh credentials "
            "service (the web Models page)"
        )
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
                        "or inspect `argus --model-api-status`; vault override: "
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
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
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
                "choose one of: codex, copilot, claude, opencode, pi, grok, qoder, dsh",
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
        runner_bin
        or resolve_runner_bin_setting(env=env_map, persisted=read_persisted_knobs())
    ).strip()
    executable = resolve_runner_bin(profile.backend, configured_bin or None)
    if executable is None:
        report.problems.append(
            ReadinessProblem(
                "backend executable",
                f"`{profile.backend}` was not found on PATH",
                (
                    f"{backend_install_command(profile.backend)}, or set "
                    "ARGUS_SKILL_RUNNER_BIN"
                ),
            )
        )
        return report
    report.executable = executable

    try:
        for attempt in range(2):
            try:
                version_result = _run_backend_version(
                    profile.backend,
                    executable,
                    timeout_s=timeout_s,
                )
                break
            except subprocess.TimeoutExpired:
                if attempt:
                    raise
    except (OSError, subprocess.SubprocessError) as exc:
        report.problems.append(
            ReadinessProblem(
                "backend version",
                f"version check failed: {type(exc).__name__}: {exc}",
                f"reinstall with `{backend_install_command(profile.backend)}`",
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
                f"reinstall with `{backend_install_command(profile.backend)}`",
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
    elif profile.backend == "pi" and version_tuple < PI_MIN_VERSION:
        report.problems.append(
            ReadinessProblem(
                "backend version",
                f"Pi {report.version} is below the supported floor >=0.83.0",
                "upgrade with `pi update --self`",
            )
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
        # Pi's auth probe already reads the full authenticated catalog; reuse
        # that one subprocess to also validate the selectors Argus will send.
        pi_catalog: dict[str, set[str]] = {}
        if profile.backend == "pi":
            pi_catalog, detail = _probe_pi_catalog(executable, timeout_s)
            ok = bool(pi_catalog)
        else:
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
        elif pi_catalog:
            _check_pi_model_routing(report, pi_catalog, env=env_map)
    if profile.auth_mode == AUTH_MODE_SUBSCRIPTION:
        # Subscription mode only: under model_api the operator points Codex at
        # an arbitrary OpenAI-compatible endpoint, so a foreign-looking id may
        # be exactly right and the vault check above already judges the route.
        _check_backend_model_catalog(report, env=env_map)
    return report


def persist_validated_profile(
    report: BackendReadiness,
    *,
    model: str = "",
) -> bool:
    """Persist the validated backend profile, and the model chosen with it.

    ``model`` carries the id setup adopted from
    :func:`default_model_for_backend` (empty when the operator had already
    chosen one, or when the backend has no verified default). Writing it here
    is what stops a Codex-shaped shared default from silently becoming the
    selector for a non-OpenAI backend — the Pi path has always persisted its
    model this way; every other backend used to persist none.
    """
    if not report.ok:
        return False
    values = {
        "ARGUS_SKILL_RUNNER_BACKEND": report.profile.backend,
        AUTH_MODE_KNOB: report.profile.auth_mode,
        "ARGUS_SKILL_BACKEND_VALIDATED_VERSION": report.version,
    }
    adopted = str(model or "").strip()
    if adopted:
        values["ARGUS_SKILL_MODEL"] = adopted
    return write_persisted_knobs(values)


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
        if report.auth_checked and report.profile.backend == "opencode":
            auth = "credentials listed; live token not checked"
        else:
            auth = "checked" if report.auth_checked else "configuration validated"
        lines.append(f"  ready: yes ({auth})")
    else:
        lines.append("  ready: no")
        for problem in report.problems:
            lines.append(f"  failed capability: {problem.capability}")
            lines.append(f"    {problem.detail}")
            lines.append(f"    fix: {problem.remediation}")
    return "\n".join(lines)
