---
name: "Working a research result into shape"
description: "Treat the first implementation as a first draft, work down the gap over many rounds against a fixed claim, stay with the flat stretches, and let the implementation change as you learn. Use this whenever a method falls short of its baseline or the project is deciding whether it has done enough."
---

# Working a research result into shape

## Why this exists

Argus already knows how to grind. Watch it maintain itself: a failing test gets
read, hypothesised about, fixed, re-run, and re-fixed until it is green, and
nobody has to tell it that the third attempt is allowed. It does not conclude
after one failure that the feature was a bad idea.

Research gets a strangely different treatment. A method is implemented once,
measured once, and if the number comes back under the baseline the campaign
starts writing about what it found. The same system that will spend twenty
rounds making a benchmark harness correct will spend two on making the science
work.

That asymmetry is the whole problem, and it has nothing to do with capability.
Bring the same persistence to the experiment that you bring to infrastructure.

## The first number is not a result

A first implementation is a first draft. An unfavorable first measurement needs
an explanation, and the claim in `METHOD.md` is not the thing that changes to
supply one. Work the diagnosis ladder from `research-experiment-playbook.md`
in order, one rung per attempt, and write down each rung's evidence:

- implementation fidelity: the code does not do what the card describes
  (anchors, knockouts, differential tests);
- setup and evaluator: the positive control, the data pipeline, leakage, a
  pilot too small to resolve the margin;
- hyperparameters and recipe: the optimizer never found the regime the
  method needs; start from the framework recipe and move one factor at a time;
- scale and data: the slice is too small, too easy, or not the one the claim
  is about; the scale is below where the effect exists at all;
- baseline fairness: the baseline is run at an advantage the method does not
  get, or lands away from its published number;
- a method variant that still satisfies the claim as stated.

Each rung suggests a different check. Resolve it, then retain the measurement
as evidence about the implementation. Three distinct, diagnosed attempts come
before any escalation, and escalation is an operator question with the
evidence, not a reworded claim.

A useful discipline: reproduce the baseline first, with your own harness. If
your DAS, your SAE, your full-context oracle does not land where the paper that
published it says it lands, then nothing measured against it is about your
method yet.

## Develop a meaningful improvement

Define a meaningful improvement against the strongest relevant published
baseline, at matched budget, on the benchmark's established evaluation split.
Develop on separate data while preserving that comparison. Improving only over
an internal ablation does not establish an advantage over the field.

The loop is unglamorous and it is the job:

1. Measure. Write the gap down with its size.
2. Propose an explanation and a matched control that distinguishes it from the
   strongest competing explanation.
3. Make the method change and preserve a faithful comparison with the baseline.
4. Measure again. Keep all outcomes, the executed change, and what you learned.

Expect repeated iterations. Judge progress by stronger evidence and resolved
uncertainty; the number of attempts does not establish success or failure.

Log every round even when the number does not move — the shape of what did not
work is what tells you where the next fix is, and it is the first thing you will
want when the result finally lands.

## Troughs are part of the shape

There will be stretches where nothing improves. Those observations constrain
the explanations already tested. Use them to choose a new intervention or a
more informative experiment; repeating the same deterministic comparison adds
no independent evidence.

When several interventions leave the gap unchanged, revisit the causal
explanation and the family of fixes. Look for a different mechanism or a
discriminating prediction whose outcome would change the next decision.

What to do inside a trough:

- Change the altitude. If you have been tuning, go read the raw predictions. If
  you have been staring at rows, go re-derive the method on paper.
- Go find someone who solved something adjacent. The fix is often published.
- Shrink the loop. If a round takes six hours, find the two-minute version that
  reproduces the symptom, and iterate there.
- Take the strongest baseline apart. Understanding exactly why it wins is
  usually the shortest path to beating it.

Keep the substantive target and work on the method, evidence, or explanation.
Correct unsupported statements promptly as results arrive, while continuing
the scientific repair. Wording changes alone do not close a scientific gap.

## Let the implementation change while you grind; the claim does not

The implementation you have after twenty rounds is usually not the one you
started with. You stabilized a term because the gradients were unstable. You
moved a computation because the first placement never reached the regime the
method needs. You fixed the recipe, the data loader, the evaluator. Each step
was a local repair inside the components the card names; together they are a
faithful implementation of the same claim. That is the research happening.

What does not change is the claim in `METHOD.md`: the components the idea
prescribes, the protocol, the falsifier. When a repair would change what the
method *is* — a different objective, a different intervention point, a
mechanism the card does not name — that is not a repair. Stop, record what the
evidence shows, and put the change to the operator as a question with the
evidence (`operator_options`). Only the operator edits the claim.

So when the number finally lands, stop and look at what you are holding:

- Is the mechanism in the card still the mechanism in the code? Read the code
  as though someone else wrote it, anchor by anchor.
- Did the thing that made the difference belong to a component the card
  names? If not, the result is an operator question, not a paper.
- Does the protocol that produced the number match the card's protocol at the
  card's scale?

## Flexibility, and knowing what to chase

None of this is a procedure to execute. The plan is a hypothesis about how to
spend the next few days, and it is allowed to be wrong.

Follow the surprising thing. If an ablation does something you cannot explain,
that is worth more than the next three planned runs. If the method wins on a
slice nobody asked about, find out why before deciding it is noise. The result
that makes a paper is frequently something the plan did not contain.

Treat a slice discovered from results as exploratory. Record how it was found,
freeze the proposed mechanism, regime, comparator and outcome, then test new
independent units within that regime. Cases examined while choosing a layer,
budget or rule remain development evidence even when they were not selected
for a figure. Data held out from model fitting may already have been used for
method selection. Preserve those observations and obtain a separate confirmation
when the claim needs generalization; follow the experiment playbook for the
sampling and paired analysis.

The metric is the route's metric. When the evidence says it was the wrong
question, that is an operator question with the evidence, raised once; it is
never changed because the original one was not going your way. The test is
whether you can state the reason without mentioning your own result.

Spend attention where the uncertainty is. A run that will tell you the same
thing you already believe is not worth its GPU-hours, no matter how neatly it
completes a matrix. Completeness is for the appendix. The main path is
whichever experiment most changes what you think.

## What this never becomes

A failed attempt is not a publishable contribution, and neither is a restated
claim. Continue feasible, high-value improvements within the claim as stated
and the current stage. If rigorous controls still contradict the claim after
three distinct, diagnosed attempts, escalate to the operator with the evidence
and the options; do not write a restricted-case or negative-result paper on
your own authority. Preserving an adverse result and pursuing the claim belong
together. The independent Reviewer decides whether the actual evidence meets
the claim, the selected venue and the operator's completion standard.

## The short version

Implement, measure, diagnose, fix, measure again, against a claim that does
not move. Expect it to take far more rounds than feels reasonable. Sit through
the flat parts. Let the implementation become whatever it needs to become to
satisfy the card, then look honestly at what you built and write about that.
Preserve all outcomes, escalate with evidence rather than rewording, and
confirm the resulting scientific argument independently.
