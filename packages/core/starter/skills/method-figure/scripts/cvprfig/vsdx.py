"""Native Visio (.vsdx) writer.

Emits real Visio shapes -- rectangles, polygons, ellipses and connector
polylines with their own geometry, fill, line and character sections -- rather
than a picture wrapper.  Every module can therefore be selected, recoloured,
resized and retyped inside Visio without an Ungroup step.

Visio's page space is inches with the origin at the bottom-left and Y pointing
up, so the point-based, Y-down layout is flipped on the way out.
"""

import os
import zipfile
import xml.sax.saxutils as sx

from . import layout as L, shapes as SH, style, text as T
from . import edges as edgemod

NS = "http://schemas.microsoft.com/office/visio/2012/main"
RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ESC = sx.escape

PT = 72.0
MARGIN_IN = 0.35


def n(v):
    return ("%.6f" % float(v)).rstrip("0").rstrip(".") or "0"


class _Builder(object):
    def __init__(self, fig):
        self.fig = fig
        cv = fig.render()
        self.W, self.H = fig._viewbox
        self.page_w = self.W / PT + 2 * MARGIN_IN
        self.page_h = self.H / PT + 2 * MARGIN_IN
        self.shapes = []
        self.next_id = 1
        self.placeholders = []

    # coordinate helpers ---------------------------------------------------
    def X(self, x):
        return MARGIN_IN + x / PT

    def Y(self, y):
        return MARGIN_IN + (self.H - y) / PT

    def sid(self):
        i = self.next_id
        self.next_id += 1
        return i

    # ------------------------------------------------------------- shapes
    def rect(self, x, y, w, h, fill, line, lw, name="Box", rounding=0.0,
             dash=None, text=None, tstyle=None, rotate=0):
        i = self.sid()
        cells = [
            ("PinX", self.X(x + w / 2.0)), ("PinY", self.Y(y + h / 2.0)),
            ("Width", w / PT), ("Height", h / PT),
        ]
        rows = [
            '<Row T="RelMoveTo" IX="1"><Cell N="X" V="0"/><Cell N="Y" V="0"/></Row>',
            '<Row T="RelLineTo" IX="2"><Cell N="X" V="1"/><Cell N="Y" V="0"/></Row>',
            '<Row T="RelLineTo" IX="3"><Cell N="X" V="1"/><Cell N="Y" V="1"/></Row>',
            '<Row T="RelLineTo" IX="4"><Cell N="X" V="0"/><Cell N="Y" V="1"/></Row>',
            '<Row T="RelLineTo" IX="5"><Cell N="X" V="0"/><Cell N="Y" V="0"/></Row>',
        ]
        self.shapes.append(self._shape(i, name, cells, rows, fill, line, lw, dash,
                                       text, tstyle, rounding, closed=True, rotate=rotate))
        return i

    def polygon(self, points, fill, line, lw, name="Poly", dash=None, text=None,
                tstyle=None, closed=True):
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        w = max(x1 - x0, 0.01)
        h = max(y1 - y0, 0.01)
        i = self.sid()
        cells = [("PinX", self.X((x0 + x1) / 2.0)), ("PinY", self.Y((y0 + y1) / 2.0)),
                 ("Width", w / PT), ("Height", h / PT)]
        rows = []
        for k, (px, py) in enumerate(points):
            lx = (px - x0) / PT
            ly = (y1 - py) / PT           # flip inside the shape's own frame
            tag = "MoveTo" if k == 0 else "LineTo"
            rows.append('<Row T="%s" IX="%d"><Cell N="X" V="%s"/><Cell N="Y" V="%s"/></Row>'
                        % (tag, k + 1, n(lx), n(ly)))
        if closed and points[0] != points[-1]:
            rows.append('<Row T="LineTo" IX="%d"><Cell N="X" V="%s"/><Cell N="Y" V="%s"/></Row>'
                        % (len(points) + 1, n((points[0][0] - x0) / PT), n((y1 - points[0][1]) / PT)))
        self.shapes.append(self._shape(i, name, cells, rows, fill, line, lw, dash,
                                       text, tstyle, 0.0, closed=closed))
        return i

    def ellipse(self, x, y, w, h, fill, line, lw, name="Ellipse", text=None, tstyle=None):
        i = self.sid()
        cells = [("PinX", self.X(x + w / 2.0)), ("PinY", self.Y(y + h / 2.0)),
                 ("Width", w / PT), ("Height", h / PT)]
        rows = ['<Row T="Ellipse" IX="1">'
                '<Cell N="X" V="%s" F="Width*0.5"/><Cell N="Y" V="%s" F="Height*0.5"/>'
                '<Cell N="A" V="%s" F="Width*1"/><Cell N="B" V="%s" F="Height*0.5"/>'
                '<Cell N="C" V="%s" F="Width*0.5"/><Cell N="D" V="%s" F="Height*1"/></Row>'
                % (n(w / PT / 2), n(h / PT / 2), n(w / PT), n(h / PT / 2),
                   n(w / PT / 2), n(h / PT))]
        self.shapes.append(self._shape(i, name, cells, rows, fill, line, lw, None,
                                       text, tstyle, 0.0, closed=True))
        return i

    def textbox(self, x, y, w, h, label, tstyle, name="Label"):
        i = self.sid()
        cells = [("PinX", self.X(x + w / 2.0)), ("PinY", self.Y(y + h / 2.0)),
                 ("Width", max(w, 4) / PT), ("Height", max(h, 4) / PT)]
        rows = [
            '<Row T="RelMoveTo" IX="1"><Cell N="X" V="0"/><Cell N="Y" V="0"/></Row>',
            '<Row T="RelLineTo" IX="2"><Cell N="X" V="1"/><Cell N="Y" V="0"/></Row>',
            '<Row T="RelLineTo" IX="3"><Cell N="X" V="1"/><Cell N="Y" V="1"/></Row>',
            '<Row T="RelLineTo" IX="4"><Cell N="X" V="0"/><Cell N="Y" V="1"/></Row>',
            '<Row T="RelLineTo" IX="5"><Cell N="X" V="0"/><Cell N="Y" V="0"/></Row>',
        ]
        self.shapes.append(self._shape(i, name, cells, rows, None, None, 0, None,
                                       label, tstyle, 0.0, closed=True, noshow=True))
        return i

    def polyline(self, points, color, lw, dash=None, arrow_end=True,
                 arrow_start=False, name="Connector", label=None, tstyle=None):
        xs = [p[0] for p in points]; ys = [p[1] for p in points]
        x0, x1 = min(xs), max(xs); y0, y1 = min(ys), max(ys)
        w = max(x1 - x0, 0.0001); h = max(y1 - y0, 0.0001)
        i = self.sid()
        cells = [("PinX", self.X((x0 + x1) / 2.0)), ("PinY", self.Y((y0 + y1) / 2.0)),
                 ("Width", w / PT), ("Height", h / PT)]
        rows = []
        for k, (px, py) in enumerate(points):
            rows.append('<Row T="%s" IX="%d"><Cell N="X" V="%s"/><Cell N="Y" V="%s"/></Row>'
                        % ("MoveTo" if k == 0 else "LineTo", k + 1,
                           n((px - x0) / PT), n((y1 - py) / PT)))
        extra = ['<Cell N="EndArrow" V="%d"/>' % (4 if arrow_end else 0),
                 '<Cell N="BeginArrow" V="%d"/>' % (4 if arrow_start else 0),
                 '<Cell N="EndArrowSize" V="1"/>', '<Cell N="BeginArrowSize" V="1"/>']
        self.shapes.append(self._shape(i, name, cells, rows, None, color, lw, dash,
                                       label, tstyle, 0.0, closed=False, extra=extra))
        return i

    # ---------------------------------------------------------------- xml
    def _shape(self, sid, name, cells, geom_rows, fill, line, lw, dash, text,
               tstyle, rounding, closed=True, extra=None, noshow=False, rotate=0):
        out = ['<Shape ID="%d" NameU="%s.%d" Name="%s.%d" Type="Shape" '
               'LineStyle="0" FillStyle="0" TextStyle="0">' % (sid, name, sid, name, sid)]
        for k, v in cells:
            out.append('<Cell N="%s" V="%s"/>' % (k, n(v)))
        w = [v for k, v in cells if k == "Width"][0]
        h = [v for k, v in cells if k == "Height"][0]
        out.append('<Cell N="LocPinX" V="%s" F="Width*0.5"/>' % n(w / 2.0))
        out.append('<Cell N="LocPinY" V="%s" F="Height*0.5"/>' % n(h / 2.0))
        out.append('<Cell N="Angle" V="%s"/>' % n(rotate))
        out.append('<Cell N="FlipX" V="0"/><Cell N="FlipY" V="0"/>')
        out.append('<Cell N="ResizeMode" V="0"/>')

        if fill and fill != "none":
            out.append('<Cell N="FillForegnd" V="%s"/>' % fill.lower())
            out.append('<Cell N="FillBkgnd" V="#ffffff"/>')
            out.append('<Cell N="FillPattern" V="1"/>')
        else:
            out.append('<Cell N="FillPattern" V="0"/>')
        out.append('<Cell N="ShdwPattern" V="0"/>')

        if line and line != "none" and lw:
            out.append('<Cell N="LineColor" V="%s"/>' % line.lower())
            out.append('<Cell N="LineWeight" V="%s"/>' % n(lw / PT))
            out.append('<Cell N="LinePattern" V="%d"/>' % (dash or 1))
            out.append('<Cell N="LineCap" V="0"/><Cell N="Rounding" V="%s"/>' % n(rounding / PT))
        else:
            out.append('<Cell N="LinePattern" V="0"/><Cell N="LineWeight" V="0"/>')
        if extra:
            out.extend(extra)

        ts = tstyle or {}
        out.append('<Cell N="LeftMargin" V="0"/><Cell N="RightMargin" V="0"/>'
                   '<Cell N="TopMargin" V="0"/><Cell N="BottomMargin" V="0"/>')
        out.append('<Cell N="VerticalAlign" V="%d"/>' % ts.get("valign", 1))
        # One Character row per distinct (bold, italic, script) style in the
        # label, so inline markup and maths come through as formatting rather
        # than as literal asterisks.  Pos: 0 normal, 1 super, 2 sub.
        base_st = ts.get("style", 0)
        runs = T.parse_runs(str(text), bool(base_st & 1), bool(base_st & 2)) if text else []
        styles = []
        for _, rb, ri, sc in runs:
            key = ((1 if rb else 0) | (2 if ri else 0), {1: 1, -1: 2}.get(sc, 0))
            if key not in styles:
                styles.append(key)
        if not styles:
            styles = [(base_st, 0)]
        out.append('<Section N="Character">')
        for ix, (st, pos) in enumerate(styles):
            out.append('<Row IX="%d"><Cell N="Font" V="%s"/><Cell N="Color" V="%s"/>'
                       '<Cell N="Size" V="%s"/><Cell N="Style" V="%d"/>'
                       '<Cell N="Case" V="0"/><Cell N="Pos" V="%d"/></Row>'
                       % (ix, ts.get("font", "Times New Roman"),
                          ts.get("color", "#000000").lower(),
                          n(ts.get("size", 7.0) / PT), st, pos))
        out.append('</Section>')
        out.append('<Section N="Paragraph"><Row IX="0">'
                   '<Cell N="HorzAlign" V="%d"/><Cell N="SpLine" V="-1.2"/>'
                   '</Row></Section>' % ts.get("halign", 1))
        if ts.get("text_angle"):
            out.append('<Cell N="TxtAngle" V="%s"/>'
                       '<Cell N="TxtWidth" V="%s" F="Height*1"/>'
                       '<Cell N="TxtHeight" V="%s" F="Width*1"/>'
                       '<Cell N="TxtPinX" V="%s" F="Width*0.5"/>'
                       '<Cell N="TxtPinY" V="%s" F="Height*0.5"/>'
                       '<Cell N="TxtLocPinX" V="%s" F="TxtWidth*0.5"/>'
                       '<Cell N="TxtLocPinY" V="%s" F="TxtHeight*0.5"/>'
                       % (n(ts["text_angle"]), n(h), n(w), n(w / 2.0), n(h / 2.0),
                          n(h / 2.0), n(w / 2.0)))

        out.append('<Section N="Geometry" IX="0">')
        out.append('<Cell N="NoFill" V="%d"/><Cell N="NoLine" V="0"/>'
                   '<Cell N="NoShow" V="%d"/><Cell N="NoSnap" V="0"/><Cell N="NoQuickDrag" V="0"/>'
                   % (0 if (closed and fill and fill != "none") else 1, 1 if noshow else 0))
        out.extend(geom_rows)
        out.append('</Section>')
        if text:
            body = []
            for chars, rb, ri, sc in runs:
                key = ((1 if rb else 0) | (2 if ri else 0), {1: 1, -1: 2}.get(sc, 0))
                # Run boundaries are positional (<cp/> marks), as Visio writes
                # them.  LibreOffice's importer counts UTF-8 bytes when it turns
                # these into run lengths, so after a non-ASCII glyph (an arrow,
                # a Greek letter) its formatting lands late; Visio is unaffected.
                body.append('<cp IX="%d"/>%s' % (styles.index(key), ESC(chars)))
            out.append('<Text>%s</Text>' % "".join(body))
        out.append('</Shape>')
        return "".join(out)


# --------------------------------------------------------------- emission
def _tstyle(spec, fig, override=None):
    font = "Times New Roman" if spec.get("_font", fig.font) == "times" else "Arial"
    st = 0
    if spec.get("_bold"):
        st |= 1
    if spec.get("_italic"):
        st |= 2
    d = {"font": font, "size": spec.get("_tsize", 7.0), "style": st,
         "color": style.resolve_color(spec.get("text_color"), style.INK, ink=True),
         "halign": {"start": 0, "left": 0, "end": 2, "right": 2}.get(spec.get("anchor"), 1)}
    rot = spec.get("rotate")
    if rot in (90, -90, 270):
        # Visio rotates the text block independently of the shape, so a tall
        # thin module keeps its geometry and only the label turns.
        d["text_angle"] = 1.5707963 if rot == 90 else -1.5707963
    if override:
        d.update(override)
    return d


def _dash_code(spec_dash):
    if not spec_dash:
        return None
    return 2


def _emit_node(b, it, fig):
    s = it.spec
    shape = s.get("shape", "box")
    fill, stroke, tc = SH.node_colors(s)
    lw = style.stroke_width(s.get("lw"), style.STROKE["box"])
    dash = _dash_code(s.get("dash"))
    x, y, w, h = it.body()
    ts = _tstyle(s, fig)
    label = s.get("text") or None

    if shape in ("box", "roundbox"):
        b.rect(x, y, w, h, fill, stroke, lw, "Module", style.GEOM["corner"], dash, label, ts)
    elif shape in ("sharpbox", "rect", "bar"):
        b.rect(x, y, w, h, fill, stroke, lw, "Block", 0.0, dash, label, ts)
    elif shape in ("trapezoid", "encoder", "invtrapezoid", "decoder", "chevron",
                   "parallelogram", "hexagon", "plane"):
        b.polygon(_poly_points(shape, s, x, y, w, h), fill, stroke, lw,
                  shape.capitalize(), dash, label, ts)
    elif shape in ("circle", "ellipse"):
        b.ellipse(x, y, w, h, fill, stroke, lw, "Oval", label, ts)
    elif shape in ("circleop", "op"):
        d = min(w, h)
        cx, cy = x + w / 2.0, y + h / 2.0
        b.ellipse(cx - d / 2, cy - d / 2, d, d, "#ffffff", stroke, lw, "Operator")
        k = d * 0.28
        sym = s.get("op", s.get("text", "+"))
        if sym in ("+", "add", "sum", "oplus"):
            b.polyline([(cx - k, cy), (cx + k, cy)], stroke, lw, None, False, False, "OpBar")
            b.polyline([(cx, cy - k), (cx, cy + k)], stroke, lw, None, False, False, "OpBar")
        elif sym in ("x", "*", "mul", "otimes"):
            j = k * 0.72
            b.polyline([(cx - j, cy - j), (cx + j, cy + j)], stroke, lw, None, False, False, "OpBar")
            b.polyline([(cx - j, cy + j), (cx + j, cy - j)], stroke, lw, None, False, False, "OpBar")
        else:
            b.textbox(cx - d / 2, cy - d / 2, d, d, sym, _tstyle(s, fig, {"size": d * 0.6}))
    elif shape in ("slab", "slabstack"):
        _emit_slab(b, s, x, y, w, h, fill, stroke, lw, fig)
    elif shape == "cube":
        _emit_cube(b, s, x, y, w, h, fill, stroke, lw)
    elif shape == "tokengrid":
        rows = int(s.get("rows", 4)); cols = int(s.get("cols", 1))
        cell = float(s.get("cell", 8.0)); gap = float(s.get("cellgap", 2.4))
        colors = s.get("colors")
        for i in range(rows):
            for j in range(cols):
                f = fill
                if isinstance(colors, list) and colors:
                    f = style.resolve_color(colors[(i * cols + j) % len(colors)], fill)
                b.rect(x + j * (cell + gap), y + i * (cell + gap), cell, cell, f,
                       stroke, lw, "Token", 1.2)
    elif shape == "framestrip":
        mask = SH.frame_mask(s)
        cell = float(s.get("cell", 5.0)); gap = float(s.get("cellgap", 1.2))
        on = style.resolve_color(s.get("on_fill"), style.FAMILIES["steel"][4])
        off = style.resolve_color(s.get("off_fill"), style.FAMILIES["grey"][1])
        edge = style.resolve_color(s.get("cell_stroke"), "none")
        for i, bit in enumerate(mask):
            b.rect(x + i * (cell + gap), y, cell, h, on if bit else off, edge,
                   style.stroke_width(s.get("cell_lw"), style.STROKE["hairline"]), "Frame", 0.6)
    elif shape in ("dist", "density"):
        b.polygon(_dist_points(s, x, y, w, h), fill, stroke, lw, "Density")
    elif shape == "brace":
        d = s.get("dir", "right")
        col = style.resolve_color(s.get("color"), style.INK, ink=True)
        blw = style.stroke_width(s.get("lw"), style.STROKE["hairline"] * 1.6)
        if d in ("right", "left"):
            span = float(s.get("depth", 3.0))
            tip = x + span if d == "right" else x
            back = x if d == "right" else x + span
            b.polyline([(back, y), (tip, y + h * 0.12), (tip, y + h * 0.44),
                        (tip + (tip - back) * 0.6, y + h / 2.0),
                        (tip, y + h * 0.56), (tip, y + h * 0.88), (back, y + h)],
                       col, blw, None, False, False, "Brace")
        else:
            span = float(s.get("depth", 3.0))
            b.polyline([(x, y), (x + w * 0.12, y + span), (x + w * 0.44, y + span),
                        (x + w / 2.0, y + span * 1.6), (x + w * 0.56, y + span),
                        (x + w * 0.88, y + span), (x + w, y)],
                       col, blw, None, False, False, "Brace")
    elif shape in ("image", "photo", "imagestack"):
        b.rect(x, y, w, h, "#f2f2f2", style.FRAME_GREY, style.STROKE["hairline"],
               "ImageSlot", 0.0, 2, s.get("text") or s.get("src") or "image",
               _tstyle(s, fig, {"size": style.TYPE["caption"]["size"], "color": style.MUTED_INK}))
        if s.get("src"):
            b.placeholders.append(s["src"])
    elif shape in ("text", "note", "mathlabel"):
        b.textbox(x, y, w, h, label, ts, "Label")
    elif shape == "ellipsis":
        r = float(s.get("r", 1.05)); k = int(s.get("n", 3))
        for i in range(1, k + 1):
            b.ellipse(x + w * i / (k + 1.0) - r, y + h / 2.0 - r, 2 * r, 2 * r,
                      style.INK, "none", 0, "Dot")
    elif shape == "spacer":
        return
    else:
        b.rect(x, y, w, h, fill, stroke, lw, "Module", style.GEOM["corner"], dash, label, ts)

    if s.get("caption"):
        b.textbox(it.x, y + h + float(s.get("caption_gap", 3.0)), it.w, s["_cap_h"],
                  s["caption"], _tstyle(s, fig, {"size": s["_cap_size"], "style": 0}), "Caption")
    if s.get("badge"):
        bs = float(s.get("badge_size", style.TYPE["annot"]["size"]))
        b.textbox(x + w - 22, y - bs - 1, 22, bs + 2, s["badge"],
                  _tstyle(s, fig, {"size": bs, "style": 2, "halign": 2}), "Badge")


def _dist_points(s, x, y, w, h):
    """Polygon approximation of the density glyph, for the vector writers."""
    import math
    kind = s.get("kind", "gauss")
    spread = float(s.get("spread", 1.0))
    skew = float(s.get("skew", 0.0))
    steps = 32
    pts = [(x, y + h)]
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
    pts.append((x + w, y + h))
    return pts


def _poly_points(shape, s, x, y, w, h):
    if shape in ("trapezoid", "encoder", "invtrapezoid", "decoder"):
        d = s.get("dir", "right")
        if shape in ("invtrapezoid", "decoder"):
            d = {"right": "left", "left": "right"}.get(d, "left")
        slant = float(s.get("slant", 0.22)) * h
        if d in ("right", "e"):
            return [(x, y), (x + w, y + slant), (x + w, y + h - slant), (x, y + h)]
        if d in ("left", "w"):
            return [(x, y + slant), (x + w, y), (x + w, y + h), (x, y + h - slant)]
        sl = float(s.get("slant", 0.22)) * w
        if d in ("down", "s"):
            return [(x, y), (x + w, y), (x + w - sl, y + h), (x + sl, y + h)]
        return [(x + sl, y), (x + w - sl, y), (x + w, y + h), (x, y + h)]
    if shape == "chevron":
        pt = float(s.get("point", 7.0))
        if s.get("dir", "right") in ("left", "w"):
            return [(x + w, y), (x + pt, y), (x, y + h / 2.0), (x + pt, y + h), (x + w, y + h)]
        return [(x, y), (x + w - pt, y), (x + w, y + h / 2.0), (x + w - pt, y + h), (x, y + h)]
    if shape == "hexagon":
        k = min(h / 2.0, w * 0.16)
        return [(x + k, y), (x + w - k, y), (x + w, y + h / 2.0),
                (x + w - k, y + h), (x + k, y + h), (x, y + h / 2.0)]
    k = float(s.get("skew", 0.28 if shape == "parallelogram" else 0.34)) * (h if shape == "parallelogram" else w)
    return [(x + k, y), (x + w, y), (x + w - k, y + h), (x, y + h)]


def _emit_slab(b, s, x, y, w, h, fill, stroke, lw, fig):
    d = style.GEOM["slab_depth"]
    cnt = int(s.get("n", 1)); cw = float(s.get("cell", 8.0)); gap = float(s.get("cellgap", 3.0))
    ch = h - d
    colors = s.get("colors")
    for i in range(cnt):
        cx = x + i * (cw + gap); cy = y + d
        f = fill
        if isinstance(colors, list) and colors:
            f = style.resolve_color(colors[i % len(colors)], fill)
        b.polygon([(cx, cy), (cx + d, cy - d), (cx + cw + d, cy - d), (cx + cw, cy)],
                  SH._shade(f, 1.16), stroke, lw, "SlabTop")
        b.polygon([(cx + cw, cy), (cx + cw + d, cy - d),
                   (cx + cw + d, cy + ch - d), (cx + cw, cy + ch)],
                  SH._shade(f, 0.90), stroke, lw, "SlabSide")
        b.rect(cx, cy, cw, ch, f, stroke, lw, "SlabFace", 0.0)
    if s.get("text"):
        b.textbox(x, y, w, h, s["text"], _tstyle(s, fig), "SlabLabel")


def _emit_cube(b, s, x, y, w, h, fill, stroke, lw):
    d = style.GEOM["slab_depth"] * 1.6
    side = min(w - d, h - d)
    cx, cy = x, y + d
    b.rect(cx, cy, side, side, fill, stroke, lw, "CubeFace", 0.0)
    b.polygon([(cx, cy), (cx + d, cy - d), (cx + side + d, cy - d), (cx + side, cy)],
              SH._shade(fill, 1.16), stroke, lw, "CubeTop")
    b.polygon([(cx + side, cy), (cx + side + d, cy - d),
               (cx + side + d, cy + side - d), (cx + side, cy + side)],
              SH._shade(fill, 0.90), stroke, lw, "CubeSide")


def _emit_container(b, it, fig):
    s = it.spec
    frame = s.get("frame", "none")
    fill = style.resolve_color(s.get("fill"), "none")
    if frame not in (None, "none", False) or fill != "none":
        stroke = style.resolve_color(s.get("frame_color"),
                                     style.FRAME_GREY if frame in ("dashed", "region") else style.INK,
                                     ink=True)
        if frame in (None, "none", False):
            stroke = "none"
        b.rect(it.x, it.y, it.w, it.h, fill, stroke,
               style.stroke_width(s.get("frame_lw"), style.STROKE["frame"]), "Stage",
               float(s.get("corner", style.GEOM["frame_corner"])),
               2 if frame in ("dashed", "region", True) else None)
    title = s.get("title")
    if title:
        ts = float(s.get("title_size", style.TYPE["stage_title"]["size"]))
        st = (1 if s.get("title_bold", True) else 0) | (2 if s.get("title_italic", True) else 0)
        pos = s.get("title_pos", "above")
        tw, th, _ = T.measure(title, ts, fig.font, True, True)
        if pos == "above":
            ty = it.y - style.GEOM["stage_title_gap"] - th
        elif pos == "below":
            ty = it.y + it.h + style.GEOM["stage_title_gap"]
        elif pos == "inside-bottom":
            ty = it.y + it.h - th - style.GEOM["stage_pad"]
        else:
            ty = it.y + 4
        b.textbox(it.x, ty, it.w, th + 1.5, title,
                  {"font": "Times New Roman" if fig.font == "times" else "Arial",
                   "size": ts, "style": st,
                   "color": style.resolve_color(s.get("title_color"), style.INK, ink=True),
                   "halign": {"left": 0, "right": 2}.get(s.get("title_align", "center"), 1)})
    if s.get("badge"):
        bs = float(s.get("badge_size", style.TYPE["annot"]["size"]))
        b.textbox(it.x + it.w - 24, it.y + 1.5, 22, bs + 2, s["badge"],
                  {"font": "Times New Roman", "size": bs, "style": 2,
                   "color": style.INK, "halign": 2})
    for c in it.children:
        if c.kind == "node":
            _emit_node(b, c, fig)
        else:
            _emit_container(b, c, fig)


def _emit_edges(b, fig):
    for r in fig.routes:
        e = r.spec
        b.polyline(r.pts, r.color, r.lw, 2 if r.dash else None,
                   r.arrow_end, r.arrow_start, "Flow")
        if e.get("label"):
            x, y, anchor, size, tc = edgemod.label_box(e, r.pts, r.color, fig.font)
            tw, th, _ = T.measure(e["label"], size, fig.font)
            x0 = {"start": x, "end": x - tw}.get(anchor, x - tw / 2.0)
            b.textbox(x0 - 1, y - th / 2.0 - 1, tw + 2, th + 2, e["label"],
                      {"font": "Times New Roman" if fig.font == "times" else "Arial",
                       "size": size, "style": 0, "color": tc, "halign": 1}, "FlowLabel")


def _emit_notes(b, fig):
    for nt in (fig.spec.get("notes") or []):
        if not isinstance(nt, dict):
            continue
        size = float(nt.get("size", style.TYPE["annot"]["size"]))
        if nt.get("at"):
            x, y = float(nt["at"][0]), float(nt["at"][1])
        elif nt.get("near"):
            item, side = edgemod.resolve_ref(nt["near"], fig.nodes)
            x, y = item.port(side or "n")
        else:
            continue
        x += float(nt.get("dx", 0.0)); y += float(nt.get("dy", 0.0))
        tw, th, _ = T.measure(nt.get("text", ""), size, fig.font)
        anchor = nt.get("anchor", "middle")
        x0 = {"start": x, "end": x - tw}.get(anchor, x - tw / 2.0)
        b.textbox(x0, y - th / 2.0, tw + 2, th + 2, nt.get("text", ""),
                  {"font": "Times New Roman" if fig.font == "times" else "Arial",
                   "size": size, "style": 2 if nt.get("italic", True) else 0,
                   "color": style.resolve_color(nt.get("color"), style.INK, ink=True),
                   "halign": {"start": 0, "end": 2}.get(anchor, 1)}, "Note")


# ------------------------------------------------------------------ package
CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="png" ContentType="image/png"/>
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/visio/document.xml" ContentType="application/vnd.ms-visio.drawing.main+xml"/>
<Override PartName="/visio/pages/pages.xml" ContentType="application/vnd.ms-visio.pages+xml"/>
<Override PartName="/visio/pages/page1.xml" ContentType="application/vnd.ms-visio.page+xml"/>
<Override PartName="/visio/windows.xml" ContentType="application/vnd.ms-visio.windows+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/document" Target="visio/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""

DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/pages" Target="pages/pages.xml"/>
<Relationship Id="rId2" Type="http://schemas.microsoft.com/visio/2010/relationships/windows" Target="windows.xml"/>
</Relationships>"""

PAGES_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/page" Target="page1.xml"/>
</Relationships>"""

WINDOWS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Windows xmlns="%s" xmlns:r="%s" ClientWidth="1600" ClientHeight="900">
<Window ID="0" WindowType="Drawing" WindowState="1073741824" WindowLeft="0" WindowTop="0"
 WindowWidth="1600" WindowHeight="900" ContainerType="Page" Page="0" ViewScale="1"
 ViewCenterX="%%s" ViewCenterY="%%s"/>
</Windows>""" % (NS, RNS)

DOCUMENT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<VisioDocument xmlns="%s" xmlns:r="%s" xml:space="preserve">
<DocumentSettings TopPage="0" DefaultTextStyle="0" DefaultLineStyle="0" DefaultFillStyle="0" DefaultGuideStyle="0">
<GlueSettings>9</GlueSettings><SnapSettings>65847</SnapSettings>
<SnapExtensions>34</SnapExtensions><SnapAngles/><DynamicGridEnabled>1</DynamicGridEnabled>
<ProtectStyles>0</ProtectStyles><ProtectShapes>0</ProtectShapes><ProtectMasters>0</ProtectMasters>
<ProtectBkgnds>0</ProtectBkgnds>
</DocumentSettings>
<Colors/>
<FaceNames>
<FaceName NameU="Times New Roman" UnicodeRanges="3 0 0 0" CharSets="536871327 0" Panos="2 2 6 3 5 4 5 2 3 4" Flags="0"/>
<FaceName NameU="Arial" UnicodeRanges="3 0 0 0" CharSets="536871327 0" Panos="2 11 6 4 2 2 2 2 2 4" Flags="0"/>
</FaceNames>
<StyleSheets>
<StyleSheet ID="0" NameU="No Style" Name="No Style">
<Cell N="LineWeight" V="0.0069"/><Cell N="LineColor" V="#000000"/><Cell N="LinePattern" V="1"/>
<Cell N="FillForegnd" V="#ffffff"/><Cell N="FillBkgnd" V="#ffffff"/><Cell N="FillPattern" V="1"/>
<Cell N="ShdwPattern" V="0"/><Cell N="BeginArrow" V="0"/><Cell N="EndArrow" V="0"/>
<Cell N="LineCap" V="0"/><Cell N="Rounding" V="0"/>
<Cell N="TextBkgnd" V="0"/><Cell N="VerticalAlign" V="1"/>
<Cell N="LeftMargin" V="0"/><Cell N="RightMargin" V="0"/>
<Cell N="TopMargin" V="0"/><Cell N="BottomMargin" V="0"/>
<Section N="Character"><Row IX="0"><Cell N="Font" V="Times New Roman"/><Cell N="Color" V="#000000"/><Cell N="Size" V="0.0972"/><Cell N="Style" V="0"/></Row></Section>
<Section N="Paragraph"><Row IX="0"><Cell N="HorzAlign" V="1"/></Row></Section>
</StyleSheet>
</StyleSheets>
</VisioDocument>""" % (NS, RNS)

CORE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>%s</dc:title><dc:creator>Method-Figure</dc:creator><cp:lastModifiedBy>Method-Figure</cp:lastModifiedBy>
</cp:coreProperties>"""

APP = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
<Application>Microsoft Visio</Application><AppVersion>15.0000</AppVersion>
</Properties>"""


def _pages_xml(page_w, page_h):
    return ("""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Pages xmlns="%s" xmlns:r="%s" xml:space="preserve">
<Page ID="0" NameU="Page-1" Name="Page-1" ViewScale="1" ViewCenterX="%s" ViewCenterY="%s">
<PageSheet LineStyle="0" FillStyle="0" TextStyle="0">
<Cell N="PageWidth" V="%s"/><Cell N="PageHeight" V="%s"/>
<Cell N="ShdwOffsetX" V="0.1181"/><Cell N="ShdwOffsetY" V="-0.1181"/>
<Cell N="PageScale" V="1" U="IN_F"/><Cell N="DrawingScale" V="1" U="IN_F"/>
<Cell N="DrawingSizeType" V="3"/><Cell N="DrawingScaleType" V="0"/>
<Cell N="InhibitSnap" V="0"/><Cell N="PageLockReplace" V="0"/>
<Cell N="PageLockDuplicate" V="0"/><Cell N="UIVisibility" V="0"/>
<Cell N="ShdwType" V="0"/><Cell N="ShdwObliqueAngle" V="0"/><Cell N="ShdwScaleFactor" V="1"/>
</PageSheet>
<Rel r:id="rId1"/>
</Page>
</Pages>""" % (NS, RNS, n(page_w / 2), n(page_h / 2), n(page_w), n(page_h)))


def _emit_legend(b, fig):
    if not fig.spec.get("legend"):
        return
    lay = fig.legend_layout()
    sw = lay["swatch"]
    face = "Times New Roman" if fig.font == "times" else "Arial"
    if lay["framed"]:
        bx, by, bw, bh = lay["box"]
        b.rect(bx, by, bw, bh, "#FFFFFF", style.FRAME_GREY, style.STROKE["hairline"],
               "LegendFrame", 2.0)
    for ent, cx, cy, size, kind, col, tw in lay["rows"]:
        ymid = cy + size / 2.0
        if kind in ("line", "arrow", "dashed"):
            lw = style.stroke_width(ent.get("lw"), style.STROKE["flow"])
            dash = 2 if (kind == "dashed" or ent.get("dash")) else None
            b.polyline([(cx, ymid), (cx + sw, ymid)], col, lw, dash,
                       ent.get("arrow", kind != "line"), False, "LegendSwatch")
        else:
            b.rect(cx, cy + size * 0.1, sw, size * 0.8,
                   style.resolve_color(ent.get("fill"), col), style.INK,
                   style.STROKE["hairline"], "LegendSwatch")
        _, th, _ = T.measure(ent.get("text", ""), size, fig.font)
        b.textbox(cx + sw + 3.0, ymid - th / 2.0 - 1, tw + 3, th + 2, ent.get("text", ""),
                  {"font": face, "size": size, "style": 0,
                   "color": style.resolve_color(ent.get("text_color"), style.INK),
                   "halign": 0}, "LegendText")


def write(fig, path):
    """Write ``fig`` to a native Visio drawing at ``path``."""
    with fig.scope():
        return _write(fig, path)


def _write(fig, path):
    b = _Builder(fig)
    for it in fig.items:
        _emit_container(b, it, fig)
    _emit_edges(b, fig)
    _emit_notes(b, fig)
    _emit_legend(b, fig)

    page = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<PageContents xmlns="%s" xmlns:r="%s" xml:space="preserve"><Shapes>%s</Shapes>'
            '</PageContents>' % (NS, RNS, "".join(b.shapes)))
    title = (fig.spec.get("figure") or {}).get("id", "figure")

    zf = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED)
    try:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", ROOT_RELS)
        zf.writestr("docProps/core.xml", CORE % ESC(str(title)))
        zf.writestr("docProps/app.xml", APP)
        zf.writestr("visio/document.xml", DOCUMENT)
        zf.writestr("visio/_rels/document.xml.rels", DOC_RELS)
        zf.writestr("visio/windows.xml", WINDOWS % (n(b.page_w / 2), n(b.page_h / 2)))
        zf.writestr("visio/pages/pages.xml", _pages_xml(b.page_w, b.page_h))
        zf.writestr("visio/pages/_rels/pages.xml.rels", PAGES_RELS)
        zf.writestr("visio/pages/page1.xml", page)
    finally:
        zf.close()
    return {"path": path, "shapes": len(b.shapes),
            "image_placeholders": sorted(set(b.placeholders))}
