#!/usr/bin/env python3
"""Audit a figure spec (and its rendered SVG) before it goes into a paper.

    python3 validate.py spec.yaml [--svg out/fig.svg] [--json] [--strict]

Two classes of check:

  LEGIBILITY / CORRECTNESS -- things a reviewer will notice: sub-6 pt text,
  labels overflowing their boxes, arrows crossing modules, a figure so tall it
  eats the page, unreachable nodes.

  HOUSE STYLE -- the things that make a figure read as machine-generated:
  off-palette colours, too many hues, the same concept drawn in two colours,
  gradients, drop shadows, five different stroke weights, sentence-long labels,
  and the stock colours of web UI kits that image models default to.

  SELF-CONTAINED -- since v3: every symbol in the figure is defined in a
  label or in the caption, the legend lists exactly the line styles the figure
  uses, and bent arrows end in a run long enough for WPS to aim the head.

The spec is only the first half.  Once the figure is handed over as a PPTX
and edited by hand, audit the files that actually ship as well:

    python3 audit_pptx.py fig.pptx --spec spec.yaml
    python3 audit_pdf.py  fig.pdf

Exit code is 0 when nothing above the failure threshold is reported.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cvprfig import Figure, load_path, layout as L, style, text as T   # noqa: E402
from cvprfig import edges as edgemod, imgcolor                          # noqa: E402

PALETTE = set()
for _fam in style.FAMILIES.values():
    PALETTE.update(c.upper() for c in _fam)
PALETTE.update({"#FFFFFF", "#000000", "#C00000", "#B1001C", "#953735", "#2E75B6",
                "#1F4E79", "#7F6000", "#C55A11", "#833C0C", "#4F6228", "#5F497A",
                "#632523", "#205867", "#1E524A", "#3F3151", "#76923C", "#31859B"})

MAX_LABEL_WORDS = 6
MAX_HUE_FAMILIES = 6      # corpus median is 5 distinct families, p75 is 9
MAX_STROKE_WEIGHTS = 4


class Report(object):
    def __init__(self):
        self.rows = []

    def add(self, level, code, msg, where=None):
        self.rows.append({"level": level, "code": code, "message": msg, "where": where})

    error = lambda self, *a: self.add("error", *a)
    warn = lambda self, *a: self.add("warning", *a)
    note = lambda self, *a: self.add("note", *a)

    def counts(self):
        c = {"error": 0, "warning": 0, "note": 0}
        for r in self.rows:
            c[r["level"]] += 1
        return c


# ------------------------------------------------------------- geometry
def _boxes(fig):
    out = []
    for it in L.walk(fig.items):
        if it.kind == "node" and it.spec.get("shape", "box") != "spacer":
            out.append(it)
    return out


def text_elements(fig):
    """Every piece of text the figure prints: (where, text, size in spec pt,
    kind).  ``kind`` is ``module`` for a label inside a module box."""
    out = []
    for it in L.walk(fig.items):
        sp = it.spec
        if it.kind == "node":
            if sp.get("text") and sp.get("shape", "box") != "spacer":
                kind = "module" if sp.get("shape", "box") in L.BOXY else "label"
                out.append((it.id or "?", sp["text"], sp.get("_tsize"), kind))
            if sp.get("caption"):
                out.append((it.id or "?", sp["caption"], sp.get("_cap_size"), "caption"))
            if sp.get("badge"):
                out.append((it.id or "?", sp["badge"], float(sp.get(
                    "badge_size", style.TYPE["annot"]["size"])), "badge"))
        else:
            if sp.get("title"):
                out.append((it.id or "?", sp["title"], float(sp.get(
                    "title_size", style.TYPE["stage_title"]["size"])), "title"))
            if sp.get("badge"):
                out.append((it.id or "?", sp["badge"], float(sp.get(
                    "badge_size", style.TYPE["annot"]["size"])), "badge"))
    for k, ed in enumerate(fig.spec.get("edges") or []):
        if ed.get("label"):
            out.append(("edge#%d" % k, ed["label"], float(ed.get(
                "label_size", style.TYPE["caption"]["size"])), "edge label"))
    for k, n in enumerate(fig.spec.get("notes") or []):
        if isinstance(n, dict) and n.get("text"):
            out.append(("note#%d" % k, n["text"], float(n.get(
                "size", style.TYPE["annot"]["size"])), "note"))
    lg = fig.spec.get("legend") or {}
    for e in lg.get("items") or []:
        if e.get("text"):
            out.append(("legend", e["text"], float(lg.get(
                "size", style.TYPE["caption"]["size"])), "legend"))
    return out


def check_legibility(fig, rep):
    s = fig.scale
    for where, txt, size, kind in text_elements(fig):
        if not size:
            continue
        eff = size * s
        show = T.plain(txt).replace("\n", " ")[:32]
        has_script = any(r[3] for r in T.parse_runs(txt))
        if eff < style.MIN_RENDERED_PT:
            rep.error("text-too-small",
                      "%s %r prints at %.1f pt (floor %.1f pt)"
                      % (kind, show, eff, style.MIN_RENDERED_PT), where)
        elif eff < style.WARN_RENDERED_PT:
            rep.warn("text-small", "%s %r prints at %.1f pt (warning below %.1f pt)"
                     % (kind, show, eff, style.WARN_RENDERED_PT), where)
        elif kind == "module" and eff < style.MIN_MODULE_PT - 0.01:
            rep.warn("module-label-small",
                     "module name %r prints at %.1f pt; module names stay at or "
                     "above %.1f pt" % (show, eff, style.MIN_MODULE_PT), where)
        if has_script and eff * T.SCRIPT_SCALE < 4.5:
            rep.warn("script-too-small",
                     "sub/superscripts in %r print at %.1f pt" % (show, eff * T.SCRIPT_SCALE),
                     where)
    if s < 0.85:
        rep.warn("overscaled",
                 "layout is %.0f%% wider than the column, so everything shrinks to %.0f%%"
                 % ((1 / s - 1) * 100, s * 100))


def check_overflow(fig, rep):
    for it in _boxes(fig):
        sp = it.spec
        if not sp.get("text") or sp.get("shape") in ("text", "note", "mathlabel",
                                                     "slab", "slabstack", "image",
                                                     "photo", "imagestack"):
            continue
        if sp.get("rotate"):
            continue
        w, h, lines = T.measure(sp["text"], sp["_tsize"], sp.get("_font", "times"),
                                sp.get("_bold", False), sp.get("_italic", False),
                                style.GEOM["line_gap"])
        if w > it.w - 2.0:
            rep.error("label-overflow",
                      "label %r is %.1f pt wide inside a %.1f pt box"
                      % (sp["text"][:32], w, it.w), it.id)
        if h > it.h - 1.0:
            rep.warn("label-tall", "label %r is taller than its box" % sp["text"][:32], it.id)


def _rects_overlap(a, b, tol=0.5):
    return (a.x + a.w - tol > b.x and b.x + b.w - tol > a.x
            and a.y + a.h - tol > b.y and b.y + b.h - tol > a.y)


def check_collisions(fig, rep):
    boxes = [b for b in _boxes(fig) if b.spec.get("shape") not in ("ellipsis",)]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            if a.parent is not b.parent:
                continue
            if _rects_overlap(a, b, 1.0):
                rep.error("node-overlap", "%s and %s overlap"
                          % (a.id or "?", b.id or "?"), a.id)


def _seg_hits_rect(p, q, it, pad=1.0):
    x0, y0, x1, y1 = it.x + pad, it.y + pad, it.x + it.w - pad, it.y + it.h - pad
    if x1 <= x0 or y1 <= y0:
        return False
    if abs(p[1] - q[1]) < 0.05:                      # horizontal
        return y0 < p[1] < y1 and min(p[0], q[0]) < x1 and max(p[0], q[0]) > x0
    if abs(p[0] - q[0]) < 0.05:                      # vertical
        return x0 < p[0] < x1 and min(p[1], q[1]) < y1 and max(p[1], q[1]) > y0
    return False


def check_edges(fig, rep):
    boxes = _boxes(fig)
    for k, msg in getattr(fig, "bad_edges", []):
        rep.error("bad-edge", msg, "edge#%d" % k)
    for r in fig.routes:
        ed, k, a, b, pts = r.spec, r.index, r.a, r.b, r.pts
        for i in range(len(pts) - 1):
            for it in boxes:
                if it is a or it is b:
                    continue
                # Bare labels and measured whitespace are not obstacles: their
                # bounding boxes are wide but their ink is not, so a line
                # passing between two centred labels is fine.
                if it.spec.get("shape") in ("image", "photo", "imagestack",
                                            "text", "note", "mathlabel",
                                            "spacer", "ellipsis", "brace"):
                    continue
                if _seg_hits_rect(pts[i], pts[i + 1], it):
                    rep.warn("edge-crosses-node",
                             "edge %s -> %s passes through %s"
                             % (ed["from"], ed["to"], it.id or "?"), "edge#%d" % k)
                    break


def check_arrow_runs(fig, rep):
    """The last segment of a bent arrow must be long enough to aim its head.

    WPS (and, by report, PowerPoint) sets the head direction by stepping one
    head length back along the path.  If the last segment is shorter, the
    step lands on the previous segment and the head is drawn at 45 degrees.
    Straight arrows are immune.  Threshold: 2 x the longest head any editor
    draws for this line's preset.
    """
    for r in fig.routes:
        lim = 2.0 * style.worst_head(r.lw, fig.arrow_app)
        wps = 2.0 * style.head_size(r.lw, r.preset, "wps")[0]
        for flag, a, b, end in ((r.short_end, r.pts[-2], r.pts[-1], "end"),
                                (r.short_start, r.pts[1], r.pts[0], "start")):
            if flag:
                L_ = abs(a[0] - b[0]) + abs(a[1] - b[1])
                if L_ >= wps:
                    rep.warn("short-final-segment",
                             "edge %s -> %s: the %s run after the last bend is %.1f pt, "
                             "under two PowerPoint-size arrowheads (%.1f pt); the PPTX "
                             "gives this head its own segment"
                             % (r.spec["from"], r.spec["to"], end, L_, lim), "edge#%d" % r.index)
                    continue
                rep.error("short-final-segment",
                          "edge %s -> %s: the %s run after the last bend is %.1f pt, "
                          "under 2 arrowheads (%.1f pt); WPS draws the head at 45 deg. "
                          "Widen the gap, align the ports, or set route: straight "
                          "(the PPTX writer splits the head off meanwhile)"
                          % (r.spec["from"], r.spec["to"], end, L_, lim),
                          "edge#%d" % r.index)


def check_corridors(fig, rep):
    """Wires must not run on top of each other, and every gap between stages
    must be wide enough for the wires that jog inside it."""
    segs = []
    for r in fig.routes:
        for p, q in zip(r.pts, r.pts[1:]):
            if abs(p[0] - q[0]) < 0.05:
                segs.append((r, "v", p[0], min(p[1], q[1]), max(p[1], q[1])))
            elif abs(p[1] - q[1]) < 0.05:
                segs.append((r, "h", p[1], min(p[0], q[0]), max(p[0], q[0])))
    seen = set()
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            r1, o1, c1, a1, b1 = segs[i]
            r2, o2, c2, a2, b2 = segs[j]
            if r1 is r2 or o1 != o2 or abs(c1 - c2) >= 2.0:
                continue
            if min(b1, b2) - max(a1, a2) <= 1.0:
                continue
            if {id(r1.a), id(r1.b)} & {id(r2.a), id(r2.b)}:
                continue          # a fan-out / fan-in trunk, or a chain
            key = (r1.index, r2.index)
            if key in seen:
                continue
            seen.add(key)
            rep.warn("edges-share-corridor",
                     "edges %s -> %s and %s -> %s run %.1f pt apart for %.0f pt; "
                     "they read as one wire"
                     % (r1.spec["from"], r1.spec["to"], r2.spec["from"], r2.spec["to"],
                        abs(c1 - c2), min(b1, b2) - max(a1, a2)),
                     "edge#%d" % r1.index)
    gaps = getattr(fig, "gaps", None) or []
    if gaps:
        for i, (need, have) in enumerate(zip(fig.corridor_demand(), gaps)):
            if need > have + 0.05:
                rep.error("corridor-too-narrow",
                          "the gap after stage %s is %.1f pt but the wires jogging in it "
                          "need %.1f pt (one %.0f pt lane each plus an arrow's final run)"
                          % (fig.items[i].id or i + 1, have, need, style.GEOM["lane"]))


def _text_rect(x, y, anchor, size, txt, font, rot=0):
    tw, th, _ = T.measure(txt, size, font)
    x0 = {"start": x, "end": x - tw}.get(anchor, x - tw / 2.0)
    r = [x0, y - th / 2.0, tw, th]
    if rot in (90, -90, 270):
        r = [x - th / 2.0, y - tw / 2.0, th, tw]
    return r


def label_rects(fig):
    """(where, rect) for every free-standing label: edge labels and notes."""
    out = []
    for r in fig.routes:
        e = r.spec
        if e.get("label"):
            x, y, anchor, size, _ = edgemod.label_box(e, r.pts, r.color, fig.font)
            out.append(("edge %s -> %s label %r" % (e["from"], e["to"], T.plain(e["label"])),
                        _text_rect(x, y, anchor, size, e["label"], fig.font,
                                   e.get("label_rotate", 0))))
    for k, n in enumerate(fig.spec.get("notes") or []):
        if not isinstance(n, dict) or not n.get("text"):
            continue
        if n.get("at"):
            x, y = float(n["at"][0]), float(n["at"][1])
        elif n.get("near") and str(n["near"]).split(".")[0] in fig.nodes:
            item, side = edgemod.resolve_ref(n["near"], fig.nodes)
            x, y = item.port(side or "n")
        else:
            continue
        x += float(n.get("dx", 0.0)); y += float(n.get("dy", 0.0))
        size = float(n.get("size", style.TYPE["annot"]["size"]))
        out.append(("note %r" % T.plain(n["text"]),
                    _text_rect(x, y, n.get("anchor", "middle"), size, n["text"], fig.font,
                               n.get("rotate", 0))))
    return out


def text_rects(fig):
    """Rects of every caption and container title, plus labels and notes."""
    out = list(label_rects(fig))
    for it in L.walk(fig.items):
        sp = it.spec
        if it.kind == "node" and sp.get("caption"):
            bx, by, bw, bh = it.body()
            cw = T.measure(sp["caption"], sp["_cap_size"], fig.font)[0]
            out.append(("caption of %s" % it.id,
                        [it.cx - cw / 2.0, by + bh + float(sp.get("caption_gap", 3.0)),
                         cw, sp["_cap_h"]]))
        elif it.kind != "node" and sp.get("title"):
            ts = float(sp.get("title_size", style.TYPE["stage_title"]["size"]))
            tw, th, _ = T.measure(sp["title"], ts, fig.font, True, True)
            pos = sp.get("title_pos", "above")
            ha = sp.get("title_align", "center")
            tx = {"left": it.x + 2.0, "right": it.x + it.w - 2.0 - tw}.get(ha, it.cx - tw / 2.0)
            if pos == "above":
                ty = it.y - style.GEOM["stage_title_gap"] - th
            elif pos == "below":
                ty = it.y + it.h + style.GEOM["stage_title_gap"]
            else:
                continue
            out.append(("title %r" % T.plain(sp["title"]), [tx, ty, tw, th]))
    return out


def check_legend_placement(fig, rep):
    """The legend must sit in free space, not on a module or a label."""
    if not (fig.spec.get("legend") or {}).get("items"):
        return
    bx, by, bw, bh = fig.legend_layout()["box"]
    W, H = fig._viewbox
    if bx < -0.5 or by < -0.5 or bx + bw > W + 0.5 or by + bh > H + 0.5:
        rep.error("legend-off-canvas", "the legend extends past the canvas")

    def inter(a, b):
        w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
        h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
        return w > 0.5 and h > 0.5
    box = (bx + 1.0, by + 1.0, bw - 2.0, bh - 2.0)
    for it in _boxes(fig):
        if it.spec.get("shape") == "spacer":
            continue
        if inter(box, it.body()):
            rep.warn("legend-overlap", "the legend sits on %s; move it with legend: "
                     "{corner: ..} or {at: [x, y]}" % (it.id or "a module"), "legend")
            return
    for where, r in text_rects(fig):
        if inter(box, r):
            rep.warn("legend-overlap", "the legend sits on %s" % where, "legend")
            return


def check_canvas_bounds(fig, rep):
    """Nothing may print outside the canvas; LaTeX would crop it."""
    W, H = fig._viewbox
    for where, (x, y, w, h) in text_rects(fig):
        if x < -0.5 or y < -0.5 or x + w > W + 0.5 or y + h > H + 0.5:
            rep.error("text-off-canvas", "%s extends past the canvas" % where)
    for r in fig.routes:
        if any(p[0] < -0.5 or p[1] < -0.5 or p[0] > W + 0.5 or p[1] > H + 0.5 for p in r.pts):
            rep.error("edge-off-canvas", "edge %s -> %s leaves the canvas; add figure "
                      "padding or choose other ports" % (r.spec["from"], r.spec["to"]),
                      "edge#%d" % r.index)


def check_edges_through_text(fig, rep):
    """A wire drawn through a caption, title or label makes it unreadable."""
    rects = text_rects(fig)
    for r in fig.routes:
        own = set()
        if r.spec.get("label"):
            own.add("edge %s -> %s label" % (r.spec["from"], r.spec["to"]))
        for p, q in zip(r.pts, r.pts[1:]):
            for where, (x, y, w, h) in rects:
                if any(where.startswith(o) for o in own):
                    continue
                box = type("B", (), {"x": x + 0.8, "y": y + 0.8, "w": w - 1.6, "h": h - 1.6})
                if _seg_hits_rect(p, q, box, 0.0):
                    rep.warn("edge-crosses-text",
                             "edge %s -> %s runs through %s"
                             % (r.spec["from"], r.spec["to"], where), "edge#%d" % r.index)
                    break
            else:
                continue
            break
        for p, q in zip(r.pts, r.pts[1:]):
            if abs(p[0] - q[0]) > 0.3 and abs(p[1] - q[1]) > 0.3:
                rep.warn("diagonal-edge",
                         "edge %s -> %s has a diagonal run (%.1f, %.1f) -> (%.1f, %.1f); "
                         "align the ports or let the router turn it"
                         % (r.spec["from"], r.spec["to"], p[0], p[1], q[0], q[1]),
                         "edge#%d" % r.index)
                break


def check_label_overlap(fig, rep):
    """Edge labels and notes must not sit on a module or on each other."""
    solid = [b for b in _boxes(fig) if b.spec.get("shape", "box") not in
             ("text", "note", "mathlabel", "spacer", "brace", "ellipsis")]
    labels = label_rects(fig)

    def inter(a, b):
        w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
        h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
        return w * h if w > 0 and h > 0 else 0.0
    for i, (where, r) in enumerate(labels):
        area = max(r[2] * r[3], 1e-6)
        shrink = [r[0] + 0.6, r[1] + 0.8, r[2] - 1.2, r[3] - 1.6]   # glyph ink, not box
        for b in solid:
            if inter(shrink, (b.x, b.y, b.w, b.h)) > 0.08 * area:
                rep.warn("label-overlap", "%s sits on %s" % (where, b.id or "a module"),
                         b.id)
                break
        for where2, r2 in labels[i + 1:]:
            if inter(shrink, r2) > 0.08 * area:
                rep.warn("label-overlap", "%s overlaps %s" % (where, where2))


def check_reachability(fig, rep):
    ids = set(n.id for n in _boxes(fig) if n.id)
    touched = set()
    for ed in (fig.spec.get("edges") or []):
        for key in ("from", "to"):
            ref = str(ed.get(key, ""))
            touched.add(ref.rsplit(".", 1)[0] if "." in ref else ref)
    orphans = sorted(i for i in ids - touched
                     if fig.nodes[i].spec.get("shape") not in
                     ("text", "note", "mathlabel", "ellipsis", "image", "photo",
                      "imagestack", "spacer", "brace"))
    if orphans:
        rep.note("unconnected",
                 "no edge touches: %s -- intentional, or a missing arrow?"
                 % ", ".join(orphans[:8]))


def check_shape(fig, rep):
    """Page economy.  A double-column figure taller than ~0.55 of its width
    swallows most of a page; a single-column one has far more vertical room."""
    w, h = fig.meta["canvas_pt"]
    single = w < 300
    limit = 1.45 if single else 0.58
    if h > w * limit:
        rep.warn("tall-figure",
                 "%.0f x %.0f pt exceeds the %s-column height budget (%.2f x width); "
                 "it will push text off the page"
                 % (w, h, "single" if single else "double", limit))
    if h < w * 0.12:
        rep.note("thin-figure", "%.0f x %.0f pt is a very flat band" % (w, h))


# --------------------------------------------------------------- style
def check_palette(fig, rep):
    used, families = {}, set()
    for it in _boxes(fig):
        sp = it.spec
        if sp.get("shape") in ("spacer", "text", "note", "mathlabel"):
            continue
        from cvprfig.shapes import node_colors
        fill, stroke, _ = node_colors(sp)
        for col in (fill, stroke):
            if not col or col == "none":
                continue
            c = col.upper()
            used[c] = used.get(c, 0) + 1
            if c not in PALETTE:
                rep.warn("off-palette",
                         "%s uses %s, which is not in the house tint ladder" % (it.id, c),
                         it.id)
        for fam, ladder in style.FAMILIES.items():
            if fill and fill.upper() in (x.upper() for x in ladder) and fam != "grey":
                families.add(fam)
    if len(families) > MAX_HUE_FAMILIES:
        rep.warn("too-many-hues",
                 "%d colour families in one figure (%s); the reference corpus "
                 "median is 5 and its 75th percentile is 9, so past %d colour "
                 "has stopped meaning anything"
                 % (len(families), ", ".join(sorted(families)), MAX_HUE_FAMILIES))
    return used


def check_real_content(fig, rep):
    """Framework figures in this literature show real data, not just boxes.

    57% of the architecture diagrams in the reference corpus embed at least
    one raster -- median 11 of them, about a quarter of the canvas -- almost
    always inputs on the left and predictions on the right.  A pure box
    diagram is legal, but for anything pipeline-sized it is worth a nudge.
    """
    nodes = _boxes(fig)
    if len(nodes) < 6:
        return
    slots = [it for it in nodes
             if it.spec.get("shape") in ("image", "photo", "imagestack",
                                         "imagegrid", "cameraring", "surroundview")]
    if not slots:
        rep.warn("no-real-content",
                 "%d boxes and no image slot; 57%% of framework figures in the "
                 "reference corpus show real inputs and predictions alongside the "
                 "architecture (shape: image / cameraring / imagegrid)" % len(nodes))
        return
    empty = [it.id for it in slots if not it.spec.get("src") and not it.spec.get("srcs")]
    if empty:
        rep.warn("empty-image-slot",
                 "%d image slot(s) have no `src`: %s -- these export as crossed "
                 "placeholder boxes" % (len(empty), ", ".join(empty[:5])))


def check_image_resolution(fig, rep):
    """Effective resolution of every embedded raster at its printed size.

    pixels wide / printed inches wide.  Under 600 ppi is soft next to vector
    lines at 100% zoom; under 300 ppi is visibly blurred in print.
    """
    from cvprfig import imgsize
    for it in _boxes(fig):
        sp = it.spec
        shape = sp.get("shape")
        srcs = []
        if shape in ("image", "photo", "imagestack") and sp.get("src"):
            srcs = [(sp["src"], it.w)]
        elif shape in ("imagegrid",) and sp.get("srcs"):
            srcs = [(p, float(sp.get("cell", 34.0))) for p in sp["srcs"]]
        elif shape in ("cameraring", "surroundview") and sp.get("srcs"):
            srcs = [(p, float(sp.get("cell", 30.0))) for p in sp["srcs"]]
        for src, wpt in srcs:
            path = os.path.join(fig.base or "", src)
            if not os.path.exists(path):
                rep.error("missing-image", "%s points at %s, which does not exist"
                          % (it.id, src), it.id)
                continue
            if path.lower().endswith((".pdf", ".svg", ".eps")):
                continue                       # vector, resolution-free
            wh = imgsize.size(path)
            if not wh:
                continue
            inches = wpt * fig.scale / 72.0
            ppi = wh[0] / inches if inches else 0
            if ppi < 300:
                rep.error("image-low-ppi",
                          "%s: %s is %d px wide printed at %.2f in -> %.0f ppi (< 300); "
                          "it will print blurred" % (it.id, src, wh[0], inches, ppi), it.id)
            elif ppi < 600:
                rep.warn("image-soft",
                         "%s: %s prints at %.0f ppi (< 600); soft next to vector lines"
                         % (it.id, src, ppi), it.id)


# ------------------------------------------------------------ symbols
OP_GLYPH = {"+": "\u2295", "add": "\u2295", "sum": "\u2295", "oplus": "\u2295",
            "x": "\u2297", "*": "\u2297", "mul": "\u2297", "otimes": "\u2297",
            "c": "\u00a9", "concat": "\u00a9", "cat": "\u00a9", "-": "\u2296",
            "sub": "\u2296"}
# How a caption usually writes each glyph.
GLYPH_TEX = {"\u2295": ("\\oplus", "\u2295"), "\u2297": ("\\otimes", "\u2297"),
             "\u2299": ("\\odot", "\u2299"), "\u2296": ("\\ominus", "\u2296"),
             "\u00a9": ("\\textcircled", "\\copyright", "\u00a9", "\u24b8"),
             "\u2218": ("\\circ",), "\u03c3": ("\\sigma",), "\u2211": ("\\sum",),
             "\u03a3": ("\\Sigma",), "\u220f": ("\\prod",), "\u2016": ("\\|", "\\Vert"),
             "[;]": ("[", ";")}


def symbol_key(raw):
    """File a symbol-table key the way ``text.symbols`` files a label."""
    raw = str(raw).strip()
    inner = raw.strip("$")
    if inner in OP_GLYPH:
        return OP_GLYPH[inner]
    if inner in ("[;]", "[\\cdot;\\cdot]", "[\u00b7;\u00b7]", "[.;.]", "[;;]"):
        return "[;]"
    found = T.symbols("$%s$" % inner) or T.symbols(inner)
    return found[0]["key"] if found else inner


def figure_symbols(fig):
    """(key, shown, where, upright, label) for every symbol the figure prints."""
    out = []
    for where, txt, _, kind in text_elements(fig):
        for sym in T.symbols(txt):
            out.append((sym["key"], sym["shown"], where, sym["upright"], txt))
    for it in _boxes(fig):
        if it.spec.get("shape") in ("circleop", "op"):
            op = str(it.spec.get("op", it.spec.get("text", "+")))
            g = OP_GLYPH.get(op, op)
            out.append((g, g, it.id or "?", False, ""))
    return out


def read_caption(fig):
    """The caption text: ``caption:`` in the spec, or ``caption_tex:`` --
    ``path/to/sec.tex#fig:label`` -- read out of the paper source."""
    cap = fig.spec.get("caption")
    if isinstance(cap, str) and cap.strip():
        return cap, "spec"
    ref = fig.spec.get("caption_tex")
    if not ref:
        return None, None
    path, _, label = str(ref).partition("#")
    full = path if os.path.isabs(path) else os.path.join(fig.base or "", path)
    if not os.path.exists(full):
        return None, "missing:%s" % path
    src = open(full, encoding="utf-8", errors="replace").read()
    for m in re.finditer(r"\\begin\{figure\*?\}(.*?)\\end\{figure\*?\}", src, re.S):
        body = m.group(1)
        if label and ("\\label{%s}" % label) not in body:
            continue
        k = body.find("\\caption")
        if k < 0:
            continue
        j = body.find("{", k)
        depth, e = 0, j
        while e < len(body):
            depth += {"{": 1, "}": -1}.get(body[e], 0)
            if depth == 0:
                break
            e += 1
        return body[j + 1:e], path
    return None, "nolabel:%s" % ref


def caption_symbols(cap):
    """Symbol keys a caption defines: every symbol in its maths, plus
    operators written as commands outside maths."""
    keys = set()
    for m in re.finditer(r"\$([^$]+)\$", cap):
        for sym in T.symbols("$%s$" % m.group(1)):
            keys.add(sym["key"])
        if re.search(r"\[[^\]]*;", m.group(1)):
            keys.add("[;]")
    for glyph, forms in GLYPH_TEX.items():
        if glyph != "[;]" and any(f in cap for f in forms):
            keys.add(glyph)
    if re.search(r"\[\s*\\cdot\s*;", cap) or re.search(r"\[\\,?\s*\\cdot", cap):
        keys.add("[;]")
    return keys


def check_symbols(fig, rep):
    """Every symbol in the figure is defined where a reader looking only at
    the figure and its caption will find it.

    Reviewers read the figures before the text.  A ``c_s`` defined only in
    Section 3 sends them hunting.  The ``symbols:`` table (in the spec or in
    the shared ``paper.yaml``) says, for each symbol, what it means, where it
    is defined (``figure`` -- spelled out in a label next to it -- or
    ``caption``) and how the text writes it.
    """
    table = fig.spec.get("symbols") or {}
    own = set(symbol_key(k) for k in ((fig.spec.get("_own_symbols") or {})))
    defined = {}
    for k, v in table.items():
        v = v if isinstance(v, dict) else {"meaning": str(v)}
        defined[symbol_key(k)] = dict(v, _raw=k)
    used = figure_symbols(fig)
    cap, cap_src = read_caption(fig)
    cap_keys = caption_symbols(cap) if cap else set()
    reported = set()
    for key, shown, where, upright, label in used:
        if upright and key not in reported:
            rep.warn("upright-variable",
                     "%r is set upright; the text sets it in maths italic. Write it "
                     "as $%s$" % (shown, shown.replace("_", "_{", 1) + "}"
                                  if "_" in shown else shown), where)
        if key in reported:
            continue
        reported.add(key)
        d = defined.get(key)
        if d is None:
            if key in cap_keys:
                rep.note("symbol-caption-only",
                         "%r is defined in the caption but not in symbols:" % shown, where)
                continue
            rep.error("undefined-symbol",
                      "%r appears in the figure but is defined neither in a label nor in "
                      "the caption; add it to symbols: with its meaning and where it is "
                      "defined" % shown, where)
            continue
        loc = str(d.get("defined_in", "caption")).lower()
        if loc == "caption":
            if cap is None:
                if cap_src and cap_src.startswith(("missing", "nolabel")):
                    rep.warn("caption-unreadable", "caption_tex %s could not be read"
                             % cap_src.split(":", 1)[1])
                continue
            if key not in cap_keys:
                rep.error("symbol-not-in-caption",
                          "%r is marked defined_in: caption, but the caption never "
                          "writes it" % shown, where)
        elif loc in ("figure", "label"):
            labels = [lb for k2, _, _, _, lb in used if k2 == key and lb]
            if not any(re.search(r"[A-Za-z]{3,}", T.plain(re.sub(r"\$[^$]*\$", "", lb)))
                       for lb in labels):
                rep.warn("symbol-label-bare",
                         "%r is marked defined_in: figure, but no label beside it names "
                         "it in words" % shown, where)
        else:
            rep.warn("symbol-defined-elsewhere",
                     "%r is defined in %r; a reader of the figure alone needs it in a "
                     "label or the caption" % (shown, loc), where)
        tex = d.get("tex")
        if tex and upright:
            rep.warn("symbol-style", "%r is written %s in the text but upright here"
                     % (shown, tex), where)
    keys_used = set(k for k, *_ in used)
    for key, d in defined.items():
        if key not in keys_used and (key in own or not fig.paper):
            rep.warn("unused-symbol", "symbols: lists %r but the figure never shows it"
                     % d["_raw"])
    if cap:
        words = len(re.sub(r"\$[^$]*\$|\\[a-zA-Z]+", " ", cap).split())
        if words > 84:
            rep.note("caption-long",
                     "caption is %d words (corpus p90 is 84); consider moving symbol "
                     "definitions into labels, and recompile to check the page count"
                     % words)
    elif used and not table:
        pass


# ------------------------------------------------------------- legend
def _edge_style_key(ed):
    color, lw, dash = edgemod.edge_style(ed)
    return (color.upper(), bool(dash))


def _picture_paths(fig):
    """Files shown in the figure's image slots."""
    out = []
    for it in L.walk(fig.items):
        sp = it.spec
        for src in ([sp["src"]] if sp.get("src") else []) + list(sp.get("srcs") or []):
            path = os.path.join(fig.base or "", str(src))
            if os.path.isfile(path) and path not in out:
                out.append(path)
    return out


def check_legend(fig, rep):
    """The legend and the figure must agree both ways.

    Every legend entry's line style or swatch must occur in the figure (a
    leftover entry for a deleted arrow sends readers hunting), and every line
    style that carries meaning -- dashed, or coloured -- must be in the legend
    or carry its own label.
    """
    lg = fig.spec.get("legend") or {}
    items = lg.get("items") or []
    edge_styles = {}
    for r in fig.routes:
        edge_styles.setdefault((r.color.upper(), bool(r.dash)), []).append(r)
    fills = set()
    from cvprfig.shapes import node_colors
    for it in L.walk(fig.items):
        if it.kind == "node":
            fills.add((node_colors(it.spec)[0] or "").upper())
        elif it.spec.get("fill"):
            fills.add(style.resolve_color(it.spec.get("fill"), "none").upper())
    legend_lines = set()
    pics = imgcolor.Pictures(_picture_paths(fig))
    in_pics, absent = [], []

    def unused(txt, col, what, e):
        # A class legend (occupancy, segmentation) explains colours that only
        # the renders in image slots use.
        seen = True if e.get("in_image") else imgcolor.lookup(col, pics)
        if seen is True or (seen and seen[0]):
            in_pics.append(txt)
        elif seen:
            absent.append((txt, col, what, seen[1]))
        elif seen is None:
            rep.warn("legend-unused-entry", what + "; install Pillow or PyMuPDF to look for it "
                     "in the image slots, or mark the entry `in_image: true`", "legend")
        else:
            rep.error("legend-unused-entry", what, "legend")

    for e in items:
        kind = e.get("kind", "line")
        col = style.resolve_color(e.get("color"), style.INK, ink=True).upper()
        txt = T.plain(e.get("text", ""))
        if kind in ("line", "arrow", "dashed"):
            key = (col, kind == "dashed" or bool(e.get("dash")))
            legend_lines.add(key)
            if key not in edge_styles:
                unused(txt, key[0], "legend entry %r shows a %s %s line that no edge in the "
                       "figure uses" % (txt, "dashed" if key[1] else "solid", key[0]), e)
        else:
            f = style.resolve_color(e.get("fill"), col).upper()
            if f not in fills:
                unused(txt, f, "legend entry %r shows fill %s, which no module uses" % (txt, f), e)
    if absent and len(in_pics) >= max(2, len(absent)):
        rep.warn("legend-unused-entry",
                 "%d class(es) of a picture legend occur in no image slot: %s. Drop them from "
                 "this figure's legend, or keep the full palette on purpose to match the other "
                 "figures" % (len(absent), ", ".join("%s %s" % (t, c) for t, c, _, _ in absent)),
                 "legend")
    else:
        for t, c, what, v in absent:
            rep.error("legend-unused-entry", what + " (%.3f %% of an image slot at most)"
                      % (100 * v), "legend")
    def family(col):
        col = (col or "").upper()
        for fam, ladder in style.FAMILIES.items():
            if col in (c.upper() for c in ladder):
                return fam
        return None

    def families_around(it):
        """Fill families of a node and of every container it sits in."""
        out = set()
        while it is not None:
            if it.kind == "node":
                out.add(family(node_colors(it.spec)[0]))
            elif it.spec.get("fill"):
                out.add(family(style.resolve_color(it.spec["fill"], "none")))
            it = it.parent
        return out

    def text_colours(it):
        sp = it.spec
        return set(style.resolve_color(sp.get(k), "", ink=True).upper()
                   for k in ("text_color", "caption_color", "title_color") if sp.get(k))

    def tied(r):
        # An arrow coloured after the module or stage it leaves or enters --
        # or after the coloured label of either end -- explains itself.
        if r.color.upper() in (text_colours(r.a) | text_colours(r.b)):
            return True
        fam = family(r.color)
        return fam is not None and fam in (families_around(r.a) | families_around(r.b))

    for key, rs in edge_styles.items():
        meaningful = key[1] or key[0] not in (style.INK.upper(), "#000000", "#404040")
        if not meaningful or key in legend_lines:
            continue
        rs = [r for r in rs if not r.spec.get("label") and (key[1] or not tied(r))]
        if not rs:
            continue
        rep.warn("unexplained-line-style",
                 "%d edge(s) drawn %s %s are neither in the legend nor labelled (%s)"
                 % (len(rs), "dashed" if key[1] else "solid", key[0],
                    ", ".join("%s->%s" % (r.spec["from"], r.spec["to"]) for r in rs[:3])))


# ------------------------------------------------------- AI-palette tells
# Stock colours of the web UI kits (Tailwind, Material, Bootstrap) and of the
# "modern" palettes image models and LLM-written plotting code reach for.  A
# research figure drawn in PowerPoint never lands on these by accident.
AI_COLOURS = {
    # Tailwind 400-700
    "#3B82F6": "Tailwind blue-500", "#2563EB": "Tailwind blue-600",
    "#1D4ED8": "Tailwind blue-700", "#60A5FA": "Tailwind blue-400",
    "#6366F1": "Tailwind indigo-500", "#4F46E5": "Tailwind indigo-600",
    "#8B5CF6": "Tailwind violet-500", "#7C3AED": "Tailwind violet-600",
    "#A855F7": "Tailwind purple-500", "#9333EA": "Tailwind purple-600",
    "#EC4899": "Tailwind pink-500", "#DB2777": "Tailwind pink-600",
    "#F43F5E": "Tailwind rose-500", "#EF4444": "Tailwind red-500",
    "#F97316": "Tailwind orange-500", "#F59E0B": "Tailwind amber-500",
    "#EAB308": "Tailwind yellow-500", "#84CC16": "Tailwind lime-500",
    "#22C55E": "Tailwind green-500", "#10B981": "Tailwind emerald-500",
    "#14B8A6": "Tailwind teal-500", "#06B6D4": "Tailwind cyan-500",
    "#0EA5E9": "Tailwind sky-500", "#64748B": "Tailwind slate-500",
    "#DBEAFE": "Tailwind blue-100", "#E0E7FF": "Tailwind indigo-100",
    "#EDE9FE": "Tailwind violet-100", "#F3E8FF": "Tailwind purple-100",
    "#FCE7F3": "Tailwind pink-100", "#D1FAE5": "Tailwind emerald-100",
    "#FEF3C7": "Tailwind amber-100", "#CCFBF1": "Tailwind teal-100",
    # Material 500
    "#2196F3": "Material blue", "#3F51B5": "Material indigo",
    "#673AB7": "Material deep purple", "#9C27B0": "Material purple",
    "#E91E63": "Material pink", "#F44336": "Material red",
    "#FF5722": "Material deep orange", "#FF9800": "Material orange",
    "#4CAF50": "Material green", "#009688": "Material teal",
    "#00BCD4": "Material cyan", "#607D8B": "Material blue grey",
    # Bootstrap
    "#0D6EFD": "Bootstrap primary", "#6610F2": "Bootstrap indigo",
    "#6F42C1": "Bootstrap purple", "#D63384": "Bootstrap pink",
    "#198754": "Bootstrap success", "#20C997": "Bootstrap teal",
    "#0DCAF0": "Bootstrap info", "#FFC107": "Bootstrap warning",
    "#DC3545": "Bootstrap danger",
    # "Ocean dusk"-style picks from generated plotting code
    "#4A90D9": "generated-figure blue", "#5BA58B": "generated-figure teal",
    "#D4A252": "generated-figure amber", "#7B8794": "generated-figure slate",
    "#D6E4F0": "generated-figure wash", "#F5DEB3": "wheat wash",
}


def _hsl(hexcol):
    import colorsys
    h = hexcol.lstrip("#")
    if len(h) != 6:
        return 0, 0, 1
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
    return hh, ss, ll


SMALL_MARKS = ("tokengrid", "framestrip", "marker", "voxelgrid", "voxel", "gaussians",
               "dist", "density", "lane", "planestack", "featmaps", "slab", "slabstack",
               "cube", "ellipsis", "brace", "bar")


def check_ai_palette(fig, rep):
    """Colours that make a figure read as generated rather than drawn."""
    from cvprfig.shapes import node_colors
    seen = {}

    def note(col, where):
        if col and col != "none":
            seen.setdefault(col.upper(), where)
    tinted = boxes = 0
    for it in _boxes(fig):
        sp = it.spec
        fill, stroke, tc = node_colors(sp)
        note(fill, it.id); note(stroke, it.id); note(tc, it.id)
        shape = sp.get("shape", "box")
        if shape in L.BOXY:
            boxes += 1
            if stroke and stroke.upper() not in ("#000000", "NONE") and \
                    _hsl(stroke)[1] > 0.25:
                tinted += 1
            if fill and fill != "none" and it.w * it.h > 150:
                _, sat, light = _hsl(fill)
                if sat > 0.45 and light < 0.72:
                    rep.warn("saturated-fill",
                             "%s is filled %s; module fills are pale tints (light/pale "
                             "rungs), saturated colour belongs on arrows and small marks"
                             % (it.id, fill.upper()), it.id)
    for r in fig.routes:
        note(r.color, "edge#%d" % r.index)
    for col, where in seen.items():
        if col in AI_COLOURS:
            rep.error("ai-palette",
                      "%s is %s -- a web-UI default that image models and generated "
                      "plotting code reach for; pick from the Office ladder instead "
                      "(references/house-style.md)" % (col, AI_COLOURS[col]), where)
    if boxes >= 4 and tinted * 2 > boxes:
        rep.warn("tinted-outlines",
                 "%d of %d modules have colour-matched outlines; pale fill + darker "
                 "outline of the same hue is the UI-kit card look. Published figures "
                 "outline in black or grey" % (tinted, boxes))
    arrow_hues = set()
    for r in fig.routes:
        hh, ss, _ = _hsl(r.color)
        if ss > 0.3:
            arrow_hues.add(int(hh * 12))
    if len(arrow_hues) > 3:
        rep.warn("arrow-rainbow",
                 "arrows use %d different hues; colour an arrow only to tie it to the "
                 "module it comes from, and keep the rest black" % len(arrow_hues))


def check_role_consistency(fig, rep):
    """One concept, one colour.

    A node may opt out with ``same_label_ok: true`` -- but only do that when
    two identically named things are *deliberately* contrasted (an online and
    a momentum copy, say).  Usually the right fix is to name them apart.
    """
    by_label = {}
    for it in _boxes(fig):
        sp = it.spec
        txt = (sp.get("text") or "").strip()
        if not txt or sp.get("shape") in ("text", "note", "mathlabel"):
            continue
        if sp.get("same_label_ok"):
            continue
        from cvprfig.shapes import node_colors
        fill, _, _ = node_colors(sp)
        by_label.setdefault(txt, set()).add(fill)
    for txt, fills in by_label.items():
        if len(fills) > 1:
            rep.error("inconsistent-role",
                      "%r is drawn in %d different fills (%s); one concept, one colour"
                      % (txt[:32], len(fills), ", ".join(sorted(fills))))


def check_labels(fig, rep):
    for it in _boxes(fig):
        sp = it.spec
        txt = (sp.get("text") or "").strip()
        if not txt or sp.get("shape") in ("text", "note", "mathlabel"):
            continue
        plain = re.sub(r"[*_^{}]", "", txt).replace("\n", " ")
        words = [w for w in plain.split() if w]
        if len(words) > MAX_LABEL_WORDS:
            rep.warn("label-verbose",
                     "%r is %d words; module labels are noun phrases, not sentences"
                     % (plain[:40], len(words)), it.id)
        if plain.endswith("."):
            rep.warn("label-period", "%r ends in a full stop" % plain[:40], it.id)
        if plain.isupper() and len(plain) > 4:
            rep.note("label-shouting", "%r is all caps" % plain[:40], it.id)
        if re.search(r"[\U0001F300-\U0001FAFF✀-➿]", plain):
            rep.error("emoji", "%r contains an emoji or dingbat" % plain[:40], it.id)


def check_strokes(fig, rep):
    weights = set()
    for it in _boxes(fig):
        weights.add(round(style.stroke_width(it.spec.get("lw"), style.STROKE["box"]), 2))
    for ed in (fig.spec.get("edges") or []):
        weights.add(round(style.stroke_width(ed.get("lw"), style.STROKE["flow"]), 2))
    if len(weights) > MAX_STROKE_WEIGHTS:
        rep.warn("stroke-zoo",
                 "%d distinct line weights (%s); pick a hairline, a box weight and a "
                 "flow weight and stop there"
                 % (len(weights), ", ".join(str(w) for w in sorted(weights))))


def check_fonts(fig, rep):
    fams = set()
    for it in _boxes(fig):
        fams.add(it.spec.get("_font", fig.font))
    if len(fams) > 1:
        rep.warn("mixed-fonts",
                 "the figure mixes %s; match the venue body font instead"
                 % " and ".join(sorted(fams)))


SVG_TELLS = [
    (r"<linearGradient|<radialGradient|url\(#grad", "gradient",
     "gradients read as slide decoration, not as a method figure"),
    (r"feDropShadow|filter\s*=|<filter", "shadow",
     "drop shadows and filters do not survive greyscale printing"),
    (r"font-family='[^']*(Comic|Papyrus|Impact)", "novelty-font", "novelty font"),
]


def check_svg(path, rep):
    if not path or not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        blob = fh.read()
    for pat, code, msg in SVG_TELLS:
        if re.search(pat, blob):
            rep.warn(code, msg, os.path.basename(path))
    ops = [float(m) for m in re.findall(r"opacity='([0-9.]+)'", blob)]
    faint = [o for o in ops if 0 < o < 0.35]
    if faint:
        rep.note("faint-elements",
                 "%d element(s) below 35%% opacity may vanish in print" % len(faint))


# ------------------------------------------------------------------ main
def audit(spec_path, svg_path=None, base=None):
    """Audit a spec given as a path or as an already-loaded dict."""
    if isinstance(spec_path, dict):
        spec, base = spec_path, base or os.getcwd()
    else:
        spec = load_path(spec_path)
        base = base or os.path.dirname(os.path.abspath(spec_path))
    spec["_own_symbols"] = dict(spec.get("symbols") or {})
    fig = Figure(spec, base=base)
    fig.render()
    with fig.scope():
        return fig, _run_checks(fig, svg_path)


def _run_checks(fig, svg_path):
    rep = Report()
    for w in fig.warnings:
        rep.warn("engine", w)
    check_legibility(fig, rep)
    check_overflow(fig, rep)
    check_collisions(fig, rep)
    check_edges(fig, rep)
    check_arrow_runs(fig, rep)
    check_corridors(fig, rep)
    check_label_overlap(fig, rep)
    check_edges_through_text(fig, rep)
    check_canvas_bounds(fig, rep)
    check_legend_placement(fig, rep)
    check_reachability(fig, rep)
    check_shape(fig, rep)
    check_palette(fig, rep)
    check_real_content(fig, rep)
    check_image_resolution(fig, rep)
    check_role_consistency(fig, rep)
    check_symbols(fig, rep)
    check_legend(fig, rep)
    check_ai_palette(fig, rep)
    check_labels(fig, rep)
    check_strokes(fig, rep)
    check_fonts(fig, rep)
    check_svg(svg_path, rep)
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--svg", help="also audit a rendered SVG for gradients/shadows")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero on warnings as well as errors")
    args = ap.parse_args(argv)

    fig, rep = audit(args.spec, args.svg)
    counts = rep.counts()
    if args.json:
        print(json.dumps({"counts": counts, "findings": rep.rows, "meta": fig.meta},
                         indent=2))
    else:
        icon = {"error": "FAIL", "warning": "WARN", "note": "note"}
        for r in rep.rows:
            where = " [%s]" % r["where"] if r["where"] else ""
            print("%-4s %-20s %s%s" % (icon[r["level"]], r["code"], r["message"], where))
        m = fig.meta
        print("\n%d error(s), %d warning(s), %d note(s) | %.0f x %.0f pt | %d nodes"
              % (counts["error"], counts["warning"], counts["note"],
                 m["canvas_pt"][0], m["canvas_pt"][1], m["node_count"]))
    if counts["error"]:
        return 1
    if args.strict and counts["warning"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
