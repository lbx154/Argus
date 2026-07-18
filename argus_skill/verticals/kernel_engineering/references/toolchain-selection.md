# Kernel toolchain selection — primary-source map

Last refreshed: 2026-07-18. Re-check current releases and the target repository
before pinning versions; URLs are discovery anchors, not a universal install
recipe.

## Start with the target repository

- Read its `AGENTS.md`, `CONTRIBUTING.md`, install/environment docs, package
  extras, lockfiles, CI matrix, tests, benchmark runner, backend registry, and
  reference kernels.
- Prefer its supported dependency versions. A globally newest compiler/DSL can
  be incompatible with the repository's PyTorch/Triton/CUDA stack.

## NVIDIA architecture, compilation, and debugging

- CUDA Programming Guide: https://docs.nvidia.com/cuda/cuda-programming-guide/
- Blackwell Tuning Guide: https://docs.nvidia.com/cuda/blackwell-tuning-guide/
- CUDA Binary Utilities (`ptxas`, `cuobjdump`, `nvdisasm`):
  https://docs.nvidia.com/cuda/cuda-binary-utilities/
- Nsight Compute: https://docs.nvidia.com/nsight-compute/
- Nsight Systems: https://docs.nvidia.com/nsight-systems/
- Compute Sanitizer: https://docs.nvidia.com/compute-sanitizer/

## Maintained kernel libraries and DSLs

- CUTLASS and CuTe DSL: https://github.com/NVIDIA/cutlass
- CuTe DSL documentation: https://docs.nvidia.com/cutlass/latest/media/docs/pythonDSL/overview.html
- cuTile Python: https://docs.nvidia.com/cuda/cutile-python/
- Triton: https://triton-lang.org/
- Triton Gluon tutorials: https://triton-lang.org/main/getting-started/tutorials/gluon/
- TileLang: https://github.com/tile-ai/tilelang
- PyTorch custom operators: https://docs.pytorch.org/tutorials/advanced/custom_ops_landing_page.html

Use project-compatible stable releases first. Nightlies or source builds need a
pinned commit, isolated environment, smoke test, and explicit reason.

## Specialist ML kernels

- FlashAttention: https://github.com/Dao-AILab/flash-attention
- FlashInfer: https://github.com/flashinfer-ai/flashinfer
- Transformer Engine: https://github.com/NVIDIA/TransformerEngine
- xFormers: https://github.com/facebookresearch/xformers

Study and reuse shared kernels/backends already present in the target project
before adding a new copy.

## Training/RL infrastructure boundary

- TorchTitan: https://github.com/pytorch/torchtitan
- Megatron-LM: https://github.com/NVIDIA/Megatron-LM
- NeMo: https://github.com/NVIDIA-NeMo/NeMo
- DeepSpeed: https://github.com/deepspeedai/DeepSpeed
- veRL: https://github.com/volcengine/verl
- OpenRLHF: https://github.com/OpenRLHF/OpenRLHF
- TRL: https://github.com/huggingface/trl

For an end-to-end benchmark, installing and configuring the maintained framework
is part of setup. Do not ask the agent to recreate distributed launch, rollout,
checkpoint, optimizer, data, or evaluation infrastructure unless changing that
infrastructure is the actual task.
