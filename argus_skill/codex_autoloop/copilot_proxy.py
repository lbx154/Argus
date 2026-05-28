from __future__ import annotations

import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .codex_runner import CodexRunner
from .runner_backend import RunnerBackend, backend_supports_copilot_proxy


DEFAULT_COPILOT_PROXY_PORT = 18080
DEFAULT_COPILOT_PROVIDER = "copilot"
DEFAULT_COPILOT_PROXY_REPO_URL = "https://github.com/lbx154/copilot-codex-proxy.git"
AUTO_DETECTED_PROXY_DIRS = "~/copilot-proxy, ~/copilot-codex-proxy, or ~/.argusbot/tools/copilot-proxy"
AUTO_DETECTED_PROXY_DIR_HELP = f"Auto-detects {AUTO_DETECTED_PROXY_DIRS}."


@dataclass(frozen=True)
class CopilotProxyConfig:
    enabled: bool = False
    proxy_dir: str | None = None
    port: int = DEFAULT_COPILOT_PROXY_PORT
    provider_name: str = DEFAULT_COPILOT_PROVIDER
    log_file: str | None = None

    def resolved_proxy_dir(self) -> Path | None:
        return resolve_proxy_dir(self.proxy_dir)

    def resolved_log_file(self) -> Path | None:
        if self.log_file:
            return Path(self.log_file).expanduser().resolve()
        proxy_dir = self.resolved_proxy_dir()
        if proxy_dir is None:
            return None
        return proxy_dir / "proxy.log"

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def health_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/health"


def resolve_proxy_dir(raw: str | None = None) -> Path | None:
    if raw:
        candidate = Path(raw).expanduser().resolve()
        if (candidate / "proxy.mjs").exists():
            return candidate
        return None
    for candidate in _default_proxy_dir_candidates():
        if (candidate / "proxy.mjs").exists():
            return candidate
    return None


def _default_proxy_dir_candidates() -> list[Path]:
    home = Path.home()
    return [
        (home / "copilot-proxy").resolve(),
        (home / "copilot-codex-proxy").resolve(),
        managed_proxy_dir(),
    ]


def managed_proxy_dir() -> Path:
    return (Path.home() / ".argusbot" / "tools" / "copilot-proxy").resolve()


def bootstrap_proxy_checkout(
    *,
    target_dir: str | Path | None = None,
    repo_url: str = DEFAULT_COPILOT_PROXY_REPO_URL,
    on_progress: Callable[[str], None] | None = None,
) -> Path:
    target = Path(target_dir).expanduser().resolve() if target_dir is not None else managed_proxy_dir()
    git_bin = shutil.which("git")
    if not git_bin:
        raise RuntimeError("Copilot proxy bootstrap requires `git` in PATH.")
    node_bin = shutil.which("node")
    if not node_bin:
        raise RuntimeError("Copilot proxy bootstrap requires `node` in PATH.")

    try:
        if target.exists():
            if (target / "proxy.mjs").exists():
                _emit_progress(on_progress, f"Running copilot-proxy setup in {target}")
                _run_setup(node_bin=node_bin, proxy_dir=target)
                return target
            if any(target.iterdir()):
                raise RuntimeError(
                    f"Copilot proxy bootstrap target is not empty: {target}. "
                    "Set --copilot-proxy-dir to an existing checkout or remove that directory first."
                )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)

        _emit_progress(on_progress, f"Cloning copilot-proxy into {target}")
        subprocess.run([git_bin, "clone", repo_url, str(target)], check=True)
        _emit_progress(on_progress, f"Running copilot-proxy setup in {target}")
        _run_setup(node_bin=node_bin, proxy_dir=target)
        return target
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Copilot proxy bootstrap failed with exit code {exc.returncode}."
        ) from exc


def _emit_progress(on_progress: Callable[[str], None] | None, message: str) -> None:
    if on_progress is not None:
        on_progress(message)


def _run_setup(*, node_bin: str, proxy_dir: Path) -> None:
    setup_script = proxy_dir / "setup.mjs"
    if not setup_script.exists():
        raise RuntimeError(f"Copilot proxy checkout is missing setup.mjs: {proxy_dir}")
    subprocess.run([node_bin, str(setup_script)], cwd=str(proxy_dir), check=True)


def config_from_args(args: Any, *, prefix: str = "") -> CopilotProxyConfig:
    enabled = bool(getattr(args, f"{prefix}copilot_proxy", False))
    provider_name = str(getattr(args, f"{prefix}copilot_provider_name", DEFAULT_COPILOT_PROVIDER) or DEFAULT_COPILOT_PROVIDER)
    provider_name = provider_name.strip() or DEFAULT_COPILOT_PROVIDER
    raw_port = getattr(args, f"{prefix}copilot_proxy_port", DEFAULT_COPILOT_PROXY_PORT)
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        port = DEFAULT_COPILOT_PROXY_PORT
    return CopilotProxyConfig(
        enabled=enabled,
        proxy_dir=getattr(args, f"{prefix}copilot_proxy_dir", None),
        port=max(1, port),
        provider_name=provider_name,
        log_file=getattr(args, f"{prefix}copilot_proxy_log_file", None),
    )


def codex_config_overrides(config: CopilotProxyConfig) -> list[str]:
    if not config.enabled:
        return []
    provider = config.provider_name
    return [
        "-c",
        f'model_provider="{provider}"',
        "-c",
        f'model_providers.{provider}.name="GitHub Copilot"',
        "-c",
        f'model_providers.{provider}.base_url="{config.base_url()}"',
        "-c",
        f'model_providers.{provider}.wire_api="responses"',
        "-c",
        f"model_providers.{provider}.requires_openai_auth=false",
    ]


def proxy_is_healthy(config: CopilotProxyConfig, *, timeout_seconds: float = 2.0) -> bool:
    req = urllib.request.Request(config.health_url(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def ensure_proxy_running(
    config: CopilotProxyConfig,
    *,
    startup_timeout_seconds: float = 15.0,
) -> None:
    if not config.enabled:
        return
    if proxy_is_healthy(config):
        return
    proxy_dir = config.resolved_proxy_dir()
    if proxy_dir is None:
        raise RuntimeError(
            "Copilot proxy is enabled but proxy.mjs was not found. "
            "Set --copilot-proxy-dir to a valid copilot-proxy checkout."
        )
    node_bin = shutil.which("node")
    if not node_bin:
        raise RuntimeError("Copilot proxy is enabled but `node` was not found in PATH.")
    log_path = config.resolved_log_file()
    if log_path is None:
        raise RuntimeError("Copilot proxy log path could not be resolved.")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_file:
        subprocess.Popen(
            [node_bin, str(proxy_dir / "proxy.mjs"), "--port", str(config.port)],
            cwd=str(proxy_dir),
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    deadline = time.monotonic() + max(1.0, startup_timeout_seconds)
    while time.monotonic() < deadline:
        if proxy_is_healthy(config):
            return
        time.sleep(0.5)
    raise RuntimeError(
        "Copilot proxy failed to start or become healthy. "
        f"Check {log_path} for details."
    )


def build_codex_runner(
    *,
    backend: RunnerBackend,
    runner_bin: str | None,
    config: CopilotProxyConfig,
    event_callback=None,
) -> CodexRunner:
    if not backend_supports_copilot_proxy(backend):
        return CodexRunner(
            codex_bin=runner_bin,
            backend=backend,
            event_callback=event_callback,
        )
    if not config.enabled:
        return CodexRunner(codex_bin=runner_bin, backend=backend, event_callback=event_callback)
    return CodexRunner(
        codex_bin=runner_bin,
        backend=backend,
        event_callback=event_callback,
        default_extra_args=codex_config_overrides(config),
        before_exec=lambda: ensure_proxy_running(config),
    )


def prompt_for_proxy_dir(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None
    resolved = resolve_proxy_dir(value)
    if resolved is None:
        return None
    return str(resolved)


def format_proxy_summary(config: CopilotProxyConfig) -> str:
    if not config.enabled:
        return "disabled"
    proxy_dir = config.resolved_proxy_dir()
    return (
        f"enabled provider={config.provider_name} "
        f"base_url={config.base_url()} "
        f"dir={proxy_dir or '-'}"
    )


def shell_args_for_display(config: CopilotProxyConfig) -> str:
    if not config.enabled:
        return ""
    parts = [
        "--copilot-proxy",
        "--copilot-proxy-port",
        str(config.port),
    ]
    if config.proxy_dir:
        parts.extend(["--copilot-proxy-dir", config.proxy_dir])
    return shlex.join(parts)
