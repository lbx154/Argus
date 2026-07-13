---
name: Creative Brief And Style Profile
description: Turn an operator's fiction request into a structured creative_brief.json (language, form, mode, genre, market_style, length, viewpoint, tense, constraints) and a checkable style_profile.json of ABSTRACT features — never "imitate author X". The intake stage of fiction_writing.
category: fiction-intake
version: 1
protected: true
---

## Title
Creative Brief And Style Profile

## Description
Normalize a free-text fiction request into two machine-readable artifacts that
every later stage consumes: a `creative_brief.json` (the task contract) and a
`style_profile.json` (abstract, checkable style features). This removes guesswork
downstream and makes "did the draft match the ask?" a checkable question.

## Category
fiction-intake

## When to use
- The `intake` stage of the `fiction_writing` vertical, before any planning.
- Whenever a fiction request must be pinned down: language, length, viewpoint,
  tense, genre/market style, and hard constraints.
- For a continuation, to record that mode=continuation and bind to an existing
  `story_state` as ground truth.

Do NOT use for research/paper intake, literature review scoping, or non-narrative
tasks — this is narrative-fiction request normalization only.

## How to solve
1. **Read the request** and extract, asking the operator only for what is truly
   missing (bias to sensible defaults, record them):
   - `language` (`zh`|`en`), `form` (short_story|chapter|scene),
   - `mode` (`from_scratch`|`continuation`),
   - `genre` (suspense|romance|scifi|literary|realism|…) and `market_style`
     (web_fiction|literary|genre|…) — these are PROFILES, not new verticals,
   - `length` (target words), `viewpoint` (first|third_limited|third_omni),
     `tense` (past|present), `constraints[]` (must/avoid).
   Write `fiction/creative_brief.json`.
2. **Build the style profile as ABSTRACT FEATURES**, not author imitation:
   `sentence_rhythm`, `narrative_distance`, `dialogue_ratio`, `imagery_density`,
   `exposition_level`, `emotional_expression`, `ending_strategy`. Each value is a
   small enum/scale so the reviewer can later CHECK adherence. If the operator
   named an author, translate the *effect* into these features and note the
   translation — do not set a goal of mechanically reproducing that author.
   Write `fiction/style_profile.json`.
3. **For a continuation**, load the existing `story_state` and confirm the brief
   is consistent with it (language, viewpoint, established facts); never invent a
   fresh state when one exists.
4. **Pin the language adapter**: the brief's `language` selects the zh or en
   style/anti-AI reference set for later stages.

## When NOT to use
- Research/literature-review intake (route to the research vertical).
- Deriving style from an in-copyright author with intent to clone them.

## Common pitfalls
- Leaving viewpoint/tense implicit — later drift becomes unprovable.
- Encoding "write like <author>" verbatim instead of abstract features.
- Inventing a story_state for a continuation instead of loading the real one.
