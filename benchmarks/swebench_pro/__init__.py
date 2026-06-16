"""SWE-Bench-Pro adapter for Argus-Skill.

End-to-end:
  - load tasks from `ScaleAI/SWE-bench_Pro` (or local jsonl)
  - run each task inside a `jefzda/sweap-images:<tag>` docker container
  - drive `MissionLoopEngine` (engineer + reviewer) against that container
  - extract the final patch via `git -C /app diff HEAD`
  - write `results.jsonl` consumable by skill-agent's `eval_swebench_pro_partial.sh`
    (which runs the official scaleapi eval).

Usage:
    python -m benchmarks.swebench_pro --max-tasks-per-repo 2

This module mirrors the layering of `harbor_adapter.py` so the
engineer/reviewer/skill-cache configuration is byte-identical to the
harbor-adapter benchmark setup.
"""
