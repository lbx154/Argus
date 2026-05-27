---
name: training-experiment-runner
description: "Run GPU training experiments: fine-tuning, pretraining, RLHF/DPO. Manages distributed training (DeepSpeed, FSDP), checkpointing, wandb logging, and hyperparameter sweeps. Use for any paper that involves model training."
category: experiment-execution
version: "1.0"
scientist_model: gpt-5.4
created_at: "2025-07-27"
---

# Training Experiment Runner

End-to-end management of GPU training experiments for research papers.

## When to Use

- Paper requires fine-tuning, pretraining, or alignment training
- Need to run distributed training across multiple GPUs/nodes
- Managing hyperparameter sweeps for ablation studies
- Any research that is NOT training-free (complements `agent-research-benchmark-runner`)

## Supported Training Paradigms

| Paradigm | Tools | Typical Use |
|----------|-------|-------------|
| Full fine-tuning | DeepSpeed ZeRO-3, FSDP2 | <7B params or multi-node |
| Parameter-efficient (LoRA/QLoRA) | PEFT, Unsloth, Axolotl | 7B-70B on limited GPU |
| Pretraining | Megatron-Core, TorchTitan, NanoGPT | Architecture papers |
| RLHF/DPO/KTO | TRL, Axolotl, OpenRLHF | Alignment papers |
| Multimodal training | LLaVA, BLIP-2, OpenVLA | Vision-language papers |
| Distributed | DeepSpeed, PyTorch FSDP2, Ray Train | Scale papers |

## Workflow

### Step 1: Environment Setup

```bash
# Verify GPU availability
nvidia-smi
python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}, GPUs: {torch.cuda.device_count()}')"

# Standard training stack
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
pip install transformers datasets accelerate peft wandb
pip install deepspeed  # if multi-GPU
```

### Step 2: Training Configuration

Create a structured training config:

```yaml
# training_config.yaml
model:
  name_or_path: "meta-llama/Llama-3.1-8B"
  dtype: bfloat16
  
training:
  method: lora  # full | lora | qlora | dpo | pretraining
  epochs: 3
  batch_size: 4
  gradient_accumulation: 8
  learning_rate: 2e-4
  warmup_ratio: 0.1
  weight_decay: 0.01
  max_seq_length: 2048
  
lora:  # if method == lora/qlora
  r: 16
  alpha: 32
  target_modules: [q_proj, k_proj, v_proj, o_proj]
  dropout: 0.05

distributed:
  strategy: deepspeed_zero2  # none | ddp | fsdp | deepspeed_zero2 | deepspeed_zero3
  num_gpus: 4
  
logging:
  wandb_project: "paper-experiments"
  wandb_run_name: "exp-001-lora-r16"
  log_every_n_steps: 10
  save_every_n_steps: 500
  
evaluation:
  eval_every_n_steps: 250
  eval_dataset: "validation_split"
  metrics: [loss, accuracy, perplexity]
```

### Step 3: Launch Training

```bash
# Single GPU
python train.py --config training_config.yaml

# Multi-GPU with DeepSpeed
deepspeed --num_gpus=4 train.py --config training_config.yaml \
  --deepspeed ds_config_zero2.json

# Multi-GPU with Accelerate
accelerate launch --num_processes=4 --mixed_precision=bf16 \
  train.py --config training_config.yaml

# With Axolotl (YAML-driven)
accelerate launch -m axolotl.cli.train axolotl_config.yaml
```

### Step 4: Monitoring & Checkpointing

**During training, continuously monitor:**
- Loss curves (train/val) — detect divergence or overfitting
- Learning rate schedule — verify warmup/decay
- GPU memory utilization — ensure not OOM
- Gradient norms — detect exploding gradients
- Throughput (samples/sec) — for efficiency claims

**Checkpoint strategy:**
```python
# Save best + last + every N steps
checkpointing:
  save_best: true  # by val_loss
  save_last: true
  save_every_n_steps: 1000
  keep_top_k: 3  # disk management
```

### Step 5: Hyperparameter Sweeps

For ablation studies, use structured sweeps:

```python
# sweep_config.py
sweeps = {
    "learning_rate": [1e-5, 5e-5, 1e-4, 2e-4, 5e-4],
    "lora_r": [4, 8, 16, 32, 64],
    "batch_size": [2, 4, 8, 16],
}

# Grid search for small spaces, random for large
# Track all runs in wandb for paper figures
```

### Step 6: Result Collection

After training completes:
```bash
# Collect final metrics
python eval.py --checkpoint best --datasets "test_set_1,test_set_2"

# Export results to standard format
python export_results.py --format json --output results/training_exp_001.json
```

Result file format:
```json
{
  "experiment_id": "exp-001",
  "method": "lora-r16",
  "model": "Llama-3.1-8B",
  "training": {
    "epochs": 3,
    "total_steps": 5000,
    "wall_time_hours": 2.5,
    "gpu_hours": 10.0,
    "final_train_loss": 0.42,
    "best_val_loss": 0.51
  },
  "evaluation": {
    "test_accuracy": 0.847,
    "test_f1": 0.831,
    "perplexity": 8.2
  },
  "hardware": {
    "gpus": "4x A100-80GB",
    "framework": "DeepSpeed ZeRO-2"
  }
}
```

## DeepSpeed Configs (Reference)

### ZeRO Stage 2 (most common for fine-tuning)
```json
{
  "bf16": {"enabled": true},
  "zero_optimization": {
    "stage": 2,
    "offload_optimizer": {"device": "none"},
    "allgather_partitions": true,
    "allgather_bucket_size": 2e8,
    "reduce_scatter": true,
    "reduce_bucket_size": 2e8
  },
  "gradient_accumulation_steps": 8,
  "train_micro_batch_size_per_gpu": 4
}
```

### ZeRO Stage 3 (for very large models)
```json
{
  "bf16": {"enabled": true},
  "zero_optimization": {
    "stage": 3,
    "offload_optimizer": {"device": "cpu"},
    "offload_param": {"device": "cpu"},
    "overlap_comm": true,
    "contiguous_gradients": true,
    "sub_group_size": 1e9
  }
}
```

## Common Pitfalls

- **OOM**: Reduce batch_size, enable gradient checkpointing, use ZeRO-3 offload
- **Loss spike**: Lower LR, increase warmup, check data quality
- **Slow convergence**: Increase LR, check if LoRA rank is too low
- **Distributed deadlock**: Ensure all processes reach same collective ops
- **Checkpoint corruption**: Always verify checkpoint loading before claiming results
- **Reproducibility**: Set seeds, log all configs, pin library versions

## Integration

- Complements `agent-research-benchmark-runner` (which is for training-free evaluation)
- Results feed into `result-to-claim` for claim verification
- Training configs documented for `emnlp-paper-drafting` reproducibility section
- Hardware/time stats needed for `emnlp-format-preflight` (reproducibility checklist)
