# Seed provenance

The calendar and topic catalogs are self-contained materialized snapshots derived from
the sibling `research_roadmap/data` deliverable so Flywheel can be deployed independently.
Generation-time source filenames, byte sizes and SHA-256 hashes remain in the catalogs
as provenance labels; they do not claim those sibling fragment files are bundled. The
domain evidence contract is a Flywheel-owned adapter keyed to the same category IDs.

- `conference_calendar_2026-08-22_2027-08-22.json`: 58 CCF-A venues and all
  Full/Regular Paper point targets in the inclusive planning window. Forecast values
  remain forecasts and require official confirmation. PPoPP, NDSS and SIGKDD have
  uncertainty bands whose upper edge extends past 2027-08-22; they are included because
  their point planning estimate is inside the requested window, not because the actual
  future deadline is known to fall inside it.
- `topics_all_58x5.json`: five candidate topic seeds per venue (290 total). They are
  hypotheses for portfolio screening, not verified novelty claims or promised papers.
  The static exporter binds these 58 × 5 seeds to each venue's earliest planning
  target; it does not produce five new topics for each of the 85 deadline/round events.
- `domain_evidence.json`: Flywheel-authored domain-specific minimum evidence contracts
  consumed by the Prompt Factory; these are internal gates, not official venue rules.

The upstream calendar/topic documents contain their generation date, upstream URLs,
tracker commit and provenance hashes. Runtime source refreshes must create a new
snapshot and delta; they must not rewrite these seed files or a locked campaign.

The calendar source attribution and license are preserved in
[`../../THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md).

`topics_all_58x5.json` retains the upstream ideation constraint
`4 x NVIDIA A6000 48GB`. That string documents the environment assumed while drafting
the ideas; it is neither detected inventory nor authorization to spend that compute.
Prompt preview/start must replace it with an explicitly configured runtime resource
contract. The unconfigured seed resource is intentionally blocked from launch.
