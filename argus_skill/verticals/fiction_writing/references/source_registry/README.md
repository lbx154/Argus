# Source Registry & Knowledge-Base Plan (fiction_writing)

How `fiction_writing` acquires real literary knowledge **without** reinventing a
corpus/RAG stack and **without** license risk. This is the plan; ingestion is a
separate, tracked track that must NOT block the vertical's first end-to-end demo.

## Principle: ride Argus's existing machinery, don't rebuild it

Argus already ships the pieces a "knowledge base" needs:

- the **`wiki`** store — immutable `sources` (write-once) + verbatim
  **evidence-span** verification (anti-fabrication);
- the **`learning`** vertical — ingest operator material → distill into
  skills/wiki cards, each carrying `{source_id, locator, quote}`.

So the fiction knowledge base = **curated `references/`** (craft cards +
checkable anti-AI patterns, this directory) **+ this `source_registry`**
(rights/provenance) **+ using the `learning` vertical to distill licensed
corpora into evidence-anchored craft cards**. No parallel RAG subsystem.

## Four layers (each serves a different capability)

1. **Structure/technique — public-domain works.**
   - en: Project Gutenberg (public-domain; per-text rights still verified).
   - classical zh: ctext.org (CLASSICAL only — NOT a modern-fiction source).
   - Use: distill technique cards (viewpoint, scene turns, dialogue), not to clone.
2. **Language naturalness — modern corpora, QUERY/STATS ONLY (no bulk training).**
   - zh: BCC (modern Chinese). en: COCA. Use to validate collocations, register,
     and to calibrate the anti-AI pattern thresholds in `../zh|en/`.
3. **Craft "why it works" — criticism/narratology (licensed).**
   - Narratology/creative-writing texts, journal articles. Distill into
     `references/shared/narrative-craft.md` cards WITH a bibliographic source.
4. **Modern genre + continuation — self-built LICENSED sample set.**
   - Author-permitted works, team-authored samples, commissioned/licensed text,
     open-licensed works. NEVER scrape in-copyright/for-sale web-fiction to
     train or clone a living author's style.

## Rights discipline (matches the repo's provenance-hygiene bar)

Every source recorded in `sources.yaml` MUST carry: `title, author, language,
period, genres, source_type, rights_status, allowed_uses, prohibited_uses,
retrieved_at, checksum`. Corpora used for "naturalness" are `query_or_stats_only`.
Anything model-seeded is marked so and is NOT presented as sourced until a real
source with a verbatim span replaces it. Pin fixed versions/commits + checksums.

## Extraction: three separate products (never mixed)

- **facts** → wiki cards (era, place, profession, objects) for grounding;
- **technique cases** → craft cards in `references/shared/`;
- **language evidence** → anti-AI/style calibration in `references/zh|en/`.

Mixing them is what turns "retrieval + technique + style" into one indistinct
blob — keep them apart.

## Week-1 vs later (honest scoping)

- **Week-1 (this track):** this registry + a SEED set of craft cards and anti-AI
  patterns (model-seed, clearly marked) + the evaluation fixtures. The vertical's
  first end-to-end demo does NOT depend on a fully ingested corpus.
- **Later (its own track):** run the `learning` vertical over the layer-1/2/3
  sources to replace seed cards with evidence-anchored ones. Target (not a
  week-1 gate): ≥100 sourced craft cards, ≥200 zh/en language counter-examples,
  ≥20 continuation-consistency eval cases — each traceable to a source or a
  human-authored record.

## Note on the databases

GPT proposed Gutenberg/ctext/BCC/COCA; adopted with two corrections: ctext is
classical-only (wrong for modern zh fiction), and there is little clean OPEN
modern-zh fiction — so modern zh relies on the self-built licensed set (layer 4)
plus BCC for naturalness only. The list is not exhaustive; add sources only with
a completed `sources.yaml` rights entry.
