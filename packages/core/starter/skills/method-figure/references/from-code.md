# Drafting a figure from source code

`scripts/from_code.py` reads a model implementation and emits a spec. It is a
drafting aid, not an oracle: it gets the modules, their order and the branch
structure right, and it is your job to decide what the paper is actually
about.

## Which source to point it at

Prefer a config when one exists.

| Source | Reader | Why |
|---|---|---|
| `configs/*.py` with `model = dict(type=..., ...)` | `--mm` | Names every stage, gives channel widths, and is already in pipeline order. This is the best source. |
| a package of `nn.Module` subclasses | default | Recovers real dataflow including branches and residuals, but has to guess which class is the model. |
| a single file | either | Fine, but a class that inherits half its stack from a base in another file will come out incomplete — pass the whole package. |

Neither reader imports or executes anything. Both parse with `ast`, so no
dependency install, no checkpoint download, no CUDA.

## Commands

```bash
# what model classes are in here?
python3 scripts/from_code.py bevdepth/layers/ --list

# draw one of them
python3 scripts/from_code.py bevdepth/layers/ --model DepthNet -o fig.yaml

# an mmengine / mmdet / mmdet3d config
python3 scripts/from_code.py configs/sparseworld.py --mm -o fig.yaml

# widen the budget, keep the loss terms, name the panels yourself
python3 scripts/from_code.py src/ --max-nodes 16 --keep-loss \
        --stages "Input,Encoder,Our Module,Heads" -o fig.yaml
```

## What it does to make the draft fit

A draft wider than its column gets silently downscaled at render time, and
downscaling is how figures end up with 5 pt labels. So the emitter measures
its own draft and reshapes it, in this order, reporting each concession on
stderr:

1. drop the channel-count captions (the widest thing per box);
2. abbreviate module names the way the reference corpus writes them —
   `img_bev_encoder_backbone` becomes `BEV Enc. Backbone`;
3. shed the least-connected boxes, protecting the endpoints and anything whose
   role came out as `core`;
4. as a last resort, fold into two bands with a proper return sweep.

If you see `dropped 6 peripheral modules`, that is a signal to raise
`--max-nodes` deliberately or, more often, to accept it: the corpus median
framework figure has **11** boxes.

## What it infers, and how it can be wrong

**Roles** come from name and class patterns (`backbone`, `neck`, `head`,
`attn`, `temporal`, …). Those patterns were drawn from identifier frequency
across 48 repositories, so they cover this literature's vocabulary — but a
module you named `block3` is going to come out `neutral`.

**Branch colour.** When role inference leaves most boxes colourless, parallel
branches downstream of a fork get their own tints instead. That is why
`DepthNet`'s context and depth branches come out blue and green with no roles
assigned at all.

**Repeat counts.** `for i in range(6)` becomes a `×6` badge; `for blk in
self.blocks` becomes `×N`, because the count is not in the source.

**Residuals.** `x = x + self.block(x)` becomes a `⊕` node.

Known limits, stated plainly:

- Control flow the reader cannot resolve is flattened — both arms of an `if`
  are emitted, and a branch selected by a config flag will appear even when
  your experiments never take it.
- `forward` is chosen by name from a fixed list (`forward`, `forward_train`,
  `extract_feat`, `simple_test`, …). A model that does its real work in a
  differently named method will fall back to listing `__init__` submodules in
  declaration order, which is order-correct but branch-blind.
- Functional calls (`F.conv2d`, tensor methods) are not modules and are not
  drawn.
- The mm reader follows one config file; `_base_` inheritance is not resolved,
  so a config that only overrides fields will look sparse. Point it at the
  fully expanded config, or at the base.

## Turning the draft into a figure

The draft is step 3b of the workflow, not the end of it. Before delivering:

1. **Rename every box** to the name the paper uses. Attribute names are for
   the code; `Img View Transformer` is `Lift-Splat` in the text.
2. **Delete what the paper does not discuss.** A reader counts boxes and
   assumes each one is a claim.
3. **Attach real crops.** Add `src:` to the input and output slots — 57% of
   framework figures in the corpus embed real rasters, and the auditor will
   remind you.
4. **Check the arrows against `forward`.** Cross-branch edges are where the
   reader is least reliable.
5. **Mark the contribution.** Give it `role: core` and put it in its own
   panel; the draft has no idea which module is the paper.
6. **Finish the caption.** Operators the draft draws (⊕, ⊗, ⓒ) are declared
   in its `symbols:` table with a `TODO` caption that already defines them;
   write the claim in front and define any symbol you add
   ([symbols-and-captions.md](symbols-and-captions.md)).
7. Run `scripts/validate.py`, and look at the PNG.
