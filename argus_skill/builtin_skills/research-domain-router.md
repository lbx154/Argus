---
name: research-domain-router
description: "Routes research tasks to the correct domain-specific pipeline based on paper type. Supports: agent/LLM (training-free), CV, multimodal, AI infrastructure/systems, NLP, and training-based methods. Use at project initialization to select the right skill set."
category: orchestration
version: "1.0"
scientist_model: gpt-5.4
created_at: "2025-07-27"
---

# Research Domain Router

Automatically detect paper domain and route to appropriate pipeline skills.

## Design Principle: Domain × Training are Orthogonal

Training is NOT tied to any domain. Any domain can be training-free OR training-based:

| Domain | Training-Free Examples | Training-Based Examples |
|--------|----------------------|------------------------|
| Agent/LLM | Prompting, tool-use, multi-agent orchestration | Fine-tuning for tool calling, training reward models for agents, RL for planning |
| CV | Zero-shot CLIP, pretrained detector inference | ViT pretraining, detection fine-tuning, segmentation training |
| Multimodal | GPT-4V prompting, zero-shot VQA | LLaVA training, BLIP-2 fine-tuning, VLM alignment |
| AI Infra | Serving optimization, kernel benchmarks | Training system papers (Megatron, parallel strategies) |
| NLP | In-context learning, RAG | LLM pretraining, RLHF, domain fine-tuning |
| RL/Alignment | Prompt-based alignment, constitutional AI | PPO/DPO training, reward model training |

## Default Compute Policy

When the operator has made substantial local GPU capacity available, prefer a training-based or hybrid route for publishable empirical work. Training-free routes remain valid for pure evaluation, systems, or prompt/API papers, but they must be justified against the field's current frontier. A tiny custom scorer, bag-of-words classifier, lexical ranker, exact-oracle policy, or prompt wrapper should be routed as smoke/baseline work, not as the proposed model for an EMNLP/ACL-ready paper.

Every route must bind the method to existing real benchmarks or official task/data releases before experiments. If the only available evaluation is synthetic/local proxy tasks, route back to planning and pivot/block rather than accepting the benchmark.

**Decision flow:**
```
1. Classify DOMAIN (what area?)
   → Agent | CV | Multimodal | AI Infra | NLP | RL | Theory

2. Classify METHODOLOGY (how?)
   → Training-free | Training-based | Hybrid (both)

3. Check benchmark reality:
   → Existing real benchmark / official task release | Block/Pivot

4. Select skills = domain_skills ∪ methodology_skills ∪ cross_domain_skills
```

## Domain Classification

| Domain | Signals | Primary Pipeline | Venue Targets |
|--------|---------|-----------------|---------------|
| Agent/LLM | prompting, tool use, multi-agent, planning, memory | `auto-research-pipeline` + `agent-research-benchmark-runner` | EMNLP, ACL, NeurIPS, ICLR |
| CV | images, detection, segmentation, ViT, CNN, 3D | `domains/cv-multimodal/*` + `domains/research-ops/run-experiment.md` | CVPR, ICCV, ECCV, NeurIPS |
| Multimodal/VLM | vision-language, VQA, image+text, video-language | `domains/cv-multimodal/*` + `domains/research-ops/paper-figure.md` | CVPR, NeurIPS, EMNLP, ICLR |
| AI Infrastructure | serving, throughput, latency, kernel, distributed | `domains/inference-serving/*` + `domains/infrastructure/*` + `domains/optimization/*` | MLSys, OSDI, ATC, SC, ISCA |
| NLP | language models, parsing, generation, summarization | `auto-research-pipeline` + `agent-research-benchmark-runner` | EMNLP, ACL, NAACL, EACL |
| RL/Alignment | RLHF, DPO, reward model, policy optimization | Domain-specific skills + `domains/training/*` + `domains/evaluation/*` | NeurIPS, ICML, ICLR |
| Theory/Analysis | proofs, bounds, mechanistic interp | `research-brief-to-experiment-plan` | NeurIPS, ICML, ICLR |

## Methodology Classification

| Methodology | When | Add These Skills |
|-------------|------|-----------------|
| Training-free | API-only, zero-shot, prompting, inference | `agent-research-benchmark-runner` (eval only) |
| Training-based | Fine-tuning, pretraining, RL, any gradient updates | `domains/training/*`, `domains/research-ops/run-experiment.md`, `domains/research-ops/monitor-experiment.md` |
| Hybrid | Train a model + evaluate with agent/inference pipeline | Both of the above |

## Routing Logic

```
Given a research brief / idea:

1. Extract keywords and classify DOMAIN:
   - Contains "agent", "tool-use", "prompt", "multi-agent", "planning" → Agent
   - Contains "image", "detection", "segmentation", "ViT", "CNN", "pixel" → CV
   - Contains "vision-language", "VQA", "multimodal", "image-text" → Multimodal
   - Contains "throughput", "latency", "serving", "kernel", "CUDA" → AI Infra
   - Contains "language model", "parsing", "summariz", "translat" → NLP
   - Contains "RLHF", "DPO", "reward", "alignment", "policy" → RL/Alignment

2. Classify METHODOLOGY (independent of domain!):
   - Mentions "fine-tune", "pretrain", "train", "LoRA", "gradient" → Training-based
   - Mentions "zero-shot", "prompt", "in-context", "API", "inference-only" → Training-free
   - Both signals present → Hybrid
   - If large local GPUs are available and the proposed contribution is learned, upgrade training-free or tiny-scorer plans to Hybrid/Training-based unless the operator explicitly requests a non-training study.

3. Require real benchmark binding:
   - Identify the existing benchmark(s), official dataset/task release, primary metric, and strong baseline before running.
   - If benchmark evidence would be synthetic/local only, return to `research-brief-to-experiment-plan` for pivot/block.

4. Compose skill set:
   skills = domain_pipeline(domain)
         ∪ methodology_skills(methodology)
         ∪ cross_domain_skills
```

**Examples of correct routing:**
- "Fine-tune LLM for better tool calling" → Agent + Training-based
- "Zero-shot CLIP for medical image classification" → CV + Training-free
- "Train a reward model for multi-agent coordination" → Agent + Training-based
- "Benchmark vLLM vs TensorRT-LLM serving" → AI Infra + Training-free
- "Pretrain a video-language model" → Multimodal + Training-based
- "Prompt engineering for code generation" → Agent + Training-free

## Skill Selection Per Domain

### Agent/LLM
```
Domain skills (builtin_skills/domains/agents-rag/):
- langchain, llamaindex, crewai (agent frameworks)
- faiss, qdrant, chroma, sentence-transformers (RAG)

If training-free:
  + agent-research-benchmark-runner
If training-based (e.g., fine-tune for tool-calling, train reward model):
  + domains/training/ (axolotl, peft, deepspeed, etc.)
```

### CV / Multimodal
```
Domain skills (builtin_skills/domains/cv-multimodal/):
- clip, llava, blip-2, segment-anything (vision-language)
- stable-diffusion, cosmos-policy (generation)
- openvla-oft, whisper (embodied, audio)

If training-free (e.g., zero-shot CLIP, pretrained model eval):
  + agent-research-benchmark-runner
If training-based (e.g., train ViT, LLaVA multi-stage):
  + domains/training/ (peft, deepspeed, accelerate, etc.)
```

### AI Infrastructure / Serving
```
Domain skills (builtin_skills/domains/inference-serving/):
- vllm, sglang, tensorrt-llm, llama-cpp (serving systems)

Domain skills (builtin_skills/domains/infrastructure/):
- modal, skypilot, lambda-labs (compute platforms)

Domain skills (builtin_skills/domains/optimization/):
- flash-attention, awq, bitsandbytes, gptq (efficiency)

If training system paper:
  + domains/training/ (megatron-core, torchtitan, deepspeed)
```

### NLP / LLM Training
```
Domain skills (builtin_skills/domains/training/):
- axolotl, llama-factory, peft, unsloth (fine-tuning)
- deepspeed, pytorch-fsdp2, accelerate, megatron-core, ray-train (distributed)
- torchtitan, nanogpt (pretraining)

Domain skills (builtin_skills/domains/evaluation/):
- lm-evaluation-harness, bigcode-evaluation-harness (benchmarking)
```

### Research Operations (all domains)
```
Domain skills (builtin_skills/domains/research-ops/):
- arxiv (paper search + download)
- citation-audit (verify references)
- paper-compile (LaTeX → PDF, fix errors)
- paper-figure (matplotlib/seaborn generation)
- rebuttal (reviewer response drafting)
- monitor-experiment (watch training runs)
- run-experiment (execute experiment plans)
```

## Cross-Domain Skills (Always Available)

These skills work for ANY domain:
- `novelty-check` — literature novelty verification
- `result-to-claim` — experiment → claims routing
- `ablation-planner` — ablation study design
- `experiment-audit` — integrity verification
- `paper-illustration-image2` — figure generation
- `semantic-scholar-search` — literature search
- `research-ideation` — brainstorming
- `emnlp-paper-drafting` — paper writing (adaptable to any venue)
- `paper-review-revision-loop` — review response
- `claims-evidence-audit` — evidence audit
- `research-submission-assurance-gate` — submission preflight

## Venue-Specific Notes

### EMNLP/ACL (NLP)
- 7.5--8 pages main body; conclusion, limitations, and ethics fit by page 8
- References and appendix start on page 9 or later, with no total page cap after the body
- Double-blind review
- Reproducibility checklist required
- Ethics statement required

### CVPR/ICCV (CV)
- 8 pages body + references
- Supplementary material (videos, extra results)
- Heavy emphasis on qualitative results (visual examples)

### NeurIPS/ICML/ICLR (ML)
- 9 pages body + appendix
- ICLR: OpenReview (public reviews)
- NeurIPS: reproducibility checklist, broader impact

### MLSys/OSDI/ATC (Systems)
- 12-14 pages
- Artifact evaluation expected
- Reproduction scripts mandatory
- Hardware setup section critical
