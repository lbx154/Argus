---
name: Training Infrastructure Guide
description: Guide the engineer to use established training and inference frameworks. Covers LLM, agent RL, diffusion, and API inference. Do NOT write custom training loops or inference loops.
category: engineering
priority: high
version: 2
created_at: 2026-05-28T00:00:00+00:00
---

# Training & Inference Infrastructure Guide

When experiments involve model training or large-scale inference, use established frameworks. Do NOT write custom loops from scratch.

## ⚡ YOUR RESOURCES (configured by operator)

These resources are allocated to you. Use them.

**GPU**:
- Config file: `~/.argus-skill/capabilities/gpu_resources.json`
- Read with: `json.load(open(os.path.expanduser('~/.argus-skill/capabilities/gpu_resources.json')))`
- `CUDA_VISIBLE_DEVICES` is auto-set by the daemon. All training/inference inherits it.

**API (for reward models, VLM scoring, image generation)**:
- Config file: `~/.argus-skill/capabilities/model_api.json`
- Read API key: `json.load(open(os.path.expanduser('~/.argus-skill/capabilities/model_api.json')))['capabilities']['model_api']['routes']['text']['api_key']`
- Available routes: `text` (LLM), `image` (generation), `image_review` (VLM)
- Use for: reward model scoring, VLM-based image quality evaluation, Qwen-VL as reward

**Project venv** (for ML dependencies):
- Path: `.venv/bin/python` (in project directory)
- If not exists: `python3 -m venv .venv --system-site-packages && .venv/bin/pip install torch diffusers transformers accelerate peft safetensors`
- NEVER install ML deps in `/root/argus-skill/.venv/`

**Subagent** (for long GPU tasks):
- Submit: `python -m argus_skill.tools.subagent submit --task-id <id> --mode supervised --command '.venv/bin/python ...'`
- Check: `python -m argus_skill.tools.subagent status --task-id <id>`
- Do NOT block — submit and continue other work.

---

## Core rule

If a task involves gradient-based training or inference on >100 examples, the engineer MUST use an approved framework. Custom `for epoch` training loops and bare `model.generate()` inference loops are hard blockers in experiment plan review.

---

## 1. LLM Post-Training (SFT / DPO / RLHF)

| Task | Framework | Why |
|------|-----------|-----|
| SFT / instruction tuning | **LLaMA-Factory** | 100+ architectures, LoRA/QLoRA/full, YAML config |
| SFT (HF ecosystem) | **TRL** | HuggingFace official, clean API |
| SFT (ModelScope/Chinese) | **Swift** (ms-swift) | ModelScope integration |
| DPO / ORPO / SimPO / KTO | **LLaMA-Factory** or **TRL** | Both support modern preference methods |
| RLHF (PPO at scale) | **OpenRLHF** | Ray-based distributed, production-grade |
| GRPO / RLVR / reasoning RL | **veRL** | ByteDance, cutting-edge reasoning RL |
| Pretraining | **Megatron-LM** or **LitGPT** | Distributed pretraining |

Decision flow:
1. DPO/preference → LLaMA-Factory (fastest to set up)
2. PPO RLHF at scale → OpenRLHF
3. GRPO/reasoning RL → veRL
4. Simple SFT → TRL or LLaMA-Factory

## 2. Agent RL (multi-turn, environment interaction)

| Task | Framework | Why |
|------|-----------|-----|
| Multi-turn agent RL | **AgentGym-RL** | ICLR 2026 oral, modular, multi-env, PPO/GRPO/REINFORCE++ |
| Agent RL (general) | **veRL** | Async rollouts, LangGraph integration, tool-use RL |
| Code agent RL | **SLIME** (slime-rl) | SWE-Bench oriented, code execution RL |
| Web agent RL | **AgentGym-RL** | Built-in web navigation environments |

Decision flow:
1. Agent RL with environment → AgentGym-RL (most complete)
2. Tool-use / reasoning RL → veRL (best async support)
3. Code-specific RL → SLIME

## 3. Diffusion / Text-to-Image

| Task | Framework | Why |
|------|-----------|-----|
| Full training (multi-GPU, production) | **SimpleTuner** | 14+ architectures (SD3/SDXL/Flux), DeepSpeed/FSDP, caching, web UI |
| Research training (efficient) | **Diffusers** (HuggingFace) | Gold standard API, DreamBooth/LoRA/ControlNet |
| High-res T2I (budget-efficient) | **PixArt-α pipeline** | 10x faster than SD v1.5, DiT architecture |
| LoRA fine-tuning (consumer GPU) | **kohya_ss** or **Musubi** | Optimized for low-VRAM, community standard |
| LoRA fine-tuning (cloud, easy) | **FluxGym** | Web UI, RunPod integration, streamlined |
| Multi-task (gen + understanding) | **OneDiffusion** | Unified T2I/depth/pose/segmentation training |
| Inference / generation | **Diffusers** or **ComfyUI** | Pipeline API or node-based GUI |

Decision flow:
1. Production multi-GPU training → **SimpleTuner** (most complete, best distributed support)
2. Research with HF ecosystem → **Diffusers** training scripts
3. Budget-efficient DiT training → PixArt-α style pipeline
4. LoRA on consumer GPU → kohya_ss / Musubi
5. Quick LoRA experiments → FluxGym

## 4. LLM Inference

| Task | Framework | Why |
|------|-----------|-----|
| Local batch inference | **vLLM** | Fastest, PagedAttention, continuous batching |
| Structured generation | **SGLang** | RadixAttention, constrained decoding |
| API inference | **OpenAI client** (`openai` package) | Standard interface for OpenAI/Azure/compatible |
| Multi-provider API | **LiteLLM** | Unified API for 100+ providers |
| Docker serving | **TGI** | HuggingFace official, Docker-native |
| CPU / edge | **llama.cpp** / **ollama** | Quantized inference |

Decision flow:
1. Local GPU + open model → **vLLM** (default choice)
2. Need structured output → **SGLang**
3. API-based (OpenAI/Azure) → **OpenAI client** (openai package)
4. Multiple API providers → **LiteLLM**

## 5. API Inference (standard)

All API-based inference MUST use the **OpenAI client interface**:

```python
from openai import OpenAI

client = OpenAI(
    api_key="...",
    base_url="https://api.openai.com/v1",  # or Azure/compatible endpoint
)
response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": prompt}],
)
```

For Azure:
```python
from openai import AzureOpenAI
client = AzureOpenAI(
    api_key="...",
    api_version="2024-06-01",
    azure_endpoint="https://your-resource.openai.azure.com",
)
```

Do NOT use raw `urllib`/`requests` for LLM API calls. Use the `openai` package.

## What NOT to do

- Custom PyTorch training loop with manual loss.backward() / optimizer.step()
- Bare model.generate() in a for-loop for >100 examples
- Raw HTTP requests to LLM APIs instead of openai client
- Reimplementing gradient accumulation, mixed precision, or distributed training
- Writing custom KV-cache management
- Using bare transformers pipeline for benchmark evaluation at scale
- Training a tiny custom MLP/scorer when GPU budget allows a real model with a framework
