# Symbols, maths and captions

A reader must be able to understand the figure from the figure and its caption
alone. Many reviewers look at the figures before they read Section 3; a `c_s`
that is defined only in an equation sends them hunting, and the comment comes
back as "the notation in Fig. 2 is not explained".

v3 makes this checkable. Every symbol the figure prints is collected,
looked up in a symbol table, and verified against the caption.

## 1. Decide which symbols belong in the figure at all

Before drawing, list every symbol the figure would show. For each one write
three things:

| Symbol | Meaning, one sentence | Where the reader finds it | How the text writes it |
|---|---|---|---|
| `c_s` | world memory at map scale *s* | caption | `$c_s$` |
| `⊕` | element-wise addition | caption | `$\oplus$` |
| `z_t` | BEV feature map of frame *t* | label "BEV features $z_t$" | `$z_t$` |

Then cut. Put a symbol in the figure only if the text uses it too. Anything
else is better as words: "World canvas" needs no definition, `c_s` does. Each
symbol you remove is one definition fewer in a caption that is already
competing with the page limit.

## 2. Write variables as maths

Wrap every variable in `$...$` in the spec. The engine typesets it the way
LaTeX does, so the symbol looks the same in the figure as in the paragraph that
cites it:

| Spec | Prints as |
|---|---|
| `"BEV features $z_{1:4}$"` | BEV features *z*₁:₄ — *z* italic, index upright |
| `"$\tilde c_s = c_s + W[d_1;\dots;d_4]$"` | accents, spaced `=` and `+`, subscripts |
| `"$\hat O_{5:10}$"` | *Ô*₅:₁₀ (accents compose) |
| `"$N \times C$"` | *N* × *C* with TeX spacing |
| `"$\times L$"` | ×*L* (an operator with no left operand is not spaced) |
| `"$\mathrm{softmax}(\log\gamma)$"` | upright function names |
| `"cost \$5"` | a literal dollar sign |

Supported: Latin letters (italic), Greek (`\alpha` … `\omega` italic,
capitals upright), `_{}`/`^{}`, primes, `\hat \tilde \bar \dot \ddot \vec
\check`, `\mathrm \text \operatorname \mathbf \boldsymbol \mathit \mathbb`,
`\oplus \otimes \odot \ominus \times \cdot \circ \dots \cdots \pm \in \to
\leftarrow \mapsto \sum \prod \leq \geq \neq \approx \sim \propto \infty
\partial \nabla \|`, `\log \exp \max \min \arg \softmax …`, and the spacing
commands `\, \; \: \quad \qquad`.

The old markup (`*z*_{t}`) still works but sets the base letter upright when
the asterisks are left off; the auditor flags that as `upright-variable`.

## 3. The symbol table

```yaml
symbols:
  "c_s":         {meaning: "world memory at map scale s", defined_in: caption, tex: "$c_s$"}
  "\\tilde c_s": {meaning: "memory corrected by the frame order", defined_in: caption}
  "z_t":         {meaning: "BEV feature map of frame t", defined_in: figure}
  "\\oplus":     {meaning: "element-wise addition", defined_in: caption}
  "[;]":         {meaning: "stacking along channels", defined_in: caption}
```

- Keys are written like the maths (`c_s`, `\tilde c_s`, `\oplus`); the
  auditor files them by base symbol, so `z_t` covers `z_{1:4}` and `z_4`,
  while `z`, `\hat z` and `\tilde z` are three different symbols.
- `defined_in: caption` — the caption must write it (checked).
- `defined_in: figure` — a label next to it names it in words, as in
  "BEV features $z_t$" (checked: the label must contain a real word).
- Anything else (`text`, `section 3`) is reported: a reader of the figure
  alone will not find it.
- Operator nodes (`shape: op`, `op: "+"`) count as symbols (⊕, ⊗, ©), and so
  do repeat badges (`×L`).

## 4. One table for the whole paper

Put the palette and the symbol table in one `paper.yaml` next to the figure
specs, and point every figure at it:

```yaml
# figures/src/paper.yaml
venue: cvpr
font: times
roles:                 # override the default role -> colour map once, for all figures
  core: orange.light
  temporal: green.light
symbols:
  "z_t":     {meaning: "BEV feature map of frame t", defined_in: figure, tex: "$z_t$"}
  "c_s":     {meaning: "world memory at map scale s", defined_in: caption, tex: "$c_s$"}
  "\\oplus": {meaning: "element-wise addition", defined_in: caption}
```

```yaml
# figures/src/fig2_overview.yaml
paper: paper.yaml
figure: {id: fig2_overview, width: double}
caption_tex: ../../sec/3_method.tex#fig:overview
```

The figure's own keys win over the paper's. Symbols that come from
`paper.yaml` and are unused in one figure are not reported; symbols listed in
the figure's own spec and never shown are (`unused-symbol`).

## 5. The caption

Give the auditor the caption, either inline or straight out of the paper:

```yaml
caption: >-
  \textbf{...} ... $c_s$ ...
# or
caption_tex: ../sec/3_method.tex#fig:overview   # path#\label
```

Template — four sentences, in this order:

> **Figure N.** *⟨The claim, one sentence, present tense.⟩* ⟨The parts, in
> the order the eye reads them.⟩ ⟨What the colours mean, if not obvious.⟩
> ⟨Every symbol in the figure that is not spelled out in it, in the order the
> eye meets them.⟩

A good one, adapted from a real submission (names removed):

> **Overview of our method, drawn on a real validation clip.** ① Each observed
> occupancy grid $O_t$ is encoded into a BEV feature map $z_t$. ② The present
> map $z_4$ is carried to every future frame by a bilinear warp along the
> given future ego trajectory, then modulated and refined, giving
> $\hat z_{5:10}$. ③ The ten maps are read by a ConvGRU and by a world memory
> $c_s$ that averages each map cell over the frames, at map scale $s$.
> ④ Our order module stacks the time-ordered per-frame differences
> $d_1,\dots,d_4$ ($[\,\cdot\,;\cdot\,]$) and adds them ($\oplus$) to that
> memory through a $1{\times}1$ convolution $W$ that starts at zero, giving
> $\tilde c_s$. ⑤ Both paths are concatenated (ⓒ) and decoded into the
> reconstructed occupancy $\hat O_{1:4}$ and the forecast $\hat O_{5:10}$.
> Orange marks our changes to the baseline, grey the parts we keep.

Every symbol in that figure is defined, in reading order, and the colours are
explained in one clause.

## 6. Mind the page count

Adding definitions to a caption costs lines. A `figure*` caption that grows by
one line pushes the text below it by one line; a single-column caption that
grows by one line can push a whole column. After completing the symbol
definitions, **recompile and check the page count**. When the caption runs
long (the corpus median is 39 words, the 90th percentile 84 — the auditor
notes anything above), move some definitions into the figure as words: write
"World canvas $c_s$" in the label instead of defining $c_s$ in the caption.
