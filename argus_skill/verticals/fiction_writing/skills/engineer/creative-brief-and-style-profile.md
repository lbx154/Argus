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
2. **Build the style profile as a VOICE CARD** conforming to
   `schemas/style_profile.schema.json` — abstract features, never author imitation.
   Fill what the work needs (every field is optional; a thin card is valid):
   - `abstract_features`: `sentence_rhythm`, `narrative_distance`, `dialogue_ratio`,
     `imagery_density`, `exposition_level`, `emotional_expression`,
     `ending_strategy` — each a small enum so the reviewer can CHECK adherence;
   - `meta.register` (classical|literary|contemporary|web|colloquial) and
     `lexicon.appellations` (称谓表: how each referent is addressed, by whom);
   - `forbidden_lexicon` — words that must NOT appear (a HARD contract: a hit is a
     BLOCKING finding). For a continuation this is where anachronisms live — e.g.
     a classical-register work forbids `手机`/`地铁`/`OK`;
   - optional `sentence_targets` (mean length band, parallelism_ok),
     `dialogue_conventions`, and `ai_tell_budget.max_hits_per_1000_chars`.
   If the operator named an author, translate the *effect* into these features and
   note the translation — do not set a goal of mechanically reproducing that
   author. Write `fiction/style_profile.json`.
   - **Start from the library, don't build from scratch.** Compose the card as
     `base` ← domain preset ← your work/character overlay (see
     `references/voice_cards/` and `style.compose_voice_card` /
     `voice_card_from_brief`). `domain_for_brief` picks the domain preset from the
     brief's genre (悬疑→suspense, 红楼/章回→classical_zhanghui, 网文→web_fiction, …)
     — the DETERMINISTIC half of building the card from the prompt.
   - **Author the per-character voices** (`character_voices`): read the prompt's
     cast and give each one a register / verbal tics / diction / words-forbidden-
     from-this-mouth — '什么样的人物什么卡'. This is the half only you can do from
     the prompt; the classical preset ships 黛玉/宝玉/凤姐/刘姥姥 as worked examples.
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
