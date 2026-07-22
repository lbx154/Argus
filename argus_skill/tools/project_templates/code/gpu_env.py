"""Project-local GPU and cache bootstrap — standalone, no argus_skill dependency.

Use this before any model download, training, or inference so that:
  * HuggingFace / Torch caches live in the project's ``./models/`` (never the
    shared host cache), and
  * you know exactly which GPUs you may use and how many processes to launch.

Typical use inside an experiment/training script::

    import gpu_env
    gpu_env.configure_caches()          # set HF_HOME etc -> ./models
    devices = gpu_env.visible_devices() # e.g. ["0", "1", "2", "3"]

Or as a one-screen readiness check from the project root::

    .venv/bin/python code/gpu_env.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

_ARGUS_HOME = Path(os.environ.get("ARGUS_SKILL_HOME") or (Path.home() / ".argus-skill"))
_GPU_RESOURCES_PATH = _ARGUS_HOME / "capabilities" / "gpu_resources.json"

CACHE_ENV_VARS = (
    "HF_HOME",
    "HUGGINGFACE_HUB_CACHE",
    "HF_DATASETS_CACHE",
    "TRANSFORMERS_CACHE",
    "TORCH_HOME",
)


def cache_env(root: str | os.PathLike[str] = "models") -> dict[str, str]:
    """Return the cache environment variables that pin all weights under ``root``.

    ``root`` is resolved against the current working directory (the project
    root), matching the launcher-created ``./models/`` store.
    """
    base = Path(root).resolve()
    hf = base / "huggingface"
    return {
        "HF_HOME": str(hf),
        "HUGGINGFACE_HUB_CACHE": str(hf / "hub"),
        "HF_DATASETS_CACHE": str(hf / "datasets"),
        "TRANSFORMERS_CACHE": str(hf / "hub"),
        "TORCH_HOME": str(base / "torch"),
    }


def configure_caches(root: str | os.PathLike[str] = "models") -> dict[str, str]:
    """Point HuggingFace/Torch caches at the project model store (idempotent).

    Must be called *before* importing ``transformers`` / ``huggingface_hub`` or
    loading any model. Returns the env vars that were set.
    """
    env = cache_env(root)
    for key, value in env.items():
        os.environ[key] = value
        Path(value).mkdir(parents=True, exist_ok=True)
    return env


def load_gpu_allocation() -> dict[str, Any]:
    """Load the operator's GPU allocation, or an empty dict if unconfigured."""
    path = Path(os.environ.get("ARGUS_GPU_RESOURCES_PATH", str(_GPU_RESOURCES_PATH)))
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def visible_devices() -> list[str]:
    """Return the GPU ids this process may use, most authoritative first.

    Prefers the daemon-injected ``CUDA_VISIBLE_DEVICES``; falls back to the
    operator allocation file; returns ``[]`` when no GPU is configured.
    """
    raw = os.environ.get("CUDA_VISIBLE_DEVICES")
    if raw is not None:
        ids = [token.strip() for token in raw.split(",") if token.strip() != ""]
        return ids
    allocation = load_gpu_allocation()
    cuda_vis = allocation.get("cuda_visible_devices")
    if isinstance(cuda_vis, str) and cuda_vis.strip():
        return [token.strip() for token in cuda_vis.split(",") if token.strip()]
    devices = allocation.get("allowed_devices")
    if isinstance(devices, list):
        return [str(device) for device in devices]
    return []


def device_count() -> int:
    """Number of GPUs visible to this process (0 means CPU-only)."""
    return len(visible_devices())


def torch_cuda_report() -> dict[str, Any]:
    """Best-effort torch view of CUDA. Never raises and never hard-imports torch."""
    report: dict[str, Any] = {"torch_installed": False, "cuda_available": False, "devices": []}
    try:
        import torch  # noqa: PLC0415 - optional dependency, probed lazily
    except Exception:  # pragma: no cover - torch absence is environment-specific
        return report
    report["torch_installed"] = True
    report["torch_version"] = getattr(torch, "__version__", "unknown")
    try:
        report["cuda_available"] = bool(torch.cuda.is_available())
        if report["cuda_available"]:
            for index in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(index)
                report["devices"].append(
                    {
                        "index": index,
                        "name": props.name,
                        "total_memory_gb": round(props.total_memory / (1024**3), 1),
                    }
                )
    except Exception:  # pragma: no cover - driver/runtime mismatch
        pass
    return report


def nvidia_smi_summary() -> str:
    """Return a short ``nvidia-smi`` memory summary, or '' when unavailable."""
    binary = shutil.which("nvidia-smi")
    if not binary:
        return ""
    try:
        out = subprocess.run(
            [
                binary,
                "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def suggest_nproc() -> int:
    """Suggested ``--nproc_per_node`` / number of processes (>=1)."""
    return max(1, device_count())


def total_vram_gb() -> float:
    """Best-effort total VRAM across visible GPUs, in GiB (0.0 if unknown)."""
    report = torch_cuda_report()
    if report.get("cuda_available") and report.get("devices"):
        return round(sum(float(d.get("total_memory_gb", 0.0)) for d in report["devices"]), 1)
    # Fall back to nvidia-smi memory.total (MiB) for visible devices.
    smi = nvidia_smi_summary()
    if smi:
        total_mib = 0.0
        for row in smi.splitlines():
            cols = [c.strip() for c in row.split(",")]
            if len(cols) >= 4:
                try:
                    total_mib += float(cols[3])
                except ValueError:
                    continue
        if total_mib > 0:
            return round(total_mib / 1024.0, 1)
    return 0.0


def recommended_backbone_scale() -> str:
    """A concrete model-size nudge for the *headline* run, sized to real VRAM.

    The aim is to stop the common failure of defaulting to a tiny/legacy model
    while large GPUs sit underused. This is guidance, not a hard cap — always
    pick a *current-generation* open model and confirm fit empirically.
    """
    vram = total_vram_gb()
    n = device_count()
    if vram <= 0:
        return "GPU VRAM unknown — run on real GPUs before sizing the backbone."
    if vram >= 320:
        scale = "an 8-14B (or larger) current-gen backbone; full fine-tune with FSDP/DeepSpeed-ZeRO is feasible"
    elif vram >= 120:
        scale = "an ~8-9B current-gen backbone (LoRA/QLoRA comfortably; full FT with ZeRO-3)"
    elif vram >= 40:
        scale = "a 7-8B current-gen backbone with LoRA/QLoRA"
    elif vram >= 16:
        scale = "a 3-7B current-gen backbone with QLoRA"
    else:
        scale = "a small (1-3B) backbone — headline claims need more VRAM than this"
    return (
        f"~{vram:.0f} GiB total VRAM across {n} GPU(s): headline run should use {scale}. "
        f"Target >=70% VRAM utilization per card and drive all {n} GPU(s)."
    )


def suggest_launcher(framework: str = "") -> str:
    """Return a *hint* for launching a multi-GPU job — adapt to your framework.

    This is intentionally generic. Each framework (TRL, LLaMA-Factory, veRL,
    vLLM, Diffusers, SimpleTuner, ...) has its own launcher conventions; consult
    that framework's docs. Only the process count is authoritative here.
    """
    nproc = suggest_nproc()
    framework = framework.strip().lower()
    if nproc <= 1:
        return "single process (no multi-GPU launcher needed)"
    hints = {
        "torchrun": f"torchrun --standalone --nproc_per_node={nproc} <script.py> ...",
        "accelerate": f"accelerate launch --num_processes {nproc} <script.py> ...",
        "deepspeed": f"deepspeed --num_gpus {nproc} <script.py> ...",
        "llama-factory": "llamafactory-cli train <config.yaml>  # set deepspeed/FSDP in the YAML",
        "vllm": f"vllm serve <model> --tensor-parallel-size {nproc}",
    }
    if framework in hints:
        return hints[framework]
    return (
        f"{nproc} GPUs visible. Use your framework's native multi-GPU launcher "
        f"(e.g. torchrun --nproc_per_node={nproc}, accelerate launch "
        f"--num_processes {nproc}, deepspeed --num_gpus {nproc}, or vLLM "
        f"--tensor-parallel-size {nproc}). Caveat: confirm the exact flag in the "
        "framework's own docs."
    )


def readiness_report() -> str:
    """Human-readable one-screen GPU + cache readiness report."""
    lines = ["# GPU / cache readiness", ""]
    devices = visible_devices()
    lines.append(f"Visible GPUs (CUDA_VISIBLE_DEVICES order): {devices or 'none (CPU only)'}")
    lines.append(f"Suggested processes for multi-GPU: {suggest_nproc()}")
    lines.append("")

    env = cache_env()
    lines.append("Model-store cache targets (call configure_caches() before loading):")
    for key in CACHE_ENV_VARS:
        lines.append(f"  {key}={env[key]}")
    lines.append("")

    torch_report = torch_cuda_report()
    if not torch_report["torch_installed"]:
        lines.append("torch: not installed in this venv (install it before GPU training).")
    elif not torch_report["cuda_available"]:
        lines.append(f"torch {torch_report.get('torch_version', '?')}: CUDA NOT available.")
    else:
        lines.append(f"torch {torch_report.get('torch_version', '?')}: CUDA available, devices:")
        for device in torch_report["devices"]:
            lines.append(
                f"  [{device['index']}] {device['name']} ({device['total_memory_gb']} GB)"
            )
    lines.append("")

    smi = nvidia_smi_summary()
    if smi:
        lines.append("nvidia-smi (index, name, mem.used MiB, mem.total MiB, util %):")
        for row in smi.splitlines():
            lines.append(f"  {row.strip()}")
    else:
        lines.append("nvidia-smi: unavailable.")
    lines.append("")
    lines.append("Launcher hint: " + suggest_launcher())
    lines.append("Backbone sizing: " + recommended_backbone_scale())
    return "\n".join(lines)


def main() -> int:
    print(readiness_report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
