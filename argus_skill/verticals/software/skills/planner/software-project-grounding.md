---
name: "Software Project Grounding Before Decomposition"
description: "Validate the Manager grounding brief against the repository, then decompose software work around architecture boundaries and independent acceptance risks."
---

# Software Project Grounding Before Decomposition

## When to use

Use before scheduling Engineer nodes for staged software work.

## Planner method

1. Read the Manager grounding brief and verify its key claims with a bounded
   inspection of the named files and closest sibling implementation.
2. Fill only material gaps: affected callers, configuration/schema surfaces,
   generated code, compatibility defaults, and task-native verification.
3. Decompose by independently verifiable architecture boundaries, not by prose
   sections or arbitrary file counts.
4. Put exact acceptance risks into node objectives: return types, field
   mappings, ordering, invalid inputs, boundaries, and platform/user semantics.
5. Keep shared setup and verification dependencies explicit so Engineer does
   not rediscover the project from scratch in each node.

## Handoff

Each Engineer node receives relevant file paths, the closest analogue, the
behavioral contract, and the narrow command that can falsify it. Do not include
opaque hashes or duplicate the full repository survey.

## Pitfalls

- Do not treat the Manager brief as proof; verify it.
- Do not schedule implementation before identifying the independent oracle.
- Do not create a plan whose nodes can each pass while the integrated public
  interface remains incompatible.
