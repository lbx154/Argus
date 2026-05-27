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

## Domain Classification

| Domain | Signals | Primary Pipeline | Venue Targets |
|--------|---------|-----------------|---------------|
| Agent/LLM (training-free) | prompting, API calls, tool use, multi-agent | `auto-research-pipeline` + `agent-research-benchmark-runner` | EMNLP, ACL, NeurIPS, ICLR |
| CV | images, detection, segmentation, ViT, CNN | `cv-research-pipeline` + `training-experiment-runner` | CVPR, ICCV, ECCV, NeurIPS |
| Multimodal/VLM | vision-language, VQA, image+text | `multimodal-research-pipeline` + `training-experiment-runner` | CVPR, NeurIPS, EMNLP, ICLR |
| AI Infrastructure | serving, throughput, latency, kernel, distributed | `ai-infra-research-pipeline` | MLSys, OSDI, ATC, SC, ISCA |
| NLP (training) | fine-tuning, pretraining, tokenization | `training-experiment-runner` + `agent-research-benchmark-runner` | EMNLP, ACL, NAACL, EACL |
| RL/Alignment | RLHF, DPO, reward model | `training-experiment-runner` | NeurIPS, ICML, ICLR |
| Theory/Analysis | proofs, bounds, mechanistic interp | `research-brief-to-experiment-plan` | NeurIPS, ICML, ICLR |

## Routing Logic

```
Given a research brief / idea:

1. Extract keywords and classify domain:
   - Contains "agent", "tool-use", "prompt", "API", "multi-agent" → Agent
   - Contains "image", "detection", "segmentation", "ViT", "CNN", "pixel" → CV
   - Contains "vision-language", "VQA", "multimodal", "image-text" → Multimodal
   - Contains "throughput", "latency", "serving", "kernel", "CUDA" → AI Infra
   - Contains "fine-tune", "pretrain", "tokeniz", "corpus" → NLP (training)
   - Contains "RLHF", "DPO", "reward", "alignment" → RL/Alignment

2. Determine if training is required:
   - No training needed → use `agent-research-benchmark-runner`
   - Training required → use `training-experiment-runner`
   - Both (train + inference eval) → use both

3. Select venue-appropriate formatting:
   - ML venues (NeurIPS, ICML, ICLR) → 9-page limit, appendix allowed
   - NLP venues (EMNLP, ACL) → 8-page limit, strict formatting
   - CV venues (CVPR, ICCV) → 8-page limit, heavy on figures
   - Systems venues (MLSys, OSDI) → 12-14 pages, heavy on benchmarks
```

## Skill Selection Per Domain

### Agent/LLM (Training-Free)
```
Required skills:
- auto-research-pipeline (orchestration)
- agent-research-benchmark-runner (evaluation)
- novelty-check (literature)
- result-to-claim (claims)
- emnlp-paper-drafting (writing)

Optional:
- research-ideation (if idea phase)
- ablation-planner (after results)
```

### CV (Training-Based)
```
Required skills:
- cv-research-pipeline (domain-specific)
- training-experiment-runner (training)
- result-to-claim (claims)
- ablation-planner (ablations are critical for CV)
- emnlp-paper-drafting (writing — works for CVPR too)

Optional:
- paper-illustration-image2 (architecture figures)
- experiment-audit (integrity check)
```

### Multimodal/VLM
```
Required skills:
- multimodal-research-pipeline (domain-specific)
- training-experiment-runner (multi-stage training)
- agent-research-benchmark-runner (inference eval)
- result-to-claim (claims)
- emnlp-paper-drafting (writing)

Optional:
- cv-research-pipeline (vision components)
- ablation-planner (architecture ablations)
```

### AI Infrastructure
```
Required skills:
- ai-infra-research-pipeline (domain-specific)
- result-to-claim (claims)
- emnlp-paper-drafting (writing — works for MLSys too)

Optional:
- training-experiment-runner (if training system paper)
- ablation-planner (for optimization papers)
```

### NLP (Training-Based)
```
Required skills:
- training-experiment-runner (fine-tuning/pretraining)
- agent-research-benchmark-runner (evaluation)
- novelty-check (literature)
- result-to-claim (claims)
- emnlp-paper-drafting (writing)

Optional:
- ablation-planner (component analysis)
- semantic-scholar-search (literature)
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
