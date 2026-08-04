---
name: "Continuity, Style and Plot Review"
description: "Gate a fiction chapter/story. Blocks on hard narrative contradictions (dead character returns, impossible knowledge, item teleport, location/timeline clash, world-rule break, motive-incoherent action, dropped/leaked foreshadowing, viewpoint/tense/language drift); records craft and AI-flavor issues as non-blocking, evidence-located observations — never a faked numeric score."
---

## Title
Continuity, Style and Plot Review

## Description
Review a fiction_writing draft (or final) against the established `story_state`.
Separate **blocking continuity contradictions** from **non-blocking craft
observations**. Every finding is typed, severity-tagged, and cites an evidence
location. Do not invent a mysterious overall "literary score".

## Category
fiction-review

## When to use
- Reviewing a chapter draft or final in the `fiction_writing` vertical
  (`draft` / `review` / `revise` stages).
- Checking a continuation for consistency with prior chapters via `story_state`.
- Deciding whether a chapter may advance, must be revised, or is blocked.

Do NOT use for research/literature-review missions, paper drafting, code review,
or poetry prosody — this skill is about narrative prose consistency and craft.

## How to solve
1. **Load the ground truth.** Read `story_state.json` (characters, their
   `status` and `knows`, relationships, world_rules, locations, items and their
   holder/location, timeline `order`, open_threads, foreshadowing) and the
   brief/style_profile. For a continuation, the state — not your memory — is
   authoritative.
2. **Continuity pass — EVERY item below is `blocking: true`.** These are
   hard contradictions with the ground-truth state, not matters of taste; do not
   downgrade any of them (impossible knowledge and language drift are BLOCKING
   too). For each, cite the exact draft span + the state fact it violates:
   - a character marked `dead`/`absent` acts on-stage without an explained return;
   - a character uses information not in their `knows` and never learned on-page;
   - an item's holder/location contradicts `items` without a move on-page (teleport);
   - a character is in two places at one timeline `order`;
   - events violate timeline `order`, or a stated `world_rule`;
   - an action contradicts the character's established `motivation` with no turn;
   - a `planted` foreshadow is dropped, or paid off before it was planted / leaked;
   - viewpoint or tense drifts from the brief; the draft's language ≠ brief language.
   - **temporal_consistency**: an age/year contradiction the deterministic check
     reports — a character's stated `age` ≠ `world_clock.current_year` −
     `birth_year`, a birth after the current year, or a later timeline `order`
     carrying an earlier `year`.
   - **voice (forbidden_lexicon)**: a term the voice card's `forbidden_lexicon`
     names appears in the prose — an author-declared HARD contract (e.g. a modern
     word in a classical-register continuation). Mirror the machine style lint.
3. **Craft pass (NON-BLOCKING; heuristic + observable proxies).** Note, don't
   gate: style consistency vs the profile, character-voice distinctness, scene
   concreteness (concrete objects vs abstract emotion-naming), show-don't-tell,
   over-summarization, telegraphed/mechanical twist, pacing/pressure, whether the
   ending closes the core question, and observable AI-tells (slogan/uplift
   ending, abstract-word piling, homogeneous imagery, over-explaining). Mirror the
   deterministic style lint's `ai_tell` notes here, but remember its cliché tables
   are **model-seed / BCC-pending** — a table hit is a prompt for judgment, NOT
   proof; only a `forbidden_lexicon` hit (above) is a hard, blocking contract.
4. **Emit `review.json` in the EXACT literary_review contract** — one JSON object
   and nothing else around it. The revise stage parses this; a shape mismatch
   makes it unparseable, so follow it precisely:
   ```json
   {"verdict": "revise",
    "findings": [
      {"id": "F1", "type": "status", "severity": "critical", "blocking": true,
       "location": "第五回首段「贾敏走进潇湘馆」", "evidence": "已故的贾敏在场叙话",
       "suggested_action": "删去贾敏出场，或改写为黛玉的幻景",
       "violated_constraint": "贾敏 status=dead"}
   ]}
   ```
   - `verdict` ∈ {`revise`, `done`}: `revise` iff any finding has `blocking: true`;
     `done` iff there are no findings. (A blocking finding can never coexist with
     `done`.)
   - Every finding REQUIRES `id, type, severity, blocking, location, evidence,
     suggested_action`. `severity` ∈ {`critical`, `major`, `minor`, `note`} is the
     IMPORTANCE axis; `blocking` is a SEPARATE boolean gate axis — they are
     DECOUPLED (a critical craft note may be non-blocking; a continuity
     contradiction is blocking). Never put `blocking` inside the `severity` field.
   - `type` MUST be one of the fiction vocabulary — never invent one (e.g. not
     `continuity`): `status, knowledge, item_location, co_location, timeline,
     world_rule, motivation, foreshadowing, viewpoint, language,
     temporal_consistency, verbatim_copy, voice, ai_tell, concreteness, show_tell,
     over_summary, pacing, mechanical_twist, ending, style`.
   - JSON discipline: inside any string VALUE, use full-width 「」 for inner quotes,
     NEVER the ASCII `"` — an unescaped inner quote breaks the JSON the revise
     stage must parse.
5. **Be honest.** If continuity holds and the craft issues are minor
   trade-offs, pass it — do not manufacture problems, and do not pretend craft
   quality is a deterministic measurement.

## When NOT to use
- Anything outside narrative fiction (see the exclusion in "When to use").
- As a source of a single numeric "quality score" — craft is judgment, recorded
  as evidence-located observations, not a gate number.

## Common pitfalls
- Trusting your recollection over `story_state` (the state is ground truth).
- Blocking on taste ("I'd end it differently") — taste is a non-blocking note.
- Letting a real contradiction pass because the prose is pretty.
