# The figure contract

Fill this in **before** writing any spec — all eight items. Three minutes here removes most of
the revision cycles; skipping it is why generated figures look like they were
generated.

## 1. The one-sentence claim

> This figure makes the reader believe: ______________________________

If the sentence needs an "and", the figure is two figures.

Good: *"Our queries stay sparse and adapt their range, so we forecast the same
scene with a fraction of the tokens a dense grid needs."*

Bad: *"This is the architecture of our method."* — that is a description, not
a claim, and it produces a figure with nothing to emphasise.

## 2. The archetype

Pick exactly one (see `references/archetypes.md`):

| Archetype | Answers | Where it goes |
|---|---|---|
| `teaser-comparison` | *Why is the existing way wrong?* | page 1, single column |
| `pipeline` | *What runs, in what order?* | method section, double column |
| `module-detail` | *What exactly does the new block compute?* | method section, single column |
| `attention-tokens` | *What is the interaction pattern?* | method section |
| `streaming-worldmodel` | *How does state persist across steps?* | method section, double column |

## 3. The contribution, and where the eye lands first

Name the one element that is the paper's contribution. It gets the `core` /
`ours` role, the warmest fill, and the most generous surrounding whitespace.
Everything else is context and takes a pale or grey fill. If you cannot point
at one element, the figure has no focal point and will read as a flowchart.

## 4. Semantic colour assignment

Write out the map before drawing. Reuse it in every figure of the paper — put
it in a shared `paper.yaml` (`roles:`) so it cannot drift.

```
core      -> <the contribution>                 orange.light by default
encoder   -> <feature extraction>               blue.light
attention -> <token mixing>                     blue.soft
temporal  -> <anything time-indexed>            gold.light
memory    -> <state / cache>                    red.light
decoder   -> <heads and outputs>                green.light
grey      -> <everything the paper does not claim>
```

Pale Office tints for fills, black outlines, saturated colour only on arrows
and on label text that ties back to an entity. Never Tailwind/Material
defaults (`#3B82F6`, `#8B5CF6`, `#10B981` ...) — the auditor fails them.

## 5. Venue and physical size

`venue` and `width` fix the canvas in points. Check the real numbers:

| Venue | single | double |
|---|---|---|
| CVPR / ICCV | 237.1 pt | 496.8 pt |
| ECCV (LNCS) | 347.1 pt | — |
| NeurIPS / ICLR | 397.5 pt | — |
| ICML | 234.9 pt | 487.8 pt |
| AAAI | 238.5 pt | 504.0 pt |
| ACL / EMNLP | 219.1 pt | 455.2 pt |

Height budget: a double-column figure should stay under ~0.58 × its width, a
single-column one under ~1.45 ×. The auditor enforces this.

## 6. What real data goes in

List the crops you will drop into the `image` slots: input frames, an output
render, a failure case. Note where they come from. A figure with every slot
still a placeholder is not finished.

## 7. The symbol table

List every symbol the figure will show — variables, indices, operators
(⊕ ⊗ ⓒ), stacking brackets, repeat badges (`×L`). For each:

| Symbol | Meaning, one sentence | Defined in | Written in the text as |
|---|---|---|---|
| `c_s` | world memory at map scale *s* | caption | `$c_s$` |
| `z_t` | BEV feature map of frame *t* | label "BEV features $z_t$" | `$z_t$` |
| `⊕` | element-wise addition | caption | `$\oplus$` |

Put a symbol in the figure only if the text uses it too; otherwise write the
words ("World canvas", not `c_s`). The reader must understand the figure from
the figure and its caption alone. This table becomes the spec's `symbols:`,
the caption goes into `caption:` (or `caption_tex:`), and the auditor checks
both. See `references/symbols-and-captions.md`.

## 8. Reviewer risks

- Which arrow direction could be misread?
- Which two boxes could be confused because they share a fill?
- Is any claim visible only in colour?
- Does the caption state the claim, or just name the picture?
- Is every symbol defined in a label or in the caption, in reading order?
- Who will edit the PPTX, in which application, and which application will
  export the paper PDF? (`pptx_app`, and `export_pptx.py` afterwards.)
