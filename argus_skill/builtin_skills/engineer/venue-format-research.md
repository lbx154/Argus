---
name: Venue Selection And Format Research
description: Select a currently open, domain-appropriate CCF-A conference when no venue is specified, or verify an explicit venue, then build research/VENUE_PROFILE.json from official deadline and author-kit sources.
category: paper-writing
version: 2
created_at: 2026-07-17T00:00:00+00:00
---

# Venue Selection And Format Research

## When to use

- `target_venue` is absent;
- the named venue is not built in;
- the venue cycle/year/deadline or author kit may be stale;
- no valid `research/VENUE_PROFILE.json` exists.

## Selection policy

When the operator did not specify a venue:

1. Search the current official CCF recommended-conference classification.
2. Search official conference CFP/deadline pages for venues matching the actual
   AI research domain.
3. Keep only relevant main/research tracks whose submission deadline has not
   passed at the current UTC date. Record the exact time zone.
4. Compare scope fit, evidence expectations, conference cycle, and remaining
   preparation time.
5. Select the best-fitting open CCF-A venue. Do not choose a closed venue merely
   because Argus has a bundled profile for it.

If no suitable open CCF-A venue is verifiable, write the blocker and stop
venue-dependent drafting rather than inventing a target.

## Required artifacts

### `research/VENUE_SELECTION.md`

Record:

- current UTC timestamp;
- paper domain and contribution shape;
- CCF classification source;
- candidate venues;
- official CFP/deadline URLs and time zones;
- open/closed calculation;
- scope fit;
- selected venue and rejection reasons.

### `research/VENUE_PROFILE.json`

Build a flat `VenueProfile` from the selected venue's official author
instructions/kit, including:

- key/display name and year;
- page or word limits;
- column layout;
- required/end/post-reference sections;
- document class, style package/files, review macro, anonymity;
- bibliography behavior;
- forbidden packages;
- required checklist/pdfinfo/line-number rules;
- reviewer and figure persona.

Use official sources. Record uncertainty rather than guessing.

### `paper/TEMPLATE_SOURCE.md`

Record exact official URLs, extracted values, downloaded style-file provenance,
and any unresolved uncertainty.

### Pipeline state

Update only the descriptive `target_venue` field to the selected profile key.
Do not edit `current_stage` or stage statuses.

## Verification

```bash
python -c "from argus_skill.skills.venue_profiles import resolve_venue_profile as r; p=r('.'); print(p.key, p.display_name, p.page_budget_line())"
```

Fetch the official style files under `paper/` and never modify the venue's
`.sty`/`.cls` to make an invalid paper compile.
