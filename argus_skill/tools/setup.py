"""Interactive setup wizard for Argus.

Guides the user through configuring:
0. Author identity (name + email for papers / project commits)
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


# -- GPU keep-alive (anti-reclaim) -----------------------------------------

# Unique, inert marker passed to the loader so gpu_lease's `match` token can
# find THIS keep-alive precisely instead of relying on the broad `gpu_load.py`
# basename (which could match unrelated loaders or stale processes).
_KEEPALIVE_TOKEN = "argus-skill-gpu-keepalive"


def _special_prompts_dir() -> Path:
    env = os.environ.get("ARGUS_SKILL_SPECIAL_PROMPTS_DIR")
    d = Path(env).expanduser() if env else Path.home() / ".argus-skill" / "special_prompts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _gpu_load_script_path() -> Path:
    """Absolute path to the bundled standalone keep-alive loader."""
    return (Path(__file__).resolve().parent / "gpu_load.py")


def _keepalive_config_path() -> Path:
    return _capabilities_dir() / "gpu_keepalive.json"


def _keepalive_log_path() -> Path:
    return Path.home() / ".argus-skill" / "logs" / "gpu_keepalive.log"


def _load_existing_keepalive() -> dict | None:
    path = _keepalive_config_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _python_has_torch_cuda(python: str) -> bool:
    """Return True if ``python`` can import torch with CUDA available."""
    try:
        res = subprocess.run(
            [python, "-c", "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 3)"],
            capture_output=True, text=True, timeout=60,
        )
        return res.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _detect_torch_python() -> str:
    """Find an interpreter that has torch + CUDA.

    The interpreter running Argus may not have torch (it can live in a separate
    env), so we probe a few sensible candidates and return the first that works.
    """
    candidates: list[str] = [sys.executable]
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    for pattern in (
        "/opt/conda/envs/*/bin/python",
        str(Path.home() / ".conda" / "envs" / "*" / "bin" / "python"),
        str(Path.home() / "miniconda3" / "envs" / "*" / "bin" / "python"),
        str(Path.home() / "anaconda3" / "envs" / "*" / "bin" / "python"),
    ):
        from glob import glob as _glob
        candidates.extend(sorted(_glob(pattern)))
    seen: set[str] = set()
    for cand in candidates:
        real = str(Path(cand).resolve()) if cand else cand
        if not cand or real in seen:
            continue
        seen.add(real)
        if _python_has_torch_cuda(cand):
            return cand
    return ""


def _build_keepalive_config(
    python: str, script: Path, devices: list[int], util: float, mem: float,
    *, cwd: str | None = None, log: str | None = None,
    token: str = _KEEPALIVE_TOKEN,
) -> dict:
    """Build a gpu_lease-compatible keep-alive config (pure)."""
    gpus_csv = ",".join(str(d) for d in devices)
    command = [
        python, str(script),
        "--gpus", gpus_csv,
        "--mem", str(mem),
        "--util", str(util),
        "--keepalive-token", token,
    ]
    return {
        "command": command,
        "cwd": cwd or str(Path.home()),
        "match": token,
        "log": log or str(_keepalive_log_path()),
        "devices": devices,
    }


def _save_gpu_keepalive(config: dict) -> Path:
    path = _keepalive_config_path()
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def _render_gpu_keepalive_prompt(devices_csv: str) -> str:
    """Operator special-prompt body teaching agents the keep-alive protocol."""
    return (
        "# GPU keep-alive — never leave the cards idle\n\n"
        "This box runs on a managed scheduler that **reclaims GPUs that sit "
        "idle**. If GPUs " + devices_csv + " go idle (no live GPU process / "
        "~0% utilization) while you are only calling the model API, thinking, "
        "writing prose, or waiting, the scheduler can **reclaim the cards and "
        "you lose in-progress training/inference state**. That must not "
        "happen.\n\n"
        "## Standing rule\n\n"
        "Whenever you are NOT actively running real GPU work, a keep-alive "
        "loader must be HOLDING GPUs " + devices_csv + " so they never look "
        "idle. The keep-alive is managed by the lease tool and configured in "
        "`~/.argus-skill/capabilities/gpu_keepalive.json`. Treat \"GPUs held\" "
        "as the default resting state of this machine.\n\n"
        "## Run real GPU work through the lease tool\n\n"
        "NEVER kill the keep-alive by hand and NEVER start a job on top of it. "
        "ALWAYS go through the lease tool, which frees the cards, runs your "
        "job, then re-parks the keep-alive on exit:\n\n"
        "```bash\n"
        "python -m argus_skill.tools.gpu_lease run -- python train.py ...\n"
        "# long/detached jobs (lease restored even if the mission ends first):\n"
        "python -m argus_skill.tools.gpu_lease run --detach -- python train.py ...\n"
        "```\n\n"
        "## Quiet periods\n\n"
        "```bash\n"
        "python -m argus_skill.tools.gpu_lease status   # keep-alive + leases + per-GPU\n"
        "python -m argus_skill.tools.gpu_lease park      # (re)hold the cards if unheld\n"
        "```\n\n"
        "`park` refuses while a real job lease is active, so it is always safe "
        "to call.\n\n"
        "## Do / don't\n\n"
        "- DO route every real GPU command through `gpu_lease run`.\n"
        "- DO `gpu_lease park` if the cards are ever unheld with no active job.\n"
        "- DON'T `kill` the loader directly or free the cards outside the lease "
        "tool.\n"
        "- DON'T leave the GPUs at 0% with no process during API-only stretches.\n"
        "- DON'T write GPU ids or this keep-alive plumbing into the paper prose, "
        "figures, or commits — it is deployment detail, not a research result.\n"
    )


def _write_special_prompt(name: str, body: str) -> Path:
    """Write an operator special prompt (0644) that passes the trust check."""
    directory = _special_prompts_dir()
    path = directory / name
    path.write_text(body, encoding="utf-8")
    os.chmod(path, 0o644)
    return path


# -- Experiment use of the configured model API ----------------------------

_EXPERIMENT_API_PROMPT_NAME = "30-experiment-api.md"


def _experiment_api_prompt_path() -> Path:
    return _special_prompts_dir() / _EXPERIMENT_API_PROMPT_NAME


def _render_experiment_api_prompt() -> str:
    """Operator special-prompt body authorizing API use inside experiments."""
    return (
        "# Experiment use of the configured model API (allowed)\n\n"
        "The model API in `~/.argus-skill/capabilities/model_api.json` is "
        "APPROVED for use **inside experiments**, not just for your own "
        "engineer/reviewer reasoning. You may call it as a working component of "
        "the research method, for example:\n\n"
        "- an **LLM reward / preference model** in RL experiments "
        "(RLHF/RLVR/RLAIF style),\n"
        "- an **LLM-as-judge / verifier** for scoring rollouts or grading "
        "outputs,\n"
        "- **synthetic data / preference-pair generation**, prompt evaluation, "
        "or labeling.\n\n"
        "## How to call it\n\n"
        "The mission process already has the credentials exported into the "
        "environment by the framework — use them, do NOT paste keys into code "
        "or config:\n\n"
        "- `OPENAI_API_KEY`, `OPENAI_BASE_URL` — the default (engineer) route.\n"
        "- `ARGUS_SKILL_TEXT_MODELS` — comma-separated text model id(s) you may "
        "use.\n"
        "- For route-specific needs, read the vault routes "
        "(`~/.argus-skill/capabilities/model_api.json`, "
        "`capabilities.model_api.routes.*`) programmatically; never echo the "
        "key to logs, stdout, or files.\n\n"
        "Use the standard OpenAI-compatible client pointed at `OPENAI_BASE_URL` "
        "with `OPENAI_API_KEY`.\n\n"
        "## Guardrails\n\n"
        "- **Budget-aware.** These calls cost money and count against the "
        "per-mission / daily caps. Cache responses, batch, and pick the "
        "cheapest adequate model (prefer a `*-mini` text route when one is "
        "configured). Don't spin unbounded judge/reward loops.\n"
        "- **No secret leakage.** Never write the API key, base URL, vault "
        "path, or any route/provider/deployment detail into the paper prose, "
        "figures, sidecars, commits, or logs. Read from env at runtime only.\n"
        "- **Methodological honesty.** If an API model is part of the method "
        "(reward model, judge, data generator), describe it as such in the "
        "paper at the appropriate abstraction level (e.g., \"an LLM-based "
        "reward model\"), and keep the **evaluation benchmarks independent and "
        "real** — do not let the same model that provides the reward also be "
        "the sole judge of success in a way that inflates claims. Report the "
        "judge/reward setup so results are reproducible in spirit.\n"
        "- This permission is about USING the API as a tool in experiments; it "
        "does not relax any evidence-quality, baseline-strength, or "
        "anti-mediocrity requirement.\n"
    )


def _configure_experiment_api(routes: dict[str, dict]) -> bool:
    """Ask whether experiments may call the configured model API; persist prompt."""
    print(_cyan("  ── Experiment API access ──"))
    print()
    has_api = any((routes.get(r) or {}).get("api_key") for r in ("engineer", "text", "reviewer"))
    if not has_api:
        print(_dim("  No model API configured; skipping."))
        print()
        return False

    print(_dim("  Allow experiments to CALL the configured model API as a"))
    print(_dim("  working component (e.g. an LLM reward model / judge, synthetic"))
    print(_dim("  data generation), not just for the agents' own reasoning?"))
    print(_dim("  Credentials are read from the environment at runtime; keys are"))
    print(_dim("  never written into code, the paper, or logs."))
    print()

    default_enable = "y" if _experiment_api_prompt_path().exists() else "n"
    enable = _prompt("Allow API use inside experiments (reward/judge/etc.)? (y/N)",
                     default_enable)
    if enable.lower() not in ("y", "yes"):
        print(_dim("  Experiment API use not authorized."))
        print()
        return False

    path = _write_special_prompt(_EXPERIMENT_API_PROMPT_NAME, _render_experiment_api_prompt())
    print()
    print(f"  {_green('✓')} Operator prompt   → {path}")
    print(_dim("    (this also satisfies the launch gate's required special "
               "prompt)"))
    print()
    return True


def _configure_gpu_keepalive(
    gpus: list[dict], gpu_config: dict, existing: dict | None,
) -> dict | None:
    """Ask whether to hold GPUs against reclaim; persist config + prompt."""
    print(_cyan("  ── GPU Keep-Alive (anti-reclaim) ──"))
    print()
    if not gpus:
        print(_dim("  No GPUs detected; skipping keep-alive."))
        print()
        return None

    print(_dim("  Some managed/cloud boxes reclaim GPUs that sit idle. Argus"))
    print(_dim("  can run a low-duty keep-alive loader that holds the cards"))
    print(_dim("  during quiet periods (API-only thinking, drafting) so long"))
    print(_dim("  paper runs are not reclaimed and lost. Real GPU jobs"))
    print(_dim("  automatically pre-empt it via the lease tool."))
    print()

    default_enable = "y" if existing else "n"
    enable = _prompt("Does this machine reclaim idle GPUs? Enable keep-alive? (y/N)",
                     default_enable)
    if enable.lower() not in ("y", "yes"):
        print(_dim("  Keep-alive disabled."))
        print()
        return None

    # Default to the FULL allocated set so no allocated card is left unprotected.
    allocated = list(gpu_config.get("allowed_devices") or [g["index"] for g in gpus])
    allocated = sorted(allocated)
    ex_devices = (existing or {}).get("devices")
    default_n = len(ex_devices) if ex_devices else len(allocated)

    raw_n = _prompt(
        f"How many GPUs to hold (of allocated {allocated})",
        str(default_n),
    )
    try:
        n_hold = int(raw_n)
    except ValueError:
        print(_yellow(f"  Invalid number '{raw_n}', holding all allocated."))
        n_hold = len(allocated)
    n_hold = max(1, min(n_hold, len(allocated)))
    devices = allocated[:n_hold]
    unheld = allocated[n_hold:]
    if unheld:
        print(_yellow(f"  Warning: allocated GPUs {unheld} will NOT be held and "
                      f"may be reclaimed if idle."))

    ex_mem = (existing or {}).get("_mem", 10.0)
    ex_util = (existing or {}).get("_util", 20.0)
    try:
        mem = float(_prompt("VRAM % to hold per GPU", str(ex_mem)))
    except ValueError:
        mem = 10.0
    try:
        util = float(_prompt("Best-effort GPU utilization %", str(ex_util)))
    except ValueError:
        util = 20.0

    # Interpreter that actually has torch (may differ from the Argus env).
    detected = (existing or {}).get("_python") or _detect_torch_python()
    if detected:
        print(_dim(f"  Detected torch interpreter: {detected}"))
    python = _prompt("Python interpreter for the loader (needs torch+CUDA)",
                     detected or sys.executable)
    if not _python_has_torch_cuda(python):
        print(_yellow("  Warning: that interpreter could not import torch with "
                      "CUDA available. Saving anyway — fix it before relying on "
                      "the keep-alive."))

    script = _gpu_load_script_path()
    config = _build_keepalive_config(python, script, devices, util, mem)
    # Stash the wizard inputs so a re-run can offer them as defaults.
    config["_python"] = python
    config["_mem"] = mem
    config["_util"] = util

    cfg_path = _save_gpu_keepalive(config)
    prompt_path = _write_special_prompt(
        "20-gpu-keepalive.md", _render_gpu_keepalive_prompt(",".join(str(d) for d in devices)))

    print()
    print(f"  {_green('✓')} Keep-alive config → {cfg_path}")
    print(f"  {_green('✓')} Operator prompt   → {prompt_path}")
    print(_dim("    (this also satisfies the launch gate's required special "
               "prompt)"))
    print()

    start = _prompt("Start the keep-alive now (hold the cards)? (y/N)", "n")
    if start.lower() in ("y", "yes"):
        try:
            from argus_skill.tools import gpu_lease
            res = gpu_lease.park(gpu_lease.load_config())
            if res.get("started"):
                print(f"  {_green('✓')} Keep-alive started (pid {res.get('pid')}).")
            elif res.get("already_running"):
                print(_dim("  Keep-alive already running."))
            elif res.get("refused"):
                print(_yellow("  Not started: an active GPU lease holds the "
                              "cards free."))
            else:
                print(_dim(f"  park result: {res}"))
        except Exception as exc:  # pragma: no cover - host specific
            print(_yellow(f"  Could not start keep-alive automatically: {exc}"))
        print()

    return config


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
        'model_reasoning_effort = "xhigh"\n'
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


# -- Author identity -------------------------------------------------------


def _author_config_path() -> Path:
    return _capabilities_dir() / "author.json"


def _load_existing_author() -> dict | None:
    """Load a previously-saved author identity, if any."""
    path = _author_config_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_author(name: str, email: str) -> Path:
    """Persist the paper/project author identity."""
    path = _author_config_path()
    data = {"name": name, "email": email}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def _git_global_identity() -> tuple[str, str]:
    """Return the current global git (user.name, user.email), or empty strings."""
    def _read(key: str) -> str:
        try:
            out = subprocess.run(
                ["git", "config", "--global", key],
                capture_output=True, text=True, timeout=5,
            )
            return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    return _read("user.name"), _read("user.email")


def _apply_git_identity(name: str, email: str) -> bool:
    """Set the global git identity so generated project commits are attributed."""
    try:
        subprocess.run(["git", "config", "--global", "user.name", name],
                       check=True, capture_output=True, text=True, timeout=5)
        subprocess.run(["git", "config", "--global", "user.email", email],
                       check=True, capture_output=True, text=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _configure_author(existing: dict | None) -> dict | None:
    """Prompt for the author identity used on generated papers / project commits."""
    print(_dim("  Who authors the generated papers and project commits?"))
    print(_dim("  Used as the git author for the research workspace and for the"))
    print(_dim("  camera-ready author block (EMNLP submission PDFs stay anonymous)."))
    print()

    git_name, git_email = _git_global_identity()
    default_name = (existing or {}).get("name") or git_name
    default_email = (existing or {}).get("email") or git_email

    name = _prompt("Author name", default_name)
    email = _prompt("Author email", default_email)

    if not name and not email:
        print(_yellow("  Skipped: no author identity provided."))
        print()
        return None

    if email and "@" not in email:
        print(_yellow(f"  Warning: '{email}' does not look like an email address."))

    path = _save_author(name, email)
    print()
    print(f"  {_green('✓')} Author identity → {path}")
    if _apply_git_identity(name, email):
        print(f"  {_green('✓')} git --global user.name/user.email set")
    else:
        print(_yellow("  Could not set global git identity (git unavailable?)"))
    print()
    return {"name": name, "email": email}


def _summary(routes: dict[str, dict], gpu: dict, keepalive: dict | None = None,
             experiment_api: bool = False, author: dict | None = None) -> None:
    """Print final summary."""
    print(_bold("═" * 60))
    print(_bold("  Configuration Summary"))
    print(_bold("═" * 60))
    print()
    if author:
        who = f"{author.get('name', '')} <{author.get('email', '')}>"
        print(f"  {_cyan('Author'):30s} {who}")
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
    if keepalive:
        held = ",".join(str(d) for d in keepalive.get("devices", []))
        print(f"  {_cyan('GPU keep-alive'):30s} holding device(s) {held}")
    else:
        print(f"  {_cyan('GPU keep-alive'):30s} {_dim('disabled')}")
    state = "allowed" if experiment_api else "not authorized"
    print(f"  {_cyan('Experiment API use'):30s} {state}")
    print()


def run_setup() -> int:
    """Run the interactive setup wizard."""
    _banner()

    existing_routes = _load_existing_routes()
    existing_gpu = _load_existing_gpu()
    existing_author = _load_existing_author()

    # Step 0: Author identity
    print(_bold("  Step 0: Author Identity"))
    print()
    author = _configure_author(existing_author)

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
            existing_routes.get("planner", existing_routes.get("author", {})).get("model", "gpt-5.5"),
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
            "author": {**shared_route, "model": planner_model},
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
            "Planner", existing_routes.get("planner", existing_routes.get("author")), "gpt-5.5",
        )
        routes["planner"] = planner
        routes["author"] = planner  # planner = author

        routes["engineer"] = _configure_agent(
            "Engineer", existing_routes.get("engineer"), "gpt-5.5",
        )
        routes["reviewer"] = _configure_agent(
            "Reviewer", existing_routes.get("reviewer"), "gpt-5.5",
        )
        routes["text"] = routes["engineer"]

    # Step 1b: Experiment API access
    print(_bold("  Step 1b: Experiment API access"))
    print()
    experiment_api = _configure_experiment_api(routes)

    # Step 2: GPU
    print(_bold("  Step 2: GPU Resources"))
    print()
    gpus = _detect_gpus()
    gpu_config = _configure_gpus(gpus, existing_gpu)

    # Step 2b: GPU keep-alive (anti-reclaim)
    existing_keepalive = _load_existing_keepalive()
    keepalive_config = _configure_gpu_keepalive(gpus, gpu_config, existing_keepalive)

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
    _summary(routes, gpu_config, keepalive_config, experiment_api, author)

    print(_green("  ✓ Setup complete! To start working on a project:"))
    print()
    print(_dim('    cd <your project directory>'))
    print(_dim('    argus            # enter the Manager conversation to launch a mission'))
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_setup()


if __name__ == "__main__":
    raise SystemExit(main())
