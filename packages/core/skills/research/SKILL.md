---
name: research
description: "Research that ends in a result worth publishing: where to start, how to use the machine, and the few setup errors that quietly ruin a result"
---

A measurement, a one-off script or a comparison asked for in a sentence needs none of this.

## Start where the field is

- Before writing code, find where the question stands now: the strongest recent models, methods
  and codebases for it, and the numbers they report on the benchmark you will use, from papers,
  model cards and repositories checked today (their release dates too). A model's memory of the
  field is a year or more old, and it reaches for the models and methods of its training data.
- Build on the official code of the strongest fitting method or framework, and on the strongest
  current open model the machine can run, unless the question names others. A study of an
  outdated model or a toy setup is a result nobody will use.
- Use the whole machine. `node <this folder>/machine.mjs` shows the GPUs with their free memory,
  the Python ML stack and the disk; size the model, batch and number of runs to it, and run
  independent jobs side by side. Work that generates a lot (RL rollouts, evaluation, synthetic
  data) runs through an inference engine such as vLLM or SGLang, often ten times faster than
  generating inside the training loop.
- Go end to end small first: a few steps on a few dozen examples through the real evaluator, then
  scale. Measure the unchanged starting point under the same protocol before building the method:
  every result is compared with it.

## The claim and its evidence

- The claim is the operator's goal; it does not shrink to fit the results. If it must change, say
  so and keep the earlier results.
- Only what ran is reported: a mock, a stand-in model or synthetic data where the claim names a
  real system belongs in a test, never in a result. Every number comes from the raw rows a run
  wrote.
- Superiority means beating the strongest same-information baseline under the same protocol, run
  from its official code and landing near its published number.
- A benchmark carries a claim only if its labels and scoring can: know where each gold label
  comes from and what a constant answer scores.

## When a number looks wrong

- A result far from what the model, method or benchmark is known to do is a setup defect until
  shown otherwise. One campaign measured 6.0% on MATH-500 for a model published at 79.7%: its
  token cap and a missing tool execution were the cause, not the model.
- Look first at the single setting that would produce exactly that number, usually something
  between the model's output and the score:
  - a generation cap (a cap-hit rate above zero means you measure the cap; in RL a truncated
    rollout scores zero and teaches the model to stop early);
  - the chat template, which must match between training and inference;
  - the scorer's parsing of the answer;
  - the protocol behind the published number.
- Read ten raw outputs before anything expensive: most defects show in ten generations and hide
  in an aggregate.
- A weak first result is a defect to find, not a finding. Change one thing at a time, starting
  from what could explain most of the gap: implementation fidelity, the setup and evaluator, the
  recipe and scale, or the baseline's fairness.

## The paper

- It argues for the claim with what ran; it is not an experiment log.
- References are copied from their source, never recalled.
- Figures are vector PDFs with TrueType fonts.
- The venue's rules come from its current author kit.
