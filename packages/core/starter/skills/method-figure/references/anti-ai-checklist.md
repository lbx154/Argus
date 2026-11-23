# The "does this look generated?" audit

Run `python3 scripts/validate.py spec.yaml --svg out/fig.svg` first — it
mechanises everything below that can be mechanised. Then do the eye pass, which
it cannot.

## Automated (the auditor)

| Code | Level | What it catches |
|---|---|---|
| `text-too-small` | error | any text under 6.0 pt at final size |
| `text-small` | warning | 6.0–6.5 pt |
| `module-label-small` | warning | a module name under 7.0 pt |
| `script-too-small` | warning | sub/superscripts under 4.5 pt |
| `label-overflow` | error | text wider than its box |
| `node-overlap` | error | two siblings occupying the same space |
| `edge-crosses-node` | warning | an arrow routed through a third module |
| `edge-crosses-text` | warning | an arrow through a caption, title or label |
| `diagonal-edge` | warning | a non-orthogonal run |
| `short-final-segment` | error / warning | a bent arrow's last run under two WPS / PowerPoint arrowheads — the head is drawn at 45° |
| `edges-share-corridor` | warning | two wires within 2 pt of each other, read as one |
| `corridor-too-narrow` | error | a gap between stages too narrow for the wires that jog in it |
| `label-overlap` | warning | an edge label or note on a module or another label |
| `text-off-canvas` / `edge-off-canvas` | error | anything LaTeX would crop |
| `bad-edge` | error | an edge naming a node that does not exist |
| `undefined-symbol` | error | a symbol defined neither in a label nor in the caption |
| `symbol-not-in-caption` | error | `defined_in: caption`, but the caption never writes it |
| `upright-variable` | warning | `z_t` set upright while the text sets $z_t$ italic |
| `unused-symbol` | warning | a registered symbol the figure never shows |
| `legend-unused-entry` | error | a legend entry nothing in the figure uses |
| `unexplained-line-style` | warning | a dashed or coloured arrow in no legend and with no label |
| `legend-overlap` | warning | the legend sits on content |
| `ai-palette` | error | a Tailwind / Material / Bootstrap stock colour |
| `saturated-fill` | warning | a module filled with a saturated colour |
| `tinted-outlines` | warning | most modules outlined in their own hue |
| `arrow-rainbow` | warning | more than three saturated arrow hues |
| `inconsistent-role` | error | one label drawn in two different fills |
| `off-palette` | warning | a colour outside the Office ladders |
| `too-many-hues` | warning | more than 6 colour families |
| `stroke-zoo` | warning | more than 4 line weights |
| `mixed-fonts` | warning | two type families |
| `label-verbose` / `label-period` | warning | sentences in boxes |
| `emoji` | error | emoji or dingbats |
| `image-low-ppi` / `image-soft` | error / warning | an embedded raster under 300 / 600 ppi at print size |
| `tall-figure` | warning | over the venue height budget |
| `gradient` / `shadow` | warning | gradients or filters in the SVG |
| `caption-long` | note | caption over 84 words |
| `unconnected` | note | a module no arrow touches |

After the hand-over the same discipline applies to the PPTX and the PDF:
`audit_pptx.py` and `audit_pdf.py`, see [after-handover.md](after-handover.md).

## By eye, at 100% on paper

Print it. Actually print it, or at minimum view the PDF at 100% zoom on a
laptop. Then:

1. **Squint.** What is darkest and largest? It must be the contribution. If a
   grey baseline box wins the squint test, the emphasis is inverted.
2. **Follow one input to one output** with a finger. If you lose the thread,
   the routing is wrong, not the reader.
3. **Cover the caption.** Can you still state the claim? If not, the figure is
   decoration around text.
4. **Convert to greyscale** (`magick fig.png -colorspace Gray g.png`). Anything
   that stops being distinguishable was relying on hue alone.
5. **Read every label out loud.** Any that is a sentence gets cut to a noun
   phrase.
6. **Check the count badges.** `×L`, `×N` — do they match the equations?
7. **Look at the four corners.** Empty corners are fine; a lonely orphan
   element in one is not.
8. **Compare against the neighbouring figure in the paper.** Same fills for the
   same concepts? Same title style? Same label size?

## The tells, in the order reviewers notice them

1. Saturated rainbow fills instead of a pale tint ladder.
2. Web-UI colours: Tailwind blue-500, indigo, violet, emerald — the palette of
   image models and generated plotting code.
3. Drop shadows, gradients, glow.
4. Icons — gears, brains, clouds, robots, sparkles.
5. Cards with a coloured left strip, pale fill plus same-hue outline.
6. Sentence-length text inside boxes.
7. Every box exactly the same size in a perfect grid.
8. Curved, floating arrows that miss their ports, or wobble with 2 pt jogs.
9. No real data anywhere in the figure.
10. Colour used decoratively, so the same hue means different things.
11. Symbols nobody defined.
12. A caption that names the figure instead of stating a claim.

## Caption template

> **Figure N.** *<Claim in one sentence, present tense.>* <One sentence naming
> the parts, in the order the eye reads them.> <What the colours mean, if it
> is not obvious.> <Every symbol in the figure that is not spelled out in it,
> in the order the eye meets them.>

Good: *"Figure 2. Sparse queries adapt their sampling range to scene depth, so
distant geometry is covered without a dense grid. Multi-view images are encoded
(left), refined by $L$ range-adaptive layers (centre, orange), and rolled forward
$f$ steps by the state-conditioned forecaster (right, yellow). $\oplus$:
element-wise addition."*

Bad: *"Figure 2. Overview of the proposed framework."*

A longer worked example, with every symbol of a dense figure defined in
reading order, is in [symbols-and-captions.md](symbols-and-captions.md).
After completing the definitions, recompile: a caption one line longer moves
the text after it by a line.
