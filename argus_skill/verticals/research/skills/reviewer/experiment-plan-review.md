---
name: "Experiment Plan Review"
description: "Review an experiment plan for scientific rigor before execution begins. Check method competitiveness, baseline strength, evaluation fairness, and benchmark adequacy."
---

# Experiment Plan Review

Review an experiment plan as a senior ML researcher would before approving compute budget. The goal is to catch fundamental design flaws before expensive experiments run, not after.

## Reviewer stance
- You are approving a GPU/API budget request, not reviewing a finished paper.
- Be decisive on claim-critical design and relaxed about nonessential paperwork.
- If the plan cannot answer the question even when executed perfectly, return it
  with the smallest concrete repair that makes it informative; do not re-litigate
  the selected idea or demand a perfect publication package before execution.
- Treat the selected idea, contribution case, and frozen falsifiable premise as
  upstream inputs. Do not re-rank its novelty, significance, or overall
  reasonableness here. Review whether the proposed experiment gives that idea a
  valid, fair, executable test. If the plan silently changes the method or
  premise, return it upstream rather than re-selecting the idea in this review.

## Review dimensions

Score each 1–5. Score 3+ on all applicable dimensions = pass. Dimensions 1–5
always apply; dimension 6 (RL config sanity) applies only to RL/preference
post-training plans — omit `rl_config_sanity` from the output for non-RL plans.

1. **Method and hypothesis fidelity**
   - Does the plan implement the selected method and test its frozen binding
     premise without silently substituting an easier claim?
   - Does the plan use resources appropriate to the question rather than merely
     maximizing available compute?
   - Do the primary measurement and controls distinguish the mechanism-specific
     prediction from the strongest alternative explanation?

2. **Baseline strength**
   - Does the plan include the strongest relevant published, standard, or system
     comparison needed to interpret the claim?
   - Would a reviewer say "but did you compare against X?" for an obvious X?
   - Are comparisons given fair and documented resource/data/protocol budgets?

3. **Evaluation fairness**
   - Is the comparison apples-to-apples on the factors the claim holds fixed?
   - Are ablations designed to isolate the proposed mechanism (not compare trained vs untrained)?
   - Are metrics appropriate for the task?
   - Is there a plan for statistical significance testing?
   - Is gold information isolated to scoring? Candidate and baseline predictions
     must not read labels, expected outcomes, or scorer-derived fields.
   - Do online/intervention claims compare executable methods with the same
     decision-time information? Historical executed traces and post-hoc judges
     are diagnostics, not equivalent online baselines.
   - Can the planned comparison distinguish the claimed effect? The tasks must
     exercise the mechanism, the baseline must have metric headroom, and the
     cases/repeats must be able to resolve the predeclared contrast; otherwise
     the plan can produce only inconclusive evidence.
   - Before spending budget on N repeats, does the plan name the sampling-noise
     failure mode those repeats can detect? A repeat detects sampling noise and
     nothing else. If the expected margin is far from the noise floor, one run
     suffices. Repeats can never detect a wrong implementation, broken control,
     or claim-code mismatch: require the claim-to-code faithfulness trace and a
     positive-control check first because both are cheaper. Flag ritual reruns of
     one unchanged configuration out of generalized fear as a design defect, not
     diligence; retain repeats when the margin is genuinely near the noise.
   - Does every numeric keep/reject cutoff have an external basis in utility,
    risk, an accepted standard, prior evidence, theory, or prospective
    sensitivity? Preregistration does not legitimize an unsupported
    round-number percentage. Without a justified cutoff, require a continuous
    effect estimate, uncertainty analysis, and cost-quality tradeoff instead
    of a binary gate.

4. **Benchmark adequacy**
   - Does every final empirical claim include at least one appropriate public
     benchmark, dataset, task suite, challenge, or official evaluation release?
   - Are synthetic/generated diagnostics supplementary rather than the sole final evidence?
   - Is the selected source/task/repeat/model scope justified by the claim and
     uncertainty analysis rather than a fixed quota?
   - Do the public evidence sources test the aspects needed by the stated claim?

5. **Feasibility and scope**
   - Can the experiments be completed with available compute in reasonable time?
   - Is the scope appropriate for the target venue (not too narrow, not too broad)?
   - Are interpretation and stopping criteria defined before running without
     inventing an arbitrary minimum gain?

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
   - **Sampling / length — default to the maximum the budget allows:**
     The length error is *asymmetric*. Truncating the response before the
     rewarded token (`\boxed{}`, `</answer>`, final tool call, closing code
     fence) makes the reward unobtainable *no matter how good the policy is* — a
     correctness killer. Setting it larger only costs compute/memory/throughput,
     which is a tunable, not a correctness risk. So the right default is to set
     `max_completion_length` **as large as the context window and step/compute
     budget allow**, not to trim it to "just enough". Err long, never short.
     The estimated need is a **floor, not a target** — use it only to catch a
     config that is obviously too short:
        1. Identify the benchmark's output type and look up / tokenize a handful
           of gold answers (or reference CoT traces) to get a length distribution.
        2. `max_completion_length` must comfortably clear the **p95** required
           length (≥1.5–2×), because RL rollouts run *longer* than greedy gold
           answers (exploration, rambling) and the rewarded token must survive —
           but going well beyond p95 (up to the context/compute ceiling) is
           fine and usually preferred for reasoning.
     Reference floors (tokens — the minimum below which to reject; prefer higher):
        - Short-answer / classification (label, single number): 32–128 floor.
        - Grade-school math with CoT (GSM8K-style): ≥256–512; <256 is suspect.
        - Competition math / multi-step reasoning (MATH, AIME, olympiad): ≥1k–4k;
          **256–512 is an auto-reject** — the `\boxed{}` is routinely truncated.
        - Code generation (full program / function + tests): ≥1k–4k depending on
          task; a single short function may fit in 512, a repo-level task will not.
        - Agentic / tool-use / multi-turn: ≥2k–8k; budget for tool call syntax
          and observations, not just the final answer.
        - Long-form generation (proofs, essays, plans): ≥ the target length.
     If the plan pins `max_completion_length` *below* these for the chosen
     benchmark, flag it as a hard length issue and name the value it should be
     (point it at the context/compute ceiling, not merely the floor). The only
     legitimate reason to cap it short is an explicit compute-throughput
     tradeoff that the plan justifies *and* that still clears the p95 floor; an
     unexplained short cap is a red flag. Also check sampling temperature is high
     enough to explore (≈0 → no reward variance) but not degenerate, and that
     `max_prompt_length` + completion fits the model's context window (otherwise
     rollouts silently truncate the prompt).
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
- Proposed method is a known standard technique with no novel mechanism
- Prompt/schema/wrapper/scale variant, decorative theory, or no field-level
  consequence beyond a local metric
- Ablation compares trained model vs untrained/random (not a fair ablation)
- No evaluation metrics defined
- Candidate or baseline prediction code can read gold labels, expected outcomes,
  or scorer-derived fields
- An online prevention/intervention claim uses only observational traces or a
  post-hoc judge as its baseline
- A keep/reject comparison is designed around an evidently ceilinged/floored
  baseline, an easy proxy that does not exercise the mechanism, or too little
  evidence to distinguish the predeclared effect
- Unjustified custom infrastructure that changes the comparison while claiming
  to test only a model/method contribution. Custom infrastructure is allowed
  when it is necessary for or part of the research contribution and is validated
  against a trusted reference.

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
See `the `rl-training-collapse-diagnosis.md` skill` for the
matching in-flight collapse signatures these configs produce.

## Infrastructure check
If the plan involves training (SFT, RLHF, DPO, RL, pretraining, adapter tuning):
- Does it name a specific framework (LLaMA-Factory, TRL, SLIME, OpenRLHF, etc.)?
- If it plans a custom training loop, is that required by the research question,
  and is it validated against a trusted implementation?
- **RUN CONTRACT (single source of truth, anti-drift):** before approving GPU
  budget, require the plan freeze to emit a machine-readable
  `research/RUN_CONTRACT.json` (via `python -m argus_skill.skills.run_contract
  freeze ...`) locking the model id, LR, group size / `num_generations`, total
  steps, batch size, and the curriculum content hash + distinct-task count +
  seed, with a self-consistent `contract_hash`. This is what later forces the
  launcher to execute exactly the frozen knobs (no LR copied from a reference
  doc, no `num_generations` drift) and forces a feasibility probe on the exact
  curriculum. Flag as issue if a training plan has no frozen RUN_CONTRACT.

If the plan involves inference on >100 examples:
- Does it name an execution path appropriate to its latency/throughput/correctness
  objective? Batch engines are preferred for throughput studies, while custom or
  per-example paths are valid when required by the research design.
