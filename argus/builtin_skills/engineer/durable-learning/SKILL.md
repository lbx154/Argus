---
name: "Keeping what the task taught"
description: "When and how the Engineer turns a verified, reusable procedure from this task into a project Skill."
---

# Keeping what the task taught

After verification, if the task produced a durable procedure that would change
how later tasks are done, create or update the Engineer Skill for it in the
project Skill directory the prompt names, before you hand off. If there is
none, make no Skill edit.

- Read the existing Markdown there first; update a related Skill instead of
  duplicating it, and never write shared or global layers.
- Each Skill is `<name>/SKILL.md` with exactly `name` and `description`
  frontmatter, then Markdown. Use an explicit semantic name.
- When a step is better run than read, keep the script you ran beside it and
  say in SKILL.md how to run it.
- A Skill that belongs to another vertical names it in its frontmatter:
  `vertical: <name>`.
- Keep only reusable role learning. Durable project facts, contracts, support
  limits, environment constraints and scoped measurements go to the shared
  project Wiki when the prompt lists one.
- Do not turn task-specific hypotheses, causal attributions, failed attempts or
  replacement recommendations into Skills unless phase attribution, profiling
  or a controlled comparison verified the causal rule. Keep inconclusive
  findings out.
