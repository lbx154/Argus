---
name: "Research Reality Probe"
description: "Run the cheapest faithful observation that informs a research idea, preserve the raw result, and leave its interpretation and next move to the Planner."
---

# Research Reality Probe

## Purpose

Before committing substantial compute, obtain one real observation about the
idea's binding premise on available models, data, or systems. This is a source of
information, not a routing gate.

## How to work

1. Read the research brief and identify the uncertain premise whose answer would
   most change the plan.
2. Choose the cheapest faithful probe that can inform it. Use real public data or
   the actual system when the claim depends on them; do not replace the premise
   with an easy toy proxy.
3. Record the setup before running: model/system identity, data slice, comparator
   or control, metric/observation, and the limitations of the probe.
4. Run it for real and preserve the command, raw output, and analysis under a
   sensible project path. Reuse existing run conventions instead of creating a
   special de-risk packet.
5. Write a short factual note in `research/RESEARCH_BRIEF.md` or the existing
   experiment log:
   - what was observed;
   - what remains uncertain;
   - plausible explanations, including implementation weakness;
   - paths to the raw material.

Do not emit PASS/FAIL, force a pivot, or automatically schedule another direction.
The Planner reads the stored observation with the rest of the project and decides
what it changes. A wiring smoke test is not evidence for the scientific thesis.

## Integrity

Never type expected numbers as results, hide failed calls, or relabel synthetic
examples as public evidence. If the probe is not faithful enough to inform the
premise, say so plainly.

## Handoff

Report the observation and its limits in ordinary prose, with paths to the raw
run. Avoid verdict packets and workflow language.
