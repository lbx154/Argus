# CAPABILITY_REGISTRY_DESIGN

`argus_skill/skills/capability_registry.py` — the single interface the physics
research gates use to read capabilities. Gates depend only on this interface,
never on a file path.

## Goals
- Run standalone in tests/CI with **no external library** (in-source base is the default).
- Read the large external **distilled library** (55 caps from 223 papers) **read-only**
  when present, without vendoring it into source and without ever mutating it.
- Preserve **provenance** on every capability.
- Let future distillations **append/version** without touching existing gates.
- **No hardcoded absolute paths** in source; **fail-open with diagnostics** on a
  malformed external library.

## `Capability` schema
```
capability_id, name, family, group, domains, is_generic, gates,
applicability, basic_criteria, advanced_criteria, hard_fail, metric, pass_threshold,
paper_evidence_refs,            # supporting paper evidence (provenance)
version,                        # provenance
source_path, source_layer       # provenance: which file / which layer
```
`source_layer ∈ {base, external, overlay}`.

## Load order (later augments/overrides earlier, keyed by `capability_id`)
1. **base** — `argus_skill/verticals/physics/capabilities/*.json` (version-controlled,
   always present). Base ids use a distinct namespace (e.g. `CAP-LITBASE-*`) so they
   never collide with external ids.
2. **external distilled** — READ-ONLY. Path resolution:
   - primary: env var `ARGUS_SKILL_PHYSICS_CAPABILITY_LIB`
     (empty string explicitly **disables** external);
   - else: best-effort **relative** discovery — walk the worktrees dir + workspace
     root for a sibling `PHYSICS_CAPABILITY_TRACE_V2.json` /
     `PHYSICS_CAPABILITY_SYNTHESIS_FROM_223.json`. **No absolute literals in source.**
   - `_normalize_external()` maps the distilled TRACE_V2 schema → `Capability`
     (family code extracted from `"I. Literature ..."` → `"I"`; `metric`/`pass_threshold`/
     `hard_fail_conditions`/`extracted_research_actions`/`full_text_supporting_refs`
     carried into the fields incl. `paper_evidence_refs`; `source_layer="external"`).
3. **project overlay** — optional `research/CAPABILITY_OVERLAY.json` for one run
   (`source_layer="overlay"`), for per-project append/override.

## Merge / provenance rules
- Union by `capability_id`; a later layer overriding the same id records its own
  `source_layer`/`source_path`. Base ids are namespaced so base and external
  coexist rather than clobbering each other.
- Every capability keeps `source_path`, `source_layer`, `paper_evidence_refs`,
  `version`, queryable via `sources()`.

## Failure handling (fail-open + diagnostics)
- external file **missing** → base only + a `not found` diagnostic.
- external file **malformed** (invalid JSON / no `capabilities[]`) → base kept,
  external dropped, a `malformed ... ignored (base kept)` diagnostic recorded in
  `diagnostics()`. Never crashes; never silently swallows.

## Query interface (what gates call)
`load()`, `all()`, `get(id)`, `by_family(f)`, `by_domain(d)`,
`for_gate(gate_id)` (matches `gates` list OR the `FAMILY_GATES` family→gate map,
e.g. family `I`→`literature`, `F`/`G`→`numerical`, `D`/`E`→`theory`, `L`→`novelty`),
`sources()`, `diagnostics()`.

## Extending with future distilled capabilities
- Add JSON to the in-source base dir (version-controlled), **or**
- point `ARGUS_SKILL_PHYSICS_CAPABILITY_LIB` at a new external distilled file, **or**
- drop a `research/CAPABILITY_OVERLAY.json` for a single project.

Existing gates are unaffected because they depend only on `for_gate(...)` /
`by_family(...)`, never on a path or on specific capability ids.

## Hermetic tests
Tests pass `external_path=` explicitly (a fixture path, or `None` to disable), so
they never depend on a machine path or the real distilled library — covering:
base-only run, external merge with provenance, malformed-external fail-open,
missing-external fallback, env-empty disable, read-only guarantee, project overlay.
