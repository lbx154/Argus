---
name: "Suspect the Setup Before the Idea"
description: "When a measured result is far below what the model, method or benchmark is known to do, find the setting that caused it before drawing any scientific conclusion. Covers generation caps, training sequence length, protocol steps, scorers, and how to reason about settings not listed here."
---

# Suspect the Setup Before the Idea

## Why this exists

One campaign measured 6.0% on MATH-500 for a model published at 79.7%. Raising a
token cap took it to 68.8%. Executing the tool the published protocol assumes
took it to 76.4%. Two settings, an order of magnitude, and at every stage the
number looked like a scientific finding — it was written into a paper as a
"boundary result" before anyone checked.

A wrong setting and a wrong idea produce the same artifact: a low number with
clean plumbing. Nothing about the run announces which one you have. So a result
far from what this model, method or benchmark is known to do is a defect report
until proven otherwise, and the work is to find the defect, not to interpret the
number.

## The move

Ask one question: **which single setting, if wrong, would produce exactly the
number in front of me?** Then go and look at that setting. Not a checklist sweep
— one hypothesis, one inspection, repeat.

Two anchors make the question answerable:

- **The published number.** For this model on this benchmark, what does the
  literature report, and under which protocol? Put it beside yours before
  interpreting anything.
- **The positive control.** Run the case the evaluation cannot fail to detect —
  a model instructed outright to do the thing, a known-correct answer, an oracle
  condition. If that cannot be separated from random, the instrument is broken
  and no number from it means anything.

## Settings that silently destroy results

These are examples of the shape, not a list to tick off.

**Generation length.** A cap shorter than the answer needs cuts the model off
before it can be right. Reasoning traces need thousands of tokens; a concept may
not have been expressed at all in twelve. Symptom: a truncation or cap-hit rate
well above zero, outputs that stop mid-derivation, or a positive control that
scores at chance. Read the budget off the length distribution of real correct
completions rather than picking a round number.

**Training sequence length.** In SFT or RL, training on sequences much shorter
than the task needs at inference teaches the model to stop early, and the damage
shows up as a quality drop that looks like the method failing. Check that the
training length, the reward or loss masking, and the inference budget describe
the same task.

**Protocol steps the published number assumes.** Tool-integrated reasoning
scores near zero if the tool is never executed — the model writes code nobody
runs. Few-shot format, chat template, system prompt, stop sequences and decoding
settings all belong here. Reproduce the protocol that produced the number you
are comparing against, and name any deviation beside both numbers.

**The scorer.** A scorer that cannot recognise a correct answer in the form the
model writes it — a boxed expression, a differently normalised string, an
equivalent fraction — reports the model as wrong. Replay a sample of scored rows
through the current scorer and check that the stored rewards still match.

**Scale.** An evaluation too small to resolve the declared margin has not tested
the idea. Put the spread of your own repeats beside the margin you promised.

**Precision, device and dtype.** A model silently on CPU, in the wrong dtype, or
with a mismatched tokenizer produces degraded output and hours of wall clock.
Check device placement and observed throughput on the first few examples.

## Reasoning about settings not listed here

The listed ones are only the failures already seen. The general form is: **any
setting that bounds what the model is allowed to produce, or that stands between
the model's output and the score, can destroy a result while leaving the
pipeline looking healthy.**

When you meet a surprising number, enumerate the settings of that shape in
*your* pipeline and rank them by how much of the gap each could explain. A
setting that could explain the whole gap is worth an hour; one that could
explain a point is not, yet.

## After the fix

A repaired number is not the end of the story. If the result is still short, the
gap is a gap to close — implementation, optimization, data, scale, evaluator, or
the method itself — and closing it over many rounds is how strong results are
normally reached. Stopping at the first honest measurement and writing up a
restricted negative result is the failure this skill exists to prevent.
