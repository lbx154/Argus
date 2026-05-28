---
name: project-environment-management
description: "Manage per-project Python virtual environments for ML/training workloads. Each research project gets its own venv with domain-specific dependencies (torch, diffusers, transformers, etc.), separate from the argus-skill system venv."
category: infrastructure
version: "1.0"
created_at: "2026-05-28"
---

# Project Environment Management

Each research project maintains its own Python virtual environment for ML workloads.
The argus-skill system venv (`/root/argus-skill/.venv`) is for pipeline tools only —
never install torch/diffusers/training dependencies there.

## ⚡ RESOURCE FILES (read these first)

All resources configured by the operator are in `~/.argus-skill/capabilities/`:

| File | Contents | How to read |
|------|----------|-------------|
| `gpu_resources.json` | Allocated GPU devices, CUDA_VISIBLE_DEVICES | `json.load(open(path))` |
| `model_api.json` | API keys, base URLs, models for text/image/review | `...['capabilities']['model_api']['routes']['text']` |

These are YOUR resources. Use them for training, inference, reward models, etc.

## Rules

1. **One venv per project**: create `.venv/` in the project root directory
2. **System Python as base**: use `/usr/bin/python3` or the system Python, not the argus-skill venv
3. **Never pollute argus-skill venv**: torch, diffusers, transformers, accelerate, peft, etc. go in the project venv only
4. **Activate before any ML command**: always use the project venv Python for training/inference

## Setup

```bash
# Create project venv (run once at project start)
cd /path/to/agent-emnlp-auto-research-vN
python3 -m venv .venv --system-site-packages
source .venv/bin/activate

# Install base ML stack
pip install torch torchvision torchaudio
pip install diffusers transformers accelerate peft safetensors
pip install datasets wandb tensorboard

# Install project-specific dependencies
pip install -r requirements.txt  # if exists
```

## Usage in experiments

Always reference the project venv Python explicitly:

```bash
# Correct — uses project venv
.venv/bin/python code/train.py --config config.yaml

# Correct — activate first
source .venv/bin/activate && python code/train.py

# WRONG — uses argus-skill system venv
/root/argus-skill/.venv/bin/python code/train.py
```

## For subagent commands

When submitting long-running GPU tasks to the subagent system, always use the project venv:

```bash
python -m argus_skill.tools.subagent submit \
  --task-id train-grpo \
  --description "Train zImage with GRPO" \
  --command ".venv/bin/python code/train.py --config experiments/grpo_config.yaml"
```

## Environment variables

The project venv inherits `CUDA_VISIBLE_DEVICES` from the daemon process (set via `gpu_resources.json`).
Additional env vars for training:

```bash
export HF_HOME=/root/.cache/huggingface
export HUGGINGFACE_HUB_CACHE=/root/.cache/huggingface/hub
export HF_DATASETS_CACHE=/root/.cache/huggingface/datasets
export TRANSFORMERS_CACHE=/root/.cache/huggingface/hub
export TORCH_HOME=/root/.cache/torch
export XDG_CACHE_HOME=/root/.cache
```

## Dependency management

Record installed packages for reproducibility:

```bash
.venv/bin/pip freeze > requirements.lock
```

Keep `requirements.txt` with loose versions for the essential packages only.
Keep `requirements.lock` with exact versions for full reproducibility.

## Troubleshooting

- If `torch.cuda.is_available()` returns False, check `CUDA_VISIBLE_DEVICES` and that CUDA toolkit is installed system-wide
- If import errors occur, verify you're using `.venv/bin/python`, not `/root/argus-skill/.venv/bin/python`
- If disk space is low, use `--system-site-packages` to share system torch installation
