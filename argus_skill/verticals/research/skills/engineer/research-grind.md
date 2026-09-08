---
name: "Working a research result into shape"
description: "Treat the first implementation as a first draft, work down the gap over many rounds, stay with the flat stretches, and let the idea change as you learn. Use this whenever a method falls short of its baseline or the project is deciding whether it has done enough."
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
an explanation. Check whether:

- the implementation does not do what the method describes;
- the optimizer never found the regime the method needs;
- the data slice is too small, too easy, or not the one the claim is about;
- the scale is below where the effect exists at all;
- the evaluator is measuring something adjacent to the claim;
- the baseline is being run at an advantage the method does not get.

Each explanation suggests a different check. Resolve the plausible setup and
implementation defects, then retain the measurement as evidence about the
method. An expected advantage is a hypothesis; it may fail under a valid test.
Use that evidence to develop a substantive improvement or a better explanation.

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

## Let the idea change while you grind

Here is the part that is easy to miss.

The method you have after twenty rounds is usually not the method you started
with. You added a term because the gradients were unstable. You changed the
objective because the original one was measuring the wrong thing. You moved
where the intervention is applied. Each step was a local repair; together they
are a different method.

That is not drift to be corrected. That is the research happening.

So when the number finally lands, stop and look at what you are holding:

- What is the thing that actually made the difference? It is often not the part
  the original idea was named after.
- Is the mechanism in your head still the mechanism in the code? Read the code
  as though someone else wrote it.
- Would the first version of this idea have predicted the result you got?
- What is the shortest honest description of what you built?

Then write the paper about *that* — the method you ended with, the insight that
turned out to carry it, the framing your evidence actually supports. Papers
written about the original proposal, with the real discovery buried in an
implementation detail, are the most common way a good result becomes a
forgettable paper.

And if the answer to "what made the difference" is small and clean and not what
you expected, that is not a disappointment. That is the contribution.

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

Be willing to change what you are measuring when the evidence says the original
metric was the wrong question. Be unwilling to change it because the original
one was not going your way — the difference is whether you can state the reason
without mentioning your own result.

Spend attention where the uncertainty is. A run that will tell you the same
thing you already believe is not worth its GPU-hours, no matter how neatly it
completes a matrix. Completeness is for the appendix. The main path is
whichever experiment most changes what you think.

## What this never becomes

A failed method alone does not establish a publishable contribution. Continue
feasible, high-value improvements within the selected research question and
current stage. If rigorous controls reveal a boundary, develop the new principle,
explanatory model, guarantee, or prediction that makes it important, and test it
against the closest work. Preserving an adverse result and pursuing a stronger
contribution belong together. The independent Reviewer decides whether the
actual evidence meets the selected venue and the operator's completion standard.

## The short version

Implement, measure, diagnose, fix, measure again. Expect it to take far more
rounds than feels reasonable. Sit through the flat parts. Let the method become
whatever it needs to become, then look honestly at what you built and write
about that. Pursue the surprising mechanism, preserve all outcomes, and confirm
the resulting scientific argument independently.
