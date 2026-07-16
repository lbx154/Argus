# Example: 红楼梦 continuation canon (`honglou`)

A worked **example of a seeded `story_state`** for a *continuation* mission — the
long-term-memory ground truth a `fiction_writing` continuation is written against.
Genre-agnostic machinery, a concrete 红楼梦 (classical zhanghui) instance.

## Files
- `story_state.json` — the canon seed: 29 principal characters (十二钗 + 主子 +
  丫鬟 + 已故), relationships, world rules, a dated timeline, open threads,
  foreshadowing, locations (大观园各馆) and items (通灵玉/金锁/金麒麟/题帕).
- `style_profile.json` — the voice card in force (classical register, appellations,
  forbidden lexicon, per-character 口吻), composed from the `classical_zhanghui`
  preset.
- `chapter.md` — a sample continuation grounded on the above.

## Guarantees (checked by `tests/test_fiction_writing_examples.py`)
- `story_state.json` validates against `schemas/story_state.schema.json`.
- It is **temporally self-consistent** — every living character satisfies
  `age == world_clock.current_year − birth_year`, nobody is born in the future,
  and the timeline `order`/`year` do not invert (`temporal.check_temporal_consistency`
  returns no findings).
- `style_profile.json` validates against `schemas/style_profile.schema.json`.

## Notes
- `current_year = 15` is set at the 大观园 period (秦可卿 already died, 元春 已省亲).
- The original novel's stated ages are mutually inconsistent; this canon fixes a
  single internally-consistent set so the deterministic `temporal` gate has a
  coherent ground truth to check against — it is a usable seed, not a scholarly
  claim about the source text.
- Reproduced/extended through the safe patch engine (`state.apply_patch`), never by
  hand — so referential integrity (every item holder is a real character, etc.)
  holds by construction.
