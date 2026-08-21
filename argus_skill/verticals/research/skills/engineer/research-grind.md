---
name: "Research Grind"
description: "How a result actually gets earned: treat the first implementation as a first draft, grind the gap down over many rounds, sit through the flat stretches, and let the idea change while you do it. Use whenever a method is short of its baseline, or a campaign is deciding whether it has done enough."
---

# Research Grind

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
Bring the infrastructure appetite to the experiment.

## The first number is not a result

A first implementation is a first draft. When it comes back under the baseline,
the honest reading is almost never "the idea is wrong". It is one of:

- the implementation does not do what the method describes;
- the optimizer never found the regime the method needs;
- the data slice is too small, too easy, or not the one the claim is about;
- the scale is below where the effect exists at all;
- the evaluator is measuring something adjacent to the claim;
- the baseline is being run at an advantage the method does not get.

Each of those is a different next run, and each is cheap next to abandoning the
question. Before treating a number as evidence about the idea, be able to say
which of these it is not.

A useful discipline: reproduce the baseline first, with your own harness. If
your DAS, your SAE, your full-context oracle does not land where the paper that
published it says it lands, then nothing measured against it is about your
method yet.

## Grind to the number

Pick the number the field would recognise and go get it. Not "we improved over
our own ablation" — the published baseline on the public benchmark, at matched
budget, on the split everybody reports.

The loop is unglamorous and it is the job:

1. Measure. Write the gap down with its size.
2. Say what the gap is made of. One named cause, not a list of five.
3. Buy the fix that addresses that cause, and only that cause.
4. Measure again. Keep the number, keep the diff, keep what you learned.

Ten rounds is normal. Thirty is not remarkable. Teams that publish these results
do exactly this and do not mention it in the paper, which is why the paper makes
it look like the method worked the first time. It did not.

Log every round even when the number does not move — the shape of what did not
work is what tells you where the next fix is, and it is the first thing you will
want when the result finally lands.

## Troughs are part of the shape

There will be stretches where nothing improves. Five rounds, ten, where each
fix is sound, each hypothesis is reasonable, and the number sits exactly where
it was. This is not a signal. It is what the middle of a hard problem feels
like from inside.

A flat stretch is only informative when you can say *why* it is flat — when the
diagnosis has stopped producing new candidate causes and every remaining idea is
a restatement of one already tried. Until then, a plateau means the current
family of fixes is exhausted, not that the question is.

What to do inside a trough:

- Change the altitude. If you have been tuning, go read the raw predictions. If
  you have been staring at rows, go re-derive the method on paper.
- Go find someone who solved something adjacent. The fix is often published.
- Shrink the loop. If a round takes six hours, find the two-minute version that
  reproduces the symptom, and iterate there.
- Take the strongest baseline apart. Understanding exactly why it wins is
  usually the shortest path to beating it.

Do not respond to a trough by lowering the target, softening the claim, or
starting to write. Those feel like progress and are the opposite.

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

Be willing to change what you are measuring when the evidence says the original
metric was the wrong question. Be unwilling to change it because the original
one was not going your way — the difference is whether you can state the reason
without mentioning your own result.

Spend attention where the uncertainty is. A run that will tell you the same
thing you already believe is not worth its GPU-hours, no matter how neatly it
completes a matrix. Completeness is for the appendix. The main path is
whichever experiment most changes what you think.

## What this never becomes

A loss does not become the paper. If the gap is still open, the campaign is not
finished — it is mid-grind, which is a normal place to be and an honest thing to
report internally.

Deciding an idea is genuinely dead is the Manager's call, it is rare, and it
takes more than a stubborn number: sustained optimization already spent across
implementation, data, scale and evaluator, the gap unmoved by any of it, and a
reason the next round would fail that is not simply that the last one did.
Anything short of that is impatience, and impatience has never once been right
about this in retrospect.

## The short version

Implement, measure, diagnose, fix, measure again. Expect it to take far more
rounds than feels reasonable. Sit through the flat parts. Let the method become
whatever it needs to become, then look honestly at what you built and write
about that. Chase the surprising result over the planned one. Get the number.
