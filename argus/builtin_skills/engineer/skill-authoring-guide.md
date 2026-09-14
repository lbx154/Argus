---
name: "Skill Authoring Guide"
description: "沉淀或审查可复用方法，合并重复技能。 Create, revise or review a Skill only when task evidence supports a reusable procedure; choose global, vertical or project scope and prefer no edit to unsupported learning."
---

# Skill Authoring Guide

A Skill transfers a method: when it applies, how to act, how to check the result,
and when to stop. It is not a task history, a memorized answer or a new source of
operator authority.

## Decide whether anything should be retained

Read the current task evidence and existing relevant Skill first. A successful
mission establishes its accepted output, not every causal explanation in the
summary. Promote a causal rule only with the corresponding comparison, profiling
or direct diagnosis. One verified correction can be reusable; an unresolved error,
transient outage or compliance with a one-time user request is not enough.

If no durable method is supported, make no edit. Ordinary work can teach the Agent;
there is no required CREATE/OPTIMIZE/ABSORB mode or separate author loop to satisfy.
Use the existing role maintenance and reviewed sharing path, within current write
authority. Do not start another learning agent or rerun the project to fill a Skill.

## Choose the narrowest reusable scope

- **Project:** local integration, experiment protocol, incomplete hypothesis or
  project-specific recovery. New learning stays here until broader use is supported.
- **Vertical:** a method tied to an algorithm family, discipline, toolchain or
  workflow, such as RL health or scientific citation verification.
- **Global:** stable methods that transfer across domains, with explicit triggers
  and no hidden project, machine, role-authority or user-preference assumptions.

User preferences and permissions belong in the appropriate user/project context.
Never turn one user's consent, access grant, chosen model, or acceptance of mock
values into permission for future work. Reusable guidance must re-check the current
contract. Skills may not redefine success or authorize edits to gates/certificates.

## Write the smallest useful change

1. Prefer refining the existing semantic path; merge genuine duplicates rather
   than appending another near-synonym. Preserve valid exceptions and user edits.
2. State the trigger and important exclusions in the description, using language
   users actually employ. Bilingual descriptions are useful for multilingual tasks.
3. Give the procedure, decisive check, failure/uncertainty branches and stop condition.
   Qualify versions, heuristics and numerical examples instead of calling them laws.
4. Reproduce executable examples on a small fixture where practical. Missing logs,
   API metadata and a green exit code each prove only their own narrow fact.
5. Cite existing evidence beside the claim that needs it; do not copy secrets,
   transcripts, outcomes or temporary identifiers into the procedure.

A document has exactly `name` and `description` YAML fields followed by Markdown.
Do not add counters, scores or a parallel registry. For consequential revisions,
compare the same representative tasks with and without the guidance using their
real acceptance checks and actual cost. Preserve a useful improvement, narrow it
when counterexamples appear, and archive guidance that no longer helps.
