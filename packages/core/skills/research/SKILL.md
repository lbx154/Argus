---
name: research
description: "Research that ends in a result worth publishing: choosing the question, building a method and improving it until it works, using the machine, and the few setup errors that quietly ruin a result"
---

## What the work is for

- The result is something others will use: a method that beats the strongest current one on the
  question, or a finding that changes what people do. A list of ideas that did not work is the
  way there, not the result.
- Most of the time goes to running experiments and reading what they produced. A plan, protocol
  or audit is written only where it changes what runs next.

## Choose the question

- When the operator names a field but not the question, the team picks it: read what this
  year's strongest work leaves open, take the question with the best chance of a strong result
  on this machine, and start. The letter to the operator names it, why, and the two runners-up;
  they can redirect.
- Before writing code, find where the question stands today: the strongest recent models,
  methods and codebases, and the numbers they report on the benchmark you will use, from papers,
  model cards and repositories checked today (their release dates too).
- Close prior work is the bar to clear, not a reason to leave. When the idea is already
  published, start from that result and go past it: what it leaves out, where it breaks, what
  more data, scale or a missing piece would add.
- Build on the official code of the strongest fitting method, the strongest current open model
  the machine can run, and the cases the question is about. A study of an outdated model, a toy
  setup, or a benchmark the model already nearly solves (GSM8K for a current 4B model) is a
  result nobody will use.

## Build it and make it work

- Measure the unchanged starting point first, under the same protocol: every result is compared
  with it.
- Go end to end small: a few steps on a few dozen examples through the real evaluator. Then
  scale up to the starting point's models and size; a pilot's small model or short run is never
  carried into the main experiment.
- An idea is judged after it has been made to work, not after its first run. When it falls
  short, find why: read the cases it gets wrong, take its parts out one at a time, find the
  setting that limits it. Change the method and run again. Leave it only when the reason it
  cannot work is understood and written down; that reason usually points to the next idea.
- Run many cheap experiments side by side rather than one carefully guarded one, and keep the
  ones that move the number.
- Size the evaluation to the difference you are after: a gain of two points cannot show on 24
  examples. When the data at hand is too small, get more before running more.

## Use the machine

- `node <this folder>/machine.mjs` shows the CPU load, the GPUs with their free memory, the
  Python ML stack and the disk. Size the model, batch, threads and number of runs to it
  together, and run independent jobs from one queue, so nothing idles while work is left.
- GPUs left idle while experiments wait are lost time. When another job holds them, run what
  fits beside it, queue the rest, and keep going on what needs no GPU.
- Work that generates a lot (RL rollouts, evaluation, synthetic data) runs through an inference
  engine such as vLLM or SGLang, often ten times faster than generating inside the training loop.
- Training is part of the work: when the question is about a model, train or fine-tune one.
- Save a run's raw outputs before any step that can fail after it, such as writing a video,
  so a failed save does not cost the run.

## Data and labels

- Missing data is built, not waited for: public datasets, mined or filtered data, synthetic
  data where it fits the claim, and labels the team makes by looking or reading with a model.
  The record says how each label was made.
- The operator is asked only for what the team cannot get: an access, a private file, a fact
  only they know. The rest of the work goes on while waiting.

## The claim and its evidence

- The claim is the operator's goal; it does not shrink to fit the results. If it must change,
  that is the operator's choice: write to them with the result that shows it, and keep going on
  the strongest route meanwhile.
- Choose methods and settings on development data; the test set is scored once, at the end.
- Only what ran is reported: a mock, a stand-in model or synthetic data where the claim names a
  real system belongs in a test, never in a result. Every number comes from the raw rows a run
  wrote; those rows and the scripts that read them are the record.
- A benchmark carries a claim only if its labels and scoring can: know where each gold label
  comes from and what a constant answer scores.

## When a number looks wrong

- A result far from what the model, method, benchmark or engine is known to do, in accuracy or
  in speed, is a setup defect until shown otherwise. One campaign measured 6.0% on MATH-500 for
  a model published at 79.7%: its token cap and a missing tool execution were the cause.
- Look first at the single setting that would produce exactly that number, usually something
  between the model's output and the score:
  - a generation cap (a cap-hit rate above zero means you measure the cap; in RL a truncated
    rollout scores zero and teaches the model to stop early);
  - the chat template, which must match between training and inference;
  - the scorer's parsing of the answer;
  - the rule that turns scores into labels: a default threshold after the class balance moved
    shows as a drop in accuracy that AUC does not show;
  - the protocol behind the published number.
- Read ten raw outputs before anything expensive: most defects show in ten generations and hide
  in an aggregate.
- A weak first result is a defect to find, not a finding. Change one thing at a time, starting
  from what could explain most of the gap: implementation fidelity, the setup and evaluator, the
  recipe and scale, or the baseline's fairness.

## The paper

- It argues for the claim with what ran; it is not an experiment log.
- It states what the results show plainly; a caveat goes in only where it would change what a
  reader does.
- References are copied from their source, never recalled.
- Figures are vector PDFs with TrueType fonts.
- The venue's rules come from its current author kit.
