# Corpus Ingestion Architecture (fiction_writing)

Status: **design + scaffold only.** Real ingestion is BLOCKED on authorized
source material (§4). Nothing here fetches, stores, or fabricates text. The
runnable contract lives in `ingest.py` (+ `schemas/craft_card.schema.json`); the
downstream threshold calibration lives in `evaluations/calibrate_novelty.py`.

## Goal & the hard constraint

Improve craft (quality) WITHOUT ingesting liftable prose (the ¬抄 leg). So the
pipeline distills **abstract technique**, never text — the same principle the
voice cards and the `novelty` gate already enforce. A `craft_card` carries a
technique described in the ingester's own words plus evidence *locators* and a
length-capped paraphrase note; it can never reconstruct a source passage.

## Rides existing argus infrastructure (no new store)

- **Rights / provenance:** `sources.py` + `references/source_registry/` define
  input authorization (a source's `allowed_uses`). Ingestion consumes
  only rights-cleared sources; `plan_ingestion()` refuses when none are authorized.
- **Distillation:** the `learning` vertical already turns material into
  evidence-backed skills/wiki — ingestion is a fiction-flavored use of it, not a
  new engine.
- **Immutable evidence:** argus wiki's source + evidence-span model holds the
  locators a `craft_card` points at.

## Four layers (`INGESTION_LAYERS` in `ingest.py`)

| Layer | Source kind | Allowed use |
|-------|-------------|-------------|
| `public_domain_study` | Gutenberg / ctext (public domain) | study technique → distill abstract craft cards |
| `modern_corpus_retrieval` | BCC / COCA (licensed) | **retrieval-only** naturalness lookup — never ingest verbatim |
| `criticism_narratology` | scholarship on craft/narratology | distill craft cards with attribution |
| `authorized_samples` | self-authored / licensed modern samples | genre exemplars for modern verticals |

The `modern_corpus_retrieval` layer is deliberately query-only: it informs a
naturalness signal, it does not deposit copyrighted text into the store.

## Data flow

```
authorized source (rights-cleared)
   -> evidence spans (locator + short note, via argus wiki)
      -> distill_fn (learning vertical / LLM)          # injected, never built here
         -> craft_card  (validate_craft_card, schema)  # abstract, no liftable prose
            -> voice/craft guidance at draft time
```

`craft_card` (contract in `craft_card.schema.json`): `id, title, technique,
language, abstracted:true, evidence[{source_id, locator, note≤200}], rights{source_id,
allowed_use}`. `abstracted:true` and the note length cap are the machine-checkable
anti-copy boundary.

## What is done vs blocked

- **Done (data-free):** the contract (`craft_card` schema), `validate_craft_card`,
  the layered `plan_ingestion` with an honest BLOCKED path, and `distill_card`
  (validates an injected distiller's output, fabricates nothing). Tested.
- **Blocked (needs real inputs / a decision):** registering concrete sources with
  `allowed_uses`, wiring the `learning`-vertical distiller, and running it. Also
  upstream: the leader decides which sources are in scope and whether the modern
  corpora are licensed for retrieval.

## Ties to the rest

Distilled craft cards feed the same draft-time voice guidance the voice cards do;
a labelled sample of ingested-vs-original continuations is exactly the corpus the
`calibrate_novelty` harness needs to replace the model-seed novelty thresholds
with measured ones. Ingestion and calibration are the two halves of the same
"real data" phase.
