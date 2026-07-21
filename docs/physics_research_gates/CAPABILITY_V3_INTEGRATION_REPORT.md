# Capability Library V3 — A/B Integration Report

**Date:** 2026-07-14
**Verdict:** ✅ PASS — the B-side V3 library loads through `CapabilityRegistry` and
routes correctly to every gate. One **schema adapter** was added on the A side; the
B-side library was **not modified**.

## 1. What was verified

| # | Task | Result |
|---|------|--------|
| 1 | `ARGUS_SKILL_PHYSICS_CAPABILITY_LIB` → V3 file | ✅ resolved (env var, no path literal in source) |
| 2 | Load via `CapabilityRegistry` | ✅ 194 external caps loaded, 0 diagnostics |
| 3 | Counts theory≈90 / numerical≈90 / novelty≈14 | ✅ **90 / 90 / 14** (exact) |
| 4 | `for_gate("theory"/"numerical"/"novelty")` correct | ✅ routes exactly 90 / 90 / 14 external caps |
| 5 | Load/classification failure → normalizer, not B-side edit | ✅ V3 schema adapter added on A side only |
| 6 | This report | ✅ this file |
| 7 | Tests | ✅ registry + all 5 gate suites green |
| 8 | No push, no merge | ✅ local commit only |

## 2. B-side library under test

- **Path:** `_argus_review/capability_distillation_runs/physics_capability_distill_v3/PHYSICS_CAPABILITY_TRACE_V3.json`
- **Schema:** `physics_capability_trace_v3` (`version: v3`, `run_id: physics_capability_distill_v3`)
- **Size:** 1.8 MB, `capabilities[]` length **194**.
- **B-side `counts`:** total 194 — theory 90, numerical 90, novelty 14 (generic_theory 30, generic_numerical 30).
- Read **strictly read-only**; a byte-for-byte equality test asserts the registry never writes it.

## 3. Root cause: V3 ≠ TRACE_V2 schema (classification was broken before the adapter)

The pre-existing external normalizer (`_from_external_record`) was written for the
**TRACE_V2** schema, which routes by a **lettered** `capability_family` (`A`…`L`) mapped
to gates via `FAMILY_GATES`. V3 has **no** `capability_family`; instead each record
declares its gate directly:

| Routing / field | TRACE_V2 (old) | V3 (new) |
|---|---|---|
| gate routing | `capability_family` letter → `FAMILY_GATES` | `capability_type` = `theory`/`numerical`/`novelty` (== `family`) |
| domain | `source_domains[]` | `domain` + `domain_gate` (`*` = generic) |
| applicability | `why_generalizable` | `applicability_question` |
| basic tier | `extracted_research_actions[0]` | `basic_standard` |
| pass threshold | `pass_threshold` | `publishable_standard` / `strong_standard` |
| hard-fail | `hard_fail_conditions[]` | `hard_fail_indicators[]` / `failure_codes[].code` |
| paper evidence | `full_text_supporting_refs[]` | `source_evidence[].{source_id,url_or_doi,title}` |

**Before the adapter** (measured): all 194 records loaded (they have `capability_id`),
but `family` resolved to `""`, so `FAMILY_GATES.get("") == ()` and **`for_gate` routed 0
external caps** — `for_gate("theory")` returned only the 6 base caps, etc. This is the
"分类不对" the task anticipated.

## 4. Fix: V3 schema adapter (A side only)

Added to `argus_skill/skills/capability_registry.py` — **no big feature change, no
B-side edit**:

1. `_pick_external_normalizer(data, records)` — detects the schema and dispatches:
   - top-level `schema` starts with `physics_capability_trace_v3` → V3 adapter;
   - fallback: a schema-less export whose first record has `capability_type`
     (or `family`) in {literature, theory, numerical, novelty} and **no**
     `capability_family` → V3 adapter;
   - otherwise → the existing TRACE_V2 adapter (unchanged, still tested).
2. `_from_v3_record(rec, path)` — maps a V3 record → `Capability`, setting
   `gates = (capability_type,)` so routing is **direct** and does not depend on the
   lettered `FAMILY_GATES` map. `domain_gate == "*"` → `domains` contains `*` so generic
   caps match any `by_domain(...)` query. Provenance (`source_path`, `source_layer`,
   `paper_evidence_refs`, tier standards) is preserved.
3. `PHYSICS_CAPABILITY_TRACE_V3.json` added to the best-effort discovery filenames
   (env var still primary; no absolute path literal in source).

The `sources()` string now tags the schema, e.g.
`external:PHYSICS_CAPABILITY_TRACE_V3.json[v3](194)`.

## 5. After-the-adapter measurements (real V3 library)

```
total caps: 221  (194 external V3 + 27 in-source base)
sources:    ['base:literature.json', 'base:novelty.json', 'base:numerical.json',
             'base:theory.json', 'external:PHYSICS_CAPABILITY_TRACE_V3.json[v3](194)']
diagnostics: (none)

for_gate('theory'):    total= 96   external=90   base= 6   [expect 90 ✅]
for_gate('numerical'): total=100   external=90   base=10   [expect 90 ✅]
for_gate('novelty'):   total= 20   external=14   base= 6   [expect 14 ✅]
for_gate('literature'):total=  5   external= 0   base= 5   [V3 ships no literature caps]
```

Per-gate external caps match the B-side `counts` **exactly**. The in-source base library
is still merged underneath, so gates keep working with no external library present
(tests/CI). Literature caps are base-only (V3 intentionally covers theory/numerical/novelty).

**Domain routing** (external theory example): 10 domains × 6 caps + 30 generic (`domain_gate = *`)
= 90. Generic caps carry `*` and are returned by `by_domain(<any>)`.

**Provenance spot-check** (`CAP-THEORY-AMO-001`): `source_layer=external`,
`source_path=…/PHYSICS_CAPABILITY_TRACE_V3.json`, `gates=('theory',)`,
`pass_threshold` = the `publishable_standard` text, `paper_evidence_refs=('clerk-rmp-2010', …)`.

**End-to-end gate smoke** (env var set, empty project → advisory failures): the theory
gate REVIEW lists all 90 external theory caps by id + name + pass threshold; novelty
REVIEW lists all 14; no crash on the larger capability set.

## 6. Robustness preserved (unchanged guarantees)

- **Fail-open:** a malformed external library is skipped with a diagnostic; base kept (tested).
- **Missing external:** falls back to base with a "not found" diagnostic (tested).
- **Read-only:** V3 file is never written (byte-equality test, tested).
- **TRACE_V2 still works:** the old adapter path is retained and still routes family-letter caps.

## 7. Tests

`tests/skills/test_capability_registry.py` — 12 passed (7 pre-existing + 5 new V3):
`test_v3_library_routes_by_capability_type`, `test_v3_record_normalisation_and_provenance`,
`test_v3_generic_capability_matches_any_domain`, `test_v3_detected_without_schema_field`,
`test_v3_never_writes_external_library`. All V3 tests are **hermetic** (a fixture written
into `tmp_path`, `external_path` passed explicitly — no dependency on the machine path).
The literature/theory/numerical/novelty/paper-type gate suites remain green.

## 8. Scope / constraints honored

No large feature change: only the registry gained a schema adapter + two new discovery
filenames. The B-side V3 library was not modified. No push, no merge — local commit only.
