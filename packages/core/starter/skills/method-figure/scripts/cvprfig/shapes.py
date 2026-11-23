"""Shape library: the visual vocabulary of conference pipeline figures.

Each entry corresponds to an idiom that recurs across the reference corpus --
module boxes, trapezoid encoders, extruded query slabs, token grids, operator
circles, chevron memory arrows, image placeholders.  Sticking to this closed
vocabulary is deliberate: novel shapes are the fastest way to make a figure
look machine-made.
"""

from . import style, svg
from .svg import fmt, _shade

SLAB_D = style.GEOM["slab_depth"]


def node_colors(s):
    role = s.get("role", "neutral")
    fill = style.resolve_color(s.get("fill"), None)
    if fill is None:
        fill = style.fill_for(role, s.get("tint"))
    stroke = style.resolve_color(s.get("stroke"), None, ink=True)
    if stroke is None:
        stroke = style.INK if s.get("outline", "ink") == "ink" else style.stroke_for(role)
    if s.get("outline") == "match":
        stroke = style.stroke_for(role, "deep")
    if s.get("outline") in ("none", False):
        stroke = "none"
    tc = style.resolve_color(s.get("text_color"), style.INK, ink=True)
    return fill, stroke, tc


def draw(cv, item, ctx):
    s = item.spec
    shape = s.get("shape", "box")
    fill, stroke, tc = node_colors(s)
    sw = style.stroke_width(s.get("lw"), style.STROKE["box"])
    dash = style.DASH.get(s.get("dash")) if isinstance(s.get("dash"), str) else (
        style.DASH["region"] if s.get("dash") else None)
    x, y, w, h = item.body()
    fn = _DISPATCH.get(shape, _box)
    fn(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx)
    if s.get("caption"):
        svg.draw_text(cv, item.cx + float(s.get("caption_dx", 0)),
                      y + h + float(s.get("caption_gap", 3.0)) + s["_cap_size"] * 0.5,
                      s["caption"], s["_cap_size"], s["_font"], False,
                      s.get("caption_italic", False),
                      style.resolve_color(s.get("caption_color"), style.INK), "middle", "middle")
    if s.get("badge"):
        bs = float(s.get("badge_size", style.TYPE["annot"]["size"]))
        pos = s.get("badge_pos", "ne")
        bx, by = {"ne": (x + w - 1.0, y - 1.2), "nw": (x + 1.0, y - 1.2),
                  "se": (x + w - 1.0, y + h + bs), "sw": (x + 1.0, y + h + bs),
                  "n": (x + w / 2.0, y - 1.2)}.get(pos, (x + w - 1.0, y - 1.2))
        svg.draw_text(cv, bx, by, s["badge"], bs, s["_font"], False, True,
                      style.resolve_color(s.get("badge_color"), style.INK),
                      "end" if pos in ("ne", "se") else ("start" if pos in ("nw", "sw") else "middle"),
                      "middle")


def _label(cv, s, x, y, w, h, tc, dx=0.0, dy=0.0):
    if not s.get("text"):
        return
    rot = s.get("rotate", 0)
    anchor = s.get("anchor", "middle")
    # an anchored label starts (or ends) at the box edge, not at its centre
    ax = {"start": x, "left": x, "end": x + w, "right": x + w}.get(anchor, x + w / 2.0)
    svg.draw_text(cv, ax + dx, y + h / 2.0 + dy, s["text"], s["_tsize"],
                  s["_font"], s["_bold"], s["_italic"], tc,
                  s.get("anchor", "middle"), "middle", -rot if rot else 0)


# ------------------------------------------------------------------ boxes
def _box(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    r = float(s.get("corner", style.GEOM["corner"]))
    svg.rect(cv, x, y, w, h, r, fill, stroke, sw, dash, s.get("opacity"))
    _label(cv, s, x, y, w, h, tc)


def _sharpbox(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    svg.rect(cv, x, y, w, h, 0, fill, stroke, sw, dash, s.get("opacity"))
    _label(cv, s, x, y, w, h, tc)


def _trapezoid(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx, flip=False):
    slant = float(s.get("slant", 0.22)) * h
    d = s.get("dir", "right")
    if d in ("right", "e"):
        p = [(x, y), (x + w, y + slant), (x + w, y + h - slant), (x, y + h)]
    elif d in ("left", "w"):
        p = [(x, y + slant), (x + w, y), (x + w, y + h), (x, y + h - slant)]
    elif d in ("down", "s"):
        sl = float(s.get("slant", 0.22)) * w
        p = [(x, y), (x + w, y), (x + w - sl, y + h), (x + sl, y + h)]
    else:
        sl = float(s.get("slant", 0.22)) * w
        p = [(x + sl, y), (x + w - sl, y), (x + w, y + h), (x, y + h)]
    svg.poly(cv, p, fill, stroke, sw, dash, True, s.get("opacity"))
    _label(cv, s, x, y, w, h, tc)


def _invtrapezoid(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    s = dict(s); s["dir"] = {"right": "left", "left": "right"}.get(s.get("dir", "right"), "left")
    _trapezoid(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx)


def _chevron(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    pt = float(s.get("point", 7.0))
    if s.get("dir", "right") in ("left", "w"):
        p = [(x + w, y), (x + pt, y), (x, y + h / 2.0), (x + pt, y + h), (x + w, y + h)]
        _lbl_dx = pt / 2.0
    else:
        p = [(x, y), (x + w - pt, y), (x + w, y + h / 2.0), (x + w - pt, y + h), (x, y + h)]
        _lbl_dx = -pt / 2.0
    svg.poly(cv, p, fill, stroke, sw, dash, True, s.get("opacity"))
    _label(cv, s, x, y, w, h, tc, dx=_lbl_dx)


def _parallelogram(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    k = float(s.get("skew", 0.28)) * h
    p = [(x + k, y), (x + w, y), (x + w - k, y + h), (x, y + h)]
    svg.poly(cv, p, fill, stroke, sw, dash, True, s.get("opacity"))
    _label(cv, s, x, y, w, h, tc)


def _hexagon(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    k = min(h / 2.0, w * 0.16)
    p = [(x + k, y), (x + w - k, y), (x + w, y + h / 2.0),
         (x + w - k, y + h), (x + k, y + h), (x, y + h / 2.0)]
    svg.poly(cv, p, fill, stroke, sw, dash, True, s.get("opacity"))
    _label(cv, s, x, y, w, h, tc)


def _circle(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    cv.add("<ellipse cx='%s' cy='%s' rx='%s' ry='%s' %s/>"
           % (fmt(x + w / 2.0), fmt(y + h / 2.0), fmt(w / 2.0), fmt(h / 2.0),
              svg._sattrs(fill, stroke, sw, dash, s.get("opacity"))))
    _label(cv, s, x, y, w, h, tc)


# ------------------------------------------------------- tensors and tokens
def _slab(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    """One or more extruded cards -- the standard 'query / token bank' idiom."""
    n = int(s.get("n", 1))
    cw = float(s.get("cell", 8.0))
    gap = float(s.get("cellgap", 3.0))
    ch = h - SLAB_D
    colors = s.get("colors")
    for i in range(n):
        cx = x + i * (cw + gap)
        cy = y + SLAB_D
        f = fill
        if isinstance(colors, list) and colors:
            f = style.resolve_color(colors[i % len(colors)], fill)
        top = _shade(f, 1.16)
        side = _shade(f, 0.90)
        svg.poly(cv, [(cx, cy), (cx + SLAB_D, cy - SLAB_D),
                      (cx + cw + SLAB_D, cy - SLAB_D), (cx + cw, cy)], top, stroke, sw)
        svg.poly(cv, [(cx + cw, cy), (cx + cw + SLAB_D, cy - SLAB_D),
                      (cx + cw + SLAB_D, cy + ch - SLAB_D), (cx + cw, cy + ch)], side, stroke, sw)
        svg.rect(cv, cx, cy, cw, ch, 0, f, stroke, sw)
    if s.get("text"):
        svg.draw_text(cv, x + w / 2.0, y + h / 2.0, s["text"], s["_tsize"], s["_font"],
                      s["_bold"], s["_italic"], tc, "middle", "middle",
                      -s.get("rotate", 0) if s.get("rotate") else 0)


def _cube(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    d = SLAB_D * 1.6
    side = min(w - d, h - d)
    cx, cy = x, y + d
    top = _shade(fill, 1.16); rgt = _shade(fill, 0.9)
    svg.rect(cv, cx, cy, side, side, 0, fill, stroke, sw)
    svg.poly(cv, [(cx, cy), (cx + d, cy - d), (cx + side + d, cy - d), (cx + side, cy)], top, stroke, sw)
    svg.poly(cv, [(cx + side, cy), (cx + side + d, cy - d),
                  (cx + side + d, cy + side - d), (cx + side, cy + side)], rgt, stroke, sw)
    if s.get("grid"):
        g = int(s.get("grid", 3))
        for i in range(1, g):
            t = side * i / g
            svg.line(cv, cx + t, cy, cx + t, cy + side, stroke, sw * 0.8)
            svg.line(cv, cx, cy + t, cx + side, cy + t, stroke, sw * 0.8)
    _label(cv, s, x, y, w, h, tc)


def _tokengrid(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    rows = int(s.get("rows", 4)); cols = int(s.get("cols", 1))
    cell = float(s.get("cell", 8.0)); gap = float(s.get("cellgap", 2.4))
    colors = s.get("colors")
    r = float(s.get("corner", 1.2))
    for i in range(rows):
        for j in range(cols):
            f = fill
            if isinstance(colors, list) and colors:
                f = style.resolve_color(colors[(i * cols + j) % len(colors)], fill)
            svg.rect(cv, x + j * (cell + gap), y + i * (cell + gap), cell, cell, r, f, stroke, sw)


def _plane(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    """A tilted ground plane -- used for BEV / sparse-point illustrations."""
    k = float(s.get("skew", 0.34)) * w
    p = [(x + k, y), (x + w, y), (x + w - k, y + h), (x, y + h)]
    svg.poly(cv, p, fill, stroke, sw, dash, True, s.get("opacity"))
    _label(cv, s, x, y, w, h, tc)


def _op(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    d = min(w, h)
    cx, cy = x + w / 2.0, y + h / 2.0
    r = d / 2.0
    svg.circle(cv, cx, cy, r, fill if fill else "#FFFFFF", stroke, sw)
    sym = s.get("op", s.get("text", "+"))
    k = r * 0.56
    if sym in ("+", "add", "sum", "oplus"):
        svg.line(cv, cx - k, cy, cx + k, cy, stroke, sw)
        svg.line(cv, cx, cy - k, cx, cy + k, stroke, sw)
    elif sym in ("x", "*", "mul", "otimes"):
        d2 = k * 0.72
        svg.line(cv, cx - d2, cy - d2, cx + d2, cy + d2, stroke, sw)
        svg.line(cv, cx - d2, cy + d2, cx + d2, cy - d2, stroke, sw)
    elif sym in ("c", "concat", "cat"):
        svg.draw_text(cv, cx, cy, "C", d * 0.62, s["_font"], False, False, tc)
    elif sym in ("-", "sub"):
        svg.line(cv, cx - k, cy, cx + k, cy, stroke, sw)
    else:
        svg.draw_text(cv, cx, cy, sym, d * 0.62, s["_font"], False, True, tc)


def _image(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    """Embed a real asset when ``src`` is given; otherwise a labelled slot."""
    src = s.get("src")
    if src:
        path = src if not ctx.get("base") else __import__("os").path.join(ctx["base"], src)
        if __import__("os").path.exists(path):
            ext = path.rsplit(".", 1)[-1].lower()
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "gif": "image/gif", "svg": "image/svg+xml"}.get(ext, "image/png")
            import base64 as _b64
            with open(path, "rb") as fh:
                blob = _b64.b64encode(fh.read()).decode("ascii")
            cv.add("<image x='%s' y='%s' width='%s' height='%s' preserveAspectRatio='%s' "
                   "xlink:href='data:%s;base64,%s'/>"
                   % (fmt(x), fmt(y), fmt(w), fmt(h),
                      s.get("fit", "xMidYMid slice"), mime, blob))
            if s.get("outline", "ink") != "none":
                svg.rect(cv, x, y, w, h, 0, "none", stroke,
                         style.stroke_width(s.get("lw"), style.STROKE["hairline"]))
            return
    svg.rect(cv, x, y, w, h, 0, fill if s.get("fill") else style.FAMILIES["grey"][1],
             stroke, style.stroke_width(s.get("lw"), style.STROKE["hairline"]), dash)
    svg.line(cv, x, y, x + w, y + h, style.FAMILIES["grey"][4], style.STROKE["hairline"])
    svg.line(cv, x + w, y, x, y + h, style.FAMILIES["grey"][4], style.STROKE["hairline"])
    if s.get("text"):
        svg.draw_text(cv, x + w / 2.0, y + h / 2.0, s["text"], s["_tsize"], s["_font"],
                      s["_bold"], s["_italic"], style.MUTED_INK)


def _imagestack(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    n = int(s.get("n", 3)); off = float(s.get("offset", 3.2))
    for i in range(n - 1, 0, -1):
        svg.rect(cv, x + i * off, y - i * off, w - (n - 1) * off, h - (n - 1) * off, 0,
                 "#FFFFFF", stroke, style.STROKE["hairline"])
    inner = dict(s); inner["n"] = 1
    _image(cv, inner, x, y, w - (n - 1) * off, h - (n - 1) * off, fill, stroke, sw, dash, tc, ctx)


def _text(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    if s.get("fill"):
        svg.rect(cv, x, y, w, h, float(s.get("corner", 1.5)), fill, stroke if s.get("stroke") else "none", sw, dash)
    _label(cv, s, x, y, w, h, tc)


def _ellipsis(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    n = int(s.get("n", 3)); r = float(s.get("r", 1.05))
    step = w / (n + 1.0)
    col = style.resolve_color(s.get("color"), style.INK)
    vertical = s.get("dir") in ("down", "s", "vertical")
    for i in range(1, n + 1):
        if vertical:
            svg.circle(cv, x + w / 2.0, y + h * i / (n + 1.0), r, col, "none", 0)
        else:
            svg.circle(cv, x + step * i, y + h / 2.0, r, col, "none", 0)


def _spacer(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    if s.get("debug"):
        svg.rect(cv, x, y, w, h, 0, "none", "#FF0000", 0.3, "2,2")


def _bar(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    """A thin coloured band, for timelines and stage rails."""
    svg.rect(cv, x, y, w, h, float(s.get("corner", h / 2.0)), fill, stroke, sw, dash)
    _label(cv, s, x, y, w, h, tc)


def brace_path(x, y, w, h, direction="right"):
    """A curly brace built from four quadratic arcs, spanning ``h`` (vertical)
    or ``w`` (horizontal)."""
    if direction in ("right", "left"):
        k = max(min(w, h / 2.0 - 0.5), 1.2)
        mx = x + w if direction == "right" else x
        bx = x if direction == "right" else x + w
        my = y + h / 2.0
        return ("M%s,%s Q%s,%s %s,%s L%s,%s Q%s,%s %s,%s "
                "Q%s,%s %s,%s L%s,%s Q%s,%s %s,%s"
                % (fmt(bx), fmt(y), fmt(mx - (mx - bx)), fmt(y), fmt(mx - (mx - bx) * 0.0), fmt(y),
                   fmt(mx), fmt(my - k), fmt(mx), fmt(my - k),
                   fmt(mx + (bx - mx) * 0.0), fmt(my),
                   fmt(mx), fmt(my + k), fmt(mx), fmt(my + k),
                   fmt(mx), fmt(y + h - k), fmt(mx), fmt(y + h), fmt(bx), fmt(y + h)))
    k = max(min(h, w / 2.0 - 0.5), 1.2)
    my = y + h if direction == "down" else y
    mx = x + w / 2.0
    return ("M%s,%s Q%s,%s %s,%s L%s,%s Q%s,%s %s,%s Q%s,%s %s,%s L%s,%s Q%s,%s %s,%s"
            % (fmt(x), fmt(y), fmt(x), fmt(my), fmt(x), fmt(my),
               fmt(mx - k), fmt(my), fmt(mx - k), fmt(my), fmt(mx), fmt(my + (my - y)),
               fmt(mx), fmt(my), fmt(mx + k), fmt(my),
               fmt(x + w - k), fmt(my), fmt(x + w), fmt(my), fmt(x + w), fmt(y)))


def _brace(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    col = style.resolve_color(s.get("color"), style.INK, ink=True)
    lw = style.stroke_width(s.get("lw"), style.STROKE["hairline"] * 1.6)
    d = s.get("dir", "right")
    if d in ("right", "left"):
        span = float(s.get("depth", 3.0))
        cv.add("<path d='%s' fill='none' stroke='%s' stroke-width='%s' "
               "stroke-linecap='round'/>"
               % (brace_path(x, y, span, h, d), col, fmt(lw)))
    else:
        span = float(s.get("depth", 3.0))
        cv.add("<path d='%s' fill='none' stroke='%s' stroke-width='%s' "
               "stroke-linecap='round'/>"
               % (brace_path(x, y, w, span, d), col, fmt(lw)))


def _framestrip(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    """A run of per-timestep cells, filled where observed and hollow where
    missing.  The idiom for showing an observation window, a masking pattern
    or a missingness topology."""
    mask = frame_mask(s)
    n = len(mask)
    cell = float(s.get("cell", 5.0))
    gap = float(s.get("cellgap", 1.2))
    on = style.resolve_color(s.get("on_fill"), style.FAMILIES["steel"][4])
    off = style.resolve_color(s.get("off_fill"), style.FAMILIES["grey"][1])
    edge = style.resolve_color(s.get("cell_stroke"), "none")
    ew = style.stroke_width(s.get("cell_lw"), style.STROKE["hairline"])
    for i, bit in enumerate(mask):
        svg.rect(cv, x + i * (cell + gap), y, cell, h, float(s.get("corner", 0.6)),
                 on if bit else off, edge, ew)
    if s.get("text"):
        svg.draw_text(cv, x + w / 2.0, y + h / 2.0, s["text"], s["_tsize"], s["_font"],
                      s["_bold"], s["_italic"], tc)


def frame_mask(s):
    """Accept ``pattern: "..####.."``, ``mask: [0,1,1,0]`` or ``n``/``on``."""
    pat = s.get("pattern")
    if isinstance(pat, str):
        return [1 if c in "#1xX*" else 0 for c in pat]
    mask = s.get("mask")
    if isinstance(mask, list):
        return [1 if int(v) else 0 for v in mask]
    n = int(s.get("n", 20))
    on = int(s.get("on", n))
    return [1] * on + [0] * max(0, n - on)


def _dist(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    """A small density bump -- the compact way to say 'a predictive
    distribution' without plotting real numbers."""
    import math
    kind = s.get("kind", "gauss")
    spread = float(s.get("spread", 1.0))
    skew = float(s.get("skew", 0.0))
    steps = 48
    pts = []
    for i in range(steps + 1):
        t = i / float(steps)
        u = (t - 0.5) * 6.0 / max(spread, 0.15)
        if kind == "laplace":
            v = math.exp(-abs(u))
        elif kind == "bimodal":
            v = 0.62 * math.exp(-((u - 1.7) ** 2) / 1.1) + math.exp(-((u + 1.7) ** 2) / 1.1)
        else:
            v = math.exp(-(u ** 2) / 2.0)
        if skew:
            v *= (1.0 + skew * (t - 0.5) * 2.0)
        pts.append((x + t * w, y + h - max(v, 0.0) * h * 0.94))
    d = "M%s,%s " % (fmt(x), fmt(y + h)) + \
        " ".join("L%s,%s" % (fmt(a), fmt(b)) for a, b in pts) + \
        " L%s,%s Z" % (fmt(x + w), fmt(y + h))
    cv.add("<path d='%s' %s/>" % (d, svg._sattrs(fill, stroke, sw, None, s.get("opacity"))))
    if s.get("baseline", True):
        svg.line(cv, x, y + h, x + w, y + h, style.INK, style.STROKE["hairline"])
    if s.get("text"):
        svg.draw_text(cv, x + w / 2.0, y + h * 0.42, s["text"], s["_tsize"], s["_font"],
                      s["_bold"], s["_italic"], tc)



# ==========================================================================
# v2 vocabulary.  Every idiom below was counted in the reference corpus
# before it was implemented; see references/corpus-report.md for how often.
# ==========================================================================

def _iso(x, y, dx, dy):
    return (x + dx, y - dy)


def _voxelgrid(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    """Isometric cube subdivided into a g x g x g voxel lattice.

    The standard way this corpus draws a dense 3D feature volume: 26 of the
    137 diagrams contain one.  ``colors`` paints individual top-face cells so
    a figure can show occupancy or attention over the volume.
    """
    g = int(s.get("grid", 3))
    d = float(s.get("depth", style.GEOM["slab_depth"] * 1.7))
    side = min(w - d, h - d)
    cx, cy = x, y + d
    cell = side / g
    step = d / g
    top = _shade(fill, 1.16)
    rgt = _shade(fill, 0.88)
    colors = s.get("colors")

    def cell_fill(i, j, face):
        if isinstance(colors, list) and colors:
            return style.resolve_color(colors[(i * g + j) % len(colors)], fill)
        return {"f": fill, "t": top, "r": rgt}[face]

    # front face, then top, then right -- painter order keeps seams clean
    for i in range(g):
        for j in range(g):
            svg.rect(cv, cx + j * cell, cy + i * cell, cell, cell, 0,
                     cell_fill(i, j, "f"), stroke, sw)
    for i in range(g):
        for j in range(g):
            x0, y0 = cx + j * cell + i * step, cy - i * step
            svg.poly(cv, [(x0, y0), (x0 + step, y0 - step),
                          (x0 + step + cell, y0 - step), (x0 + cell, y0)],
                     cell_fill(i, j, "t"), stroke, sw)
    for i in range(g):
        for j in range(g):
            x0, y0 = cx + side + j * step, cy + i * cell - j * step
            svg.poly(cv, [(x0, y0), (x0 + step, y0 - step),
                          (x0 + step, y0 - step + cell), (x0, y0 + cell)],
                     cell_fill(i, j, "r"), stroke, sw)
    if s.get("text"):
        _label(cv, s, x, y, w, h, tc)


def _planestack(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    """Overlapping translucent planes: a multi-scale feature pyramid.

    GaussianFormer, CVT-Occ and PETR all draw multi-scale features this way --
    3-5 parallelograms offset diagonally, dashed outline, low opacity.
    """
    n = int(s.get("n", 4))
    k = float(s.get("skew", 0.22)) * w
    dx = float(s.get("offset", 3.0))
    dy = float(s.get("offset_y", 1.6))
    colors = s.get("colors")
    op = s.get("opacity", 0.85)
    pw = w - (n - 1) * dx - k
    ph = h - (n - 1) * dy
    for i in range(n - 1, -1, -1):
        f = fill
        if isinstance(colors, list) and colors:
            f = style.resolve_color(colors[i % len(colors)], fill)
        ox, oy = x + i * dx, y + (n - 1 - i) * dy
        svg.poly(cv, [(ox + k, oy), (ox + k + pw, oy),
                      (ox + pw, oy + ph), (ox, oy + ph)],
                 f, stroke, sw, dash, True, op)
    if s.get("text"):
        _label(cv, s, x, y, w, h, tc)


def _gaussians(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    """Scattered oriented ellipses -- the 3D-Gaussian primitive."""
    import math
    n = int(s.get("n", 7))
    colors = s.get("colors") or ["blue.soft", "green.soft", "orange.soft",
                                 "purple.soft", "grey.soft"]
    seed = int(s.get("seed", 7))
    # deterministic LCG: the same spec must always produce the same picture
    vals = []
    v = seed * 2654435761 % 2147483647
    for _ in range(n * 5):
        v = (v * 1103515245 + 12345) % 2147483648
        vals.append(v / 2147483648.0)
    for i in range(n):
        a, b, c, d, e = vals[i * 5:i * 5 + 5]
        rx = w * (0.07 + 0.06 * a)
        ry = rx * (0.42 + 0.4 * b)
        cx = x + rx + c * (w - 2 * rx)
        cy = y + ry + d * (h - 2 * ry)
        rot = -60 + 120 * e
        f = style.resolve_color(colors[i % len(colors)], fill)
        cv.add("<ellipse cx='%s' cy='%s' rx='%s' ry='%s' transform='rotate(%s %s %s)' "
               "fill='%s' stroke='%s' stroke-width='%s' opacity='%s'/>"
               % (fmt(cx), fmt(cy), fmt(rx), fmt(ry), fmt(rot), fmt(cx), fmt(cy),
                  f, stroke, fmt(sw), s.get("opacity", 0.9)))


def _marker(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    """Trainable / frozen indicator.

    The corpus draws these as a flame and a snowflake.  They are pasted-in
    emoji in the originals, which rasterises badly and fails accessibility
    checks, so they are vector paths here -- same reading, clean output.
    """
    kind = str(s.get("kind", s.get("text", "trainable"))).lower()
    d = min(w, h)
    cx, cy = x + w / 2.0, y + h / 2.0
    if kind in ("frozen", "snow", "snowflake", "freeze"):
        col = style.resolve_color(s.get("color"), "#2E75B6")
        r = d * 0.46
        import math
        for k in range(3):
            a = math.pi * k / 3.0
            dx, dy = r * math.cos(a), r * math.sin(a)
            svg.line(cv, cx - dx, cy - dy, cx + dx, cy + dy, col, sw * 1.3, None, "round")
            for sgn in (-1, 1):
                bx, by = cx + sgn * dx * 0.62, cy + sgn * dy * 0.62
                for da in (0.9, -0.9):
                    svg.line(cv, bx, by,
                             bx + r * 0.30 * math.cos(a + da + (0 if sgn > 0 else math.pi)),
                             by + r * 0.30 * math.sin(a + da + (0 if sgn > 0 else math.pi)),
                             col, sw, None, "round")
    else:
        col = style.resolve_color(s.get("color"), "#ED7D31")
        inner = style.resolve_color(s.get("inner"), "#FFC000")
        r = d * 0.48
        # Outer flame: tip at the top, belly at 40% width, curling base.
        cv.add("<path d='M %s %s C %s %s %s %s %s %s C %s %s %s %s %s %s Z' "
               "fill='%s' stroke='none'/>"
               % (fmt(cx), fmt(cy - r),
                  fmt(cx + r * 0.85), fmt(cy - r * 0.10),
                  fmt(cx + r * 0.66), fmt(cy + r * 0.72),
                  fmt(cx), fmt(cy + r),
                  fmt(cx - r * 0.66), fmt(cy + r * 0.72),
                  fmt(cx - r * 0.85), fmt(cy - r * 0.10),
                  fmt(cx), fmt(cy - r),
                  col))
        # Inner core, same silhouette at 55% scale, sitting low.
        r2 = r * 0.55
        cy2 = cy + r * 0.28
        cv.add("<path d='M %s %s C %s %s %s %s %s %s C %s %s %s %s %s %s Z' "
               "fill='%s' stroke='none'/>"
               % (fmt(cx), fmt(cy2 - r2),
                  fmt(cx + r2 * 0.85), fmt(cy2 - r2 * 0.10),
                  fmt(cx + r2 * 0.66), fmt(cy2 + r2 * 0.72),
                  fmt(cx), fmt(cy2 + r2),
                  fmt(cx - r2 * 0.66), fmt(cy2 + r2 * 0.72),
                  fmt(cx - r2 * 0.85), fmt(cy2 - r2 * 0.10),
                  fmt(cx), fmt(cy2 - r2),
                  inner))


def _cameraring(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    """Surround-view camera layout: 3 front tiles, ego icon, 3 rear tiles.

    This is the nuScenes/Waymo input block that opens most of the driving
    papers in the corpus.  ``srcs`` takes up to six image paths in the order
    front-left, front, front-right, back-left, back, back-right; missing
    entries fall back to labelled slots.
    """
    srcs = s.get("srcs") or []
    gap = float(s.get("cellgap", 1.6))
    cw = (w - 2 * gap) / 3.0
    ch = (h - gap) / 2.0
    labels = s.get("labels") or []
    for i in range(6):
        r, c = divmod(i, 3)
        cx0 = x + c * (cw + gap)
        cy0 = y + r * (ch + gap)
        if r == 1 and c == 1 and s.get("ego", True):
            _egocar(cv, cx0, cy0, cw, ch, s)
            continue
        sub = dict(s)
        sub["shape"] = "image"
        sub["src"] = srcs[i] if i < len(srcs) else None
        sub["text"] = labels[i] if i < len(labels) else None
        sub["caption"] = None
        _image(cv, sub, cx0, cy0, cw, ch, fill, stroke, sw, dash, tc, ctx)


def _egocar(cv, x, y, w, h, s):
    """Top-down ego-vehicle glyph with a forward FOV wedge."""
    col = style.resolve_color(s.get("ego_color"), "#4472C4")
    cx, cy = x + w / 2.0, y + h / 2.0
    bw, bh = w * 0.30, h * 0.52
    if s.get("fov", True):
        svg.poly(cv, [(cx, cy), (cx - w * 0.42, cy - h * 0.44), (cx + w * 0.42, cy - h * 0.44)],
                 _shade(col, 1.7), "none", 0, None, True, 0.5)
    svg.rect(cv, cx - bw / 2, cy - bh / 2, bw, bh, bw * 0.28, col,
             _shade(col, 0.7), style.STROKE["hairline"])
    svg.rect(cv, cx - bw * 0.31, cy - bh * 0.30, bw * 0.62, bh * 0.26, bw * 0.1,
             "#FFFFFF", "none", 0)


def _imagegrid(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    """rows x cols tile of real images -- qualitative result panels."""
    rows = int(s.get("rows", 1)); cols = int(s.get("cols", 3))
    gap = float(s.get("cellgap", 1.8))
    srcs = s.get("srcs") or []
    labels = s.get("labels") or []
    cw = (w - (cols - 1) * gap) / cols
    ch = (h - (rows - 1) * gap) / rows
    for i in range(rows * cols):
        r, c = divmod(i, cols)
        sub = dict(s)
        sub["shape"] = "image"
        sub["src"] = srcs[i] if i < len(srcs) else None
        sub["text"] = labels[i] if i < len(labels) else None
        sub["caption"] = None
        _image(cv, sub, x + c * (cw + gap), y + r * (ch + gap), cw, ch,
               fill, stroke, sw, dash, tc, ctx)


def _lane(cv, s, x, y, w, h, fill, stroke, sw, dash, tc, ctx):
    """A thick directional band -- the 'this whole row is one dataflow' idiom."""
    head = float(s.get("head", min(h * 1.1, w * 0.16)))
    hh = h * float(s.get("waist", 0.52))
    y0 = y + (h - hh) / 2.0
    pts = [(x, y0), (x + w - head, y0), (x + w - head, y),
           (x + w, y + h / 2.0), (x + w - head, y + h), (x + w - head, y0 + hh),
           (x, y0 + hh)]
    svg.poly(cv, pts, fill, stroke, sw, dash)
    if s.get("text"):
        svg.draw_text(cv, x + (w - head) / 2.0, y + h / 2.0, s["text"], s["_tsize"],
                      s["_font"], s["_bold"], s["_italic"], tc, "middle", "middle")


_DISPATCH = {
    "box": _box, "roundbox": _box, "sharpbox": _sharpbox, "rect": _sharpbox,
    "trapezoid": _trapezoid, "encoder": _trapezoid, "invtrapezoid": _invtrapezoid,
    "decoder": _invtrapezoid, "chevron": _chevron, "parallelogram": _parallelogram,
    "hexagon": _hexagon, "circle": _circle, "ellipse": _circle,
    "slab": _slab, "slabstack": _slab, "cube": _cube, "tokengrid": _tokengrid,
    "plane": _plane, "circleop": _op, "op": _op, "image": _image, "photo": _image,
    "imagestack": _imagestack, "voxelgrid": _voxelgrid, "voxel": _voxelgrid,
    "planestack": _planestack, "featmaps": _planestack,
    "gaussians": _gaussians, "marker": _marker,
    "cameraring": _cameraring, "surroundview": _cameraring,
    "imagegrid": _imagegrid, "lane": _lane, "text": _text, "note": _text, "mathlabel": _text,
    "ellipsis": _ellipsis, "spacer": _spacer, "bar": _bar, "brace": _brace,
    "framestrip": _framestrip, "dist": _dist, "density": _dist,
}
