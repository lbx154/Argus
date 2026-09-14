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
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

import certifi

from . import CLIENT_MODEL, MAX_OUTPUT_TOKENS, MODEL, REASONING_EFFORT

TRIAL_ENV = "ARGUS_SKILL_COPILOT_TRIAL"


def trial_home() -> Path:
    """Account configuration belongs to the host, not a plugin task namespace."""
    from ..core.paths import global_root

    return Path(os.environ.get("ARGUS_WORKBENCH_HOST_ROOT") or global_root()).resolve()


def profile_path() -> Path:
    candidate = os.environ.get("ARGUS_DESKTOP_TRIAL_PROFILE")
    return Path(candidate) if candidate else trial_home() / "copilot-trial.json"


def trial_enabled(env: Mapping[str, str] | None = None) -> bool:
    from ..core.knob_store import read_persisted_knobs

    source = os.environ if env is None else env
    enabled = source.get(TRIAL_ENV)
    if enabled is None:
        enabled = read_persisted_knobs().get(TRIAL_ENV, "0")
    return enabled == "1"


def trial_model_options(
    model: str | None, effort: str | None, *, env: Mapping[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """Normalize a trial selector consistently for one-shot and ACP workers.

    Adapted from upstream be5bb394. Preserve the opaque client selector and the
    user's reasoning setting; never silently accept an incompatible explicit
    model or rewrite persistent own-account settings.
    """
    if not trial_enabled(env):
        return model, effort
    selected = str(model or "").strip().casefold()
    if selected not in {"", "auto", "inherit", "default", CLIENT_MODEL.casefold()}:
        raise ValueError(
            "试用模式由服务端选择真实模型。请将当前插件或会话的模型设为 "
            "argus-trial / auto，或切回自己的账号。"
        )
    return CLIENT_MODEL, effort


def runtime_redactions() -> tuple[str, ...]:
    """Include the file-backed trial Key in normal Argus log redaction."""
    if not trial_enabled():
        return ()
    try:
        value = json.loads(profile_path().read_text(encoding="utf-8")).get("api_key", "")
        return (value,) if isinstance(value, str) and re.fullmatch(r"argus_trial_[a-f0-9]{64}", value) else ()
    except (OSError, ValueError, AttributeError):
        return ()


def apply_trial_provider(env: dict[str, str]) -> dict[str, str]:
    """Apply the user's trial key to both one-shot and ACP workers."""
    from ..core.paths import global_root

    if not trial_enabled(env):
        return env
    validate_role_overrides()
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
                desktop: bool = False, progress=print, download_progress=None) -> int:
    from ..core.backend_readiness import check_backend_readiness, format_backend_readiness
    from ..core.knob_store import write_persisted_knobs
    from ..tools.setup import _verify_setup_smoke
    from .storage import write_private

    key_options = {"api_key": api_key} if api_key is not None else {}
    base_url, api_key = connect(url, non_interactive=non_interactive, **key_options)
    if desktop:
        from .native_cli import install_native_copilot

        executable = install_native_copilot(progress=progress, download_progress=download_progress)
    else:
        executable = ensure_copilot()
    path = profile_path()
    previous = path.read_bytes() if path.exists() else None
    write_private(path, json.dumps({"base_url": base_url, "api_key": api_key}).encode())
    overrides = {
        TRIAL_ENV: "1", "ARGUS_SKILL_MODEL": CLIENT_MODEL,
        "ARGUS_SKILL_RUNNER_BACKEND": "copilot", "ARGUS_SKILL_RUNNER_BIN": executable,
    }
    from ..core.knobs import KNOBS

    # Seed the trial's visible role settings as well as enforcing high upstream.
    overrides.update({knob.name: REASONING_EFFORT for knob in KNOBS
                      if knob.name.endswith("_REASONING_EFFORT")})
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
    data = query_status(origin, api_key)
    if data["tokens_remaining"] <= 0:
        raise ValueError("此内测 Key 的额度已用完，请更换 Key 或使用自己的账号。")
    print(f"Trial: {data['tokens_remaining']} / {data.get('token_limit', '?')} tokens remaining.")
    return origin + "/v1", api_key


def validate_role_overrides() -> None:
    """Reject incompatible explicit roles; never erase the user's own choices."""
    from ..core.knob_store import read_persisted_knobs

    settings = {**read_persisted_knobs(), **os.environ}
    conflicts = []
    for role in ("MANAGER", "PLANNER", "ENGINEER", "REVIEWER", "CURATOR", "SUPERVISOR"):
        backend = str(settings.get(f"ARGUS_SKILL_{role}_BACKEND", "")).strip().lower()
        model_key = "PLAN" if role == "PLANNER" else role
        model = str(settings.get(f"ARGUS_SKILL_{model_key}_MODEL", "")).strip().lower()
        if backend and backend != "copilot" or model and model not in {"auto", CLIENT_MODEL, MODEL}:
            conflicts.append(role.lower())
    if conflicts:
        raise ValueError("以下角色有与试用不兼容的独立后端或模型设置：" + ", ".join(conflicts)
                         + "。请先在模型设置中调整；现有配置不会被自动覆盖。")


def query_status(url: str, api_key: str) -> dict:
    validate_origin(url)
    origin = url.rstrip("/")
    if not re.fullmatch(r"argus_trial_[a-f0-9]{64}", api_key):
        raise ValueError("请输入完整有效的内部测试 Key。")
    request = urllib.request.Request(
        origin + "/trial/status",
        headers={"Authorization": "Bearer " + api_key, "User-Agent": "Argus/0.1.1"},
    )
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=context)).open(request, timeout=30) as response:
            data = json.loads(response.read(8192))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ValueError("内测 Key 未被识别，请核对后重试。") from None
        if exc.code == 429:
            raise ValueError("试用服务当前繁忙，请稍后重试。") from None
        raise ValueError("试用服务暂不可用，请稍后重试。") from None
    except (urllib.error.URLError, ValueError, OSError):
        raise ValueError("无法连接试用服务，请检查网络连接。") from None
    if not isinstance(data, dict) or any(type(data.get(k)) is not int or data[k] < 0
                                          for k in ("tokens_remaining", "token_limit")):
        raise ValueError("试用服务返回了无效的额度信息。")
    return {k: v for k, v in data.items() if k in {
        "tokens_remaining", "token_limit", "tokens_used", "active_requests", "max_concurrency",
        "global_tpm_limit", "global_tpm_remaining",
    } and type(v) is int and v >= 0}
