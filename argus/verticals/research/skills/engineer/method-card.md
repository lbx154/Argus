---
name: "Method card (METHOD.md), executable spec and review anchors"
description: "Write the project-root METHOD.md once: the method as the paper will claim it, component by component in the route's own words, the protocol, and what would falsify it. Then hold the code to it: the reference implementation cloned at a pinned revision under third_party/ with the method built as a diff on it, an executable spec under tests/spec that the host runs without a model, anchors in the code that a Reviewer who was not present can follow, the mapping from thesis to executed path, and the brief a Planner writes before any code exists. Status, tests, reused code, hyperparameters and history are derived by the host, never maintained by hand."
---

# Method card (METHOD.md), executable spec and review anchors

`METHOD.md` at the project root is the one place that says what the method
*is*. The Engineer who implements the method writes it, from the selected
route, before writing method code; the Reviewer reads it before anything
else; the Atlas web UI shows it to human readers unchanged, next to a derived
panel the host fills in. It is a work product like the code and the raw
outputs: the one named exception to the rule that the research notes are the
sole cross-stage context and that no extra reporting files are written.

Everything else in this document exists to hold the code to the card: the
reference clone the method is a diff on, the spec suite the host runs after
every round, the anchors in the code the Reviewer reads, the mapping from
thesis to executed path, and the brief a Planner writes so that an Engineer
starts from the card rather than from a paraphrase of it. They are joined by
one convention: a component is spelled the same way in the card's table, in
the marker on its tests and in the anchor above its code, and the host joins
the three by name.

## Why the card exists

A project failed when the Engineer defined the method in passing inside the
notes, implemented a simplified version, wrote tests shaped to pass, and a
read-only Reviewer certified the Engineer's account of it. Nobody could point
to a sentence saying what the method was supposed to be. The card fixes the
statement first, so every later step is a comparison against it, not a
restatement of it.

## When to write it

In the first Experiment round, before any claim-bearing run, together with
the reference clone and the spec suite described below. Finish these three
before reporting the first round. In the final Review, revise the card before
a scientific repair that changes the method, never after.

## Writing the card

Write it once. Everything in it is about the method, not about the state of
the project.

1. Open the selected route. Its path is listed under "Evidence considered" in
   `RESEARCH_NOTES.md`. Quote the route's own sentences for each component;
   do not paraphrase them into something easier.
2. Copy the template at the end of this document to `METHOD.md` and fill it
   in order: a title and a one-paragraph statement of the method as the paper
   will claim it; the `Components` table (`Component | The idea prescribes |
   Notes`), quoting the route in the second column and leaving `Notes` empty
   unless you deliberately simplify a component, in which case it says what
   was simplified and why; the `Protocol` (datasets, baselines, seeds or
   repeats, metrics and the config files the runs use, copied from the route,
   every deviation on its own line with the reason); and `What would falsify
   the claim`, the observation that would make the paper's central claim
   false.
3. Spell the component name exactly as the marker on its tests and the anchor
   above its code will spell it.

That is all you maintain. The host derives, at zero model cost, and shows in
Atlas and to the Reviewer: each component's status (proven, contradicted,
partial, untested, unchecked) from the markers on `tests/spec` and its own
run of the suite; the reused code from an import scan of the project mapped
to `third_party/` clones (pinned revision, remote) and installed packages
(version); the hyperparameters from the config files with their `# why:`
reasons and a diff against the previous round; and the change history from
git. Reference implementations, reused code, key hyperparameters and the
change log are derived by the host from the code and are never written into
the card by hand.

Touch `METHOD.md` again only when the method itself changes: a component is
added or removed, a component is deliberately simplified (say so in `Notes`),
the protocol changes, or the falsifier changes. Tests, code, configs and
history speak for themselves through the derived panel. A `Notes` entry
naming a simplification is honest and allowed; a component the card states
plainly and the code contradicts is the defect the Reviewer looks for first.

## The reference implementation, and the method as a diff on it

The official implementation of every method the project compares against or
extends, or the strongest public one when no official code exists, is the
ground truth for what that method does. Reimplementing it from a paper
summary produces a weaker baseline and an unfalsifiable comparison, so in the
first Experiment round, and again whenever a new baseline enters the
protocol:

1. Clone it into `third_party/<name>` at a pinned revision (a commit hash or
   release tag) and keep it a git checkout: the host reads the revision and
   the remote from the clone itself and shows them beside `METHOD.md`. Do not
   vendor a copy without the revision, and do not edit files inside
   `third_party/` in place; the clone stays as cloned.
2. Run one of its shipped examples end to end in the project environment, at
   the smallest scale that exercises the real path. Keep the command and the
   output location in the research notes. If it needs the project environment
   repaired, repair it: this is the setup every later result depends on. The
   commands that made the clone run belong in the durable-learning Skill for
   that codebase, as the Engineer fragment asks.
3. Write the parity test (`tests/spec/test_parity_reference.py`, tagged
   `@pytest.mark.component("<baseline component>", kind="parity")`): the
   project's implementation of the *baseline* path reproduces the reference's
   output on a fixed input within a stated tolerance. Until it passes, the
   host shows the baseline component as partial or contradicted.
4. Build the new method as a delta: subclass, wrap, or call into the
   reference so that `git diff` between "reference as used" and "our method"
   is the list of ideas the paper claims. When the reference cannot be
   extended (different framework, incompatible license), implement the
   baseline separately and let the parity test carry the burden of showing it
   is the same method.

This replaces a hand-rolled baseline "following the paper", a training loop
or serving path written from memory, and a comparison whose baseline nobody
else can run. Choosing the infrastructure for training or large inference is
a separate question; `engineer/training-infrastructure.md` covers it.

## The executable spec (tests/spec)

`tests/spec` is the part of the project that says, in code the host can run
without a model, what the method must do. It is written before the first
claim-bearing run and extended whenever `METHOD.md` gains a component or a
claim. The host runs it after every Engineer round, joins the result with the
component markers on the tests, and shows the derived status per component to
the Reviewer, to the Engineer as Raw verification evidence, and to human
readers in Atlas. Nothing blocks on it; the roles read it and judge.

Start from the templates beside this document in the research skill library,
`engineer/spec_test_templates/` (`conftest.py`, `test_differential.py`,
`test_knockouts.py`, `test_claim_shape.py`, `test_parity_reference.py`).
Copy them to `tests/spec/`, replace every `TODO`, and remove the skip markers
as each test becomes real. Plain `pytest` and `numpy`; no framework is
required to run the oracle.

1. **Oracle.** Transcribe the route's equations into slow, obviously correct
   `float64` functions, one function per equation, named after the equation.
   No vectorisation tricks, no shared code with the implementation. The
   oracle is the method as written; if the route is ambiguous, quote the
   ambiguity in `METHOD.md` and pick one reading in the oracle.
2. **Differential tests** (`test_differential.py`). Implementation versus
   oracle on random inputs at several sizes with a stated tolerance, and
   implementation versus the `third_party/` reference on the baseline path
   (`test_parity_reference.py`). A tolerance loosened to make a test pass is a
   simplification: say so in the card's `Notes` column.
3. **Knockouts** (`test_knockouts.py`). One per component in the card:
   disable the component (flag, zero weight, identity replacement) and, on a
   case built so that the component matters, assert the output changes by
   more than noise. A component whose knockout does not change the output is
   not doing what the card says. `conftest.knockout()` and
   `conftest.assert_changes()` are small helpers for this.
4. **Claim-shaped tests** (`test_claim_shape.py`). For every complexity,
   memory or throughput claim in the card, measure at enough scales to fit a
   slope on log-log axes (`conftest.scaling_slope()`) and assert the slope
   matches the claimed order within a margin. For an accuracy claim, the test
   is the positive control: a case with a known recoverable signal through
   the same executed path.

Outcome-shaped tests (asserting the paper's headline number) prove nothing
about the method; do not write them here. Tests that pass by `skip`, `xfail`,
`or True`, or a tolerance wide enough to accept anything are visible to the
Reviewer as exactly that. Do not delete a failing test to turn the run green;
a `contradicted` component is information, and a deliberate simplification
belongs in the card's `Notes` column with its reason.

## Component markers

Every test carries
`@pytest.mark.component("<component>", kind="knockout"|"differential"|"invariant"|"claim"|"parity")`
with the component name spelled exactly as in the `Components` table of
`METHOD.md`. `kind` may be omitted; it is then inferred from the file name
(`test_knockouts.py` is `knockout`, `test_differential.py` is
`differential`, `test_claim_shape.py` is `claim`, `test_parity_reference.py`
is `parity`, anything else `invariant`). The template `conftest.py`
registers the marker and, at collection, writes
`<rootdir>/.argus/spec_components.json` (`{"generated_at": ..., "items":
{"<nodeid>": {"component": ..., "kind": ...}}}`); the host joins it with its
own run to derive each component's status: `contradicted` if any of its
tests failed, `proven` if all passed and at least one knockout or
differential passed, otherwise `partial`; `untested` when the card names a
component no test is tagged with; `unchecked` when tests exist but the host
has not run yet. Nobody writes status by hand.

When a component is added to the card, its tests get the marker and its code
gets the anchor in the same round; when a component is removed, so are its
markers and anchor. Nothing else needs to be kept in step.

## Anchors

The Reviewer has no tools and re-runs nothing. It sees a review packet the
host assembles from your anchors: each component's code excerpt, the host-run
test outcomes, the config changes and the files changed this round, beside
`METHOD.md`. The Reviewer was not there when the code was written and cannot
search a repository for the mechanism; the anchor is how it finds the entry
point, reads the excerpt and compares it with the card. Code the Reviewer
cannot locate cannot be credited with implementing the component, whatever
that code does, so an unanchored component reads as absent in the packet and
the round is judged on that basis. The anchors cost a comment each; they are
the difference between a review of the method and a review of your
description of it.

- `# @component <name>` on the line directly above the `def` or `class` that
  is the component's entry point. `<name>` is spelled exactly as in the
  card's `Components` table and as in the `@pytest.mark.component` marker on
  its tests. One anchor per component entry point; helpers are not anchored.
  The host shows the anchor (file:line, symbol, excerpt) next to the
  component's status; a component without an anchor is listed as such, and
  an anchor for a component the card does not name is listed too.
- `# @simplified <name>: <why>` directly below the component anchor when the
  implementation deliberately departs from what the route prescribes. The
  same departure is named in the card's `Notes` column.
- `# @reuses <library> <symbol>` on the import line for reused external code.
  Reused code is imported from the pinned `third_party/` clone or the
  installed package, never copied into the project.
- `# why: <reason>` on or directly above a non-obvious decision: a
  tolerance, a masking choice, an ordering, a shortcut taken.

The round summary names the anchors added or changed this round.

## Configuration

Hyperparameters live in config files (`configs/*.yaml`), each chosen value
with a `# why: ...` comment on the same line or the line above it; the host
reads the value and the reason from there and shows them with a diff against
the previous round. No load-bearing defaults hidden in function signatures;
the run is reproducible from the config it names.

## Shape of the code

- No dead code, no commented-out alternatives, no second implementation of a
  component kept "for comparison": one entry point per component.
- Explicit configuration over implicit behaviour; a flag that changes the
  method is named in the config and in the card.
- Tests under `tests/spec` are named by component and kind
  (`test_<component>_knockout`, `test_<component>_differential`).

## Claim attainment

After a claim-bearing run, write `.argus/claim_attainment.json`:
`{"clauses": [{"clause": ..., "obtained": ..., "met": "yes|no|partial|untested", "source": {"path": "results/x.json", "field": "ours.acc"}}]}`,
one entry per clause of the claim. The host reads the pointed field and shows
the value beside your words to the Reviewer and the Planner: the comparison
is yours, the number is the file's. A clause you cannot meet is stated as not
met, never reworded as a margin over a baseline.

## Keeping the thesis and the code aligned

After Idea selection and before claim-bearing execution, read the selected
thesis from `RESEARCH_NOTES.md` and the method as the card states it, and map
every load-bearing part of the thesis to the actual implementation:

- mechanism, intervention, prediction, and falsifier;
- the executable entry point and the branch where candidate and baseline
  diverge;
- formulas, operands, masks, reductions, timing, and gradient boundaries;
- the information available at decision time;
- the evaluator output and the positive control;
- the fair baseline and the invariants.

Implement through those concrete paths. The card's `Components` table, its
markers and its anchors are where the mapping lives; a fresh Reviewer then
follows the reachable call chain from the card (`reviewer/claim-to-code-trace.md`)
and says whether the code tests the selected mechanism under the intended
comparison, tests a different mechanism or comparison, or does not reach the
mechanism at all. Fix a contradiction or a missing mechanism in the code
before claim-bearing runs; never resolve either by rewording the card to
describe what the code does. The claim is fixed at Idea selection and only the
operator changes it; a component row changes in the same round only for a
deliberate, named simplification the operator can see, marked `# @simplified`
at the entry point. Do not reopen Idea selection. The alignment result lives
in the host-derived component status and in the Reviewer's returned
judgement, not in a separate note.

## The implementation brief

One task is one brief. Before code exists, the Planner writes the brief into
the task objective; the Engineer reads it before writing code and completes
any missing part from `METHOD.md` and the selected route, naming the
completion in the round summary. An incomplete brief is completed, not
refused. A brief never narrows the claim, the datasets or the scale to what
is convenient, and a task re-issued after a failure carries the same brief
with the same acceptance, verbatim. It answers these questions, under these
headings:

### Claim

The statement from `METHOD.md`, quoted verbatim. Not paraphrased, not scoped
down.

### Components to implement this task

Names exactly as in the card's `Components` table. For each: the entry point
to create or change as `path/to/file.py:Symbol`, and the equations or route
section it realizes.

### Interfaces

The function or class signatures, or the CLI, that the component exposes and
that the tests and later tasks will call.

### Tests that must pass

The `tests/spec` ids to write or make green, each marked
`@pytest.mark.component("<name>")` with the component it exercises: oracle,
differential, knockout, claim-shaped or parity.

### Data and scale

Datasets, splits, sizes, seeds and repeats exactly as the route's protocol
states them. "e.g." is not a dataset list.

### Commands

Environment activation, the run command with its config file, and the
evaluation command, as they will be executed.

### Environment prerequisites

The project venv and pinned dependencies, the `third_party/` reference clone
at its pinned revision, data present at its path, the GPU or accelerator
mask, any credentials the operator has supplied.

### Definition of done

Executable checks and the numbers to report: which tests are green, which
command produced which raw output, which comparison at which scale.
No adjectives ("robust", "reasonable", "works well").

### Out of scope

What this task deliberately leaves to another brief, so the Engineer does not
spend the round on it or quietly fold it in.

## What the card is not

Not a plan, not a report, not a JSON ledger; nothing reads it to block a
stage. Roles read it and judge. The manuscript's Method section and claims
follow it; a deviation appears in the card before it appears in the paper. Do
not duplicate the card into the research notes; the notes may point to it.

## The card, as a template

Copy everything inside the block to `METHOD.md` and fill it from the selected
route. Only the method itself is written by hand.

```markdown
# <Method name>

<One paragraph: the method as the paper will claim it. What it takes in,
what it changes relative to the strongest existing method, and what the
paper will say it achieves. Quote the selected route where it states this.>

## Components

| Component | The idea prescribes | Notes |
|---|---|---|
| <component 1> | "<quoted sentence from the route section>" | <empty, or a deliberate simplification and why> |
| <component 2> | "<quoted sentence>" | |

Spell each component name exactly as the `@pytest.mark.component("<name>")`
marker on its tests under `tests/spec` spells it; the host joins the two.

## Protocol

Copied from the selected route; every deviation is named on its own line.

- Datasets: <as the route names them, with splits>
- Baselines: <as the route names them; official implementation under `third_party/`>
- Seeds or repeats: <count and how chosen>
- Metrics: <as the route names them; evaluator and version>
- Configs: <`configs/<file>.yaml` the claim-bearing runs use>
- Deviations: <none> / <what differs, why, and what it costs the claim>

## What would falsify the claim

<The observation, on which data and at which scale, that would make the
paper's central claim false. Name the knockout or comparison that would
show it.>

<!-- Nothing below this line is written by hand. Implementation status per
component, the tests behind it, reused code with pinned revisions,
hyperparameters with their `# why:` reasons, and the change history are
derived by the host from the code, the markers on tests/spec, the config
files and git, and shown beside this card in Atlas and to the Reviewer. -->
```
