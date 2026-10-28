# Method card (METHOD.md) and executable spec

Use this in the first Experiment task, before any method code, and again whenever the method
itself changes, a baseline enters the protocol, or the spec suite is written or extended.

`METHOD.md` at the project root is the one place that says what the method *is*. The
Engineer who implements the method writes it, from the selected route, before writing method
code; the Reviewer reads it before anything else. It is a work product like the code and the
raw outputs, and it is shown to people as written.

Everything else here holds the code to the card: the reference clone the method is a diff on,
the spec suite that says in code what the method must do, and the mapping from thesis to
executed path. They are joined by one convention: a component is spelled the same way in the
card's table and in the marker on its tests.

## Why the card exists

A project failed when the Engineer defined the method in passing inside the notes,
implemented a simplified version, wrote tests shaped to pass, and a read-only Reviewer
certified the Engineer's account of it. Nobody could point to a sentence saying what the
method was supposed to be. The card fixes the statement first, so every later step is a
comparison against it, not a restatement of it.

## When to write it

In the first Experiment task, before any claim-bearing run, together with the reference
clone and the spec suite described below; finish all three before handing the first task to
review. Commit `METHOD.md` before the method code, or with it: `checks/method-card.mjs` (see
the README) reads the order from git. In Review, revise the card before a scientific repair
that changes the method, never after.

## Writing the card

Write it once. Everything in it is about the method, not about the state of the project.

1. Open the selected route, named in `RESEARCH_NOTES.md`. Quote the route's own sentences
   for each component; do not paraphrase them into something easier.
2. Copy the template at the end of this document to `METHOD.md` and fill it in order: a title
   and a one-paragraph statement of the method as the paper will claim it; the `Components`
   table (`Component | The idea prescribes | Notes`), quoting the route in the second column;
   `Notes` names the component's entry point as `path:Symbol` and, if you deliberately
   simplify the component, what was simplified and why; `Known prior art`; the `Protocol`
   (datasets, baselines, seeds or repeats, metrics and the config files the runs use, copied
   from the route, every deviation on its own line with the reason, and whether this
   experiment can answer the question); and `What would falsify the claim`, the observation
   that would make the paper's central claim false.
3. Spell the component name exactly as the marker on its tests will spell it.

The tests behind each component, the reused code and its revisions, the hyperparameters and
the history speak for themselves through the markers, the `third_party/` clones, the config
files and git; none of them is copied into the card.

Touch `METHOD.md` again only when the method itself changes: a component is added or
removed, a component is deliberately simplified (say so in `Notes`), the prior art or the
protocol changes, or the falsifier changes. A `Notes` entry naming a simplification is honest
and allowed; a component the card states plainly and the code contradicts is the defect the
Reviewer looks for first.

## The reference implementation, and the method as a diff on it

The official implementation of every method the project compares against or extends, or the
strongest public one when no official code exists, is the ground truth for what that method
does. Reimplementing it from a paper summary produces a weaker baseline and an unfalsifiable
comparison, so in the first Experiment task, and again whenever a new baseline enters the
protocol:

1. Clone it into `third_party/<name>` at a pinned revision (a commit hash or release tag) and
   keep it a git checkout, so its revision and remote can be read from the clone itself. Do
   not vendor a copy without the revision, and do not edit files inside `third_party/` in
   place; the clone stays as cloned.
2. Run one of its shipped examples end to end in the project environment, at the smallest
   scale that exercises the real path. Keep the command and the output location in the
   research notes. If it needs the project environment repaired, repair it: this is the setup
   every later result depends on.
3. Write the parity test (`tests/spec/test_parity_reference.py`, marked with the baseline
   component's name): the project's implementation of the *baseline* path reproduces the
   reference's output on a fixed input within a stated tolerance. Until it passes, the
   baseline is not shown to be the published method.
4. Build the new method as a delta: subclass, wrap, or call into the reference so that
   `git diff` between "reference as used" and "our method" is the list of ideas the paper
   claims. When the reference cannot be extended (different framework, incompatible license),
   implement the baseline separately and let the parity test carry the burden of showing it
   is the same method.

This replaces a hand-rolled baseline "following the paper", a training loop or serving path
written from memory, and a comparison whose baseline nobody else can run. Choosing the
infrastructure for training or large inference is a separate question;
`training-infrastructure.md` covers it.

## The executable spec (tests/spec)

`tests/spec` is the part of the project that says, in code that runs without a model, what
the method must do. It is written before the first claim-bearing run and extended whenever
`METHOD.md` gains a component or a claim. Run it when you need its result and put the command
and result in `.argus/evidence/`. Plain `pytest` and `numpy`; no framework is required to run
the oracle. Each kind of test lives in its own file:

1. **Oracle.** Transcribe the route's equations into slow, obviously correct `float64`
   functions, one function per equation, named after the equation. No vectorisation tricks,
   no shared code with the implementation. The oracle is the method as written; if the route
   is ambiguous, quote the ambiguity in `METHOD.md` and pick one reading in the oracle.
2. **Differential tests** (`test_differential.py`). Implementation versus oracle on random
   inputs at several sizes with a stated tolerance, and implementation versus the
   `third_party/` reference on the baseline path (`test_parity_reference.py`). A tolerance
   loosened to make a test pass is a simplification: say so in the card's `Notes` column.
3. **Knockouts** (`test_knockouts.py`). One per component in the card: disable the component
   (flag, zero weight, identity replacement) and, on a case built so that the component
   matters, assert the output changes by more than noise. A component whose knockout does not
   change the output is not doing what the card says. Random inputs often do not exercise a
   component (a gate that is nearly always open, a correction that vanishes at zero mean), so
   construct the case deliberately.
4. **Claim-shaped tests** (`test_claim_shape.py`). For every complexity, memory or throughput
   claim in the card, measure at enough scales to fit a slope on log-log axes (at least three
   sizes spanning a decade, the minimum of a few repeats per size) and assert the slope
   matches the claimed order within a margin. For an accuracy claim, the test is the positive
   control: a case with a known recoverable signal through the same executed path.

Outcome-shaped tests (asserting the paper's headline number) prove nothing about the method;
do not write them here. Tests that pass by `skip`, `xfail`, `or True`, or a tolerance wide
enough to accept anything are visible to the Reviewer as exactly that. Do not delete a
failing test to turn the run green; a contradicted component is information, and a
deliberate simplification belongs in the card's `Notes` column with its reason.

## Component markers

Every test carries `@pytest.mark.component("<component>")` with the component name spelled
exactly as in the `Components` table of `METHOD.md`; the file it lives in says which kind of
test it is. Register the marker once in `tests/spec/conftest.py`:

```python
def pytest_configure(config):
    config.addinivalue_line("markers", "component(name): the METHOD.md component this test exercises")
```

A knockout then reads:

```python
@pytest.mark.component("gated update")
def test_gated_update_knockout(model, case_where_gate_matters):
    baseline = model(case_where_gate_matters)
    model.use_gate = False                     # the component's switch
    knocked_out = model(case_where_gate_matters)
    change = np.max(np.abs(baseline - knocked_out)) / max(np.max(np.abs(baseline)), 1e-12)
    assert change > 1e-3, "the case does not exercise the gate"
```

When a component is added to the card, its tests get the marker in the same task; when a
component is removed, so are its markers. A component no test is marked with is untested.

## Entry points

The Reviewer was not there when the code was written; it starts reading at the `path:Symbol`
each component's row names and compares the code there with the card. Reused code is
imported from the pinned `third_party/` clone or the installed package, never copied into the
project. A non-obvious decision (a tolerance, a masking choice, an ordering, a shortcut taken)
carries a `# why: <reason>` comment on or directly above it.

## Configuration

Hyperparameters live in config files (`configs/*.yaml`), each chosen value with a
`# why: ...` comment on the same line or the line above it. No load-bearing defaults hidden in
function signatures; the run is reproducible from the config it names.

## Shape of the code

- No dead code, no commented-out alternatives, no second implementation of a component kept
  "for comparison": one entry point per component.
- Explicit configuration over implicit behaviour; a flag that changes the method is named in
  the config and in the card.
- Tests under `tests/spec` are named by component and kind (`test_<component>_knockout`,
  `test_<component>_differential`).

## Saying what the claim got

After a claim-bearing run, the letter states, clause by clause of the claim, what was
obtained, whether it is met (yes, no, partial, untested), and the results file and field that
hold the number. The comparison is yours; the number is the file's. A clause you cannot meet
is stated as not met, never reworded as a margin over a baseline.

## Keeping the thesis and the code aligned

After the idea is chosen and before claim-bearing execution, read the selected thesis from
`RESEARCH_NOTES.md` and the method as the card states it, and map every load-bearing part of
the thesis to the actual implementation:

- mechanism, intervention, prediction, and falsifier;
- the executable entry point and the branch where candidate and baseline diverge;
- formulas, operands, masks, reductions, timing, and gradient boundaries;
- the information available at decision time;
- the evaluator output and the positive control;
- the fair baseline and the invariants.

Implement through those concrete paths. The card's `Components` table and its markers are
where the mapping lives; a fresh Reviewer then follows the reachable call chain from the card
(`claim-to-code-trace.md`) and says whether the code tests the selected mechanism under the
intended comparison, tests a different mechanism or comparison, or does not reach the
mechanism at all. Fix a contradiction or a missing mechanism in the code before claim-bearing
runs; never resolve either by rewording the card to describe what the code does. The claim
changes only as its owner allows (the README says who); a component row changes only for a
deliberate, named simplification the operator can see, named in its `Notes`. The alignment
result lives in the code, the spec run you report and the Reviewer's letter, not in a
separate note.

## What the card is not

Not a plan, not a report, not a JSON ledger. The manuscript's Method section and claims
follow it; a deviation appears in the card before it appears in the paper. Do not duplicate
the card into the research notes; the notes may point to it.

## The card, as a template

Copy everything inside the block to `METHOD.md` and fill it from the selected route.

```markdown
# <Method name>

<One paragraph: the method as the paper will claim it. What it takes in,
what it changes relative to the strongest existing method, and what the
paper will say it achieves. Quote the selected route where it states this.>

## Components

| Component | The idea prescribes | Notes |
|---|---|---|
| <component 1> | "<quoted sentence from the route section>" | <`path:Symbol` of its entry point; a deliberate simplification and why> |
| <component 2> | "<quoted sentence>" | |

Spell each component name exactly as the `@pytest.mark.component("<name>")`
marker on its tests under `tests/spec` spells it.

## Known prior art

<The closest published work and any published or observed evidence that the
effect does not hold, each with its source. An operator-given direction waives
only an objection that the idea is not original; evidence against its effect
is listed here and tested, never set aside.>

## Protocol

Copied from the selected route; every deviation is named on its own line.

- Datasets: <as the route names them, with splits>
- Baselines: <as the route names them; official implementation under `third_party/`>
- Seeds or repeats: <count and how chosen>
- Metrics: <as the route names them; evaluator and version>
- Configs: <`configs/<file>.yaml` the claim-bearing runs use>
- Deviations: <none> / <what differs, why, and what it costs the claim>

### Can this experiment answer the question

Filled in before the first claim-bearing run; where an answer is no, change
the benchmark or the scale here first.

- Ceiling at both ends: <the metric for the starting point (initial model or
  simplest method) and for the best reachable result (an oracle, a stronger
  model or the published best): neither end at the metric's floor or ceiling
  nor reachable by a label shortcut, with room between them for the claimed
  effect>
- Minimum detectable effect: <from the number of items and seeds, the smallest
  difference this protocol resolves; the claimed effect exceeds it>
- Strongest same-information baseline: <the strongest method given the same
  information, and the simplest one (e.g. more samples or a tuned default);
  both run under this protocol>
- Known counter-evidence: <what under Known prior art predicts no effect here,
  and the run that would show it>

## What would falsify the claim

<The observation, on which data and at which scale, that would make the
paper's central claim false. Name the knockout or comparison that would
show it.>
```
