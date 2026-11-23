---
name: verus-query-timing
description: Time individual source-bound loop queries by replaying the supplied Verus module SMT log without changing its solver settings.
---

Use when Verus reports only function-aggregate timing but a maintenance comparison needs each loop's query time and resource count. First run the actual registered source with default settings and observational `--time --time-expanded --output-json --log smt --log-dir EVIDENCE/smt`. Keep JSON, stderr and process time/RSS separately. Do not equate failed resource exhaustion with a false theorem.

Replay the relevant module file using the same supplied Z3:

```bash
python3 <this skill's folder>/replay.py --smt EVIDENCE/smt/MODULE.smt2 --out EVIDENCE/query-replay --z3 "$VERUS_Z3_PATH"
```

The script adds echo markers around each existing check-sat/statistics group, preserves all generated options and query order, records timestamps around checks and resource-count deltas, and associates queries with the original Function-Def/source-span comments. It records full responses plus a concise query listing. This is serialized generated-query replay, not an actual Verus verification result or native per-loop wall time. Compare the same procedure before/after, separately from whole-crate wall and function SMT totals. Never inject raised limits into the selected workflow to obtain a comparison.

The case that caught the need: a base64 decoder campaign: the baseline timing aggregates decode_complete_quads, while its generated module log identifies source loops at decode.rs:284 and :390. Actual default run reports 177/2; replay shows both queries unknown/canceled at exactly 30,000,000 resource units, roughly 1.91 s and 1.67 s. The task-local predecessor `replay-loop-timing.py` produced the retained evidence. This generic script preserves that algorithm; no claim is made that timing is a repeatable constant. Inspect actual generated log format before using it with a different Verus release.

Maintenance case (same decoder): measure the executable-only variant before attempting proof repairs. Computing each decode block start once and using start..start+width made preserved E pass 179/0 with zero ghost edits; the original two queries exhausted 30M each, while the changed queries finished at 28.21M and 9.10M. The same replay command above exposes this sensitivity. Retain the executable-only patch and empty proof-repair patch when no repair is needed; do not infer a maintenance-cost advantage for reorganized proofs from the original baseline failure. Raw reproduction: `.system-proof/t3/measure.sh` and `COMPARISON.md`.

Summarize responses and all moved work with the companion script:

```bash
python3 <this skill's folder>/summarize.py EVIDENCE/query-replay/baseline-loop-queries.json --function decode_complete_quads --function prepare_decode --function canonical_quads_bounds
```

The same decoder's reorganization extracted three checked lemmas and passed 182/0 without executable edits. Include those new queries in comparisons: reporting loop reductions alone would omit moved work. The same campaign's t3 maintenance change also made original E pass 179/0 with zero proof edits, so a baseline failure does not establish a maintenance repair-cost advantage. Compare the same change on both proofs before making that claim. The script prints query response and source span and labels summed serialized costs separately from native function aggregates.
