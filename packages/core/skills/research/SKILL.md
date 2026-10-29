---
name: research
description: "Research that ends in a claim others should trust: the pitfalls past projects hit, and what good evidence looks like"
---

A measurement, a one-off script or a comparison asked for in a sentence needs none of this.

## The claim and what counts as a result

- The claim does not move to fit the results. A narrowed claim, a "restricted case" or a negative
  result does not meet the operator's goal. If the claim must change, say so and keep the earlier
  results; when the operator named the direction, the evidence goes to them.
- Nothing is reported that did not run. A mock, a stand-in model, random inputs or synthetic data
  where the claim names a real system belong in tests, never in a result. A 350-task benchmark
  whose results were written in four minutes, or a summary written 57 seconds after its script was
  last edited, cannot hold a 32k-context sweep of two 7B models; a "retrieval recall" computed on
  random keys is a mechanism test, whatever models the results file lists above it.
- Every number in a table, figure or sentence is computed from the raw rows a run wrote, not from a
  typed summary, an old table or a copied "completed" flag.
- A claim of superiority beats the strongest same-information baseline (the strongest method given
  the same information, and the simplest one, such as more samples or a tuned default) under the
  same protocol. A baseline reimplemented from a paper summary is weaker than its official code, and
  one that does not land near its published number makes every comparison against it meaningless.
- A benchmark carries a claim only if its labels, balance and scoring can. Know where each gold
  label comes from (a written rule, published human judgement, or typed in by the project), what a
  constant answer scores, and whether the targets can be told apart on the items. Uncertainty
  belongs to independent units, not to rows generated from one template.

## Suspect the setup before the idea

- Measure the starting point before building the method: the unchanged model or system, through
  the real evaluator on the development split, the number kept with its command. Every arm is
  compared with it. Three runs found theirs late or never: one trained arm then scored 43.6%
  against an untrained 47-50%, one base model scored 180/180 on its development evaluation, and
  one environment gave the base 0, so every gain was 0. A score at the evaluator's ceiling or floor
  means the evaluation cannot show the effect: change it before building on it.
- A tolerance or bar written before its quantity was measured is provisional: the first measurement
  keeps it or replaces it. One team wrote a 0.1 error bound before measuring, measured 0.1546 while
  the operator's own measure was clean, and chased its invented bound through four reviews and
  eleven diagnostic configs: 3 h 14 min and $46.43, the project's costliest task.
- Before arms that share a runner start side by side, run each arm's own config one short step on
  the real development data through the analysis the comparison uses. One project's runner passed
  its own config, then the arms met two of its defects; each cost a send-back, a new plan, a repair
  and a rerun of 25-40 minutes.
- One campaign measured 6.0% on MATH-500 for a model published at 79.7%. Raising a token cap took
  it to 68.8%; executing the tool the published protocol assumes took it to 76.4%. At every stage
  the number looked like a finding, and it was written up as a "boundary result". A result far from
  what the model, method or benchmark is known to do is a defect until shown otherwise.
- Ask which single setting, if wrong, would produce exactly the number in front of you, and look at
  that one first. Anything that bounds what the model may produce, or stands between its output and
  the score, can destroy a result while the run looks healthy.
- Generation cap: derive it from a high percentile of correct solutions' lengths plus headroom;
  multi-step reasoning commonly needs thousands of tokens. A cap-hit rate materially above zero
  means you are measuring the cap.
- RL post-training: a truncated rollout scores zero reward even when the policy was on its way, so
  training teaches the model to stop early, and it looks like the method failing. One run trained
  four RLVR variants to 0.736-0.742 against an untrained base at 0.756, with 97% of training
  completions clipped and a mean output of 191 tokens where correct solutions average 637. Evaluate
  the untrained checkpoint under the identical protocol, and confirm some reward is reachable.
- SFT: the sequence length covers the full target, the loss masks the prompt, and the chat template
  in training is the one at inference; a template mismatch destroys a result while every training
  metric looks normal.
- Evaluation: use the benchmark's own harness and the protocol behind the published number (tool
  execution, prompt format, decoding, stop sequences). A scorer that cannot recognise a correct
  answer in the form the model writes it (a boxed expression, an equivalent fraction) scores it
  wrong.
- Read ten raw outputs yourself before anything expensive: nearly every defect above is visible in
  ten generations and invisible in an aggregate score.
- A positive control, a case the evaluation cannot fail to detect, goes through the same path and
  evaluator before any null or negative result is read. If it cannot be told from chance, the
  instrument is broken.

## When the method trails its baseline

- A weak first result is a defect to find, not a finding. The usual causes are implementation
  fidelity (a component whose knockout changes nothing is doing nothing), the setup and evaluator,
  the recipe, scale and data, and baseline fairness; start with whichever could explain most of the
  gap, and change one thing at a time. Unfinished development is not a negative result.
- A regime or example found by exploring shows a mechanism can occur; a claim that it generalizes
  needs new independent units with the comparison fixed first. Held-out data chooses nothing.
- With an inference engine inside RL training, correctness is agreement of its and the trainer's
  log-probabilities on the same tokens, not identical rewards.

## The paper

- It is an argument for the claim, not an experiment report, and it reports only what ran: no
  simulated, placeholder or projected results, and no human-study numbers nobody collected.
- Bibliographic details are copied from the source, never recalled: model memory conflates
  versions, venues and authors precisely for the papers cited most.
- Figures are vector PDFs with TrueType fonts (matplotlib embeds Type 3 by default, which
  camera-ready checks reject); bars start at zero. Method diagrams built from matplotlib patches and
  arrows look unfinished and cannot be edited.
- The venue's rules come from its current official author kit; rules carried over from another
  conference (page counting, anonymity) are often wrong. No private path, internal role name or
  development narration appears in the paper.
