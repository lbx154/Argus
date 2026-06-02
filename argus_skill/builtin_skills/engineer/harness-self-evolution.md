---
name: harness-self-evolution
description: "How to adapt YOUR OWN harness — the stage checklists and reviewer/planner/engineer house rules that the framework injects into every prompt — when you hit a RECURRING gap the framework floor does not cover (e.g. you keep mis-setting RL hyperparameters, keep forgetting to log a config, keep needing a check the checklist never asks for). Use the per-project interface `python -m argus_skill.tools.harness_evolve` to ADD a checklist item, ANNOTATE (strengthen) an existing item, or ADD a house rule. Changes are per-project, hot-reloaded with no restart, and fully revertible. Use this when a SKILL alone is not enough because you keep failing to FOLLOW the skill — encode the obligation into the harness so it is enforced/scheduled, not just available. NOT for one-off task notes (use AGENTS.md) and NOT for general knowledge (distill a skill)."
category: harness-self-evolution
version: "1.0"
created_at: "2026-06-02T00:00:00+00:00"
---

# Harness self-evolution (adapt your own checklists & house rules)

The framework gives you skills (knowledge you *can* consult) and an immutable
**harness floor**: the stage checklists the L2 reviewer rules against, plus the
role house rules baked into prompts. A skill you keep *ignoring* is a harness
problem, not a knowledge problem. This interface lets you bolt the missing
obligation onto the harness itself, **per project**, so it is enforced every
round with no daemon restart — and reverted just as easily.

## When to use it
Use it when the SAME class of mistake recurs and a skill alone has not fixed it:
- You repeatedly mis-set RL knobs (`max_completion_length`, learning rate,
  `num_generations`) → add a `run`-stage engineer item that forces you to log and
  sanity-check those knobs against the `rl-training-collapse-diagnosis` skill
  BEFORE launching.
- The reviewer keeps passing single-seed RL deltas as real → propose a reviewer
  amend on `run.score_variance` requiring ≥3 seeds (lands in *pending* until you
  promote it — see safety below).
- You keep needing a check the checklist never asks for → add the item.

Do NOT use it for one-off, this-task-only notes (those go in `AGENTS.md`) or for
reusable knowledge (distill a normal skill instead).

## The interface
```
python -m argus_skill.tools.harness_evolve add-item \
  --stage run --id run.hparam_log --role engineer \
  --statement "Before every RL launch, log lr, max_completion_length, num_generations to run_config.json and sanity-check them against rl-training-collapse-diagnosis." \
  --evidence "experiments/runs/<id>/run_config.json" \
  --reason "RL collapse last mission traced to an unlogged, oversized max_completion_length."

python -m argus_skill.tools.harness_evolve amend-item \
  --id run.score_variance --role reviewer \
  --note "For RL/preference runs require >=3 seeds before accepting an improvement." \
  --reason "Single-seed RL deltas were within noise last mission."

python -m argus_skill.tools.harness_evolve add-rule \
  --role engineer --id eng.rl_preflight \
  --text "Never launch an RL run before reading rl-training-collapse-diagnosis and setting the knobs it names." \
  --reason "Recurring misconfiguration."

python -m argus_skill.tools.harness_evolve list      # see active + pending
python -m argus_skill.tools.harness_evolve promote --id run.score_variance
python -m argus_skill.tools.harness_evolve revert --id run.hparam_log
python -m argus_skill.tools.harness_evolve reset      # clear everything
```

## Safety model (read before using)
- **Floor is immutable.** You can ADD items, ANNOTATE existing items (strengthen),
  or ADD house rules. You can never delete or weaken a protected integrity item
  (evidence binding, no fabrication, variance/seed reporting, instruct-not-base,
  honest go/no-go, the done criteria). Attempts are rejected.
- **Additive only.** Overlay items can tighten the bar, never relax it. On any
  conflict the framework checklist wins.
- **Engineer changes activate immediately** (they only add work to *you*).
  **Reviewer/planner changes go to `pending`** and take effect only after
  `promote` — so you cannot quietly tell your own judge to be lenient. Promote a
  reviewer/planner change only when it makes the gate STRICTER and you can name
  the recurring failure it prevents.
- **Everything is per-project and reversible.** State lives in
  `<project>/.argus/harness/` with a `journal.jsonl` audit trail; `revert`/`reset`
  restore the floor.

## Principle
Reach for this when you notice yourself failing the same way twice. One fair
failure + a named cause is enough to justify an overlay item; encode the lesson
into the harness so future rounds cannot skip it.
