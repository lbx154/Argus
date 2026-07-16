---
name: harness-self-evolution
description: "Add a reversible per-project role prompt rule after a recurring process failure. Checklist design is not part of this skill: framework/vertical code supplies the seed, Planner checklist_ops is the sole runtime editor, and Reviewer supplies checklist_feedback."
category: harness-self-evolution
version: "2.0"
created_at: "2026-06-02T00:00:00+00:00"
---

# Harness self-evolution: prompt rules only

Use this after the same process mistake recurs and a normal skill has not made
the role follow the needed behavior. Do not use it for one-off notes or general
knowledge.

```bash
python -m argus_skill.tools.harness_evolve add-rule \
  --role engineer --id eng.rl_preflight \
  --text "Check generation length and reward variance before an RL launch." \
  --reason "The same configuration failure recurred."

python -m argus_skill.tools.harness_evolve list
python -m argus_skill.tools.harness_evolve promote --id <pending-rule>
python -m argus_skill.tools.harness_evolve revert --id <rule>
python -m argus_skill.tools.harness_evolve reset
```

Checklist gaps go through Reviewer `checklist_feedback` and Planner
`checklist_ops`. Engineer never adds, amends, or promotes checklist items.
