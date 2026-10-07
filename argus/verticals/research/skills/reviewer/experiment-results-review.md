---
name: "What the experiments establish"
description: "Read the current experimental evidence yourself, from the review packet down to the code, evaluator and raw rows, and decide how the method should develop next against the claim fixed in METHOD.md."
---

# What the experiments establish

Use this in Experiment. Read the review packet in your context first (the
component anchors with their code excerpts, the host-run test outcomes, the
config changes and the files changed this round), then `METHOD.md`, then
direct code, configuration, raw results, positive controls, evaluator outputs,
and strong baseline results. Do not write a separate result-review file.

The claim is the one in `METHOD.md`, fixed at Idea selection. You judge whether
the implementation and the evidence satisfy that claim; you never accept claim
drift. A result presented against a narrower claim than the card states, or as
a "restricted case", is a repair request. So is a negative result while a rung
of the diagnosis ladder in `engineer/research-grind.md` (implementation
fidelity, setup and evaluator, hyperparameters and recipe, scale and data,
baseline fairness, a faithful method variant) is still untested and could
change the outcome: return `continue`, name that rung, and say what evidence
would settle it. The number of attempts is not the test; what matters is
whether every rung that could explain the gap has been worked with evidence.
Once that is true and the evidence still contradicts the claim, say so plainly
so the Planner can raise the operator question with the evidence. Only the
operator changes the claim.

## Questions that determine the next experiment

- Is the executed path faithful to the selected mechanism? Does every
  component in the card carry a `# @component` anchor on its entry point and a
  knockout that fails in its absence? A component without either is a repair.
- Did the evaluator and positive control work?
- Do the raw observations actually cover the declared configurations and
  repetitions, with exclusions or failures visible and claim-critical
  invariants satisfied? Recompute the relevant aggregate from those rows;
  a success flag, old table, or copied completion record is not sufficient.
- Does the experiment directly test the claimed capability? Which control
  distinguishes it from the strongest shortcut explanation?
- Were baselines real, competitive, and fairly resourced?
- Does the benchmark exercise the claimed mechanism, and does it hold up on
  its own: label provenance, a constant-answer baseline, targets that the
  items can separate, no shortcut in the context, and a score whose meaning
  is known? The next section says how to read for these.
- Is the observed difference larger than relevant uncertainty?
- Is the evidence at the scale the claim needs: families and sizes for a claim
  about models, independent items or tasks for a claim about a phenomenon, the
  matched ablation for a claim about a mechanism? The compute and cached
  weights shown in the stage context are what was available.
- If the mechanism lost to its matched ablation or the strongest
  same-information baseline, which rung of the diagnosis ladder has not yet
  been worked with evidence? That rung is the next repair, not a restated
  thesis.
- Which concrete method or experiment change has the highest information value?

When a result is weak, first diagnose the implementation, evaluator, benchmark,
scale, or method. Keep the selected Idea, the claim as stated, and the current
stage. The experiment programme may change from development evidence; the
claim may not.

## Reading the evidence yourself

Read the direct code path, the explicit run configuration, the evaluator, the
positive control, the baseline execution and the raw result rows, without
changing them and without opening a file to record that you looked. The
summary the Engineer wrote is a claim about these things; the things
themselves are the evidence. They cannot yet support an interpretation when
the executed method differs from the claimed method; when gold or scorer
information leaks into predictions; when the positive control fails; when a
published baseline has been replaced by a renamed local heuristic; when the
evaluator cannot discriminate the target behaviour; when result rows are
missing, duplicated, selectively dropped or inconsistent with the paper; when
the comparison changes information, compute, data or scoring unfairly; or when
labels are stipulated where the claim needs derived or human-validated ones,
so that a constant answer already matches the headline.

A benchmark carries a claim only if its labels, balance and scoring can. With
the raw items and the code in front of you, ask where each gold label comes
from: derived by a solver or a theory whose rule is written down, imported from
published human judgements, or typed in by the project. Constructed tasks can
derive gold from explicit executable rules and must accept every valid answer;
author expectations alone cannot ground a claim that a model fails the task.
Ask whether a constant answer already scores well, per construction and per
cell, since accuracy against the majority label tells you whether the benchmark
measures anything. Ask whether the target variables can be told apart on the
items: when two targets share the same gold on nearly every item, no accuracy
can show that a model distinguishes them. Ask whether the contexts or prompts
state the answer in other words, so that a shortcut reads it off, and whether
a same-information classifier on the surface text would do as well. Ask what
the score is: a two-way softmax over yes/no verbalizers is a restricted score,
not a calibrated probability; single tokens, casing and leading spaces change
it; forced choice can hide that free generations are unparseable, so look at
what free generation actually produced. Ask how many independent units remain
once templates, worlds and repeated contexts are collapsed, because
uncertainty belongs to that unit, not to the row. And ask whether model
families are compared under the same rendered prompt, template, decoding and
precision, or whether the comparison changes those too.

Return the concrete defect and the smallest repair through the normal Reviewer
response. Implementation and evaluator failures stay in the current stage and
never reopen Idea selection.

## When the experiment trains a model or runs large inference

Read the dated decision record at
`engineer/<task-class>-infrastructure-decision.md` in the project skill
directory the way `engineer/training-infrastructure.md` says it was produced,
and judge the procedure, not the choice: every cited date has a cached source
under `.argus/sources/` behind it; rejected candidates carry an exclusion
reason that points at a card field; the chosen framework is pinned to a SHA
under `third_party/<name>/`; pilot numbers trace to durable-runner logs on the
allocated device; an adopted inference engine has a rollout-versus-trainer
log-prob agreement measurement behind it; and the method is a readable diff
against the official example at the pin. A date or number with nothing behind
it, a missing pin, or an engine adopted without the agreement measurement is
what you hold on, naming the missing evidence. A record past its stated
re-verify date, or made for other hardware or another task class, no longer
applies until it is re-verified. Never demand a particular framework, engine or
model; never hold because a newer release exists when the survey saw and
judged it; and never apply these checks to analysis, evaluation-only or
data-preparation experiments. An offline survey recorded as unverified with
its failed URLs is a documented limitation to note, not a reason to hold; mark
whatever it leaves unverified as unverified.

## Recommending Paper

Recommend Paper when credible evidence, at the scale the claim needs, supports
the claim as the card states it and improves at least one scientifically
meaningful dimension. Say plainly when development has cycled on the same
panels: repeated development is not confirmation. Do not require a hard
numeric margin, wins on every headline metric, or dominance over every strong
baseline. Keep uncertainty, relevant losses, and tradeoffs visible; otherwise
return one concrete Experiment repair through the normal Reviewer response.

## Start from the weakest link

The packet opens with two things the Engineer did not write: its own per-clause
claim attainment with the values the host read from the files it points to,
and the host's log of what it ran this round (commands, how long each ran at
most, tests and evaluations invoked, paths outside the workspace). Read those
first and pick the one link most likely not to hold the claim: a clause marked
not met, partial or untested; a stated value the host resolved differently; a
headline number from a run shorter than its protocol allows; a metric whose
name is not the protocol's. Open only that script. Evidence the packet already
settles is not re-read; ask at most two questions, each answerable by a file or
a run log, or judge.

## Stand-ins

The packet's "Run reality" lines list definitions in the project's own code
named mock, fake, stub, synthetic or oracle, with the lines that call them, and
how many minutes the results directory was written over. A claim-bearing number
produced through such a path is `NOT_IMPLEMENTED` for the component it stands
in for, whatever the spec tests say, unless METHOD.md names the deviation and
the paper calls the evaluation simulated. Ask for the real system, not for a
caveat. The same lines date each result file against the last edit to the code
that wrote it and name measuring functions that build their inputs from random
tensors (with their first docstring line). Read them against the protocol's
scale: a 32k-context sweep of two 7B models does not finish in the 57 s between
a script's last edit and its summary, and a "retrieval recall" computed on
random keys is a mechanism test whatever models the results file lists above
it. The mission that asked for real weights is not met by real weights in the
perplexity half alone.
