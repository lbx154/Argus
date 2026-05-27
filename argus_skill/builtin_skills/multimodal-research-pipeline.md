---
name: multimodal-research-pipeline
description: "Multimodal/Vision-Language model research pipeline: VLM training (LLaVA, BLIP-2, InternVL), benchmarks (VQA, MMBench, MMMU), dataset construction, and evaluation. Use for any paper involving vision-language or multimodal models."
category: domain-multimodal
version: "1.0"
scientist_model: gpt-5.4
created_at: "2025-07-27"
---

# Multimodal Research Pipeline

End-to-end pipeline for Vision-Language and Multimodal research papers.

## Supported Paradigms

| Paradigm | Representative Models | Key Innovation |
|----------|----------------------|----------------|
| Contrastive VLP | CLIP, SigLIP, EVA-CLIP | Image-text alignment via contrastive loss |
| Generative VLM | LLaVA, InternVL, Qwen-VL | Visual tokens → LLM decoder |
| Encoder-Decoder | BLIP-2, Flamingo, Emu | Q-Former or Perceiver bridge |
| Unified | GPT-4V/o, Gemini | Natively multimodal |
| Diffusion + LLM | DALL-E 3, Emu2 | Text-guided generation |
| Video-Language | VideoChat, Video-LLaVA | Temporal + visual reasoning |
| Embodied | OpenVLA, RT-2, Octo | Vision → action policies |

## Benchmarks

### Understanding Benchmarks
| Benchmark | Task | Metrics | Scale |
|-----------|------|---------|-------|
| VQAv2 | Visual QA | Accuracy (VQA score) | 265K questions |
| GQA | Compositional VQA | Accuracy | 22M questions |
| MMBench | Comprehensive eval | Accuracy per dimension | 3000 samples |
| MMMU | Expert-level multimodal | Accuracy | 11.5K questions |
| MM-Vet | Open-ended VQA | GPT-4 judge score | 218 samples |
| POPE | Hallucination eval | F1, Accuracy | 9K questions |
| TextVQA | OCR+QA | Accuracy | 45K questions |
| DocVQA | Document understanding | ANLS | 50K questions |
| MathVista | Math reasoning + vision | Accuracy | 6141 samples |
| RealWorldQA | Real-world understanding | Accuracy | 765 questions |
| SEEDBench | Multi-dimension | Accuracy | 19K questions |
| HallusionBench | Hallucination | Accuracy | 1129 questions |

### Generation Benchmarks
| Benchmark | Task | Metrics |
|-----------|------|---------|
| GenAI-Bench | T2I alignment | CLIP-Score, Human pref |
| T2I-CompBench | Compositional T2I | Attribute binding, spatial |
| DPG-Bench | Dense prompt T2I | DPGS score |

## Architecture Patterns

### LLaVA-style (Most Common for VLM Papers)

```python
class MultimodalLLM(nn.Module):
    def __init__(self):
        # 1. Vision encoder (frozen or trainable)
        self.vision_encoder = CLIPVisionModel.from_pretrained("openai/clip-vit-large-patch14-336")
        
        # 2. Projection layer (the key trainable bridge)
        self.mm_projector = nn.Sequential(
            nn.Linear(vision_dim, llm_dim),
            nn.GELU(),
            nn.Linear(llm_dim, llm_dim),
        )
        
        # 3. LLM backbone
        self.llm = AutoModelForCausalLM.from_pretrained("meta-llama/Llama-3.1-8B")
    
    def forward(self, images, input_ids, attention_mask):
        # Extract visual features
        vision_features = self.vision_encoder(images).last_hidden_state
        # Project to LLM space
        visual_tokens = self.mm_projector(vision_features)
        # Concatenate visual tokens with text tokens
        inputs_embeds = self.embed_multimodal(visual_tokens, input_ids)
        # Generate
        outputs = self.llm(inputs_embeds=inputs_embeds, attention_mask=attention_mask)
        return outputs
```

### Training Stages (LLaVA paradigm)

```
Stage 1: Pretraining (alignment)
  - Freeze: vision encoder + LLM
  - Train: projection layer only
  - Data: image-caption pairs (558K-1.2M)
  - LR: 1e-3, epochs: 1

Stage 2: Instruction tuning
  - Freeze: vision encoder
  - Train: projection + LLM (full or LoRA)
  - Data: multimodal instruction data (665K+)
  - LR: 2e-5, epochs: 1
```

## Dataset Construction

### Instruction Data Format
```json
{
  "id": "unique_id",
  "image": "path/to/image.jpg",
  "conversations": [
    {"from": "human", "value": "<image>\nDescribe this image in detail."},
    {"from": "gpt", "value": "The image shows..."}
  ]
}
```

### Data Sources for VLM Training
| Source | Type | Scale |
|--------|------|-------|
| ShareGPT4V | Detailed captions | 100K |
| LLaVA-Instruct | Mixed instructions | 665K |
| ALLaVA | High-quality captions | 1.4M |
| Cambrian-10M | Diverse multimodal | 10M |
| InternVL-Chat | Chinese+English | 4M |

## Evaluation Protocol

```python
# Standard VLM evaluation
from lmms_eval import evaluator

# Run multiple benchmarks
results = evaluator.evaluate(
    model="your_model",
    tasks=["vqav2", "mmbench", "mmmu", "pope", "textvqa"],
    batch_size=1,
    num_fewshot=0,
)
```

### Hallucination Evaluation (Critical for VLM papers)
```python
# POPE: Polling-based Object Probing Evaluation
# Tests if model hallucinates objects not in the image
# Report: Accuracy, Precision, Recall, F1, "Yes" ratio
```

## Paper-Specific Requirements

### For VLM Papers, You MUST Report:
1. **Vision encoder**: architecture, resolution, pretrained weights
2. **LLM backbone**: model, size, which layers are frozen/trained
3. **Training data**: exact datasets, filtering, total samples
4. **Training compute**: GPU-hours, hardware, batch size
5. **Resolution**: input image resolution (matters a LOT)
6. **Hallucination metrics**: POPE or similar (reviewers will ask)

### Standard Comparison Table
```latex
\begin{table*}[t]
\centering
\caption{Comparison with state-of-the-art VLMs.}
\begin{tabular}{lcccccccc}
\toprule
Model & LLM & Res. & VQAv2 & GQA & MMB & MMMU & POPE & TextVQA \\
\midrule
LLaVA-1.5 & Vicuna-7B & 336 & 78.5 & 62.0 & 64.3 & 35.4 & 85.9 & 58.2 \\
InternVL-2 & InternLM2-7B & 448 & 79.4 & 62.5 & 73.2 & 36.3 & 87.1 & 72.5 \\
\textbf{Ours} & Llama-3.1-8B & 384 & \textbf{80.1} & \textbf{63.8} & \textbf{74.5} & \textbf{38.2} & \textbf{88.3} & \textbf{73.1} \\
\bottomrule
\end{tabular}
\end{table*}
```

## Common Pitfalls

- **Resolution matters**: Reporting results at different resolutions is an unfair comparison
- **Training data contamination**: Ensure eval benchmarks aren't in training data
- **Hallucination hiding**: Always report POPE/HallusionBench (reviewers check)
- **Unfair data comparison**: Some models use 10x more training data — normalize
- **Missing ablation on vision encoder**: Reviewers ask "what if you use a better encoder?"
- **Ignoring efficiency**: Report FLOPs per image, inference speed, memory

## Integration

- Uses `training-experiment-runner` for training stages
- Results feed into `result-to-claim` for claim verification
- Architecture figures via `paper-illustration-image2`
- Paper writing via `emnlp-paper-drafting` (applies to CVPR/NeurIPS too)
- Benchmarking via `agent-research-benchmark-runner` for inference-only eval
