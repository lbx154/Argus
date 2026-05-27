---
name: ai-infra-research-pipeline
description: "AI Infrastructure/Systems research pipeline: throughput/latency benchmarking, distributed systems, serving optimization, kernel development, memory efficiency. Use for MLSys/OSDI/ATC/EuroSys-style systems papers."
category: domain-systems
version: "1.0"
scientist_model: gpt-5.4
created_at: "2025-07-27"
---

# AI Infrastructure Research Pipeline

Pipeline for systems/infrastructure papers (MLSys, OSDI, ATC, EuroSys, SC, ISCA).

## Supported Research Areas

| Area | Examples | Key Metrics |
|------|----------|-------------|
| Serving/Inference | vLLM, TensorRT-LLM, SGLang | Throughput (tok/s), TTFT, TBT, P99 latency |
| Training Systems | Megatron, DeepSpeed, FSDP | MFU, samples/sec, scaling efficiency |
| Memory Optimization | FlashAttention, PagedAttention, quantization | Memory savings, speed vs baseline |
| Kernel Development | Triton, CUDA kernels | TFLOPS, memory bandwidth util |
| Scheduling | Orca, Sarathi, chunk prefill | Throughput, SLO attainment |
| Compilation | torch.compile, XLA, TVM | Speedup over eager, compile time |
| Communication | NCCL, Gloo, all-reduce | Bandwidth, latency, overlap ratio |
| Storage/IO | Data loading, checkpointing | GB/s, checkpoint time |

## Workflow

### Step 1: Define System Under Test

```yaml
system_config:
  name: "our-serving-system"
  baseline: "vLLM v0.4.3"
  hardware:
    gpus: "8x H100-80GB SXM"
    interconnect: "NVLink 4.0 (900 GB/s)"
    cpu: "2x Intel Xeon 8480+"
    memory: "2TB DDR5"
    network: "400Gbps InfiniBand"
  software:
    cuda: "12.4"
    pytorch: "2.4.0"
    python: "3.11"
```

### Step 2: Benchmarking Methodology

**Critical: Systems papers live or die on benchmarking rigor.**

```python
# Standard benchmarking pattern
import time
import torch

def benchmark_throughput(model, inputs, warmup=10, trials=100):
    """Proper GPU benchmarking with warmup and sync."""
    # Warmup
    for _ in range(warmup):
        model(inputs)
    torch.cuda.synchronize()
    
    # Timed trials
    start = time.perf_counter()
    for _ in range(trials):
        model(inputs)
    torch.cuda.synchronize()
    end = time.perf_counter()
    
    avg_time = (end - start) / trials
    return avg_time

# For serving systems: use standardized request generators
# ShareGPT traces, synthetic Poisson arrivals, production traces
```

**Metrics to report for different system types:**

| System Type | Must Report | Nice to Have |
|-------------|------------|--------------|
| Serving | Throughput (req/s), TTFT, TBT, P50/P95/P99 | SLO attainment, cost/token |
| Training | MFU (%), samples/sec, time-to-accuracy | Scaling curves, communication overhead |
| Kernel | TFLOPS, memory BW utilization, speedup | Roofline plot, occupancy |
| Optimizer | Memory saved, throughput impact | Accuracy preservation proof |

### Step 3: Workload Selection

```python
# Standard workloads for LLM serving papers
workloads = {
    "shareGPT": {
        "description": "Real conversation traces",
        "avg_input_len": 161,
        "avg_output_len": 338,
        "source": "ShareGPT dataset"
    },
    "synthetic_uniform": {
        "description": "Controlled synthetic",
        "input_len": [128, 256, 512, 1024, 2048],
        "output_len": [128, 256, 512],
    },
    "long_context": {
        "description": "Long document QA",
        "input_len": [8192, 16384, 32768, 65536, 131072],
        "output_len": 256,
    }
}

# Request rate patterns
arrival_patterns = {
    "constant": lambda rate: rate,  # Fixed QPS
    "poisson": lambda rate: np.random.exponential(1/rate),  # Realistic
    "bursty": "...",  # Production-like bursts
}
```

### Step 4: Scaling Experiments

```python
# Strong scaling: fixed problem, more GPUs
strong_scaling = {
    "model": "Llama-3.1-70B",
    "gpu_counts": [1, 2, 4, 8, 16, 32],
    "metric": "throughput",  # Should scale linearly ideally
}

# Weak scaling: proportional problem, more GPUs  
weak_scaling = {
    "batch_per_gpu": 32,
    "gpu_counts": [1, 2, 4, 8, 16, 32],
    "metric": "throughput_per_gpu",  # Should stay constant ideally
}

# Scaling efficiency = actual_speedup / ideal_speedup
```

### Step 5: Profiling

```bash
# NVIDIA Nsight Systems (whole-system profiling)
nsys profile --trace=cuda,nvtx,osrt python your_code.py
nsys stats report.nsys-rep

# NVIDIA Nsight Compute (kernel-level)
ncu --set full -o kernel_report python your_code.py

# PyTorch Profiler
with torch.profiler.profile(
    activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA],
    schedule=torch.profiler.schedule(wait=1, warmup=3, active=5),
    record_shapes=True,
    with_stack=True,
) as prof:
    ...
```

### Step 6: Figure Standards

**Systems papers need these figures:**

1. **Throughput vs. QPS curve** (main result)
2. **Latency CDF** (P50/P95/P99)
3. **Scaling efficiency plot** (GPUs vs speedup)
4. **Roofline model** (for kernel papers)
5. **Timeline/Gantt chart** (for scheduling/overlap papers)
6. **Memory breakdown** (stacked bar chart)

```python
import matplotlib.pyplot as plt
import numpy as np

# Standard systems paper style
plt.rcParams.update({
    'font.size': 12,
    'font.family': 'serif',
    'figure.figsize': (6, 4),
    'axes.grid': True,
    'grid.alpha': 0.3,
})

# Throughput comparison bar chart
fig, ax = plt.subplots()
methods = ['Baseline', 'Ours', 'Ours+Opt']
throughput = [1000, 1850, 2200]
ax.bar(methods, throughput)
ax.set_ylabel('Throughput (tokens/s)')
ax.set_title('Serving Throughput Comparison')
```

### Step 7: Reproducibility

**Systems papers MUST include:**
```markdown
## Reproducibility
- Hardware: [exact GPU model, count, interconnect]
- Software: [CUDA, PyTorch, driver versions]
- Workload: [exact dataset, request pattern, input/output lengths]
- Configuration: [batch size, parallelism strategy, all knobs]
- Scripts: [link to reproduction scripts]
- Variance: [run N times, report mean ± std]
```

## Standard Table Format

```latex
\begin{table}[t]
\centering
\caption{Serving throughput (tokens/s) under ShareGPT workload.}
\begin{tabular}{lccccc}
\toprule
System & 7B & 13B & 34B & 70B & 70B×2 \\
\midrule
vLLM & 2,847 & 1,523 & 891 & 412 & -- \\
TensorRT-LLM & 3,102 & 1,689 & 957 & 468 & -- \\
\textbf{Ours} & \textbf{4,215} & \textbf{2,341} & \textbf{1,289} & \textbf{634} & \textbf{1,102} \\
\bottomrule
\end{tabular}
\end{table}
```

## Common Pitfalls

- **Cherry-picked workloads**: Test diverse workloads (short/long, bursty/uniform)
- **Missing warmup**: GPU benchmarks without warmup are meaningless
- **No error bars**: Run 3+ times, report std
- **Unfair baselines**: Use same hardware, same driver, same CUDA version
- **Missing tail latency**: P50 is easy; P99 is what matters for production
- **Ignoring accuracy**: Systems optimizations must prove no accuracy loss
- **Compile time hidden**: Report compile/setup time if it's non-trivial
- **Ignoring memory**: Throughput gains that double memory usage aren't free

## Integration

- Uses `training-experiment-runner` for training system experiments
- Results feed into `result-to-claim` and `ablation-planner`
- Profiling figures via matplotlib (not gpt-image-2 — needs precision)
- Paper writing via `emnlp-paper-drafting` (works for MLSys/OSDI too)
