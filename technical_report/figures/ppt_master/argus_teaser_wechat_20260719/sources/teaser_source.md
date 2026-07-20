# Argus teaser source brief

## Required framework

- Manager: task authority and sole Stage arbiter; may advance, hold, or roll back.
- Planner: forward planning and high-value mission scheduling.
- Engineer: reasoning, implementation, experiments, analysis, and writing.
- Reviewer: independent artifact validation with pass, continue, or block outcomes.
- Structured control plane: ReviewDecision, StageDecision, and Mission View.
- LifeSupervisor/Harness: backlog, budget, daemon, and recent memory.
- Shared workspace: Knowledge & Memory (Idea Wiki and Skill Library), Shared Log, and Shared Artifacts.

## Required result cards

1. SWE-Bench Pro: 78% Argus versus 59% Direct Copilot; 1.41× aggregate tokens.
2. SOL-ExecBench: Global #6; two #1 finishes; seven top-three finishes; 101 kernels.
3. nanochat B200: 0.9636 versus human 0.9646 BPB; lower is better.
4. nanochat H100: 0.9855 versus human 0.9879 BPB; lower is better.
5. nanoGPT speedrun: 79.77 seconds versus human 80.18 seconds; lower is better.
6. AARRI-Bench: 76.8% versus paper-reported best 68.3%.
7. Math-reasoning data: 28.0 versus Arbor 20.83, Claude Code 8.33, and Codex 6.25.

## Visible exclusions

- No formulas.
- No file paths, hashes, internal comments, or implementation chronology.
- No shared normalized axis across incompatible benchmarks.
