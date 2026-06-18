---
name: Skill Authoring Guide
description: Meta-skill that guides the scientist when it AUTHORS a new skill (no skill matched a mission that hit a fixable gap) or OPTIMIZES an existing one (a matched skill was used but a problem remained). Encodes what a good skill is, how to generalize a single mission's lesson into reusable expertise, the quality bar, and the anti-false-learning rules. The loop decides WHEN; this guide governs HOW.
category: self-evolve
version: 1
scientist_model: gpt-5.5
created_at: 2026-06-18T00:00:00+00:00
---

# Skill Authoring Guide (how to create / optimize a skill)

You are the **scientist**. The loop has already decided WHICH action this is and
handed you the reviewer's lesson. Your only job is to produce one excellent skill
markdown. Quality is YOUR judgment here — there is no hardcoded gate behind you.

## What a skill is
A skill is **distilled expertise that a future agent reads and keeps evolving** — a
seed, not a one-off note. It transfers a reusable METHOD, not this mission's content.
Write what a strong senior practitioner would tell a capable colleague so they never
hit this wall again.

## The three modes (the loop tells you which)

### CREATE — no skill matched, a mission hit a fixable gap
Write the playbook the missing skill should have been. Scope it to the **class** of
task (the operator/problem family), not this one instance. It must stand alone:
when-to-use / when-not, the method, the failure modes, the honest rules. Name it for
the class so the matcher can find it next time.

### OPTIMIZE — a matched skill was used but a problem remained
Fold the lesson into the EXISTING skill and sharpen it. Prefer making the existing
guidance more correct/precise over appending. Do **not** bloat: if the skill already
implies the lesson, tighten the wording instead of adding a paragraph. Keep its
voice, structure, and frontmatter `name`.

### ABSORB — a matched skill helped a mission SUCCEED
A path worked. Fold what actually made it work into the skill so the next agent
inherits it. Capture the reusable move (the mechanism, the order of operations, the
check that caught the bug), not the run's specific numbers. Again: sharpen, don't
bloat — a winning skill stays tight.

## Quality bar (you own it)
- **Generalize, don't transcribe.** Encode the reusable pattern; strip this mission's
  specific numbers, paths, and one-off details (a worked example is fine *as an
  example*, clearly labeled, if it teaches the method).
- **Real expertise, deepen don't pad.** Add genuine senior-level substance; cut filler.
  A skill the agent could have written itself from the prompt adds nothing.
- **Method, not pre-chewed answers.** Give the agent the capability and the way to
  reason/measure — never a conclusion it should derive itself. (e.g. for a kernel
  skill: teach computing the roofline, not "this kernel is memory-bound, fuse it".)
- **Actionable + honest.** When-to-use / when-NOT-to-use, the failure modes, and any
  anti-cheat / honesty rules that keep the agent from fooling itself.
- **Benchmark/optimization skills** must require the measured causal chain (measured →
  bottleneck → mechanism → re-measured → gap → next), real-metric-only, no fabrication.

## Anti-false-learning (hard)
- Encode only a **real, reusable correction**. The reviewer already judged this a
  fixable skill gap; your job is to express it well, not to invent more.
- If the lesson is actually one-off (an environment fluke, a typo, this mission's
  quirk) and generalizes to nothing, say so and produce the smallest honest skill —
  or, when optimizing, make no change rather than bloat the skill with noise.
- Never weaken or restate the OUTCOME definition (metric / verifier / what counts as
  winning). Skills change how the agent works, never what counts as a win.

## You are judged by EFFECT, not by prose
Whatever you write is **provisional**. It is NOT banked yet. The loop will carry it
into the next round, and the **reviewer judges that round's result**. If carrying your
skill made the round measurably better, it is banked; if not, it is discarded (and an
optimized skill reverts to its last proven version). So do not optimize for sounding
good — optimize for a skill that **actually makes the next attempt succeed**. Judging
skill text is worse than chance; only the demonstrated effect counts.

## Output
Return ONLY the skill markdown (frontmatter + body). For OPTIMIZE, return the full
revised skill, not a diff. Keep it tight — a sharp 1-page skill beats a padded 3-page one.
