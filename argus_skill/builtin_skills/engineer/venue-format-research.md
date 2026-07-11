---
name: Venue Format Research
description: "Research a NON-STANDARD publication venue's official format online and build a research/VENUE_PROFILE.json the pipeline can grade against. Use when the target venue is not one of the built-in venues (EMNLP/ACL, AAAI) — e.g. NeurIPS, ICML, ICLR, CVPR, ACL-Findings, a workshop — so the paper is drafted and reviewed against the RIGHT venue instead of the EMNLP default. Covers page limits, LaTeX style kit, mandatory sections, bibliography rules, and downloading the official .sty/.bst."
category: paper-writing
version: 1
created_at: 2026-07-10T00:00:00+00:00
---

## Title
Venue Format Research

## Description
The harness ships verified format profiles only for EMNLP/ACL and AAAI. For any
other `target_venue`, this playbook has you **web-search the venue's official
submission instructions / author kit** and distill them into
`research/VENUE_PROFILE.json` — the single file the rest of the pipeline
(reviewer checklists, layout review, structural-minimum checks, figure prompts)
reads to grade a paper against the correct venue. It also fetches the official
LaTeX style files so the draft compiles in the real template.

## When to use
- `research/PIPELINE_STATE.json`'s `target_venue` is a venue that is NOT a
  built-in (i.e. not EMNLP/ACL/ARR/Findings and not AAAI) — e.g. `NeurIPS`,
  `ICML`, `ICLR`, `CVPR`, `KDD`, a specific workshop.
- No `research/VENUE_PROFILE.json` exists yet (this runs once per project; the
  file is cached).

## When NOT to use
- The target venue is EMNLP/ACL or AAAI — the built-in profile is authoritative;
  do not override it.
- `research/VENUE_PROFILE.json` already exists and matches the target venue.

## How to solve

1. **Identify the venue** from `target_venue` and the objective (name + year).

2. **Web-search the OFFICIAL source** (mandatory grounding — do not guess from
   memory). Prefer, in order: the venue's official Call-for-Papers / author
   instructions page, the official LaTeX author kit / Overleaf template, then a
   clearly-labelled community mirror. Extract the format facts below. If the
   sources conflict or a fact cannot be confirmed, record the uncertainty in
   `paper/TEMPLATE_SOURCE.md` and pick the most official value.

3. **Write `research/VENUE_PROFILE.json`** — a flat JSON object with these
   fields (fill every format-critical one; omit a field to accept its default):

   | field | meaning | example (NeurIPS-like) |
   |-------|---------|------------------------|
   | `key` | UPPERCASE canonical key (matches `target_venue`) | `"NEURIPS"` |
   | `display_name` | human label incl. year | `"NeurIPS 2026"` |
   | `body_page_limit` | pages of technical content | `9` |
   | `conclusion_max_page` | Conclusion must land by this page (= body limit) | `9` |
   | `conclusion_underfill_page` | before this page ⇒ underfilled body (usually body-1) | `8` |
   | `references_min_page` | References start on/after this page (usually body+1) | `10` |
   | `mandatory_end_sections` | sections REQUIRED after Conclusion (e.g. `["Limitations"]`); `[]` if none | `[]` |
   | `post_reference_sections` | sections allowed AFTER References | `["Checklist","Appendix"]` |
   | `documentclass` | LaTeX documentclass line | `"\\documentclass{article}"` |
   | `style_package` | `\usepackage{...}` style name | `"neurips_2026"` |
   | `style_files` | files to fetch/compile with | `["neurips_2026.sty"]` |
   | `style_clone_url` | official kit / template URL | `"https://neurips.cc/..."` |
   | `review_mode_macro` | anonymous-review usepackage line | `"\\usepackage{neurips_2026}"` |
   | `anon_author_string` | anonymous author block text | `"Anonymous Author(s)"` |
   | `bib_style` | bibliography style name | `"plainnat"` |
   | `emit_bibliographystyle` | true unless the style sets it itself | `true` |
   | `forbidden_packages` | packages that cause desk-reject (e.g. AAAI's hyperref) | `[]` |
   | `requires_style_package` / `requires_pdfinfo` / `requires_reproducibility_checklist` | booleans if the kit demands them | `false` |
   | `reviewer_persona` | venue name used in reviewer prompts | `"NeurIPS"` |
   | `figure_style_persona` | venue family used in figure prompts | `"NeurIPS"` |
   | `abstract_word_floor` / `abstract_word_floor_is_hard` | abstract length policy | `150`, `false` |

   Write it atomically, e.g.:
   `python -c "from argus_skill.skills.venue_profiles import VenueProfile, write_venue_profile; import json,sys; write_venue_profile('.', VenueProfile.from_dict(json.load(open('/tmp/vp.json'))))"`
   (or just write valid JSON directly to `research/VENUE_PROFILE.json`). Confirm
   it loads: `python -c "from argus_skill.skills.venue_profiles import resolve_venue_profile; print(resolve_venue_profile('.').key, resolve_venue_profile('.').page_budget_line())"`.

4. **Fetch the official style files** into `paper/` (mirror the AAAI discipline):
   download the OFFICIAL kit for `style_files`; if only a community mirror is
   reachable in this environment, use it to compile locally but you MUST diff it
   against the official file before declaring submission-ready — a modified
   style sheet is desk-rejected. Never hand-edit the `.sty`.

5. **Record provenance** in `paper/TEMPLATE_SOURCE.md`: the exact URLs used, the
   values extracted, `source: official | mirror (unverified)`, and any facts you
   could not confirm. This is what a later reviewer / preflight relies on.

## Notes
- This profile is authoritative for the rest of the pipeline; if you get the
  page limit or mandatory sections wrong, the paper is graded (and possibly
  submitted) wrong. Prefer official sources and record uncertainty honestly.
- To re-research (e.g. wrong venue captured), delete `research/VENUE_PROFILE.json`
  and rerun.
