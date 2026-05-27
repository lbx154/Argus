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

**Decision flow:**
```
1. Classify DOMAIN (what area?)
   → Agent | CV | Multimodal | AI Infra | NLP | RL | Theory

2. Classify METHODOLOGY (how?)
   → Training-free | Training-based | Hybrid (both)

3. Select skills = domain_skills ∪ methodology_skills ∪ cross_domain_skills
```

## Domain Classification

| Domain | Signals | Primary Pipeline | Venue Targets |
|--------|---------|-----------------|---------------|
| Agent/LLM | prompting, tool use, multi-agent, planning, memory | `auto-research-pipeline` + `agent-research-benchmark-runner` | EMNLP, ACL, NeurIPS, ICLR |
| CV | images, detection, segmentation, ViT, CNN, 3D | `cv-research-pipeline` | CVPR, ICCV, ECCV, NeurIPS |
| Multimodal/VLM | vision-language, VQA, image+text, video-language | `multimodal-research-pipeline` | CVPR, NeurIPS, EMNLP, ICLR |
| AI Infrastructure | serving, throughput, latency, kernel, distributed | `ai-infra-research-pipeline` | MLSys, OSDI, ATC, SC, ISCA |
| NLP | language models, parsing, generation, summarization | `auto-research-pipeline` + `agent-research-benchmark-runner` | EMNLP, ACL, NAACL, EACL |
| RL/Alignment | RLHF, DPO, reward model, policy optimization | Domain-specific + `training-experiment-runner` | NeurIPS, ICML, ICLR |
| Theory/Analysis | proofs, bounds, mechanistic interp | `research-brief-to-experiment-plan` | NeurIPS, ICML, ICLR |

## Methodology Classification

| Methodology | When | Add These Skills |
|-------------|------|-----------------|
| Training-free | API-only, zero-shot, prompting, inference | `agent-research-benchmark-runner` (eval only) |
| Training-based | Fine-tuning, pretraining, RL, any gradient updates | `training-experiment-runner` |
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

3. Compose skill set:
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
Domain skills:
- auto-research-pipeline (orchestration)
- agent-research-benchmark-runner (evaluation)

If training-free:
  + (no additional training skills)
If training-based (e.g., fine-tune for tool-calling, train reward model):
  + training-experiment-runner
```

### CV
```
Domain skills:
- cv-research-pipeline (dataset/eval/visualization)

If training-free (e.g., zero-shot CLIP, pretrained model eval):
  + agent-research-benchmark-runner (inference-only eval)
If training-based (e.g., train ViT, fine-tune detector):
  + training-experiment-runner
```

### Multimodal/VLM
```
Domain skills:
- multimodal-research-pipeline (benchmarks, architecture patterns)

If training-free (e.g., GPT-4V prompting, zero-shot VQA):
  + agent-research-benchmark-runner
If training-based (e.g., LLaVA training, VLM alignment):
  + training-experiment-runner
```

### AI Infrastructure
```
Domain skills:
- ai-infra-research-pipeline (benchmarking, profiling)

If training-free (e.g., serving system, inference kernel):
  + (benchmarks are inline in ai-infra pipeline)
If training-based (e.g., training system paper, Megatron):
  + training-experiment-runner
```

### NLP
```
Domain skills:
- auto-research-pipeline (orchestration)

If training-free (e.g., in-context learning, RAG):
  + agent-research-benchmark-runner
If training-based (e.g., pretrain LM, fine-tune for task):
  + training-experiment-runner
```

### RL/Alignment
```
Domain skills:
- (use auto-research-pipeline for orchestration)

If training-free (e.g., constitutional AI via prompting):
  + agent-research-benchmark-runner
If training-based (e.g., PPO, DPO, reward model):
  + training-experiment-runner
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
- 8 pages body, unlimited appendix + references
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
