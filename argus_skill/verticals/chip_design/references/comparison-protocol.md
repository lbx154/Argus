# Fair Chip and Accelerator Comparison

## Same-flow hardware baselines

Compare candidate and open baselines under the same:

- FPGA or PDK/library/corner;
- clock and I/O constraints;
- SRAM/memory accounting;
- DMA/external bandwidth and host;
- numerical formats and quality floor;
- tools, versions, settings, and repetitions.

For an LLM accelerator, include area-matched and bandwidth-matched Gemmini/VTA-style
baselines when possible.

## System baselines

Use the exact model, quantization, prompt/context/output lengths, tokenizer,
sampling, warmup, host work, and power method. Report TTFT, TPOT, tokens/s, watts,
joules/token, and quality.

## Commercial references

Jetson, Hailo, Qualcomm, Apple, AMD, and Intel products are market context unless
physically measured on the same workload. Vendor TOPS is not a workload result.
Different-node PPA is never a direct win; state process and system differences.
