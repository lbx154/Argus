#!/usr/bin/env python3
"""Generate the module palette -- a drag-and-drop stencil for hand editing.

    python3 make_stencil.py -o templates/stencil

Writes one page holding every shape in the vocabulary, pre-styled in every
semantic role, plus the tint ladder and the line weights.  Open the .vsdx in
Visio (or the .pptx in PowerPoint), copy a module out of it and it arrives
already carrying the house fill, outline, corner radius and 7 pt Times label.

Visio has no public stencil (.vssx) schema that can be written safely by hand,
so the palette ships as an ordinary drawing.  In practice that is the same
workflow: keep it open in a second window and copy across.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cvprfig import Figure, style                      # noqa: E402
from cvprfig import vsdx as vsdx_writer                # noqa: E402
from cvprfig import pptx as pptx_writer                # noqa: E402

ROLE_ORDER = ["input", "backbone", "encoder", "attention", "core", "temporal",
              "memory", "decoder", "output", "aux", "baseline", "neutral"]

SHAPES = [
    ("box", {"text": "Module Box"}),
    ("trapezoid", {"text": "Encoder", "dir": "right"}),
    ("invtrapezoid", {"text": "Decoder", "dir": "right"}),
    ("hexagon", {"text": "Gate"}),
    ("parallelogram", {"text": "Data"}),
    ("chevron", {"text": "Memory Op"}),
    ("plane", {"text": "", "w": 62, "h": 26}),
    ("cube", {"side": 30, "grid": 3}),
    ("slab", {"n": 4, "cell": 8, "cellh": 26}),
    ("tokengrid", {"rows": 4, "cols": 1, "cell": 8}),
    ("circleop", {"op": "+", "d": 13}),
    ("circleop", {"op": "x", "d": 13}),
    ("image", {"w": 54, "h": 34, "text": "panel"}),
    ("framestrip", {"pattern": "#####.....####.#####", "cell": 2.6, "h": 11}),
    ("dist", {"kind": "gauss", "w": 44, "h": 22}),
    ("dist", {"kind": "bimodal", "w": 44, "h": 22}),
    ("brace", {"h": 26}),
    ("ellipsis", {"w": 16, "h": 8}),
]


def build_spec():
    panels = []

    # 1 -- semantic roles, the thing to standardise on across a whole paper
    role_rows = []
    for i in range(0, len(ROLE_ORDER), 4):
        chunk = ROLE_ORDER[i:i + 4]
        role_rows.append({"row": {"gap": 10, "body": [
            {"id": "role_%s" % r, "text": r.capitalize(), "role": r, "minw": 74}
            for r in chunk]}})
    panels.append({"id": "roles", "title": "Semantic roles -- keep these stable "
                                           "across every figure in the paper",
                   "title_align": "left", "title_italic": False,
                   "frame": "dashed", "body": role_rows})

    # 2 -- the shape vocabulary
    shape_rows, row = [], []
    for name, extra in SHAPES:
        spec = {"id": "shape_%s_%d" % (name, len(shape_rows) * 10 + len(row)),
                "shape": name, "role": "attention",
                "caption": name, "caption_size": 5.8}
        spec.update(extra)
        row.append(spec)
        if len(row) == 5:
            shape_rows.append({"row": {"gap": 14, "align": "end", "body": row}})
            row = []
    if row:
        shape_rows.append({"row": {"gap": 14, "align": "end", "body": row}})
    panels.append({"id": "shapes", "title": "Shape vocabulary",
                   "title_align": "left", "title_italic": False,
                   "frame": "dashed", "body": shape_rows})

    # 3 -- the tint ladder each family is drawn from
    ladder_rows = []
    for fam in ("blue", "steel", "green", "gold", "orange", "purple", "rose",
                "teal", "grey"):
        cells = [{"id": "sw_%s_%d" % (fam, k), "shape": "sharpbox", "w": 34, "h": 13,
                  "fill": "%s.%s" % (fam, t), "lw": style.STROKE["hairline"],
                  "caption": style.FAMILIES[fam][style.TINT_INDEX[t]],
                  "caption_size": 4.6}
                 for k, t in enumerate(["pale", "light", "soft", "mid", "strong",
                                        "deep", "dark"])]
        ladder_rows.append({"row": {"gap": 3, "body": [
            {"id": "lab_%s" % fam, "shape": "text", "text": fam, "w": 34,
             "size": 6.4}] + cells}})
    panels.append({"id": "tints", "title": "Tint ladders -- fills come from "
                                           "pale/light/soft, ink from strong/deep",
                   "title_align": "left", "title_italic": False,
                   "frame": "dashed", "body": ladder_rows})

    # 4 -- line weights and arrow styles
    weights = []
    for k, (name, w) in enumerate(sorted(style.STROKE.items(), key=lambda kv: kv[1])):
        weights.append({"id": "lwsrc_%s" % name, "shape": "spacer", "w": 4, "h": 10})
        weights.append({"id": "lwdst_%s" % name, "shape": "text",
                        "text": "%s (%.2f pt)" % (name, w), "size": 6.0, "w": 78})
    panels.append({"id": "lines", "title": "Line weights",
                   "title_align": "left", "title_italic": False,
                   "frame": "dashed",
                   "body": [{"row": {"gap": 26, "body": weights}}]})

    edges = []
    for name, w in style.STROKE.items():
        edges.append({"from": "lwsrc_%s" % name, "to": "lwdst_%s" % name,
                      "lw": name, "route": "straight"})

    return {"figure": {"id": "stencil", "venue": "generic", "width": 560,
                       "font": "times", "pad": 8, "pad_top": 6},
            "layout": "col", "gap": 18, "panels": panels, "edges": edges}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default="templates/stencil")
    ap.add_argument("-f", "--format", default="svg,vsdx,pptx")
    args = ap.parse_args(argv)

    spec = build_spec()
    fig = Figure(spec)
    out_dir = os.path.dirname(os.path.abspath(args.out)) or "."
    os.makedirs(out_dir, exist_ok=True)

    written = []
    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    if "svg" in formats:
        with open(args.out + ".svg", "w", encoding="utf-8") as fh:
            fh.write(fig.tostring())
        written.append(args.out + ".svg")
    if "vsdx" in formats:
        vsdx_writer.write(fig, args.out + ".vsdx")
        written.append(args.out + ".vsdx")
    if "pptx" in formats:
        pptx_writer.write(fig, args.out + ".pptx")
        written.append(args.out + ".pptx")
    for p in written:
        print("wrote %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
