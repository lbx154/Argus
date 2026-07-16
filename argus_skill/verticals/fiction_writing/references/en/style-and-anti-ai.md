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
- **Frozen moment** (register-level): "the air seemed to freeze/thicken", "the
  world held its breath / stood still".
- **Vague feeling** (register-level): "an odd sense of", "a strange feeling",
  "she couldn't quite place/name/explain it", "something unspoken passed between".

> Implementation: keep an `en` pattern table; the reviewer runs it in the review
> stage and records hits as `note`/`minor` in `review.json` (non-blocking).
> Thresholds/word-lists to be calibrated against COCA. (Implemented as
> `style_lint.py`; hits are non-blocking `ai_tell` notes marked model-seed.)

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

## 5. No copying: the novelty / anti-verbatim twin of the anti-AI rule

"Don't sound like a machine" and "don't copy the human" are one safe corridor:
the laziest way to kill AI flavor is to lean on a real author's sentences, which
is plagiarism. Getting an author's VOICE must come from abstract features, never
from reproducing their prose.

- **Style = abstract features + an explicit lexicon, never "imitate author X"**.
- A continuation/adaptation must supply `fiction/reference_text.md` (the source
  text). At review, `novelty-check` (`style_check.py`) measures verbatim overlap:
  - a long verbatim run (default ≥12 words; tighten via
    `style_profile.novelty_budget.max_verbatim_run`) is a **BLOCKING**
    `verbatim_copy` finding — a run this long in both texts is a deterministic fact;
  - medium runs / aggregate overlap ratio are non-blocking notes (unless
    `max_overlap_ratio` is declared and exceeded);
  - thresholds are model-seed and set high, so legitimate short quotation/allusion
    passes; paraphrase-level plagiarism is not machine-reliable, so it stays
    reviewer guidance — we never fake a similarity score.
- With no `reference_text.md` (an original, not a continuation) the gate passes.
