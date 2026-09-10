"""Connect Copilot CLI using a privately issued Argus trial key."""
from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import certifi

from . import CLIENT_MODEL, MAX_OUTPUT_TOKENS, MODEL

TRIAL_ENV = "ARGUS_SKILL_COPILOT_TRIAL"


def trial_home() -> Path:
    """Account configuration belongs to the host, not a plugin task namespace."""
    from ..core.paths import global_root

    return Path(os.environ.get("ARGUS_WORKBENCH_HOST_ROOT") or global_root()).resolve()


def profile_path() -> Path:
    return trial_home() / "copilot-trial.json"


def trial_enabled(env: dict[str, str] | None = None) -> bool:
    from ..core.knob_store import read_persisted_knobs

    source = os.environ if env is None else env
    enabled = source.get(TRIAL_ENV)
    if enabled is None:
        enabled = read_persisted_knobs().get(TRIAL_ENV, "0")
    return enabled == "1"


def apply_trial_provider(env: dict[str, str]) -> dict[str, str]:
    """Apply the user's trial key to both one-shot and ACP workers."""
    from ..core.paths import global_root

    if not trial_enabled(env):
        return env
    config = json.loads(profile_path().read_text(encoding="utf-8"))
    if (
        not isinstance(config, dict) or set(config) != {"base_url", "api_key"}
        or not isinstance(config["base_url"], str)
        or not config["base_url"].endswith("/v1")
        or not re.fullmatch(r"argus_trial_[a-f0-9]{64}", str(config["api_key"]))
    ):
        raise ValueError("Invalid Copilot trial profile; rerun --setup --trial-url.")
    validate_origin(config["base_url"][:-3])
    for key in list(env):
        if key.startswith("COPILOT_PROVIDER_") or key in {"COPILOT_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "ARGUS_TRIAL_KEY"}:
            env.pop(key, None)
    home = global_root() / "copilot-trial-home"
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    env.update({
        "COPILOT_PROVIDER_BASE_URL": config["base_url"],
        "COPILOT_PROVIDER_API_KEY": config["api_key"],
        # Cloudflare rejects the CLI's default agent header on the public site.
        "COPILOT_PROVIDER_HEADERS": "User-Agent: Argus/0.1.1",
        "COPILOT_PROVIDER_TYPE": "openai",
        "COPILOT_PROVIDER_WIRE_API": "completions",
        "COPILOT_PROVIDER_TRANSPORT": "http",
        "COPILOT_PROVIDER_MODEL_ID": CLIENT_MODEL,
        "COPILOT_PROVIDER_WIRE_MODEL": MODEL,
        "COPILOT_PROVIDER_MAX_PROMPT_TOKENS": "128000",
        "COPILOT_PROVIDER_MAX_OUTPUT_TOKENS": str(MAX_OUTPUT_TOKENS),
        "COPILOT_MODEL": CLIENT_MODEL,
        "COPILOT_HOME": str(home),
    })
    return env


def ensure_copilot() -> str:
    from ..agent_cli.runner_backend import resolve_runner_bin

    executable = resolve_runner_bin("copilot")
    for attempt in range(2):
        if executable:
            help_result = subprocess.run(
                [executable, "help", "providers"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
            )
            if help_result.returncode == 0 and "COPILOT_PROVIDER_WIRE_MODEL" in help_result.stdout:
                return executable
        if attempt:
            break
        npm = shutil.which("npm")
        if npm is None:
            raise ValueError("Trial requires Node.js/npm to install GitHub Copilot CLI.")
        print("Installing GitHub Copilot CLI for the trial...")
        install = subprocess.run(
            [npm, "install", "-g", "@github/copilot@latest"], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=300,
        )
        if install.returncode:
            raise ValueError("Copilot CLI installation failed; check npm installation permissions.")
        executable = resolve_runner_bin("copilot")
    raise ValueError("The selected Copilot CLI does not support custom providers; update it and retry.")


def setup_trial(url: str, *, non_interactive: bool = False, api_key: str | None = None,
                desktop: bool = False, progress=print) -> int:
    from ..core.backend_readiness import check_backend_readiness, format_backend_readiness
    from ..core.knob_store import write_persisted_knobs
    from ..tools.setup import _verify_setup_smoke
    from .secrets import write_private

    key_options = {"api_key": api_key} if api_key is not None else {}
    base_url, api_key = connect(url, non_interactive=non_interactive, **key_options)
    if desktop:
        from .native_cli import install_native_copilot

        progress("正在下载并准备 Copilot，首次使用可能需要几分钟…")
        executable = install_native_copilot()
    else:
        executable = ensure_copilot()
    path = profile_path()
    previous = path.read_bytes() if path.exists() else None
    write_private(path, json.dumps({"base_url": base_url, "api_key": api_key}).encode())
    overrides = {
        TRIAL_ENV: "1", "ARGUS_SKILL_MODEL": CLIENT_MODEL,
        "ARGUS_SKILL_RUNNER_BACKEND": "copilot", "ARGUS_SKILL_RUNNER_BIN": executable,
    }
    saved_env = {k: os.environ.get(k) for k in overrides}
    succeeded = False
    try:
        os.environ.update(overrides)
        if desktop:
            progress("正在验证试用连接，请稍候…")
        report = check_backend_readiness("copilot", runner_bin=executable)
        print(format_backend_readiness(report))
        if not report.ok or not _verify_setup_smoke("copilot", model=CLIENT_MODEL):
            return 1
        succeeded = write_persisted_knobs({
            **overrides, "ARGUS_SKILL_LIFE_BACKEND": "copilot",
            "ARGUS_SKILL_BACKEND_AUTH_MODE": "subscription_cli",
            "ARGUS_SKILL_BACKEND_VALIDATED_VERSION": report.version,
        })
        if not succeeded:
            print("Trial setup failed to persist the backend profile.", file=sys.stderr)
            return 1
        print("Trial setup complete. Copilot CLI uses the hosted trial; run `argus`.")
        return 0
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if not succeeded:
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                write_private(path, previous)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_origin(url: str):
    parsed = urlparse(url)
    if (
        parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        )
        or not parsed.hostname or parsed.username or parsed.password
        or parsed.query or parsed.fragment or parsed.path not in ("", "/")
    ):
        raise ValueError("Trial URL must be an HTTPS origin (HTTP is allowed only on loopback).")


def connect(url: str, *, non_interactive: bool = False, api_key: str | None = None) -> tuple[str, str]:
    validate_origin(url)
    origin = url.rstrip("/")
    api_key = (api_key if api_key is not None else os.environ.get("ARGUS_TRIAL_KEY", "")).strip()
    if not api_key:
        if non_interactive or not sys.stdin.isatty():
            raise ValueError("Set ARGUS_TRIAL_KEY for non-interactive trial setup.")
        api_key = getpass.getpass("Argus trial key (hidden): ").strip()
    if not re.fullmatch(r"argus_trial_[a-f0-9]{64}", api_key):
        raise ValueError("Invalid trial key format. Use the private key supplied with your trial invitation.")
    request = urllib.request.Request(
        origin + "/trial/status",
        headers={"Authorization": "Bearer " + api_key, "User-Agent": "Argus/0.1.1"},
    )
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=context)).open(request, timeout=30) as response:
            data = json.loads(response.read(8192))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ValueError("Trial key was not recognized. Check the key and retry.") from None
        raise ValueError("Trial service is unavailable. Try again later.") from None
    except (urllib.error.URLError, ValueError, OSError):
        raise ValueError("Cannot connect to the trial service. Check the URL and connection.") from None
    if not isinstance(data, dict) or type(data.get("tokens_remaining")) is not int:
        raise ValueError("Invalid trial status response.")
    if data["tokens_remaining"] <= 0:
        raise ValueError("This trial key has used its token allowance.")
    print(f"Trial: {data['tokens_remaining']} / {data.get('token_limit', '?')} tokens remaining.")
    return origin + "/v1", api_key
