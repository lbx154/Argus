# English Style and Anti-AI Patterns (en adapter)

The **English language-adapter** for `fiction_writing`. The narrative core
(characters/world/timeline/plot) is shared and language-agnostic; this file
holds only English-specific style feature definitions and **mechanically
checkable anti-AI patterns**. Chinese lives in `../zh/`.

> Provenance honesty: these are **model-seed** rules (pending corpus grounding).
> Whether a collocation is natural, whether a phrase is an over-used tell, and
> genre register differences should be validated against **COCA** (query/stats
> only — not bulk training) and distilled with sources. See
> `../source_registry/README.md`.

## 1. Style features (values for style_profile; reviewer checks adherence)

- `sentence_rhythm`: short_and_tense / long_and_flowing / varied
- `narrative_distance`: close / mid / distant
- `dialogue_ratio`: high / medium / low
- `imagery_density`: high / medium / low
- `exposition_level`: restrained / moderate / direct
- `ending_strategy`: image_out / open / reversal / stated_moral (avoid the last)

## 2. Mechanically checkable AI tells (→ reviewer proxy metrics; flag, non-blocking)

Regex/string-detectable high-frequency tells; a hit is a prompt for human review,
not an automatic error:

- **Uplift/summary ending**: closing lines like "In the end, …", "And that's when
  I realized …", "Little did she know …", "a testament to …", "reminding us that …".
- **Filter words** (distance the reader): "she felt that", "he saw that",
  "she noticed", "he realized", "she watched as".
- **Telling emotion**: naming "sadness/loneliness/joy/warmth" instead of showing.
- **Adverb-laden dialogue tags**: "he said angrily/softly/knowingly"; prefer
  action beats and plain "said".
- **Purple/cliché**: "a shiver ran down her spine", "time seemed to stand still",
  "the weight of the world", "a single tear rolled down".
- **Throat-clearing connectives** opening sentences: "However,", "Indeed,",
  "In fact,", "That being said,".
- **Em-dash/adverb overuse** and piled synonyms ("beautiful, gorgeous, stunning").

> Implementation: keep an `en` pattern table; the reviewer runs it in the review
> stage and records hits as `note`/`minor` in `review.json` (non-blocking).
> Thresholds/word-lists to be calibrated against COCA.

## 3. Dialogue and mechanics

- One consistent quotation convention; punctuation inside quotes per the chosen
  style (US vs UK) — pick one and hold it.
- Prefer action beats over adverbial tags to carry subtext.
- Vary sentence length deliberately; avoid uniform, evenly-weighted paragraphs.

## 4. Genre vs literary (both en; a profile, not a new vertical)

- Genre/commercial: faster pacing, hooks, cliffhangers, plot-forward.
- Literary: concrete imagery, restraint, indirect emotion, non-summarizing close.
- Both share the same narrative core and continuity checks; only `style_profile`
  values and anti-AI thresholds differ.
