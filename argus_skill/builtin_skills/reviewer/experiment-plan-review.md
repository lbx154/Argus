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

## Review dimensions

Score each 1–5. Score 3+ on all applicable dimensions = pass. Dimensions 1–5
always apply; dimension 6 (RL config sanity) applies only to RL/preference
post-training plans — omit `rl_config_sanity` from the output for non-RL plans.

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

6. **RL training-configuration sanity** *(score only if the method is RL/preference post-training — PPO/GRPO/RLVR/DPO/reasoning RL; skip for non-RL plans)*

   The plan should already pin the RL config (group size, sampling, lengths,
   optimizer, KL, reward, init). A senior RL researcher can tell at a glance
   whether a config can possibly produce a learning signal. Reject configs that
   are structurally unlearnable *before* burning GPU. Check:
   - **Group / advantage signal (GRPO/RLVR/PPO):** is the group size
     (`num_generations` / rollouts per prompt) large enough to create
     within-group reward contrast? 2 is almost always too few (near-zero
     advantage → zero gradient); want ≥4, typically 8–16. PPO needs a critic or
     a sound advantage estimator, not a constant baseline.
   - **Reward variance by construction:** at the *current policy's* competence,
     will the reward actually vary across samples, or is it all-or-nothing on a
     set that is too hard (reward pinned at 0) or too easy (pinned at max)? A
     reward that is constant for every rollout gives zero advantage. Is there a
     difficulty/curriculum match, and a verifiable correctness signal (not just
     length/format that is trivially hackable)?
   - **Reward plumbing:** if the reward depends on extracting a final answer
     (`\boxed{}`, tool call, AST), does the plan verify the extractor +
     gold-matching actually fire on real outputs? Unverified extraction silently
     yields zero reward.
   - **Sampling / length — judge against a concrete budget, do not eyeball:**
     A `max_completion_length` that truncates the response before the rewarded
     token (`\boxed{}`, `</answer>`, final tool call, closing code fence) makes
     the reward unobtainable *no matter how good the policy is*. To judge it,
     estimate the required output length and compare with headroom:
       1. Identify the benchmark's output type and look up / tokenize a handful
          of gold answers (or reference CoT traces) to get a length distribution.
       2. The config's `max_completion_length` must comfortably exceed the **p95**
          required length (≈1.5–2× the typical length), because RL rollouts run
          *longer* than greedy gold answers (exploration, rambling), and the
          rewarded token must survive.
     Reference budgets (tokens, as a sanity anchor — adjust to the actual data):
       - Short-answer / classification (label, single number): 32–128 ok.
       - Grade-school math with CoT (GSM8K-style): 256–512; <256 is suspect.
       - Competition math / multi-step reasoning (MATH, AIME, olympiad): 1k–4k;
         **256–512 is an auto-reject** — the `\boxed{}` is routinely truncated.
       - Code generation (full program / function + tests): 1k–4k+ depending on
         task; a single short function may fit in 512, a repo-level task will not.
       - Agentic / tool-use / multi-turn: 2k–8k+; budget for tool call syntax
         and observations, not just the final answer.
       - Long-form generation (proofs, essays, plans): size to the target length.
     If the plan pins `max_completion_length` *below* these for the chosen
     benchmark, flag it as a hard length issue and name the value it should be.
     Also check sampling temperature is high enough to explore (≈0 → no reward
     variance) but not degenerate, and that `max_prompt_length` + completion fits
     the model's context window (otherwise rollouts silently truncate the prompt).
   - **Optimization:** is the RL learning rate appropriate (RL LR ≪ SFT LR;
     e.g. 1e-6–1e-5 LoRA, lower full-tune — a SFT-scale LR diverges the policy)?
     Are KL coefficient / clip range present and sane for the algorithm? Is
     `max_steps` enough to show learning rather than only a smoke?
   - **Model init:** does the backbone match the reward? Reasoning/format RL on a
     bare base model with no format adherence (or a missing/incorrect chat
     template) makes the format/correctness reward never fire — plan an SFT /
     format warm-start when the reward needs a specific output structure.

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
    "feasibility": 1-5,
    "rl_config_sanity": 1-5
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

### RL post-training auto-fails (structurally unlearnable configs)
For PPO/GRPO/RLVR/DPO/reasoning-RL plans, reject before any GPU spend if:
- GRPO/group method with group size (`num_generations`) of 1 — no within-group
  contrast is possible, so the advantage is identically zero. A group size of 2
  is a red flag that must be justified, not a default.
- The reward function is provably constant over the planned data at the starting
  policy (zero reward variance by construction → zero gradient).
- `max_completion_length` is shorter than the benchmark's p95 gold-answer /
  required-reasoning length (with rollout headroom), so the rewarded token is
  truncated and the reward can never fire — e.g. a 256–512 budget for
  competition-math/`\boxed{}` or multi-step reasoning is an auto-reject. State
  the value it should be.
- A correctness/verifier reward depends on answer extraction (`\boxed{}`, tool
  call, AST) with no plan to validate the extractor + gold-matching on real
  outputs.
- The RL learning rate is at SFT scale (will diverge the policy), or `max_steps`
  is so small the run is only a smoke yet is presented as paper evidence.
- Reasoning/format RL on a base (non-instruct) checkpoint with no SFT/format
  warm-start, so the format/correctness reward never fires from a cold start.
See `argus_builtin_skills/engineer/rl-training-collapse-diagnosis.md` for the
matching in-flight collapse signatures these configs produce.

## Infrastructure check
If the plan involves training (SFT, RLHF, DPO, RL, pretraining, adapter tuning):
- Does it name a specific framework (LLaMA-Factory, TRL, SLIME, OpenRLHF, etc.)?
- If it plans a custom training loop, is there a justification for why no framework works?
- Flag as issue if it plans to write training from scratch without justification.

If the plan involves inference on >100 examples:
- Does it plan to use vLLM, SGLang, TGI, or an API with batching?
- If it plans bare `model.generate()` in a for-loop, flag as issue.
- Flag as issue if no inference engine is mentioned for large-scale evaluation.
