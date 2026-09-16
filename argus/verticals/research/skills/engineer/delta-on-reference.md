---
name: "Delta on reference"
description: "Clone the official or strongest public implementation of the method being compared or extended at a pinned revision into third_party/, run one of its examples, and build the new method as a diff on top of it so that the code diff is the idea diff."
---

# Delta on reference

Use in the first Experiment round for every method the project compares
against or extends, and again whenever a new baseline enters the protocol.

## The rule

The official implementation, or the strongest public one when no official
code exists, is the ground truth for what a method does. Reimplementing it
from a paper summary produces a weaker baseline and an unfalsifiable
comparison. So:

1. Clone it into `third_party/<name>` at a pinned revision (a commit hash or
   release tag) and keep it a git checkout: the host reads the revision and
   the remote from the clone itself and shows them beside `METHOD.md`. Do
   not vendor a copy without the revision, and do not edit files inside
   `third_party/` in place; the clone stays as cloned.
2. Run one of its shipped examples end to end in the project environment,
   at the smallest scale that exercises the real path. Keep the command and
   the output location in the research notes. If it needs the project
   environment repaired, repair it; this is the setup that every later
   result depends on.
3. Write the parity test (`tests/spec/test_parity_reference.py`), tagged
   `@pytest.mark.component("<baseline component>", kind="parity")`: the
   project's implementation of the *baseline* path reproduces the reference's
   output on a fixed input within a stated tolerance. Until it passes, the
   host shows the baseline component as partial or contradicted.
4. Build the new method as a delta: subclass, wrap, or call into the
   reference so that `git diff` between "reference as used" and "our method"
   is the list of ideas the paper claims. When the reference cannot be
   extended (different framework, incompatible license), implement the
   baseline separately and let the parity test carry the burden of showing
   it is the same method.

## What this replaces

A hand-rolled baseline "following the paper", a training loop or serving path
written from memory, and a comparison whose baseline nobody else can run.
Choosing infrastructure for training or large inference is a separate
question; `engineer/infrastructure-landscape-survey.md` covers it.

## Record

The card's `Protocol` names the baseline and its official implementation;
the revision, the remote and which project files import the clone are
derived by the host from `third_party/` and the code. The example that ran
and its output location go in the research notes. The commands that made the
clone run go in the durable-learning Skill for that codebase, as the
Engineer fragment asks.
