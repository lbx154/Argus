"""Interactive setup wizard for Argus.

Configures one shared agent-CLI backend and validates its authentication. An
OpenAI-compatible URL and API key use Pi, which setup installs when needed. The
canonical entrypoint is ``argus --setup``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from ..core.backend_readiness import (
    AUTH_MODE_MODEL_API,
    AUTH_MODE_SUBSCRIPTION,
    SETUP_EXIT_NOT_READY,
    SETUP_EXIT_PERSISTENCE,
    SETUP_EXIT_USAGE,
    backend_install_command,
    check_backend_readiness,
    default_model_for_backend,
    format_backend_readiness,
    persist_validated_profile,
)
def _color(text: str, code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def _bold(text: str) -> str:
    return _color(text, "1")


def _green(text: str) -> str:
    return _color(text, "32")


def _yellow(text: str) -> str:
    return _color(text, "33")


def _dim(text: str) -> str:
    return _color(text, "2")


def _banner() -> None:
    print()
    print(_bold("Argus setup"))
    print(_dim("Configure one agent backend. Press Enter to keep the default."))
    print(
        _color(
            "★ Recommended / 推荐: let your current Code Agent complete setup "
            "and verification.",
            "1;33",
        )
    )
    print(
        _dim(
            "  Guide / 指引: "
            "https://github.com/lbx154/Argus/blob/main/docs/agent-install.md"
        )
    )
    print()


def _prompt(label: str, default: str = "", secret: bool = False) -> str:
    if default and not secret:
        display = f"  {label} [{_dim(default)}]: "
    elif default and secret:
        masked = default[:8] + "..." if len(default) > 8 else "***"
        display = f"  {label} [{_dim(masked)}]: "
    else:
        display = f"  {label}: "
    val = input(display).strip()
    return val if val else default


_SUPPORTED_AGENT_BACKENDS = (
    "copilot",
    "codex",
    "claude",
    "opencode",
    "pi",
    "grok",
    "qoder",
    "dsh",
)


def _backend_install_hint(
    backend: str,
    *,
    platform_name: str | None = None,
) -> str:
    return backend_install_command(backend, platform_name=platform_name)


def _install_pi_cli() -> bool:
    npm = shutil.which("npm")
    if npm is None:
        print(_yellow("  Pi requires Node.js/npm. Install Node.js, then rerun setup."))
        return False
    print("  Installing Pi...")
    result = subprocess.run(
        [
            npm,
            "install",
            "-g",
            "--ignore-scripts",
            "@earendil-works/pi-coding-agent",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        output = result.stderr.strip() or result.stdout.strip()
        detail = output.splitlines()[-1] if output else "npm exited with an error"
        print(_yellow(f"  Pi installation failed: {detail}"))
        return False
    print(f"  {_green('✓')} Pi installed")
    return True


def _configured_runner_backend() -> str:
    """Return the explicit or persisted shared backend, if valid."""
    from ..core.knob_store import read_persisted_knobs

    persisted = read_persisted_knobs()
    for value in (
        os.environ.get("ARGUS_SKILL_RUNNER_BACKEND"),
        os.environ.get("ARGUS_SKILL_LIFE_BACKEND"),
        persisted.get("ARGUS_SKILL_RUNNER_BACKEND"),
        persisted.get("ARGUS_SKILL_LIFE_BACKEND"),
    ):
        normalized = str(value or "").strip().lower()
        if normalized in _SUPPORTED_AGENT_BACKENDS:
            return normalized
    return ""


def _resolve_setup_runner_bin(
    backend: str,
    *,
    explicit_selection: bool = False,
) -> str | None:
    from ..agent_cli.runner_backend import resolve_runner_bin
    from ..core.knobs import resolve_runner_bin_setting

    configured_backend = _configured_runner_backend()
    configured_bin = resolve_runner_bin_setting()
    explicit_bin = str(os.environ.get("ARGUS_SKILL_RUNNER_BIN") or "").strip()
    if explicit_selection and explicit_bin:
        return resolve_runner_bin(backend, explicit_bin)
    if backend == configured_backend and configured_bin:
        return resolve_runner_bin(backend, configured_bin)
    return resolve_runner_bin(backend)


def _configure_runner_backend(requested: str | None = None) -> str | None:
    """Select the agent CLI used by every role without mutating global config."""
    available = [
        name for name in _SUPPORTED_AGENT_BACKENDS if _resolve_setup_runner_bin(name)
    ]
    configured = _configured_runner_backend()
    if configured and configured in available:
        default = configured
    elif len(available) == 1:
        default = available[0]
    elif len(available) > 1:
        default = ""
    else:
        default = ""

    print(_bold("  Step 1: Agent CLI Backend"))
    print()
    detected = ", ".join(available) if available else "none"
    print(_dim(f"  Detected on PATH: {detected}"))
    selected = (
        str(requested).strip().lower()
        if requested is not None
        else _prompt(
            "Backend (copilot/codex/claude/opencode/pi/grok/qoder/dsh)", default
        ).lower()
    )
    if selected not in _SUPPORTED_AGENT_BACKENDS:
        print(_yellow(f"  Unknown backend '{selected}'."))
        print(
            _dim(
                "    Choose one of: copilot, codex, claude, opencode, pi, grok, "
                "qoder, dsh"
            )
        )
        print()
        return None

    executable = _resolve_setup_runner_bin(selected, explicit_selection=True)
    if executable is None and selected == "pi" and _install_pi_cli():
        executable = _resolve_setup_runner_bin(selected, explicit_selection=True)
    if executable is None:
        print(_yellow(f"  `{selected}` CLI is not installed."))
        print(_dim(f"    Install it with: {_backend_install_hint(selected)}"))
        if selected == "copilot":
            print(_dim("    Then authenticate with: copilot login"))
        elif selected == "opencode":
            print(_dim("    Then authenticate with: opencode auth login"))
        elif selected == "pi":
            print(_dim("    Then run `pi` and use `/login` to authenticate."))
        elif selected == "grok":
            print(_dim("    Then authenticate with: grok login"))
        elif selected == "qoder":
            print(_dim("    Then authenticate with: qodercli login"))
            print(_dim("    Or set QODER_PERSONAL_ACCESS_TOKEN for headless use."))
        elif selected == "dsh":
            print(_dim("    Then export DEEPSEEK_API_KEY=<key> in the launching environment,"))
            print(_dim("    or set it through the dsh web Models page."))
        print()
        return None

    print()
    print(f"  {_green('✓')} Agent backend selected → {selected} ({executable})")
    if selected == "copilot":
        print(_dim("    Requires an active subscription and `copilot login`."))
    print()
    return selected


def _configure_auth_mode(backend: str, requested: str | None = None) -> str | None:
    if backend != "codex":
        if requested and requested.replace("-", "_") not in {
            "cli",
            "subscription",
            AUTH_MODE_SUBSCRIPTION,
        }:
            print(_yellow(f"  `{requested}` is not supported with `{backend}`."))
            return None
        return AUTH_MODE_SUBSCRIPTION
    selected = (
        str(requested).strip().lower().replace("-", "_")
        if requested is not None
        else _prompt(
            "Codex authentication mode (subscription_cli/model_api)",
            AUTH_MODE_SUBSCRIPTION,
        ).lower().replace("-", "_")
    )
    aliases = {
        "cli": AUTH_MODE_SUBSCRIPTION,
        "subscription": AUTH_MODE_SUBSCRIPTION,
        AUTH_MODE_SUBSCRIPTION: AUTH_MODE_SUBSCRIPTION,
        "api": AUTH_MODE_MODEL_API,
        "vault": AUTH_MODE_MODEL_API,
        AUTH_MODE_MODEL_API: AUTH_MODE_MODEL_API,
    }
    normalized = aliases.get(selected)
    if normalized is None:
        print(_yellow(f"  Unknown Codex authentication mode '{selected}'."))
        return None
    print(f"  {_green('✓')} Authentication mode selected → {normalized}")
    print()
    return normalized


def _verify_setup_smoke(
    backend: str,
    *,
    model: str = "",
) -> bool:
    """Prove that Argus can complete one real turn on the selected backend."""
    from ..core.agent_probe import run_read_only_agent_prompt

    executable = _resolve_setup_runner_bin(backend, explicit_selection=True)
    if executable is None:
        print(_yellow("  Setup failed at the end-to-end smoke test."))
        print(f"    Backend: {backend}")
        print("    Reason: the selected Agent CLI disappeared from PATH")
        return False

    print(_bold("  Step 2: End-to-end smoke test"))
    print(_dim("  Argus requires one successful read-only Agent reply."))
    probe = run_read_only_agent_prompt(
        backend=backend,
        executable=executable,
        model=model,
        run_label="setup-smoke",
        prompt="This is an Argus setup smoke test. Reply with exactly: ARGUS_SETUP_OK",
    )
    if probe.ok and probe.output.strip().rstrip(".") == "ARGUS_SETUP_OK":
        print(f"  {_green('✓')} Real Agent turn completed")
        print()
        return True

    print(_yellow("  Setup failed at Step 2: real Agent turn"))
    print(f"    Backend: {backend}")
    print(f"    Executable: {executable}")
    if probe.error:
        print(f"    Error: {probe.error}")
    elif probe.output:
        print(f"    Unexpected reply: {probe.output[:240]}")
    print("    Next:")
    print(
        f"      1. Run: argus --backend {backend} doctor "
        f"--deep --advisor {backend}"
    )
    print(f"      2. Re-run: argus --setup --backend {backend}")
    print()
    return False


def _setup_smoke_model(
    backend: str,
    pi_config: tuple[str, Path] | None,
) -> str:
    if pi_config is not None:
        return pi_config[0]
    from ..core.knobs import resolve_role_model

    return resolve_role_model(
        "manager",
        role_env="ARGUS_SKILL_MANAGER_MODEL",
        backend=backend,
    )


def _pi_models_path() -> Path:
    return Path.home() / ".pi" / "agent" / "models.json"


def _save_pi_provider(base_url: str, api_key: str, model: str) -> Path:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("API URL must be an absolute http(s) URL")
    path = _pi_models_path()
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{path} must contain a JSON object")
    else:
        value = {}
    providers = value.setdefault("providers", {})
    if not isinstance(providers, dict):
        raise ValueError(f"{path} field 'providers' must be a JSON object")
    providers["argus"] = {
        "baseUrl": base_url.rstrip("/"),
        "api": "openai-completions",
        "apiKey": api_key,
        "models": [{"id": model}],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def _configure_pi_api(
    *,
    api_url: str | None,
    api_key: str | None,
    api_model: str | None,
    interactive: bool,
) -> tuple[str, Path] | None:
    url = str(api_url or "").strip()
    if interactive and not url:
        url = _prompt("OpenAI-compatible API URL (Enter to use `pi` login)")
    if not url:
        return None
    key = str(api_key or os.environ.get("ARGUS_SETUP_API_KEY") or "").strip()
    if interactive and not key:
        key = _prompt("API key", secret=True)
    if not key:
        raise ValueError("API key is required when API URL is provided")
    model = str(api_model or "").strip()
    if interactive and not model:
        model = _prompt("Model", "gpt-5.5")
    model = model or "gpt-5.5"
    return model, _save_pi_provider(url, key, model)


def _persist_pi_profile(model: str) -> bool:
    from ..core.knob_store import write_persisted_knobs

    return write_persisted_knobs({
        "ARGUS_SKILL_RUNNER_BACKEND": "pi",
        "ARGUS_SKILL_LIFE_BACKEND": "pi",
        "ARGUS_SKILL_PI_PROVIDER": "argus",
        "ARGUS_SKILL_MODEL": model,
    })


def _run_noninteractive_setup(
    *,
    backend: str | None,
    auth_mode: str | None,
    allow_prerelease: bool,
    api_url: str | None,
    api_key: str | None,
    api_model: str | None,
) -> int:
    selected = str(backend or ("pi" if api_url else "")).strip().lower()
    if not selected:
        sys.stderr.write(
            "argus: --setup --non-interactive requires --backend or --api-url\n"
        )
        return SETUP_EXIT_USAGE
    if selected not in _SUPPORTED_AGENT_BACKENDS:
        sys.stderr.write(f"argus: unsupported backend {selected!r}\n")
        return SETUP_EXIT_USAGE
    if _configure_runner_backend(selected) is None:
        return SETUP_EXIT_NOT_READY
    mode = _configure_auth_mode(selected, auth_mode)
    if mode is None:
        return SETUP_EXIT_USAGE
    pi_config: tuple[str, Path] | None = None
    if api_url or api_key:
        if selected != "pi":
            sys.stderr.write("argus: --api-url/--api-key require --backend pi\n")
            return SETUP_EXIT_USAGE
        try:
            pi_config = _configure_pi_api(
                api_url=api_url,
                api_key=api_key,
                api_model=api_model,
                interactive=False,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            sys.stderr.write(f"argus: {exc}\n")
            return SETUP_EXIT_USAGE
        assert pi_config is not None
        os.environ["ARGUS_SKILL_PI_PROVIDER"] = "argus"
        os.environ["ARGUS_SKILL_MODEL"] = pi_config[0]
    # Adopt the backend's own model BEFORE readiness runs, so the check below
    # judges the selector this machine will really send. Mirrors how the Pi
    # branch above seeds ARGUS_SKILL_MODEL ahead of the same call.
    adopted_model = default_model_for_backend(selected)
    if adopted_model:
        os.environ["ARGUS_SKILL_MODEL"] = adopted_model
    report = check_backend_readiness(
        selected,
        mode,
        runner_bin=_resolve_setup_runner_bin(selected, explicit_selection=True),
        probe_auth=mode == AUTH_MODE_SUBSCRIPTION,
        probe_vault=mode == AUTH_MODE_MODEL_API,
        allow_prerelease=allow_prerelease,
    )
    rendered = format_backend_readiness(report)
    stream = sys.stdout if report.ok else sys.stderr
    stream.write(rendered + "\n")
    if not report.ok:
        return SETUP_EXIT_NOT_READY
    smoke_model = _setup_smoke_model(selected, pi_config)
    if not _verify_setup_smoke(selected, model=smoke_model):
        return SETUP_EXIT_NOT_READY
    if not persist_validated_profile(report, model=adopted_model):
        sys.stderr.write("argus: readiness passed but profile persistence failed\n")
        return SETUP_EXIT_PERSISTENCE
    if pi_config is not None and not _persist_pi_profile(pi_config[0]):
        sys.stderr.write("argus: Pi configuration was written but profile persistence failed\n")
        return SETUP_EXIT_PERSISTENCE
    sys.stdout.write(
        "\nSetup complete. Run `argus`.\n"
    )
    return 0


# -- GPU keep-alive (anti-reclaim) -----------------------------------------

# Unique, inert marker passed to the loader so gpu_lease's `match` token can
# find THIS keep-alive precisely instead of relying on the broad `gpu_load.py`
# basename (which could match unrelated loaders or stale processes).
_KEEPALIVE_TOKEN = "argus-skill-gpu-keepalive"


# -- Experiment use of the configured model API ----------------------------

_EXPERIMENT_API_PROMPT_NAME = "30-experiment-api.md"


# -- Author identity -------------------------------------------------------


def run_setup(
    *,
    backend: str | None = None,
    auth_mode: str | None = None,
    non_interactive: bool = False,
    allow_prerelease: bool = False,
    api_url: str | None = None,
    api_key: str | None = None,
    api_model: str | None = None,
) -> int:
    """Configure and validate one explicit backend/auth contract."""
    if non_interactive:
        return _run_noninteractive_setup(
            backend=backend,
            auth_mode=auth_mode,
            allow_prerelease=allow_prerelease,
            api_url=api_url,
            api_key=api_key,
            api_model=api_model,
        )
    _banner()
    selected_backend = _configure_runner_backend(backend or ("pi" if api_url else None))
    if selected_backend is None:
        return SETUP_EXIT_USAGE
    selected_auth_mode = _configure_auth_mode(selected_backend, auth_mode)
    if selected_auth_mode is None:
        return SETUP_EXIT_USAGE

    if selected_auth_mode == AUTH_MODE_MODEL_API and selected_backend != "codex":
        print(_yellow("  model_api authentication is only supported with Codex."))
        return SETUP_EXIT_USAGE

    pi_config: tuple[str, Path] | None = None
    if selected_backend == "pi":
        try:
            pi_config = _configure_pi_api(
                api_url=api_url,
                api_key=api_key,
                api_model=api_model,
                interactive=True,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(_yellow(f"  {exc}"))
            return SETUP_EXIT_USAGE
        if pi_config is not None:
            os.environ["ARGUS_SKILL_PI_PROVIDER"] = "argus"
            os.environ["ARGUS_SKILL_MODEL"] = pi_config[0]
            print(f"  {_green('✓')} Pi API configured → {pi_config[1]}")
        else:
            print(_dim("  Using Pi's existing login/configuration."))
        print()

    adopted_model = default_model_for_backend(selected_backend)
    if adopted_model:
        os.environ["ARGUS_SKILL_MODEL"] = adopted_model
        print(f"  {_green('✓')} Model selected → {adopted_model}")
        print()

    report = check_backend_readiness(
        selected_backend,
        selected_auth_mode,
        runner_bin=_resolve_setup_runner_bin(
            selected_backend,
            explicit_selection=True,
        ),
        probe_auth=selected_auth_mode == AUTH_MODE_SUBSCRIPTION,
        probe_vault=selected_auth_mode == AUTH_MODE_MODEL_API,
        allow_prerelease=allow_prerelease,
    )
    print()
    print(format_backend_readiness(report))
    if not report.ok:
        print(_yellow("  Setup is not ready; the backend profile was not persisted."))
        return SETUP_EXIT_NOT_READY
    smoke_model = _setup_smoke_model(selected_backend, pi_config)
    if not _verify_setup_smoke(selected_backend, model=smoke_model):
        print(_yellow("  Backend checks passed, but Argus could not complete a real turn."))
        return SETUP_EXIT_NOT_READY
    if not persist_validated_profile(report, model=adopted_model):
        print(_yellow("  Readiness passed but backend profile persistence failed."))
        return SETUP_EXIT_PERSISTENCE
    if pi_config is not None and not _persist_pi_profile(pi_config[0]):
        print(_yellow("  Pi configuration was written but profile persistence failed."))
        return SETUP_EXIT_PERSISTENCE
    print()
    print(_green("Setup complete. Run `argus`."))
    print()
    return 0


def main() -> int:
    return run_setup()


if __name__ == "__main__":
    raise SystemExit(main())
