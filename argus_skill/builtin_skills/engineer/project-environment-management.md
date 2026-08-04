---
name: "project-environment-management"
description: "Manage per-project Python virtual environments for ML/training workloads. Each research project gets its own venv with domain-specific dependencies (torch, diffusers, transformers, etc.), separate from the argus-skill system venv."
---

# Project Environment Management

Each research project maintains its own Python virtual environment for ML workloads.
The argus-skill framework venv (the interpreter shown as `$ARGUS_SKILL_PYTHON`
in each round's runtime prompt) is for pipeline tools only —
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

# WRONG — uses the argus-skill framework venv
"$ARGUS_SKILL_PYTHON" code/train.py
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
Point all model/data caches at the project-local store under `./models/` (pre-created by the
launcher and gitignored) so each project owns its weights — see the
training-infrastructure-guide skill, which is the source of truth for this contract:

```bash
export HF_HOME="$(pwd)/models/huggingface"
export HUGGINGFACE_HUB_CACHE="$(pwd)/models/huggingface/hub"
export HF_DATASETS_CACHE="$(pwd)/models/huggingface/datasets"
export TRANSFORMERS_CACHE="$(pwd)/models/huggingface/hub"
export TORCH_HOME="$(pwd)/models/torch"
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
- If import errors occur, verify you're using `.venv/bin/python`, not the argus-skill framework venv (`$ARGUS_SKILL_PYTHON`)
- If disk space is low, use `--system-site-packages` to share system torch installation
