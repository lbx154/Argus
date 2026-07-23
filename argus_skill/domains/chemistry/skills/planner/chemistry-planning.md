---
name: Chemistry Research Planning
description: Add chemistry-specific planning guidance to the research workflow from the real chemical system, tested capability, tools, evidence regime, and strongest comparable baseline.
category: chemistry-planning
version: 1
---

Plan from the chemical uncertainty, not from a fixed discovery pipeline.
Chemical literature retrieval, structure and reaction databases, cheminformatics,
quantum chemistry, simulation, model training, active learning, and physical
experiments are options, not mandatory phases.

Choose the next action most likely to resolve a real chemical uncertainty. First
inspect the available project, tools, data, compute, endpoints, and permissions.
Prefer an established chemistry engine over asking the language model to imitate
one: use a structure toolkit for molecular identity, a reaction engine for
transformations, a quantum package for electronic structure, or a tested
optimizer for black-box search. The agent chooses and interprets these tools; the
harness does not decide scientific value.

For optimization or discovery, define the oracle, query budget, strongest
appropriate baseline, and evaluation split before exposing answers. Compare
methods under the same budget and retain negative observations. For one-shot
analysis, do not manufacture an iterative loop.

Define the capability being evaluated before choosing the execution topology.
Online agent control, periodic agent revision, a policy designed by an agent and
then frozen, and a conventional optimizer are different experiments. If the
operator asks whether Argus can make closed-loop decisions, keep those decisions
on the live agent path; do not replace them with fixed code merely because fixed
code is cheaper. Propose a smaller budget or request replanning if online evaluation
is infeasible. Treat that result as evidence for replanning, not project value or
completion by itself.

Do not schedule paperwork in place of science. A proposal or literature summary
is not completion when the requested result requires a real calculation,
analysis, or measurement. If critical data, licensed software, an instrument, or
external authorization is unavailable, choose an honest bounded surrogate or
request replanning rather than pretending that access exists, then seek the highest-value
in-scope question the available evidence can actually answer.
