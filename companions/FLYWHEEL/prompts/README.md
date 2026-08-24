# ARGUS / FLYWHEEL Prompt contracts

Prompt packets implement `argus.flywheel/research-protocol-v2`: immutable,
project-specific research contracts rather than mutable chat snippets. They
freeze team conditions, resources, venue, sources and operator goal; compile the
free-form goal into measurable gates, hard budget and stop criteria; and bind
Builder, Breaker, Arbiter, two fresh-review rounds, bounded revision, final
integrity and human checkpoints. `NO_WINNER` and sealed negative results are
valid. Oral and unprecedented novelty are aspirations, never guarantees.

The authoritative compiler is
`backend/src/foundry/services/prompt_compiler.py`. Every compiled packet includes a
canonical input hash and a rendered prompt hash so running campaigns cannot be
silently rewritten.

The human-readable input contract and recommended Argus handoff are documented in
[`docs/PROMPTING_ARGUS.md`](../docs/PROMPTING_ARGUS.md). Use
`scripts/export_prompts.py` to materialize all 290 = 58 venues × 5 Portfolio packets
after supplying real resource caps. Open the generated `CATALOG.md` to browse them.
Each venue uses its earliest planning target; this is not an 85-deadline × 5 export.
These are cold-start coverage seeds, not personalized launch-ready projects. A
production run targets ten defensible ideas by default but must return fewer when
evidence cannot support ten; padding is forbidden. Do not hand-edit a frozen
Locked Contract or Campaign packet after launch—create a new version with parent
and SHA provenance.
