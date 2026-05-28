---
name: Experiment Plan Review
description: Review an experiment plan for scientific rigor before execution begins. Check method competitiveness, baseline strength, evaluation fairness, and benchmark adequacy.
category: paper-review
version: 1
created_at: 2026-05-28T00:00:00+00:00
---

# Experiment Plan Review

Review an experiment plan as a senior ML researcher would before approving compute budget. The goal is to catch fundamental design flaws before expensive experiments run, not after.

## Reviewer stance
- You are approving a GPU/API budget request, not reviewing a finished paper.
- A bad plan wastes weeks of compute. Be strict on design, lenient on prose.
- If the plan would produce unconvincing evidence even if executed perfectly, reject it.

## Five review dimensions

Score each 1–5. Score 3+ on all dimensions = pass.

1. **Method competitiveness**
   - Is the proposed method a plausible contribution, or is it too trivial (bag-of-words, logistic regression) for the target venue?
   - Does it use available compute appropriately (e.g., if GPUs are available, is the method GPU-worthy)?
   - Is there a clear mechanism that differentiates it from baselines?

2. **Baseline strength**
   - Are there at least 3 non-trivial baselines?
   - Is there at least one strong published baseline (not just no-op and random)?
   - Would a reviewer say "but did you compare against X?" for an obvious X?
   - Are baselines given fair resource budgets (same model, same decoding, same budget)?

3. **Evaluation fairness**
   - Is the comparison apples-to-apples? Same model backbone, same prompts, same budget?
   - Are ablations designed to isolate the proposed mechanism (not compare trained vs untrained)?
   - Are metrics appropriate for the task?
   - Is there a plan for statistical significance testing?

4. **Benchmark adequacy**
   - Are there at least 3 independent benchmark sources (not variants of one dataset)?
   - Are benchmarks real/published (not locally invented synthetic tasks for final evidence)?
   - Is the task count sufficient (≥200 per condition for meaningful statistics)?
   - Do benchmarks cover different aspects of the method's claimed contribution?

5. **Feasibility and scope**
   - Can the experiments be completed with available compute in reasonable time?
   - Is the scope appropriate for the target venue (not too narrow, not too broad)?
   - Are there clear success/failure criteria defined before running?

## Output format

Return JSON:
```json
{
  "score": 1-5,
  "pass": true/false,
  "dimension_scores": {
    "method_competitiveness": 1-5,
    "baseline_strength": 1-5,
    "evaluation_fairness": 1-5,
    "benchmark_adequacy": 1-5,
    "feasibility": 1-5
  },
  "issues": ["specific issue 1", "specific issue 2"],
  "verdict": "one sentence overall judgment",
  "suggested_fixes": ["fix 1 before running", "fix 2"]
}
```

## Hard blockers (auto-fail regardless of score)
- No baselines defined at all
- Only one benchmark source
- Proposed method is a known standard technique with no novel mechanism
- Ablation compares trained model vs untrained/random (not a fair ablation)
- No evaluation metrics defined
- Custom training loop when an established framework would work (see training-infrastructure-guide.md)
- Custom model.generate() loop for >100 examples when vLLM/SGLang would work (see inference-infrastructure-guide.md)

## Infrastructure check
If the plan involves training (SFT, RLHF, DPO, RL, pretraining, adapter tuning):
- Does it name a specific framework (LLaMA-Factory, TRL, SLIME, OpenRLHF, etc.)?
- If it plans a custom training loop, is there a justification for why no framework works?
- Flag as issue if it plans to write training from scratch without justification.

If the plan involves inference on >100 examples:
- Does it plan to use vLLM, SGLang, TGI, or an API with batching?
- If it plans bare `model.generate()` in a for-loop, flag as issue.
- Flag as issue if no inference engine is mentioned for large-scale evaluation.
