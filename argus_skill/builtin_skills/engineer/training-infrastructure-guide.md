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

## 🔒 Selection contract (research + plan stages)

This guide is the **starting baseline**, not the final answer. During the
**research** and **plan** stages every project that needs training or
large-scale inference must commit to a specific framework on each axis
(training / inference), and that decision must satisfy ALL of the following:

1. **Open-source, actively maintained, 2026+.** The last meaningful release
   or commit must be in **2026 or later**. Anything older is treated as
   unmaintained and rejected — even if it was once state-of-the-art.
2. **No self-written training or inference loops.** A custom `for epoch`
   loop, a bare `model.generate()` benchmark loop, or a hand-rolled
   RL/PPO trainer is a hard blocker. Wrap an existing framework instead.
3. **Paper-released frameworks are allowed** if (a) the repo meets the
   2026+ recency bar and (b) the paper is cited in
   `research/LITERATURE_GROUNDING.json`. Prefer official authors' code
   over third-party reimplementations.
4. **Anchor against this guide first, then supplement.** Use the tables
   below as the curated baseline. You must additionally do at least one
   round of independent research (recent arXiv, GitHub trending, papers
   that match your domain) to (a) confirm those baseline frameworks are
   still maintained at decision time and (b) add at least one credible
   candidate of your own with URL + last-commit date + paper.
5. **Excluded entries from this guide.** If a baseline framework below
   is no longer maintained, explicitly note it as "excluded — stale" in
   `research/INFRA_SHORTLIST.md` so the reasoning is auditable.

### 🚨 Always scan the README for supersession hints

Frameworks routinely get **upstreamed into a larger project, renamed,
or superseded**. The original repo often stays publicly archived but
its own README points at the new home. Pick the wrong one and you'll
end up wrapping abandoned code while the active community has moved
on. Concrete observed example: `flow_grpo`'s own README now says
"🚀 Flow-GRPO is now supported by verl-omni, which provides a
verl-style training framework for Flow-GRPO users." A naive shortlist
that just notices "flow_grpo matches my domain" misses that the
recommended path is now `verl-omni`.

For every candidate framework you shortlist, you MUST do the
following on the freshly cloned repo before writing a row in
`research/INFRA_SHORTLIST.md`:

```bash
# Get the README text (handle both common spellings + .md/.rst):
for f in README.md README.rst README; do
  [ -f code/references/<repo>/$f ] && echo "=== $f ===" && cat code/references/<repo>/$f
done | head -200
```

Then explicitly grep for supersession / migration language:

```bash
grep -nEi 'now supported by|upstreamed (in)?to|merged (in)?to|moved to|migrated to|deprecat|archived|superseded|recommended|please use|maintained at|see also' \
    code/references/<repo>/README* 2>/dev/null
```

If any hit names a successor project, the shortlist row must:

1. **Add the successor as its own candidate** in `INFRA_SHORTLIST.md`
   (clone it under `code/references/<successor>/`, repeat the
   maintenance + README check there too).
2. **Compare the two in the rationale**: what does the original repo
   still offer that the successor does not (e.g. an algorithm-specific
   recipe the successor hasn't ported yet)?
3. **Default to the successor** unless step 2 produced a concrete
   reason to stay on the older repo. Wrapping a self-deprecated
   framework "because it appeared first in our search" is a real
   blocker, not a stylistic preference.

Also do one sanity pass at the *paper* level: if the chosen framework
backs a paper that was itself surpassed by a follow-up paper with
its own released code, the follow-up wins on the same logic.

### Artifacts the L2 reviewer will check

- **research stage** (`research.infra_shortlist`):
  `research/INFRA_SHORTLIST.md` listing every candidate framework you
  evaluated, with URL, last release/commit date, paper (if any),
  README-supersession note (or "no supersession hint found" — both
  are valid; the absence is itself a positive signal), and a one-line
  "fit" rationale.
- **plan stage** (`plan.infra_choice`): `research/INFRA_CHOICE.md`
  locking in exactly one training framework and exactly one inference
  framework, citing the chosen repo's URL + last release/commit date,
  and a one-line reason why the rejected runner-up was rejected. If a
  runner-up was rejected because of a supersession hint, name the
  successor in the reason. The same choice must also appear in an
  `## Infra` section of `research/EXPERIMENT_PLAN.md`.

Skip both artifacts only if the project genuinely needs neither
training nor large-scale inference (e.g. a pure literature analysis
paper). In that case record the decision in `research/RESEARCH_BRIEF.md`
and proceed.

## 🧬 Backbone model selection contract (research + plan stages)

Picking the **base model** is as important as picking the framework, and is a
**separate decision** that the L2 reviewer checks. Default failures here are
choosing a model that is **too small** or **too old** for the hardware — that
produces a result no main-conference reviewer will believe.

1. **Current generation only.** The backbone must be from a **current,
   actively released open model family** (latest generation at decision time,
   e.g. released/updated in the most recent ~12 months). Do **not** default to
   a previous-generation or legacy small model just because it is familiar or
   downloads fast. Verify the family is current by checking the Hugging Face
   model hub / trending / a recent open-LLM leaderboard at decision time —
   exactly as you verify framework recency.
2. **Size the model to the hardware, not to convenience.** Read your real GPU
   budget first (`.venv/bin/python code/gpu_env.py` prints total VRAM). On a
   multi-H200 box (hundreds of GB of aggregate VRAM) a **7B–14B** backbone
   trains comfortably with LoRA/QLoRA, and an 8–9B model trains comfortably
   even with full fine-tuning + FSDP/DeepSpeed-ZeRO. **The headline result must
   use a model in at least the ~8–9B class** unless the research question is
   specifically about small models. A 1–3B model is acceptable **only** as an
   ablation/scaling point or a fast smoke run — never as the paper's main
   claim when the GPUs can clearly train bigger.
3. **Justify the choice in writing.** `research/INFRA_CHOICE.md` (and the
   `## Infra` section of `research/EXPERIMENT_PLAN.md`) must name the exact
   backbone (org/model id + parameter count + release date), state the VRAM
   budget it was sized against, and give a one-line reason the size is
   appropriate. If you deliberately use a small model, the reason must be a
   research reason, not "it was easier / faster to train".
4. **Reviewer blocker.** A headline run on a stale or sub-~8B backbone while
   large GPUs sit underused is a hard blocker, the same as a self-written
   training loop. Fix the backbone before claiming the run stage complete.

## 🔥 Hardware saturation contract (run stage)

Allocated GPUs that sit idle or near-idle are wasted compute and a blocker.
The headline run must actually *use* the machine.

1. **Use every allocated GPU.** If `gpu_env.visible_devices()` reports N GPUs,
   the headline run must drive all N — one distributed job across them
   (torchrun / accelerate / DeepSpeed / FSDP, or vLLM `--tensor-parallel-size N`
   for inference) **or** several conditions fanned out one-per-GPU in parallel
   via `code/run_experiments.py`. A headline run pinned to a single GPU while
   others are free is a blocker.
2. **Fill the memory.** Target **high VRAM utilization on each card** (aim for
   ≳70% of each GPU's memory in steady state). Reach it by scaling, in order:
   model size → per-device batch size / sequence length → less aggressive
   quantization. Use **bf16**, **gradient checkpointing**, and
   **flash-attention** so the headroom goes to useful work, not waste. A run
   that trains at a few-GB footprint on a 140GB+ card is the single most common
   failure mode here — treat it as a blocker, not a default.
3. **Maximize throughput — train FAST, don't crawl.** A job that uses a GPU but
   inches along (tiny per-device batch, short sequences, `num_generations=2`)
   wastes the allocation as badly as an idle card *and* slows every iteration of
   the research loop. Push the knobs UP to the largest values that fit and keep
   step-time low / samples-per-second high:
   - **Per-device batch size + gradient accumulation** → raise the effective
     batch until VRAM is full; bigger batches mean fewer, fatter steps and far
     better card utilization than many tiny ones.
   - **Sequence / `max_len` (prompt + completion)** → set it as large as the
     task genuinely needs. For reasoning RL the completion budget must be big
     enough that generations are **not** truncated: a saturating `clipped_ratio`
     or completions pinned at the cap means `max_completion_length` is too
     **small** — raise it, do not shrink it to "save tokens".
   - **GRPO/PPO/RLVR rollouts** → increase `num_generations` / group size and the
     rollout/prompt batch; more parallel rollouts per step both fill memory and
     give a stronger advantage estimate.
   - Prefer **sequence packing**, **bf16**, **flash-attention**, and a
     **vLLM-backed generation** path so the extra memory converts into speed.
   Going deliberately small to be "cheap" or "stable" is justified **only** as a
   smoke run or a documented research/ablation reason — never as the default that
   leaves an allocated card half-empty and the loop slow. If you must bound cost,
   do it by cutting the number of steps or the model size as a stated choice, not
   by starving an otherwise-idle GPU with tiny batches and short sequences.
4. **vLLM inference/eval — fill the card, don't trickle.** Benchmark/eval and
   RL-rollout generation are throughput-bound, and the common failure is a card
   left at low VRAM and low util% by conservative defaults. When you construct a
   `vllm.LLM`, set these explicitly instead of trusting library/script defaults:
   - **`gpu_memory_utilization`** → raise to **0.85–0.92** (a default like `0.55`
     leaves ~half the KV-cache budget on the table; that alone explains a card
     sitting at ~55% VRAM). Leave a little headroom only when co-locating other
     processes on the GPU.
   - **`max_num_seqs`** (max concurrent sequences) → raise it to **64–256** so
     continuous batching actually keeps the GPU busy; a value like `8` serializes
     the work and leaves util in the teens. Don't tie it to a tiny training-style
     `batch_size`.
   - **`max_num_batched_tokens`** → raise alongside `max_num_seqs` (e.g.
     8k–32k) so the scheduler can pack many requests per step.
   - **`max_model_len`** → size it to the real prompt + generation need; for
     reasoning evals `max_tokens` (generation cap) must be long enough that
     answers/CoT are not truncated. A short generation cap silently caps quality,
     not just speed.
   - **`tensor_parallel_size = N`** for a model too big for one card, or fan out
     **one condition per GPU** when each fits — never leave N-1 cards idle.
   - Prefer **CUDA graphs** (do **not** force `enforce_eager=True` unless a real
     bug requires it — eager mode disables graph capture and slows generation).
   - **Feed all tasks at once** and let vLLM batch them; never loop one prompt at
     a time with a fresh engine. Submit the whole prompt set and stream results.
   These are defaults to set deliberately per run; if a seeded helper
   (`code/run_benchmark_condition.py` or similar) hard-codes a low
   `gpu_memory_utilization` / `max_num_seqs`, raise the defaults or pass larger
   values rather than inheriting the trickle.
5. **Verify, don't assume.** While the run is live, check actual utilization
   (`nvidia-smi` or `.venv/bin/python code/gpu_env.py`) at least once and record
   **peak VRAM per GPU, observed GPU util%, and throughput (step time or
   samples/sec)** in the run's `manifest.json`/report. "I launched a distributed
   command" is not evidence; measured utilization is. A run that trained at a
   few-GB / low-util footprint on a 140GB+ card — or that crawled at a tiny batch
   while the card sat mostly idle between steps — has not used the hardware and
   must be rescaled before it counts.
6. **Stay unblocked.** Saturating the GPUs does not mean blocking on them:
   submit the heavy job through `argus_skill.tools.subagent --mode supervised`
   and keep working. A healthy `running` job is **not** a failure — see the
   Subagent note below.



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
- NEVER install ML deps in the argus-skill framework venv (`$ARGUS_SKILL_PYTHON`)

**Project model store** (for ALL downloaded weights / adapters / datasets):
- Path: `./models/` inside the project directory (pre-created by the launcher).
- Set HF / Torch cache env vars before any download or model load:
  ```bash
  export HF_HOME="$(pwd)/models/huggingface"
  export HUGGINGFACE_HUB_CACHE="$(pwd)/models/huggingface/hub"
  export HF_DATASETS_CACHE="$(pwd)/models/huggingface/datasets"
  export TRANSFORMERS_CACHE="$(pwd)/models/huggingface/hub"
  export TORCH_HOME="$(pwd)/models/torch"
  ```
- Equivalent Python (set before `import transformers` / `from huggingface_hub`):
  ```python
  import os, pathlib
  root = pathlib.Path.cwd() / "models"
  os.environ.update({
      "HF_HOME": str(root / "huggingface"),
      "HUGGINGFACE_HUB_CACHE": str(root / "huggingface/hub"),
      "HF_DATASETS_CACHE": str(root / "huggingface/datasets"),
      "TRANSFORMERS_CACHE": str(root / "huggingface/hub"),
      "TORCH_HOME": str(root / "torch"),
  })
  ```
- `./models/` is already in `.gitignore`; never commit downloaded weights.
- NEVER download into `~/.cache/`, `/root/.cache/`, or any other project's `models/`. Each project owns its weights.
- Skip the download entirely if the model is served via the model API route in `~/.argus-skill/capabilities/model_api.json`.

**Subagent** (for long GPU tasks):
- Submit: `python -m argus_skill.tools.subagent submit --task-id <id> --mode supervised --run-dir experiments/<id> --command '.venv/bin/python ...'`
- `--mode supervised` attaches an RL-aware LLM watcher: it polls the log on an
  increasing interval (backs off while healthy to save tokens, tightens when it
  sees trouble), judges reward/KL/response-length (not just SFT loss), and writes
  `STOP` into the run dir to early-stop a diverging run.
- `--run-dir` should point at the `experiment_io` run directory so the watcher
  reads `progress.jsonl`/`status.json` and its early-stop `STOP` reaches `RunWriter`.
- Check: `python -m argus_skill.tools.subagent status --task-id <id>`
- A `status` call exits **0 while the job is healthily `running`** (and when it
  is `done`/`early_stopped`); it exits non-zero **only** for genuine failures
  (`error`/`crashed`/`timeout`). So a `running` result is NOT a failed command —
  do not "repair" it. The JSON includes `live` (worker process alive) and a
  `progress` summary (rows written + last progress line + age), so one poll
  answers "is it alive and advancing" without hand-reading `progress.jsonl`.
- Poll with backoff (the supervised watcher already does); do not spam `status`
  every few seconds. Inspect the run directory directly only if `live` is true
  but `progress` has stopped advancing.
- Do NOT block — submit and continue other work.
- `code/` is already on `PYTHONPATH` in your shell: `import gpu_env`,
  `import experiment_io`, `import benchmark_loaders` work directly. Do NOT
  prefix commands with `PYTHONPATH=$PWD/code` — it is redundant.

**Reusable project scaffolds** (seeded in `code/`, standalone, run with `.venv/bin/python`):
- `code/gpu_env.py` — call `gpu_env.configure_caches()` before any model load to pin
  HF/Torch caches to `./models/`; `gpu_env.visible_devices()` / `suggest_nproc()` tell
  you how many GPUs you may use. Run `.venv/bin/python code/gpu_env.py` for a one-screen
  GPU + cache readiness report.
- `code/experiment_io.py` — `experiment_io.RunWriter(...)` writes the full run-directory
  contract for you (`manifest.json`, `status.json`, `progress.jsonl`, `results.jsonl`
  rows with `method`/`task_id`/`score`, `STOP` handling, exit 130 on cancel). Wrap your
  framework calls with it instead of re-implementing run bookkeeping;
  `experiment_io.validate_run(<dir>)` self-audits a run before you claim it complete.
- `code/run_experiments.py` — launch a whole method×benchmark matrix as NON-BLOCKING
  sub-agent jobs from `experiments/MATRIX.json`, then `status` to poll. This is how you
  fan out and stay unblocked.

**Multi-GPU utilization** (this box has multiple GPUs — use them):
- One large job that needs all GPUs → declare one condition with `"gpus": "0,1,2,3"` in
  `MATRIX.json` and launch your framework's distributed runner inside its command
  (torchrun / accelerate / deepspeed / vLLM `--tensor-parallel-size`).
- Several independent conditions → give each a disjoint GPU subset (`gpu_policy:
  "fanout_one_gpu"` or explicit `"gpus"`) so they train/evaluate in PARALLEL on
  different GPUs. Never leave allocated GPUs idle while work is queued.
- Never run two parallel conditions on the same GPU unless you have measured the memory
  headroom; `run_experiments.py` warns on oversubscription.

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
