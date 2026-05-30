"""Interactive setup wizard for Argus.

Guides the user through configuring:
1. Planner API (model + endpoint)
2. Engineer API (model + endpoint)
3. Reviewer API (model + endpoint)
4. GPU resource allocation (which devices to use)

Usage:
    python -m argus_skill.tools.setup
    # or
    argus-skill --setup
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _capabilities_dir() -> Path:
    d = Path.home() / ".argus-skill" / "capabilities"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _color(text: str, code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def _bold(text: str) -> str:
    return _color(text, "1")


def _cyan(text: str) -> str:
    return _color(text, "36")


def _green(text: str) -> str:
    return _color(text, "32")


def _yellow(text: str) -> str:
    return _color(text, "33")


def _dim(text: str) -> str:
    return _color(text, "2")


def _banner() -> None:
    print()
    print(_bold("═" * 60))
    print(_bold("  Argus — Autonomous Research Generation & Understanding System"))
    print(_bold("  面向学术论文全流程的自主研究智能体系统"))
    print(_bold("═" * 60))
    print()
    print(_dim("  This wizard will configure your 3 agents and GPU resources."))
    print(_dim("  Press Enter to accept [default] values."))
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


def _detect_gpus() -> list[dict]:
    """Detect available GPUs via nvidia-smi."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []
        gpus = []
        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                gpus.append({
                    "index": int(parts[0]),
                    "name": parts[1],
                    "memory_mb": int(parts[2].replace("MiB", "").strip()),
                })
        return gpus
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []


def _configure_agent(
    name: str, existing: dict | None, default_model: str,
) -> dict:
    """Configure one agent's API route."""
    print(_cyan(f"  ── {name} Agent ──"))
    print()

    ex_url = (existing or {}).get("base_url", "")
    ex_key = (existing or {}).get("api_key", "")
    ex_model = (existing or {}).get("model", default_model)

    base_url = _prompt("API Base URL", ex_url)
    api_key = _prompt("API Key", ex_key, secret=True)
    model = _prompt("Model", ex_model)

    print()
    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "provider": "codex",
        "wire_api": "responses",
    }


def _configure_gpus(gpus: list[dict], existing: dict | None) -> dict:
    """Configure GPU resource allocation."""
    print(_cyan("  ── GPU Resources ──"))
    print()

    if not gpus:
        print(_yellow("  No GPUs detected. Skipping GPU configuration."))
        print()
        return existing or {}

    print("  Available GPUs:")
    for g in gpus:
        mem_gb = g["memory_mb"] / 1024
        print(f"    [{g['index']}] {g['name']} ({mem_gb:.0f} GB)")
    print()

    ex_devices = (existing or {}).get("cuda_visible_devices", "")
    raw = _prompt(
        "Devices to allocate (comma-separated, e.g. 6 or 0,1,2)",
        ex_devices,
    )

    # Parse and validate
    try:
        device_ids = [int(d.strip()) for d in raw.split(",") if d.strip()]
    except ValueError:
        print(_yellow(f"  Invalid input '{raw}', using all GPUs"))
        device_ids = [g["index"] for g in gpus]

    available = {g["index"] for g in gpus}
    invalid = [d for d in device_ids if d not in available]
    if invalid:
        print(_yellow(f"  Warning: devices {invalid} not found, ignoring"))
        device_ids = [d for d in device_ids if d in available]

    if not device_ids:
        print(_yellow("  No valid devices selected, using all GPUs"))
        device_ids = [g["index"] for g in gpus]

    cuda_vis = ",".join(str(d) for d in sorted(device_ids))
    selected_gpus = [g for g in gpus if g["index"] in device_ids]
    total_mem_gb = sum(g["memory_mb"] for g in selected_gpus) / 1024

    print()
    print(f"  {_green('✓')} Allocated: device(s) {cuda_vis} "
          f"({len(device_ids)} GPU, {total_mem_gb:.0f} GB total)")
    print()

    return {
        "allowed_devices": sorted(device_ids),
        "cuda_visible_devices": cuda_vis,
        "max_gpu_memory_gb": round(total_mem_gb),
        "notes": f"Allocated {len(device_ids)} GPU(s): {cuda_vis}",
    }


def _save_model_api(routes: dict[str, dict]) -> Path:
    """Save model API config."""
    path = _capabilities_dir() / "model_api.json"
    data = {"capabilities": {"model_api": {"routes": routes}}}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def _save_gpu_resources(config: dict) -> Path:
    """Save GPU resource config."""
    path = _capabilities_dir() / "gpu_resources.json"
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def _codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".codex"


def _codex_cli_available() -> str | None:
    return shutil.which("codex")


def _render_codex_config_toml(base_url: str, model: str) -> str:
    """Render a production-friendly minimal ``~/.codex/config.toml``.

    Mirrors the resilience knobs argus-skill's 7x24 daemon depends on
    (long idle timeout, many retries, no response storage) and points the
    default ``codex`` provider at the operator-supplied ``base_url``. The
    sandbox defaults to ``workspace-write`` with network access so the
    agent can run experiments without prompting; upgrade to
    ``danger-full-access`` only if you trust the host.
    """
    safe_model = model or "gpt-5.5"
    safe_url = base_url.rstrip("/") + "/" if base_url and not base_url.endswith("/") else base_url
    return (
        f'model = "{safe_model}"\n'
        'model_reasoning_effort = "high"\n'
        "disable_response_storage = true\n"
        'sandbox_mode = "workspace-write"\n'
        'approval_policy = "never"\n'
        'model_provider = "codex"\n'
        "\n"
        "[shell_environment_policy]\n"
        'inherit = "all"\n'
        "ignore_default_excludes = false\n"
        "\n"
        "[sandbox_workspace_write]\n"
        "network_access = true\n"
        "\n"
        "[history]\n"
        'persistence = "save-all"\n'
        "\n"
        "[features]\n"
        "plan_tool = true\n"
        "apply_patch_freeform = true\n"
        "view_image_tool = true\n"
        "\n"
        "[model_providers.codex]\n"
        f'name = "codex (argus-skill setup)"\n'
        f'base_url = "{safe_url}"\n'
        'wire_api = "responses"\n'
        "requires_openai_auth = true\n"
        "request_max_retries = 200\n"
        "stream_max_retries = 200\n"
        "stream_idle_timeout_ms = 600000\n"
    )


def _seed_codex_config(base_url: str, api_key: str, model: str) -> tuple[Path, Path] | None:
    """Write ``~/.codex/{config.toml,auth.json}`` from the supplied API.

    Returns ``(config_path, auth_path)`` on success, or ``None`` if the
    user declines overwriting an existing file. Existing files are backed
    up to ``<name>.bak`` before being overwritten.
    """
    if not base_url or not api_key:
        return None

    home = _codex_home()
    home.mkdir(parents=True, exist_ok=True)
    cfg_path = home / "config.toml"
    auth_path = home / "auth.json"

    existing = [p.name for p in (cfg_path, auth_path) if p.exists()]
    if existing:
        print()
        print(_yellow(
            f"  Existing codex files detected ({', '.join(existing)}) at "
            f"{home}/"))
        ans = _prompt(
            "Overwrite (existing files will be backed up to *.bak) [y/N]",
            "n",
        )
        if ans.lower() not in ("y", "yes"):
            print(_dim("  Skipped writing ~/.codex/config.toml and auth.json."))
            return None

    for target in (cfg_path, auth_path):
        if target.exists():
            backup = target.with_suffix(target.suffix + ".bak")
            try:
                shutil.copy2(target, backup)
            except OSError:
                pass

    cfg_path.write_text(_render_codex_config_toml(base_url, model), encoding="utf-8")
    os.chmod(cfg_path, 0o600)

    auth_path.write_text(
        json.dumps({"OPENAI_API_KEY": api_key}, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(auth_path, 0o600)
    return cfg_path, auth_path


def _check_codex_prereq() -> None:
    """Print a friendly note if the ``codex`` CLI is missing on PATH."""
    if _codex_cli_available():
        return
    print()
    print(_yellow("  Note: `codex` CLI not found on PATH."))
    print(_dim("    argus-skill drives codex non-interactively for every L1"))
    print(_dim("    round. Install once with:"))
    print()
    print(_dim("        npm install -g @openai/codex"))
    print()
    print(_dim("    The setup wizard will still write your codex config so the"))
    print(_dim("    binary is ready as soon as you install it."))
    print()


def _load_existing_routes() -> dict[str, dict]:
    """Load existing model API routes if any."""
    path = _capabilities_dir() / "model_api.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("capabilities", {}).get("model_api", {}).get("routes", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _load_existing_gpu() -> dict | None:
    """Load existing GPU config if any."""
    path = _capabilities_dir() / "gpu_resources.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _summary(routes: dict[str, dict], gpu: dict) -> None:
    """Print final summary."""
    print(_bold("═" * 60))
    print(_bold("  Configuration Summary"))
    print(_bold("═" * 60))
    print()
    for name in ("planner", "engineer", "reviewer"):
        r = routes.get(name, {})
        model = r.get("model", "not configured")
        url = r.get("base_url", "not configured")
        print(f"  {_cyan(name.capitalize()):30s} {model:20s} {_dim(url)}")
    print()
    if gpu:
        cuda = gpu.get("cuda_visible_devices", "none")
        print(f"  {_cyan('GPU'):30s} CUDA_VISIBLE_DEVICES={cuda}")
    else:
        print(f"  {_cyan('GPU'):30s} {_dim('not configured')}")
    print()


def run_setup() -> int:
    """Run the interactive setup wizard."""
    _banner()

    existing_routes = _load_existing_routes()
    existing_gpu = _load_existing_gpu()

    # Step 1: Check if all 3 agents share the same API
    print(_bold("  Step 1: API Configuration"))
    print()
    share = _prompt(
        "Do all 3 agents share the same API endpoint? (y/n)",
        "y" if not existing_routes else "y",
    )
    print()

    if share.lower() in ("y", "yes", ""):
        # Shared config
        print(_cyan("  ── Shared API (used by all 3 agents) ──"))
        print()
        ex = existing_routes.get("engineer", existing_routes.get("planner", {}))
        base_url = _prompt("API Base URL", ex.get("base_url", ""))
        api_key = _prompt("API Key", ex.get("api_key", ""), secret=True)
        print()

        planner_model = _prompt(
            "Planner model",
            existing_routes.get("planner", existing_routes.get("scientist", {})).get("model", "gpt-5.5"),
        )
        engineer_model = _prompt(
            "Engineer model",
            existing_routes.get("engineer", {}).get("model", "gpt-5.5"),
        )
        reviewer_model = _prompt(
            "Reviewer model",
            existing_routes.get("reviewer", {}).get("model", "gpt-5.5"),
        )
        print()

        shared_route = {
            "base_url": base_url,
            "api_key": api_key,
            "provider": "codex",
            "wire_api": "responses",
        }
        routes = {
            "planner": {**shared_route, "model": planner_model},
            "engineer": {**shared_route, "model": engineer_model},
            "reviewer": {**shared_route, "model": reviewer_model},
            "scientist": {**shared_route, "model": planner_model},
            "text": {**shared_route, "model": engineer_model},
        }

        # Image route
        img_model = _prompt("Image model (Enter to skip)", "gpt-image-2")
        if img_model:
            routes["image"] = {
                **shared_route,
                "model": img_model,
                "wire_api": "images",
            }
            routes["image_review"] = {**shared_route, "model": reviewer_model}
        print()
    else:
        # Per-agent config
        routes = {}
        planner = _configure_agent(
            "Planner", existing_routes.get("planner", existing_routes.get("scientist")), "gpt-5.5",
        )
        routes["planner"] = planner
        routes["scientist"] = planner  # planner = scientist

        routes["engineer"] = _configure_agent(
            "Engineer", existing_routes.get("engineer"), "gpt-5.5",
        )
        routes["reviewer"] = _configure_agent(
            "Reviewer", existing_routes.get("reviewer"), "gpt-5.5",
        )
        routes["text"] = routes["engineer"]

    # Step 2: GPU
    print(_bold("  Step 2: GPU Resources"))
    print()
    gpus = _detect_gpus()
    gpu_config = _configure_gpus(gpus, existing_gpu)

    # Step 3: codex CLI config
    print(_bold("  Step 3: Codex CLI Configuration"))
    print()
    print(_dim("  argus-skill drives the `codex` CLI for every L1 round."))
    print(_dim("  The wizard can seed ~/.codex/config.toml and ~/.codex/auth.json"))
    print(_dim("  from the API you just entered so codex talks to the same endpoint."))
    print()
    engineer_route = routes.get("engineer") or routes.get("text") or {}
    codex_base_url = engineer_route.get("base_url", "")
    codex_api_key = engineer_route.get("api_key", "")
    codex_model = engineer_route.get("model", "gpt-5.5")
    codex_paths: tuple[Path, Path] | None = None
    if codex_base_url and codex_api_key:
        codex_paths = _seed_codex_config(codex_base_url, codex_api_key, codex_model)
    else:
        print(_yellow("  Skipped: no engineer API endpoint configured."))
        print()

    # Save
    print(_bold("  Saving..."))
    api_path = _save_model_api(routes)
    print(f"  {_green('✓')} Model API → {api_path}")

    if gpu_config:
        gpu_path = _save_gpu_resources(gpu_config)
        print(f"  {_green('✓')} GPU config → {gpu_path}")

    if codex_paths:
        cfg, auth = codex_paths
        print(f"  {_green('✓')} codex config → {cfg}")
        print(f"  {_green('✓')} codex auth   → {auth}")

    _check_codex_prereq()
    print()

    # Summary
    _summary(routes, gpu_config)

    print(_green("  ✓ Setup complete! You can now create a research project:"))
    print()
    print(_dim('    python -m argus_skill.tools.new_auto_research_project \\'))
    print(_dim('      --parent ~/research --objective "My EMNLP Paper"'))
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_setup()


if __name__ == "__main__":
    raise SystemExit(main())
