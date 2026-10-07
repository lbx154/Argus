---
name: "Working a research result into shape"
description: "Treat the first implementation as a first draft and work down the gap over many rounds against a fixed claim: suspect the setup before the idea, read every result against the claim as stated in METHOD.md, work the diagnosis ladder one rung at a time, stay with the flat stretches, and let the implementation change as you learn. Use this whenever a number comes back wrong, a method falls short of its baseline, or the project is deciding whether it has done enough."
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
supply one. The claim was fixed at Idea selection; the results do not choose
the claim, they choose the next change to the implementation. Work the
diagnosis ladder in order, one rung per attempt, and write down each rung's
evidence in the run records before moving to the next:

- implementation fidelity: the code does not do what the card describes
  (the anchors, knockouts and differential tests of `engineer/method-card.md`);
- setup and evaluator: the positive control, the data pipeline, leakage, a
  pilot too small to resolve the margin (the next section);
- hyperparameters and recipe: the optimizer never found the regime the
  method needs; start from the framework recipe at the pinned revision and
  move one factor at a time (`engineer/training-infrastructure.md`);
- scale and data: the slice is too small, too easy, or not the one the claim
  is about; the scale is below where the effect exists at all;
- baseline fairness: the baseline is run at an advantage the method does not
  get, or lands away from its published number;
- method variants that still satisfy the claim as stated: a different
  realization of the same components, never a different claim.

What makes an attempt count is not that it happened but that it was diagnosed:
it named the rung it was testing and produced evidence that rules that rung in
or out. Changing three settings at once and rerunning is one undiagnosed
attempt, not three; the same comparison repeated on the same panels is not a
further attempt however many cycles it took. A method is below its baseline
for a reason you can name, and you keep working the ladder until you can name
it or until the rungs that remain could not change the claim's standing. That
is the moment to stop, not a count of tries. Stopping is then an operator
question carrying the evidence, never a reworded claim.

A useful discipline: reproduce the baseline first, with your own harness. If
your DAS, your SAE, your full-context oracle does not land where the paper that
published it says it lands, then nothing measured against it is about your
method yet.

## Suspect the setup before the idea

One campaign measured 6.0% on MATH-500 for a model published at 79.7%. Raising a
token cap took it to 68.8%. Executing the tool the published protocol assumes
took it to 76.4%. Two settings, an order of magnitude, and at every stage the
number looked like a scientific finding: it was written into a paper as a
"boundary result" before anyone checked.

A wrong setting and a wrong idea look the same from outside: a low number from
an apparently well-run experiment. Nothing about the run announces which one
you have. So a result far from what this model, method or benchmark is known to
do is a defect report until proven otherwise, and the work is to find the
defect, not to interpret the number.

### Design it before you run it

Most settings that ruin a result are chosen once, early, by whoever wrote the
config, and never revisited. Derive each one from what the task requires rather
than from a round number, and write down the derivation.

**Generation budget.** Sample the reference solutions or the model's own correct
completions, take a high percentile of their length, and add headroom. Do not
pick a number because it fits the schedule. Mathematical or multi-step
reasoning traces commonly need thousands of tokens and hard problems more, so
a cap in the hundreds is already suspicious and a cap of twelve is not an
experiment; a steering or concept-expression generation needs enough tokens for
the concept to appear at all; short-form QA can be short only when the gold
answers are short. Report the fraction of generations that hit the cap.
Anything materially above zero means you are measuring the cap.

**RL post-training (GRPO, PPO, RLVR and relatives).** The rollout length must
cover what the task needs at inference. A truncated rollout scores zero reward
even when the policy was on its way to the right answer, so the gradient teaches
the model to stop early, and the damage looks exactly like the method failing.
Check before launching: rollout length against the length distribution of
correct solutions; enough samples per prompt for the advantage estimate to have
signal; a KL or clip setting that neither freezes the policy nor lets it
collapse; reward normalisation consistent across the batch; and whether any
reward is reachable at all on the current policy, because a reward that fires
on almost nothing trains nothing. Log the reward distribution and the
truncation rate from the first steps and look at them; a run that is quietly
learning to truncate looks healthy on the loss curve.

The signature to watch for afterwards is unmistakable and easy to miss: every
trained variant scoring below the untrained starting checkpoint. That is not a
result about which variant is better; it shows that training degraded the
model, and comparing the variants to each other buries it. One run here trained
four RLVR variants that landed at 0.736-0.742 against an untrained base at
0.756, with 97% of its training completions clipped and a mean training output
of 191 tokens for a task whose correct solutions average 637. The gradient had
been teaching the model to stop early, exactly as designed, and the comparison
was about to be written up as a finding about credit assignment. Always
evaluate the untrained checkpoint under the identical protocol and put it in
the table.

**SFT.** The maximum sequence length has to cover the full target, or every
example longer than it is silently cut and you are training the model to stop
mid-answer. Confirm loss masking covers the completion and not the prompt, and
that the chat template used in training is the one used at inference; a
template mismatch destroys a result while every metric in training looks
normal.

**Evaluation protocol.** Adopt the harness the benchmark ships and reproduce the
protocol the published number used, including whether tools are actually
executed, the prompt format, the decoding settings and the stop sequences. Name
any deviation beside both numbers.

Before launching anything expensive, run a handful of examples end to end and
read the raw outputs with your own eyes. Nearly every defect in this section is
visible in ten generations and invisible in an aggregate score.

### When the number comes back wrong

Ask one question: **which single setting, if wrong, would produce exactly the
number in front of me?** Then go and look at that setting. Form one hypothesis,
inspect what would settle it, and repeat; do not sweep through settings
indiscriminately. Two anchors make the question answerable:

- **The published number.** For this model on this benchmark, what does the
  literature report, and under which protocol? Put it beside yours before
  interpreting anything.
- **The positive control.** Run the case the evaluation cannot fail to detect:
  a model instructed outright to do the thing, a known-correct answer, an
  oracle condition. If that cannot be separated from random, the instrument is
  broken and no number from it means anything.

The settings that have destroyed results before share one shape: **anything
that bounds what the model is allowed to produce, or that stands between the
model's output and the score, can destroy a result while leaving the experiment
looking healthy.** A generation cap shorter than the answer needs (symptom: a
cap-hit rate well above zero, outputs that stop mid-derivation, a positive
control at chance). A training sequence length shorter than the task needs at
inference. A protocol step the published number assumes and yours skips:
tool-integrated reasoning scores near zero when the tool is never executed, and
few-shot format, chat template, system prompt, stop sequences and decoding
settings belong in the same family. A scorer that cannot recognise a correct
answer in the form the model writes it (a boxed expression, a differently
normalised string, an equivalent fraction); replay a sample of scored rows
through the current scorer and check the stored rewards still match. An
evaluation too small to resolve the declared margin; put the spread of your own
repeats beside the margin you promised. A model silently on CPU, in the wrong
dtype, or with a mismatched tokenizer; check device placement and observed
throughput on the first few examples.

When you meet a surprising number, enumerate the settings of that shape in
*your* experiment and rank them by how much of the gap each could explain. A
setting that could explain the whole gap is worth an hour; one that could
explain a point is not, yet.

A repaired number is not the end of the story. If the result is still short,
the gap is a gap to close, and the rungs after setup are recipe, scale and
data, baseline fairness and faithful method variants. Closing it over many
rounds against the claim as stated in `METHOD.md` is how strong results are
normally reached. Stopping at the first honest measurement and writing up a
restricted negative result, or rewording the claim to match the number, is the
failure this document exists to prevent.

## Reading the results against the claim

Read the direct raw results, the executed configuration, the code, the
positive controls, the evaluator outputs and the strong baseline results, not a
summary of them. Every number you reason from, and every number that later
reaches a table or a figure, is computed from the raw per-seed rows the run
wrote. A summary someone typed, a previously generated table or a copied
completion marker cannot stand in for rows that exist, and cannot make a
partial or changed run complete; before aggregating, match the configuration
and repeat identities in the rows to the declared run and account for every
failure and exclusion.

Then decide four things: what the evidence currently supports, cell by cell,
against the claim as stated; which rung of the ladder explains the largest gap;
the one diagnosed change with the highest information value and the evidence
that will show whether it worked; and whether the headline comparisons now beat
the strongest same-information baseline with relevant wins clearly exceeding
losses.

If the claim as stated is not yet supported, keep working the ladder and record
each attempt's rung and evidence. Do not reopen Idea selection, do not
restate the claim to match what the code did, and do not turn failed attempts
into a negative-result paper. When the claim is supported, rewrite the research notes
in `RESEARCH_NOTES.md` with the claim as stated, the decisive comparisons, the
essential contrary evidence and the direct sources Paper needs.

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

Log every round even when the number does not move. The shape of what did not
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
method *is* (a different objective, a different intervention point, a
mechanism the card does not name) that is not a repair. Stop, record what the
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
when the claim needs generalization; follow `research-experiment-playbook.md`
for the sampling and paired analysis.

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
and the current stage. When rigorous controls still contradict the claim after
the ladder has been worked, each rung diagnosed with its evidence and no rung
left that could change the standing of the claim, escalate to the operator with
the evidence and the options; do not write a restricted-case or negative-result
paper on your own authority. Preserving an adverse result and pursuing the
claim belong together. The independent Reviewer decides whether the actual
evidence meets the claim, the selected venue and the operator's completion
standard.

## The short version

Implement, measure, diagnose, fix, measure again, against a claim that does
not move. Suspect the setup before the idea. Expect it to take far more rounds
than feels reasonable. Sit through the flat parts. Let the implementation
become whatever it needs to become to satisfy the card, then look honestly at
what you built and write about that. Preserve all outcomes, escalate with
evidence rather than rewording, and confirm the resulting scientific argument
independently.
