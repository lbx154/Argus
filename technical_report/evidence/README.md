# Technical Report Data Bundle

Machine-readable snapshots used to generate the technical report's empirical
tables and figures.

## Files

- `website_results.json` — the six result cards from
  `https://argusbot.cn/results.html`, each with `arena`, `protocol`,
  `result`, `human_comparison`, verbatim `website_quote`, `source_url`, and
  `evidence_tier`. Includes public-safe artifact-digest corroboration for two
  results whose artifacts live in named external project workspaces.
- `paper_inventory.json` — the 41 de-duplicated research cards from
  `https://argusbot.cn/research.html` (35 manuscripts + 6 drafts across six
  research programs), each with title, program, status, page count, and page link.
- `swebench_pro/` — the unified 731-task Direct-Copilot/Argus summary and the
  Argus-only 22-Wave Token/time record used for longitudinal convergence analysis.
  See `swebench_pro/README.md` for metric boundaries and the PowerPoint regeneration
  command.
- `erdos_trace/` — one frozen vertical mathematical trace, including its
  claim-level source table, claim--evidence map, figure brief, and provenance.
- `process_theory/` — claim--evidence map for process-data dominance, review-gated
  correction, reuse value, and verified reusable Token yield. It records the
  official website concept snapshot and the current empirical support boundary.

## Retrieval

- Retrieved (UTC): `2026-07-14T12:29:51Z`
- `results.html` — HTTP 200 — raw HTML SHA-256
  `5143a58774c513ec7917a508837d2992fb950f34923d34f2a5c85449900f38bf`
- `research.html` — HTTP 200 — raw HTML SHA-256
  `e701dd69d3c2b0f117bac3be98d547611a81bcceb06403f599510f7e78f38e2f`

## Comparison rule

In public docs **human SOTA**, a human-authored public record, or the
**paper-reported best** is primary where the website provides one. SOL-ExecBench
and Arbor preserve the site's published system/agent comparisons as secondary or
source-verbatim context. The 41-paper collection is compared to human-authored
literature/baselines only — paper count and paper quality are **not** compared to
any other agent or model.

## Evidence tiers

- `website_snapshot` — value quoted verbatim from the live public website.
- `local_artifact` — additionally corroborated by committed metadata identifying
  an artifact in a named external project by logical project + artifact ID +
  SHA-256 (no local absolute paths and no artifact bytes in this repository).
  Present for nanoGPT speedrun (79.77s, N=10) and nanochat B200 (0.963634
  MEAN_VAL_BPB). The remaining four results carry
  `corroboration: website_snapshot`; no corroboration file was fabricated where a
  source artifact digest was not available.

## Provenance and safety

These files record only public website text, retrieval metadata, and SHA-256
digests plus logical artifact IDs. They contain no API key, credential, base URL
secret, local absolute path, username, session ID, or vault pointer. To refresh,
re-fetch both pages, recompute the raw-HTML SHA-256, and re-parse (six results;
41 papers = 35 manuscript + 6 draft).
