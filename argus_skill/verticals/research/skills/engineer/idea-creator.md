---
name: "Idea Creator"
description: "Independently review twelve source-only routes and select one research idea exactly once."
---

# Idea Creator

Review each route independently against current primary sources and the
strongest prior-art attack. Do not reward a route for being cheap, training-free,
or locally convenient. Record a decisive natural-language verdict and the
strongest concern under the task's internal `.argus` output path.

After all twelve reviews finish, the fresh selector reads every route/review
pair and chooses exactly one idea. It records one single-line rejection reason
for each other route. No candidate code or experiment may be run before this
choice.

The validated portfolio replaces project-root `HANDOFF.md`, starting with
`# HANDOFF — IDEA`, with a compact but detailed winner explanation and all
eleven single-line rejection reasons. Never copy a full route dossier or
unbounded selector prose into the handoff or pipeline state. That file is the
only normal visible handoff. Do not create project-visible candidate collections,
selection reports, briefs, novelty reports, or venue profiles.

The selected idea is one-time and resumable from internal pipeline/team state.
Later failures route to repair in Build, Experiment, Paper, or Review; they do
not trigger another selector.
