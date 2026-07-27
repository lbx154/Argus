---
name: Olympus Reaction Optimization
description: Use Olympus planners and emulator surfaces for controlled reaction-optimization benchmark studies with explicit surface, parameter space, noise, budget, baselines, and simulated-evidence limits.
category: chemistry-tool-optimization
version: 2
---

## When to use

Use for reproducible optimization-method comparison on a documented Olympus
dataset/emulator or for a bounded surrogate study before any physical campaign.

## Do not use when

Do not present emulator observations as fresh experiments or assume performance
on one surface transfers to another reaction, scale, instrument, or noise regime.

## Required inputs

Olympus/version, dataset or emulator and provenance, parameter domain, objective,
constraints, noise model, initialization, budget, seeds, planners, baselines,
and whether decision control is online, periodic, frozen, or conventional.

## Minimum capability probe

Load the exact surface, evaluate documented points, verify parameter bounds and
objective direction, run a short planner trace, and confirm every observation
and budgeted decision is recorded.

## Evidence and validation

Retain surface/version, configuration, initial points, proposal-observation
trajectory, seeds, failures, budget, baselines, regret/objective definitions,
and repeated runs. Keep emulator outputs labeled simulated/retrieved.

## Output contract

Return comparable trajectories and uncertainty under equal budgets, negative
runs, control provenance, and an emulator-benchmark evidence ceiling.

## Stop or replan

Stop when the surface or objective is not reproducible, planners receive
different information/budgets, or the surrogate cannot test the claimed
real-world capability.

## Official references

- https://aspuru-guzik-group.github.io/olympus/
- https://github.com/aspuru-guzik-group/olympus
