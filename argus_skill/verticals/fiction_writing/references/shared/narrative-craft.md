# Shared Narrative Craft (language-agnostic)

Reusable, language-independent craft knowledge for `fiction_writing`: **craft
cards** (technique → checkable questions), **continuity rules** (what the
reviewer BLOCKS on), and the **review rubric** (how craft is recorded without a
faked score). Shared by zh and en; language-specific style lives in
`../zh/` and `../en/`.

> Provenance honesty: the seed cards below are **model-seeded** (distilled from
> general craft knowledge), marked `source: model-seed (pending corpus
> grounding)`. Real cards are to be distilled from licensed corpora/criticism
> via the `learning` vertical (see `../source_registry/README.md`), each carrying
> a verbatim evidence span into an immutable source. Do not present a model-seed
> card as if it were sourced.

## 1. Craft cards (format)

```yaml
technique_id: <stable_slug>
applies_to: <planning|drafting|revision>
principle: <one sentence>
counter_example: <what the failure looks like>
check_questions:
  - <question the reviewer/engineer can answer against the text>
source: <bibliography+locator, or "model-seed (pending corpus grounding)">
```

### Seed cards

```yaml
technique_id: scene_goal_conflict_turn
applies_to: planning
principle: A scene needs a viewpoint goal, an obstacle, and an irreversible turn.
counter_example: Characters converse and exchange backstory; state is unchanged at the end.
check_questions:
  - Whose goal drives this scene?
  - What resists it?
  - What is irreversibly different by the end?
source: model-seed (pending corpus grounding)
```
```yaml
technique_id: show_dont_tell
applies_to: drafting
principle: Render emotion through concrete action/sensory detail, not by naming it.
counter_example: "She felt an overwhelming sadness and loneliness."
check_questions:
  - Is any emotion stated as an abstract label instead of shown?
  - Could a reader infer the feeling from the concrete details alone?
source: model-seed (pending corpus grounding)
```
```yaml
technique_id: foreshadow_plant_then_payoff
applies_to: planning
principle: A payoff must be planted earlier; a plant must eventually pay off or be deliberately dropped.
counter_example: A twist arrives with no earlier plant; or a planted detail is never used.
check_questions:
  - For each payoff, where was it planted (earlier chapter/beat)?
  - For each plant, is it paid off or intentionally abandoned?
source: model-seed (pending corpus grounding)
```
```yaml
technique_id: central_image_reversal
applies_to: planning
principle: Bind the story to one image/word, then turn it over at the end so a reread reads differently.
counter_example: A pretty ending image unrelated to any established motif.
check_questions:
  - What single symbol carries the story?
  - Is its meaning turned at the close?
source: model-seed (pending corpus grounding)
```
```yaml
technique_id: withhold_the_thesis
applies_to: revision
principle: End on an image and let the reader infer; do not summarize the point.
counter_example: A final paragraph that states the moral / uplifts / summarizes.
check_questions:
  - Does the ending explain its own meaning?
  - Would cutting the last "summary" sentence make it stronger?
source: model-seed (pending corpus grounding)
```

## 2. Continuity rules (reviewer BLOCKS on a hard contradiction)

Each maps to a `story_state` fact the reviewer checks against the draft:

- **status**: a `dead`/`absent` character acts on-stage with no explained return.
- **knowledge**: a character uses information not in their `knows` and never
  learned on-page.
- **item location**: an item's holder/location contradicts `items` with no
  on-page `move_item` (teleport).
- **co-location**: a character in two places at one timeline `order`.
- **timeline**: events violate timeline `order`.
- **world rule**: a stated `world_rules` entry is broken without setup.
- **motivation**: an action contradicts established `motivation` with no turn.
- **foreshadowing**: a `planted` item dropped, or paid off before planted / leaked.
- **viewpoint/tense drift**; **language drift** (draft language ≠ brief language).

The deterministic engine (`../state.py`) enforces the *structural* subset
(id-reference integrity, no silent deletion, unique-integer timeline). The
*semantic* subset above is the reviewer's judgment, gated via `review.json`.

## 3. Review rubric (how craft is recorded)

- Continuity findings → `severity: blocking` when a hard contradiction; each is
  `{type, severity, location, evidence, fix}`.
- Craft/AI-flavor findings → `severity: major|minor|note`, **non-blocking**. Use
  observable proxies where possible (see the per-language anti-AI patterns), and
  never collapse craft into a single numeric "literary score".
