---
name: "Executable spec (tests/spec)"
description: "Before any claim-bearing run, write tests/spec: an oracle transcribed from the route's equations, differential tests of the implementation against the oracle and the reference, one knockout per component, and claim-shaped tests for complexity, memory and throughput claims, each tagged with the METHOD.md component it exercises. The host runs tests/spec after every round and derives component status from the result."
---

# Executable spec (tests/spec)

`tests/spec` is the part of the project that says, in code the host can run
without a model, what the method must do. It is written before the first
claim-bearing run and extended whenever `METHOD.md` gains a component or a
claim. The host runs it after every Engineer round (zero model tokens),
joins the result with the component markers on the tests, and shows the
derived status per component to the Reviewer, to the Engineer as Raw
verification evidence, and to human readers in Atlas. Nothing blocks on it;
the roles read it and judge.

## What goes in it

Start from the templates beside this Skill in the research skill library,
`engineer/spec_test_templates/` (`conftest.py`, `test_differential.py`,
`test_knockouts.py`, `test_claim_shape.py`, `test_parity_reference.py`).
Copy them to `tests/spec/`, replace every `TODO`, and remove the skip
markers as each test becomes real. Plain `pytest` and `numpy`; no framework
is required to run the oracle.

1. **Oracle.** Transcribe the route's equations into slow, obviously correct
   `float64` functions, one function per equation, named after the equation.
   No vectorisation tricks, no shared code with the implementation. The
   oracle is the method as written; if the route is ambiguous, quote the
   ambiguity in `METHOD.md` and pick one reading in the oracle.
2. **Differential tests** (`test_differential.py`). Implementation versus
   oracle on random inputs at several sizes with a stated tolerance, and
   implementation versus the `third_party/` reference on the baseline path
   (`test_parity_reference.py`). A tolerance loosened to make a test pass is
   a simplification: say so in the card's `Notes` column.
3. **Knockouts** (`test_knockouts.py`). One per component in the card:
   disable the component (flag, zero weight, identity replacement) and, on a
   case built so that the component matters, assert the output changes by
   more than noise. A component whose knockout does not change the output
   is not doing what the card says. `conftest.knockout()` and
   `conftest.assert_changes()` are small helpers for this.
4. **Claim-shaped tests** (`test_claim_shape.py`). For every complexity,
   memory or throughput claim in the card, measure at three or more scales
   and fit the slope on log-log axes (`conftest.scaling_slope()`); assert the
   slope matches the claimed order within a margin. For an accuracy claim,
   the test is the positive control: a case with a known recoverable signal
   through the same executed path.

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

## What it is not

Outcome-shaped tests (asserting the paper's headline number) prove nothing
about the method; do not write them here. Tests that pass by `skip`,
`xfail`, `or True`, or a tolerance wide enough to accept anything are
visible to the Reviewer as exactly that. Do not delete a failing test to
turn the run green; a `contradicted` component is information, and a
deliberate simplification belongs in the card's `Notes` column with its
reason.

## Keeping the card and the suite together

The join is by component name: a row in the card and the marker on its
tests must spell the name the same way. When a component is added to the
card, its tests get the marker in the same round; when a component is
removed, so are its markers. Nothing else needs to be kept in step.
