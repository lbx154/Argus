# Website Results Evidence Bundle

Machine-readable, public-safe snapshots of the results and paper collection
published on `argusbot.cn`, for use by the README and the technical report.

## Files

- `website_results.json` — the six result cards from
  `https://argusbot.cn/results.html`, each with `arena`, `protocol`,
  `result`, `human_comparison`, verbatim `website_quote`, `source_url`, and
  `evidence_tier`. Includes public-safe local corroboration for the two results
  that have reproducible on-disk artifacts.
- `paper_inventory.json` — the 41 de-duplicated research cards from
  `https://argusbot.cn/research.html` (35 manuscripts + 6 drafts across six
  research programs), each with title, program, status, page count, and page link.

## Retrieval

- Retrieved (UTC): `2026-07-14T12:29:51Z`
- `results.html` — HTTP 200 — raw HTML SHA-256
  `5143a58774c513ec7917a508837d2992fb950f34923d34f2a5c85449900f38bf`
- `research.html` — HTTP 200 — raw HTML SHA-256
  `e701dd69d3c2b0f117bac3be98d547611a81bcceb06403f599510f7e78f38e2f`

## Comparison rule

In public docs the primary comparison column is **human SOTA**, a human-authored
public record, or the **paper-reported best** where the website provides one.
Recursive is referenced **only** for the SOL-ExecBench head-to-head fact already
published on the site. The 41-paper collection is compared to human-authored
literature/baselines only — paper count and paper quality are **not** compared to
any other agent or model.

## Evidence tiers

- `website_snapshot` — value quoted verbatim from the live public website.
- `local_artifact` — additionally corroborated by an on-disk reproduction
  artifact identified by logical project + artifact ID + SHA-256 (no local
  absolute paths). Present for nanoGPT speedrun (79.77s, N=10) and nanochat B200
  (0.963634 MEAN_VAL_BPB). The remaining four results carry
  `corroboration: website_snapshot`; no corroboration file was fabricated where a
  clean local artifact was not available.

## Provenance and safety

These files record only public website text, retrieval metadata, and SHA-256
digests plus logical artifact IDs. They contain no API key, credential, base URL
secret, local absolute path, username, session ID, or vault pointer. To refresh,
re-fetch both pages, recompute the raw-HTML SHA-256, and re-parse (six results;
41 papers = 35 manuscript + 6 draft).
