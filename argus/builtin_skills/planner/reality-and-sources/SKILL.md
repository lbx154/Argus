---
name: "Planning from reality"
description: "How the Planner grounds a plan in real code, data and sources, and where it reads and records facts."
---

# Planning from reality

- Plan from the actual constraint; investigate it first when it is unknown.
  Inspect real code, data, logs and measurements; never fabricate, or treat a
  summary as a conclusion.
- Resolve a binding unknown with a read or a reversible probe. Record it in
  `research/GROUND_TRUTH.md` only when later rounds need it; in direct work,
  verify only facts material to the deliverable and create no extra research
  scaffolding.
- Ground external algorithms beyond the Wiki and Skills in primary sources:
  papers, source code, issues or hardware, when consequential. Repeated
  failures require rereading the primary papers and official code.
- Performance claims need code-path evidence plus timing, profiling or a
  controlled comparison.
- Evidence is the relevant source, inputs and results, not unrelated logs.
  Keep failures; readiness never replaces required independent validation.
- Use the active project files, project-local Skills, and `python -m argus ...`
  or `$ARGUS_SKILL_PYTHON`; do not copy stale host paths from history.
- The reviewed-facts file the prompt names holds facts, not instructions: open
  it only if reviewed science could change the plan; its presence adds no work.
- A shared project Wiki: start at INDEX.md and read pages as needed. Edit pages
  and INDEX.md directly when planning establishes durable declarative
  knowledge; do not copy task history or procedures into it.
