"""Native PowerPoint (.pptx) writer.

The reference corpus was drawn in PowerPoint -- the residual SimSun/Calibri
font references in the published PDFs give it away -- so a .pptx export puts
the figure back in the tool the community actually edits in.  PowerPoint
shapes also paste straight into Visio as native shapes, which makes this the
most forgiving route onto a Visio canvas.

Every module is a real DrawingML shape with its own geometry, fill, outline
and text body; raster panels are embedded as pictures.

v3: arrows are real connectors.  A route with at most two bends becomes a
``straightConnector1`` / ``bentConnector2`` / ``bentConnector3`` glued to its
two modules with ``stCxn``/``endCxn``, so a module moved by hand drags its
arrows with it.  Routes with three or more bends cannot be expressed as an
Office connector and stay freeform; ``write()`` reports which ones.  Custom
polygons carry the same four connection sites as a rectangle (0 top, 1 left,
2 bottom, 3 right) so every module glues the same way.  The legend is one
group with a sub-group per entry, so deleting an entry removes its swatch and
its text together.
"""

import math
import os
import zipfile
import xml.sax.saxutils as sx

from . import edges as edgemod, layout as L, shapes as SH, style, text as T

ESC = sx.escape
EMU = 12700.0          # EMU per point
SCRIPT_COMPENSATION = 1.15
# LibreOffice prints a baseline-shifted run at 58.5 % of its size (Keynote
# about 67 %) and moves it by baseline x the run's *own* size, so a run
# written 15 % larger also drops 15 % further: 3.0 wrote -25 %, which printed
# 0.29 x body below the line.  Shifts are set in body terms and divided by the
# enlargement -- a subscript 0.20 x body down (best in LibreOffice: 7.8 pt on
# 7.0 pt at -18 %), a superscript 0.32 x body up, as in the SVG.  A shift
# past 0.35 x LO_SCRIPT_SIZE (about -20 %) prints a subscript more than 35 %
# of its own height below the line in LibreOffice, where it reads as
# detached; SUB_LIMIT keeps a margin below that.
SUB_DROP = 0.20
SUB_LIMIT = 0.18
LO_SCRIPT_SIZE = 0.585
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def script_baseline(script, ratio=SCRIPT_COMPENSATION):
    """DrawingML ``baseline`` (1/1000 %) for a script run ``ratio`` x body size."""
    if script > 0:
        return int(round(T.SCRIPT_RISE / ratio * 100)) * 1000
    return -int(round(min(SUB_DROP / ratio, SUB_LIMIT) * 100)) * 1000


def e(v):
    return str(int(round(float(v) * EMU)))


def hx(c):
    return (c or "#000000").lstrip("#").upper()


class Deck(object):
    def __init__(self, fig):
        self.fig = fig
        fig.render()
        self.W, self.H = fig._viewbox
        self.shapes = []
        self.media = []          # (name, bytes)
        self.rels = []           # (rId, target)
        self.next_id = 2
        self.glue = {}           # node id -> (shape id, {side: site index}, box)
        self.freeform = []       # edges that could not become connectors
        self.split = []          # edges whose arrowhead got its own segment
        self.glued = 0
        self._last = None        # (shape id, kind) of the last shape emitted
        self._stack = []         # open groups

    def sid(self):
        i = self.next_id
        self.next_id += 1
        return i

    def add_rel(self, target):
        rid = "rId%d" % (len(self.rels) + 2)
        self.rels.append((rid, target))
        return rid

    # ------------------------------------------------------------- pieces
    def _xfrm(self, x, y, w, h, rot=0):
        r = ' rot="%d"' % int((rot % 360) * 60000) if rot else ""
        return ('<a:xfrm%s><a:off x="%s" y="%s"/><a:ext cx="%s" cy="%s"/></a:xfrm>'
                % (r, e(x), e(y), e(max(w, 0.1)), e(max(h, 0.1))))

    def _fill(self, fill):
        if not fill or fill == "none":
            return "<a:noFill/>"
        return '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>' % hx(fill)

    def _ln(self, color, lw, dash=None, head=None, tail=None, preset=None):
        """Line properties.  ``head``/``tail`` are the start/end arrowheads;
        ``preset`` is the (w, len) pair chosen by style.pick_preset so the head
        PowerPoint draws matches the one in the SVG."""
        if not color or color == "none" or not lw:
            return '<a:ln><a:noFill/></a:ln>'
        w_, len_ = preset or style.pick_preset(lw)
        parts = ['<a:ln w="%s" cap="flat">' % e(lw),
                 '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>' % hx(color)]
        if dash:
            parts.append('<a:prstDash val="dash"/>')
        parts.append('<a:miter lim="800000"/>')
        if head:
            parts.append('<a:headEnd type="triangle" w="%s" len="%s"/>' % (w_, len_))
        if tail:
            parts.append('<a:tailEnd type="triangle" w="%s" len="%s"/>' % (w_, len_))
        parts.append('</a:ln>')
        return "".join(parts)

    def _txt(self, label, tstyle, anchor="ctr", wrap="none"):
        """Body text for a shape.  ``vert`` turns the whole text block, which
        is how PowerPoint renders a rotated label in a tall thin module."""
        if not label:
            return ('<p:txBody><a:bodyPr rtlCol="0" anchor="%s" wrap="none"'
                    ' lIns="0" tIns="0" rIns="0" bIns="0"/><a:lstStyle/>'
                    '<a:p><a:endParaRPr lang="en-US"/></a:p></p:txBody>' % anchor)
        ts = tstyle or {}
        vert = ts.get("vert", "")
        font = ts.get("font", "Times New Roman")
        size = float(ts.get("size", 7.0))
        if getattr(self.fig, "arrow_app", "") == "keynote":
            size = float(math.ceil(size - 0.01))     # Keynote keeps whole points only
        sz = str(int(round(size * 100)))
        # Office applications shrink a baseline-shifted run on their own
        # (LibreOffice to 58 %, Keynote to 67 %), on top of the size given.
        # Writing scripts 15 % larger lands them near the SVG's 72 %.
        sz_script = str(int(round(size * 100 * SCRIPT_COMPENSATION)))
        shift = {1: script_baseline(1), -1: script_baseline(-1)}
        col = hx(ts.get("color", "#000000"))
        b = ' b="1"' if ts.get("bold") else ""
        i = ' i="1"' if ts.get("italic") else ""
        algn = ts.get("align", "ctr")
        paras = []
        for line in str(label).split("\n"):
            runs = []
            for chars, rb, ri, script in T.parse_runs(line, ts.get("bold", False),
                                                      ts.get("italic", False)):
                if not chars:
                    continue
                base = ' baseline="%d"' % shift[1 if script > 0 else -1] if script else ""
                runs.append('<a:r><a:rPr lang="en-US" sz="%s"%s%s%s dirty="0">'
                            '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>'
                            '<a:latin typeface="%s"/><a:cs typeface="%s"/></a:rPr>'
                            '<a:t>%s</a:t></a:r>'
                            % (sz, ' b="1"' if rb else "", ' i="1"' if ri else "",
                               base, col, font, font, ESC(chars)))
                if script:
                    runs[-1] = runs[-1].replace('sz="%s"' % sz, 'sz="%s"' % sz_script, 1)
            if not runs:
                runs.append('<a:endParaRPr lang="en-US" sz="%s"/>' % sz)
            paras.append('<a:pPr algn="%s"/>%s' % (algn, "".join(runs)))
        body = "".join("<a:p>%s</a:p>" % p for p in paras)
        return ('<p:txBody><a:bodyPr rtlCol="0" anchor="%s" wrap="%s"%s lIns="0" tIns="0"'
                ' rIns="0" bIns="0"><a:spAutoFit/></a:bodyPr><a:lstStyle/>%s</p:txBody>'
                % (anchor, wrap, ' vert="%s"' % vert if vert else "", body))

    # ------------------------------------------------------------- shapes
    def add(self, xml):
        (self._stack[-1][1] if self._stack else self.shapes).append(xml)

    def begin_group(self, name):
        self._stack.append((name, []))

    def end_group(self, box):
        name, kids = self._stack.pop()
        if not kids:
            return None
        i = self.sid()
        x, y, w, h = box
        xf = ('<a:xfrm><a:off x="%s" y="%s"/><a:ext cx="%s" cy="%s"/>'
              '<a:chOff x="%s" y="%s"/><a:chExt cx="%s" cy="%s"/></a:xfrm>'
              % (e(x), e(y), e(w), e(h), e(x), e(y), e(w), e(h)))
        self.add('<p:grpSp><p:nvGrpSpPr><p:cNvPr id="%d" name="%s"/><p:cNvGrpSpPr/>'
                 '<p:nvPr/></p:nvGrpSpPr><p:grpSpPr>%s</p:grpSpPr>%s</p:grpSp>'
                 % (i, ESC(name), xf, "".join(kids)))
        return i

    def sp(self, name, x, y, w, h, geom, fill, line, lw, dash=None, label=None,
           tstyle=None, rot=0):
        i = self.sid()
        self._last = (i, "poly" if "custGeom" in geom else
                      ("ellipse" if 'prst="ellipse"' in geom else "rect"), (x, y, w, h))
        self.add(
            '<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s %d"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            '<p:spPr>%s%s%s%s</p:spPr>%s</p:sp>'
            % (i, name, i, self._xfrm(x, y, w, h, rot), geom, self._fill(fill),
               self._ln(line, lw, dash), self._txt(label, tstyle)))
        return i

    def rect(self, x, y, w, h, fill, line, lw, dash=None, label=None, tstyle=None,
             rounded=True, name="Module"):
        if rounded and h > 0:
            adj = min(0.5, style.GEOM["corner"] / max(min(w, h), 1.0))
            geom = ('<a:prstGeom prst="roundRect"><a:avLst>'
                    '<a:gd name="adj" fmla="val %d"/></a:avLst></a:prstGeom>'
                    % int(adj * 100000))
        else:
            geom = '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        return self.sp(name, x, y, w, h, geom, fill, line, lw, dash, label, tstyle)

    def ellipse(self, x, y, w, h, fill, line, lw, label=None, tstyle=None, name="Oval"):
        return self.sp(name, x, y, w, h,
                       '<a:prstGeom prst="ellipse"><a:avLst/></a:prstGeom>',
                       fill, line, lw, None, label, tstyle)

    def polygon(self, points, fill, line, lw, dash=None, label=None, tstyle=None,
                closed=True, name="Shape", sites=None):
        xs = [p[0] for p in points]; ys = [p[1] for p in points]
        x0, x1 = min(xs), max(xs); y0, y1 = min(ys), max(ys)
        w = max(x1 - x0, 0.01); h = max(y1 - y0, 0.01)
        cw, ch = e(w), e(h)
        path = ['<a:path w="%s" h="%s">' % (cw, ch)]
        for k, (px, py) in enumerate(points):
            tag = "moveTo" if k == 0 else "lnTo"
            path.append('<a:%s><a:pt x="%s" y="%s"/></a:%s>'
                        % (tag, e(px - x0), e(py - y0), tag))
        if closed:
            path.append("<a:close/>")
        path.append("</a:path>")
        # Four connection sites at the mid-edges of the bounding box, in the
        # order a rectangle uses, so arrows glue to a trapezoid like to a box.
        # Sites are written as guides -- fractions of w and h -- as the
        # presets do; LibreOffice misplaces literal coordinates here.  They sit
        # where layout.port_frac puts the ports (n, w, s, e).
        fr = sites or [(0.5, 0.0), (0.0, 0.5), (0.5, 1.0), (1.0, 0.5)]
        gds = "".join('<a:gd name="cx%d" fmla="*/ w %d 100000"/><a:gd name="cy%d" fmla="*/ h %d 100000"/>'
                      % (k, int(round(fx * 100000)), k, int(round(fy * 100000)))
                      for k, (fx, fy) in enumerate(fr))
        cxn = "".join('<a:cxn ang="%d"><a:pos x="cx%d" y="cy%d"/></a:cxn>' % (ang, k, k)
                      for k, ang in enumerate((16200000, 10800000, 5400000, 0)))
        geom = ('<a:custGeom><a:avLst/><a:gdLst>%s</a:gdLst><a:ahLst/><a:cxnLst>%s</a:cxnLst>'
                '<a:rect l="0" t="0" r="r" b="b"/><a:pathLst>%s</a:pathLst></a:custGeom>'
                % (gds, cxn, "".join(path)))
        return self.sp(name, x0, y0, w, h, geom, fill, line, lw, dash, label, tstyle)

    def polyline(self, points, color, lw, dash=None, arrow_end=True, arrow_start=False,
                 name="Flow", preset=None):
        xs = [p[0] for p in points]; ys = [p[1] for p in points]
        x0, x1 = min(xs), max(xs); y0, y1 = min(ys), max(ys)
        w = max(x1 - x0, 0.01); h = max(y1 - y0, 0.01)
        path = ['<a:path w="%s" h="%s" fill="none">' % (e(w), e(h))]
        for k, (px, py) in enumerate(points):
            tag = "moveTo" if k == 0 else "lnTo"
            path.append('<a:%s><a:pt x="%s" y="%s"/></a:%s>'
                        % (tag, e(px - x0), e(py - y0), tag))
        path.append("</a:path>")
        geom = ('<a:custGeom><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>'
                '<a:rect l="0" t="0" r="r" b="b"/><a:pathLst>%s</a:pathLst></a:custGeom>'
                % "".join(path))
        i = self.sid()
        self.add(
            '<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s %d"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            '<p:spPr>%s%s<a:noFill/>%s</p:spPr>%s</p:sp>'
            % (i, name, i, self._xfrm(x0, y0, w, h), geom,
               self._ln(color, lw, dash, arrow_start, arrow_end, preset),
               self._txt(None, None)))
        return i

    def connector(self, points, color, lw, dash=None, arrow_end=True, arrow_start=False,
                  start=None, end=None, name="Flow", preset=None):
        """A real Office connector through ``points`` (2-4 axis-aligned
        points), glued to ``start``/``end`` = (shape id, site index).
        Returns the shape id, or None when the route has no connector form."""
        geo = connector_geometry(points)
        if geo is None:
            return None
        prst, off, ext, rot, fh, fv, adj = geo
        i = self.sid()
        attrs = (' rot="%d"' % rot if rot else "") + (' flipH="1"' if fh else "") + \
                (' flipV="1"' if fv else "")
        xf = ('<a:xfrm%s><a:off x="%s" y="%s"/><a:ext cx="%s" cy="%s"/></a:xfrm>'
              % (attrs, e(off[0]), e(off[1]), e(ext[0]), e(ext[1])))
        av = ('<a:avLst><a:gd name="adj1" fmla="val %d"/></a:avLst>' % adj
              if adj is not None else "<a:avLst/>")
        cx = ""
        if start:
            cx += '<a:stCxn id="%d" idx="%d"/>' % start
        if end:
            cx += '<a:endCxn id="%d" idx="%d"/>' % end
        self.add('<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="%d" name="%s %d"/>'
                 '<p:cNvCxnSpPr>%s</p:cNvCxnSpPr><p:nvPr/></p:nvCxnSpPr>'
                 '<p:spPr>%s<a:prstGeom prst="%s">%s</a:prstGeom><a:noFill/>%s</p:spPr>'
                 '</p:cxnSp>'
                 % (i, name, i, cx, xf, prst, av,
                    self._ln(color, lw, dash, arrow_start, arrow_end, preset)))
        if start or end:
            self.glued += 1
        return i

    def textbox(self, x, y, w, h, label, tstyle, name="Label", rot=0):
        # Slack on the free side(s): Keynote shrinks text that it measures as
        # wider than its box, and its Times metrics run slightly wide.
        slack = max(4.0, 0.12 * w)
        al = (tstyle or {}).get("align", "ctr")
        x = x - (slack if al == "r" else (0.0 if al == "l" else slack / 2.0))
        w = w + slack
        i = self.sid()
        self._last = (i, "rect", (x, y, w, h))
        self.add(
            '<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s %d"/>'
            '<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
            '<p:spPr>%s<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>%s</p:sp>'
            % (i, name, i, self._xfrm(x, y, w, h, rot), self._txt(label, tstyle, "ctr")))
        return i

    def picture(self, x, y, w, h, path):
        with open(path, "rb") as fh:
            blob = fh.read()
        ext = path.rsplit(".", 1)[-1].lower()
        ext = "png" if ext not in ("png", "jpg", "jpeg", "gif") else ext
        name = "image%d.%s" % (len(self.media) + 1, ext)
        self.media.append((name, blob))
        rid = self.add_rel("../media/" + name)
        i = self.sid()
        self._last = (i, "rect", (x, y, w, h))
        self.add(
            '<p:pic><p:nvPicPr><p:cNvPr id="%d" name="Panel %d"/>'
            '<p:cNvPicPr><a:picLocks noChangeAspect="0"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
            '<p:blipFill><a:blip r:embed="%s"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
            '<p:spPr>%s<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
            '<a:ln w="%s"><a:solidFill><a:srgbClr val="000000"/></a:solidFill></a:ln>'
            '</p:spPr></p:pic>'
            % (i, i, rid, self._xfrm(x, y, w, h), e(style.STROKE["hairline"])))
        return i


# ------------------------------------------------------------- connectors
_ROT = {0: (1, 0, 0, 1), 5400000: (0, -1, 1, 0),
        10800000: (-1, 0, 0, -1), 16200000: (0, 1, -1, 0)}   # screen, clockwise


def _apply(m, v):
    return (m[0] * v[0] + m[1] * v[1], m[2] * v[0] + m[3] * v[1])


def _canonical(prst, W, H, adj):
    if prst == "straightConnector1":
        return [(0.0, 0.0), (W, H)]
    if prst == "bentConnector2":
        return [(0.0, 0.0), (W, 0.0), (W, H)]
    a = W * adj / 100000.0
    return [(0.0, 0.0), (a, 0.0), (a, H), (W, H)]


def connector_geometry(points, tol=0.05):
    """Find the preset connector, rotation and flips that reproduce
    ``points``.  Returns (prst, off, ext, rot, flipH, flipV, adj1) or None.

    An Office connector is drawn in a local box whose top-left is its start
    and bottom-right its end; the box is flipped, then rotated about its
    centre.  So rendered = C + R . F . (L - (W/2, H/2)) with C the midpoint
    of start and end, and the solver simply tries the 16 rotation/flip
    combinations and keeps the one whose canonical path lands on the route.
    """
    n = len(points)
    if n < 2 or n > 4:
        return None
    prst = {2: "straightConnector1", 3: "bentConnector2", 4: "bentConnector3"}[n]
    p0, pn = points[0], points[-1]
    C = ((p0[0] + pn[0]) / 2.0, (p0[1] + pn[1]) / 2.0)
    for rot in (0, 5400000, 16200000, 10800000):
        R = _ROT[rot]
        Rinv = (R[0], R[2], R[1], R[3])              # transpose = inverse
        for fh in (False, True):
            for fv in (False, True):
                F = (-1 if fh else 1, 0, 0, -1 if fv else 1)
                loc = [_apply(F, _apply(Rinv, (p[0] - p0[0], p[1] - p0[1]))) for p in points]
                W, H = loc[-1]
                if W < -tol or H < -tol:
                    continue
                W, H = max(W, 0.0), max(H, 0.0)
                adj = None
                if prst == "bentConnector3":
                    if W < 0.5:
                        continue
                    adj = int(round(loc[1][0] / W * 100000))
                want = _canonical(prst, W, H, adj if adj is not None else 0)
                if all(abs(a[0] - b[0]) <= tol + 0.002 * W and abs(a[1] - b[1]) <= tol
                       for a, b in zip(loc, want)):
                    off = (C[0] - W / 2.0, C[1] - H / 2.0)
                    return prst, off, (W, H), rot, fh, fv, adj
    return None


SITES = {"rect": {"n": 0, "w": 1, "s": 2, "e": 3},
         "poly": {"n": 0, "w": 1, "s": 2, "e": 3},
         "ellipse": {"n": 0, "w": 2, "s": 4, "e": 6}}


def _register(d, it, box=None):
    """Remember the shape just emitted as the glue target for ``it``, if its
    connection sites coincide with the node's ports."""
    if not it.id or d._last is None:
        return
    sid, kind, (x, y, w, h) = d._last
    bx, by, bw, bh = box or it.body()
    if abs(x - bx) > 0.3 or abs(y - by) > 0.3 or abs(w - bw) > 0.3 or abs(h - bh) > 0.3:
        return
    d.glue[it.id] = (sid, SITES[kind])


def _end_glue(d, item, side, pt):
    g = d.glue.get(item.id) if item is not None else None
    if not g or side not in g[1]:
        return None
    port = item.port(side)
    if abs(port[0] - pt[0]) > 0.3 or abs(port[1] - pt[1]) > 0.3:
        return None
    return (g[0], g[1][side])


def _head_triangle(tip, prev, length, width):
    dx, dy = tip[0] - prev[0], tip[1] - prev[1]
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    bx, by = tip[0] - ux * length, tip[1] - uy * length
    return [tip, (bx - uy * width / 2.0, by + ux * width / 2.0),
            (bx + uy * width / 2.0, by - ux * width / 2.0)]


# ------------------------------------------------------------------ emit
def _tstyle(spec, fig, over=None):
    d = {"font": "Times New Roman" if spec.get("_font", fig.font) == "times" else "Arial",
         "size": spec.get("_tsize", 7.0),
         "bold": bool(spec.get("_bold")), "italic": bool(spec.get("_italic")),
         "color": style.resolve_color(spec.get("text_color"), style.INK, ink=True)}
    rot = spec.get("rotate")
    if rot in (90, -90, 270):
        d["vert"] = "vert270" if rot == 90 else "vert"
    d["align"] = {"start": "l", "left": "l", "end": "r", "right": "r"}.get(
        spec.get("anchor"), "ctr")
    if over:
        d.update(over)
    return d


GLUABLE = ("box", "roundbox", "sharpbox", "rect", "bar", "trapezoid", "encoder",
           "invtrapezoid", "decoder", "chevron", "parallelogram", "hexagon", "plane",
           "circle", "ellipse", "image", "photo", "imagestack", "text", "note",
           "mathlabel")


def _emit_node(d, it, fig):
    s = it.spec
    shape = s.get("shape", "box")
    fill, stroke, tc = SH.node_colors(s)
    lw = style.stroke_width(s.get("lw"), style.STROKE["box"])
    dash = bool(s.get("dash"))
    x, y, w, h = it.body()
    ts = _tstyle(s, fig)
    label = s.get("text") or None

    d._last = None
    if shape in ("box", "roundbox"):
        d.rect(x, y, w, h, fill, stroke, lw, dash, label, ts, True)
    elif shape in ("sharpbox", "rect", "bar"):
        d.rect(x, y, w, h, fill, stroke, lw, dash, label, ts, False)
    elif shape in ("trapezoid", "encoder", "invtrapezoid", "decoder", "chevron",
                   "parallelogram", "hexagon", "plane"):
        from .vsdx import _poly_points
        pts = _poly_points(shape, s, x, y, w, h)
        # the polygon's own box can differ from the node box only if a point
        # lies inside it on every side, which none of these shapes does
        d.polygon(pts, fill, stroke, lw, dash, label, ts,
                  sites=[L.port_frac(shape, s, side, w, h) for side in ("n", "w", "s", "e")])
    elif shape in ("circle", "ellipse"):
        d.ellipse(x, y, w, h, fill, stroke, lw, label, ts)
    elif shape in ("circleop", "op"):
        dd = min(w, h); cx, cy = x + w / 2.0, y + h / 2.0
        d.ellipse(cx - dd / 2, cy - dd / 2, dd, dd, "#FFFFFF", stroke, lw)
        _register(d, it)
        k = dd * 0.28
        sym = s.get("op", s.get("text", "+"))
        if sym in ("+", "add", "sum", "oplus"):
            d.polyline([(cx - k, cy), (cx + k, cy)], stroke, lw, None, False, False, "OpBar")
            d.polyline([(cx, cy - k), (cx, cy + k)], stroke, lw, None, False, False, "OpBar")
        elif sym in ("x", "*", "mul", "otimes"):
            j = k * 0.72
            d.polyline([(cx - j, cy - j), (cx + j, cy + j)], stroke, lw, None, False, False, "OpBar")
            d.polyline([(cx - j, cy + j), (cx + j, cy - j)], stroke, lw, None, False, False, "OpBar")
        else:
            d.textbox(cx - dd / 2, cy - dd / 2, dd, dd, sym, _tstyle(s, fig, {"size": dd * 0.6}))
    elif shape in ("slab", "slabstack"):
        dep = style.GEOM["slab_depth"]
        cnt = int(s.get("n", 1)); cw = float(s.get("cell", 8.0)); gap = float(s.get("cellgap", 3.0))
        ch = h - dep
        colors = s.get("colors")
        for i in range(cnt):
            cx = x + i * (cw + gap); cy = y + dep
            f = fill
            if isinstance(colors, list) and colors:
                f = style.resolve_color(colors[i % len(colors)], fill)
            d.polygon([(cx, cy), (cx + dep, cy - dep), (cx + cw + dep, cy - dep), (cx + cw, cy)],
                      SH._shade(f, 1.16), stroke, lw, None, None, None, True, "SlabTop")
            d.polygon([(cx + cw, cy), (cx + cw + dep, cy - dep),
                       (cx + cw + dep, cy + ch - dep), (cx + cw, cy + ch)],
                      SH._shade(f, 0.90), stroke, lw, None, None, None, True, "SlabSide")
            d.rect(cx, cy, cw, ch, f, stroke, lw, None, None, None, False, "SlabFace")
        if s.get("text"):
            d.textbox(x, y, w, h, s["text"], ts, "SlabLabel")
    elif shape == "cube":
        dep = style.GEOM["slab_depth"] * 1.6
        side = min(w - dep, h - dep); cx, cy = x, y + dep
        d.rect(cx, cy, side, side, fill, stroke, lw, None, None, None, False, "CubeFace")
        d.polygon([(cx, cy), (cx + dep, cy - dep), (cx + side + dep, cy - dep), (cx + side, cy)],
                  SH._shade(fill, 1.16), stroke, lw, None, None, None, True, "CubeTop")
        d.polygon([(cx + side, cy), (cx + side + dep, cy - dep),
                   (cx + side + dep, cy + side - dep), (cx + side, cy + side)],
                  SH._shade(fill, 0.90), stroke, lw, None, None, None, True, "CubeSide")
    elif shape == "tokengrid":
        rows = int(s.get("rows", 4)); cols = int(s.get("cols", 1))
        cell = float(s.get("cell", 8.0)); gap = float(s.get("cellgap", 2.4))
        colors = s.get("colors")
        for i in range(rows):
            for j in range(cols):
                f = fill
                if isinstance(colors, list) and colors:
                    f = style.resolve_color(colors[(i * cols + j) % len(colors)], fill)
                d.rect(x + j * (cell + gap), y + i * (cell + gap), cell, cell,
                       f, stroke, lw, None, None, None, True, "Token")
    elif shape == "framestrip":
        mask = SH.frame_mask(s)
        cell = float(s.get("cell", 5.0)); gap = float(s.get("cellgap", 1.2))
        on = style.resolve_color(s.get("on_fill"), style.FAMILIES["steel"][4])
        off = style.resolve_color(s.get("off_fill"), style.FAMILIES["grey"][1])
        edge = style.resolve_color(s.get("cell_stroke"), "none")
        for i, bit in enumerate(mask):
            d.rect(x + i * (cell + gap), y, cell, h, on if bit else off, edge,
                   style.stroke_width(s.get("cell_lw"), style.STROKE["hairline"]), None, None, None,
                   True, "Frame")
    elif shape in ("dist", "density"):
        from .vsdx import _dist_points
        d.polygon(_dist_points(s, x, y, w, h), fill, stroke, lw, None, None, None,
                  True, "Density")
    elif shape == "brace":
        col = style.resolve_color(s.get("color"), style.INK, ink=True)
        blw = style.stroke_width(s.get("lw"), style.STROKE["hairline"] * 1.6)
        span = float(s.get("depth", 3.0))
        if s.get("dir", "right") in ("right", "left"):
            tip = x + span if s.get("dir", "right") == "right" else x
            back = x if s.get("dir", "right") == "right" else x + span
            d.polyline([(back, y), (tip, y + h * 0.12), (tip, y + h * 0.44),
                        (tip + (tip - back) * 0.6, y + h / 2.0),
                        (tip, y + h * 0.56), (tip, y + h * 0.88), (back, y + h)],
                       col, blw, None, False, False, "Brace")
        else:
            d.polyline([(x, y), (x + w * 0.12, y + span), (x + w * 0.44, y + span),
                        (x + w / 2.0, y + span * 1.6), (x + w * 0.56, y + span),
                        (x + w * 0.88, y + span), (x + w, y)],
                       col, blw, None, False, False, "Brace")
    elif shape in ("image", "photo", "imagestack"):
        src = s.get("src")
        p = os.path.join(fig.base or "", src) if src else None
        if p and os.path.exists(p):
            d.picture(x, y, w, h, p)
        else:
            d.rect(x, y, w, h, "#F2F2F2", style.FRAME_GREY, style.STROKE["hairline"], True,
                   s.get("text") or "image",
                   _tstyle(s, fig, {"size": style.TYPE["caption"]["size"],
                                    "color": style.MUTED_INK}), False, "ImageSlot")
    elif shape in ("text", "note", "mathlabel"):
        d.textbox(x, y, w, h, label, ts)
    elif shape == "ellipsis":
        r = float(s.get("r", 1.05)); k = int(s.get("n", 3))
        for i in range(1, k + 1):
            d.ellipse(x + w * i / (k + 1.0) - r, y + h / 2.0 - r, 2 * r, 2 * r,
                      style.INK, "none", 0, None, None, "Dot")
    elif shape == "spacer":
        return
    else:
        d.rect(x, y, w, h, fill, stroke, lw, dash, label, ts, True)
    if shape in GLUABLE:
        _register(d, it, (x, y, w, h))

    if s.get("caption"):
        d.textbox(it.x, y + h + float(s.get("caption_gap", 3.0)), it.w, s["_cap_h"] + 1,
                  s["caption"], _tstyle(s, fig, {"size": s["_cap_size"], "bold": False,
                                                 "italic": False}), "Caption")
    if s.get("badge"):
        bs = float(s.get("badge_size", style.TYPE["annot"]["size"]))
        d.textbox(x + w - 24, y - bs - 1.5, 24, bs + 3, s["badge"],
                  _tstyle(s, fig, {"size": bs, "italic": True, "bold": False,
                                   "align": "r"}), "Badge")


def _emit_container(d, it, fig):
    s = it.spec
    frame = s.get("frame", "none")
    fill = style.resolve_color(s.get("fill"), "none")
    if frame not in (None, "none", False) or fill != "none":
        stroke = style.resolve_color(s.get("frame_color"),
                                     style.FRAME_GREY if frame in ("dashed", "region") else style.INK,
                                     ink=True)
        if frame in (None, "none", False):
            stroke = "none"
        d._last = None
        d.rect(it.x, it.y, it.w, it.h, fill, stroke,
               style.stroke_width(s.get("frame_lw"), style.STROKE["frame"]),
               frame in ("dashed", "region", True), None, None, True, "Stage")
        _register(d, it)
    title = s.get("title")
    if title:
        ts = float(s.get("title_size", style.TYPE["stage_title"]["size"]))
        tw, th, _ = T.measure(title, ts, fig.font, True, True)
        pos = s.get("title_pos", "above")
        if pos == "above":
            ty = it.y - style.GEOM["stage_title_gap"] - th
        elif pos == "below":
            ty = it.y + it.h + style.GEOM["stage_title_gap"]
        elif pos == "inside-bottom":
            ty = it.y + it.h - th - style.GEOM["stage_pad"]
        else:
            ty = it.y + 4
        d.textbox(it.x, ty, it.w, th + 2, title,
                  {"font": "Times New Roman" if fig.font == "times" else "Arial",
                   "size": ts, "bold": s.get("title_bold", True),
                   "italic": s.get("title_italic", True),
                   "color": style.resolve_color(s.get("title_color"), style.INK, ink=True),
                   "align": {"left": "l", "right": "r"}.get(s.get("title_align", "center"), "ctr")},
                  "StageTitle")
    if s.get("badge"):
        bs = float(s.get("badge_size", style.TYPE["annot"]["size"]))
        d.textbox(it.x + it.w - 26, it.y + 1.5, 24, bs + 3, s["badge"],
                  {"font": "Times New Roman", "size": bs, "italic": True,
                   "color": style.INK, "align": "r"}, "Badge")
    for c in it.children:
        if c.kind == "node":
            _emit_node(d, c, fig)
        else:
            _emit_container(d, c, fig)


def _emit_edges(d, fig):
    shape_heads = fig.pptx_heads == "shape"
    for r in fig.routes:
        ed, pts = r.spec, list(r.pts)
        name = "Flow %s-%s" % (ed["from"], ed["to"])
        head_end, head_start = r.arrow_end, r.arrow_start
        extra = []
        if shape_heads:
            # identical heads in every application, drawn as triangles
            hl, hw = style.arrow_geometry(r.lw, fig.arrow_app)
            if head_end:
                extra.append(_head_triangle(pts[-1], pts[-2], hl, hw))
                pts[-1] = _shorten(pts[-1], pts[-2], hl * 0.8)
            if head_start:
                extra.append(_head_triangle(pts[0], pts[1], hl, hw))
                pts[0] = _shorten(pts[0], pts[1], hl * 0.8)
            head_end = head_start = False
        tail_seg = None
        if head_end and r.short_end:
            # Too short a last run for WPS to aim the head: give the head a
            # straight segment of its own and draw the bent body without it.
            tail_seg = [pts[-2], pts[-1]]
            head_end = False
            d.split.append(name)
        st = _end_glue(d, r.a, r.sa, pts[0])
        en = _end_glue(d, r.b, r.sb, pts[-1]) if tail_seg is None else None
        sid = None
        if fig.pptx_connectors != "freeform":
            sid = d.connector(pts, r.color, r.lw, r.dash, head_end, head_start, st, en,
                              name, r.preset)
        if sid is None:
            d.polyline(pts, r.color, r.lw, bool(r.dash), head_end, head_start, name,
                       r.preset)
            if fig.pptx_connectors != "freeform":
                d.freeform.append("%s -> %s (%d bends)" % (ed["from"], ed["to"], len(pts) - 2))
        if tail_seg is not None:
            d.connector(tail_seg, r.color, r.lw, r.dash, True, False, None,
                        _end_glue(d, r.b, r.sb, tail_seg[-1]), name + " head", r.preset)
        for tri in extra:
            d.polygon(tri, r.color, "none", 0, None, None, None, True, "ArrowHead")
        if ed.get("label"):
            x, y, anchor, size, tc = edgemod.label_box(ed, r.pts, r.color, fig.font)
            tw, th, _ = T.measure(ed["label"], size, fig.font)
            rot = ed.get("label_rotate", 0)
            x0 = {"start": x, "end": x - tw}.get(anchor, x - tw / 2.0)
            d.textbox(x0 - 1, y - th / 2.0 - 1, tw + 2, th + 2, ed["label"],
                      {"font": _face(fig), "size": size, "color": tc,
                       "align": {"start": "l", "end": "r"}.get(anchor, "ctr")}, "FlowLabel",
                      rot if rot in (90, -90, 270) else 0)


def _shorten(p, q, k):
    L = math.hypot(p[0] - q[0], p[1] - q[1]) or 1.0
    k = min(k, L * 0.9)
    return (p[0] + (q[0] - p[0]) * k / L, p[1] + (q[1] - p[1]) * k / L)


def _face(fig):
    return "Times New Roman" if fig.font == "times" else "Arial"


def _emit_legend(d, fig):
    """The legend as one group, with one sub-group per entry."""
    if not fig.spec.get("legend"):
        return
    lay = fig.legend_layout()
    sw = lay["swatch"]
    d.begin_group("Legend")
    if lay["framed"]:
        bx, by, bw, bh = lay["box"]
        d.rect(bx, by, bw, bh, "#FFFFFF", style.FRAME_GREY, style.STROKE["hairline"],
               None, None, None, True, "LegendFrame")
    for ent, cx, cy, size, kind, col, tw in lay["rows"]:
        d.begin_group("Legend: %s" % T.plain(ent.get("text", ""))[:40])
        ymid = cy + size / 2.0
        if kind in ("line", "arrow", "dashed"):
            lw = style.stroke_width(ent.get("lw"), style.STROKE["flow"])
            dash = kind == "dashed" or bool(ent.get("dash"))
            arrow = ent.get("arrow", kind != "line")
            d.connector([(cx, ymid), (cx + sw, ymid)], col, lw, dash, arrow, False,
                        None, None, "LegendSwatch",
                        style.pick_preset(lw, app=fig.arrow_app))
        else:
            d.rect(cx, cy + size * 0.1, sw, size * 0.8,
                   style.resolve_color(ent.get("fill"), col), style.INK,
                   style.STROKE["hairline"], None, None, None, True, "LegendSwatch")
        _, th, _ = T.measure(ent.get("text", ""), size, fig.font)
        d.textbox(cx + sw + 3.0, ymid - th / 2.0 - 1, tw + 3, th + 2, ent.get("text", ""),
                  {"font": _face(fig), "size": size, "align": "l",
                   "color": style.resolve_color(ent.get("text_color"), style.INK)},
                  "LegendText")
        d.end_group((cx, cy, sw + 3.0 + tw + 3, size + 2))
    bx, by, bw, bh = lay["box"]
    d.end_group((bx, by, bw, bh))


def _emit_notes(d, fig):
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
        d.textbox(x0 - 1, y - th / 2.0 - 1, tw + 3, th + 2, nt.get("text", ""),
                  {"font": "Times New Roman" if fig.font == "times" else "Arial",
                   "size": size, "italic": nt.get("italic", True),
                   "align": {"start": "l", "end": "r"}.get(anchor, "ctr"),
                   "color": style.resolve_color(nt.get("color"), style.INK, ink=True)}, "Note",
                  nt.get("rotate", 0) if nt.get("rotate", 0) in (90, -90, 270) else 0)


# ------------------------------------------------------------------ package
def _content_types(media):
    exts = set(n.rsplit(".", 1)[-1] for n, _ in media)
    defaults = ['<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
                '<Default Extension="xml" ContentType="application/xml"/>']
    for x in sorted(exts):
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif"}.get(x, "image/png")
        defaults.append('<Default Extension="%s" ContentType="%s"/>' % (x, mime))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '%s'
            '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
            '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
            '<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
            '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
            '</Types>' % "".join(defaults))


ROOT_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
             '</Relationships>')

PRES_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
             '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>'
             '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/>'
             '</Relationships>')

MASTER_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
               '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
               '</Relationships>')

LAYOUT_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
               '</Relationships>')

# An explicit white background: Keynote otherwise renders a slide with no
# <p:bg> on black.
_BG = ('<p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill>'
       '<a:effectLst/></p:bgPr></p:bg>')

_EMPTY_TREE = ('<p:cSld>' + _BG + '<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/>'
               '<p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/>'
               '<a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/>'
               '</a:xfrm></p:grpSpPr></p:spTree></p:cSld>')

_PML = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')

MASTER = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<p:sldMaster %s>%s<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1"'
          ' accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5"'
          ' accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
          '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
          '</p:sldMaster>' % (_PML, _EMPTY_TREE))

LAYOUT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<p:sldLayout %s type="blank" preserve="1">%s</p:sldLayout>' % (_PML, _EMPTY_TREE))


def _theme():
    def scheme():
        names = ["dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3",
                 "accent4", "accent5", "accent6", "hlink", "folHlink"]
        vals = ["000000", "FFFFFF", "44546A", "E7E6E6", "5B9BD5", "ED7D31",
                "A5A5A5", "FFC000", "4472C4", "70AD47", "0563C1", "954F72"]
        out = []
        for nm, v in zip(names, vals):
            if nm in ("dk1", "lt1"):
                out.append('<a:%s><a:sysClr val="%s" lastClr="%s"/></a:%s>'
                           % (nm, "windowText" if nm == "dk1" else "window", v, nm))
            else:
                out.append('<a:%s><a:srgbClr val="%s"/></a:%s>' % (nm, v, nm))
        return "".join(out)
    fmt = ('<a:fmtScheme name="Office"><a:fillStyleLst>'
           '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
           '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
           '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
           '<a:lnStyleLst>'
           '<a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
           '<a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
           '<a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
           '</a:lnStyleLst>'
           '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle>'
           '<a:effectStyle><a:effectLst/></a:effectStyle>'
           '<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
           '<a:bgFillStyleLst>'
           '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
           '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
           '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>'
           '</a:fmtScheme>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<a:theme xmlns:a="%s" name="Method-Figure"><a:themeElements>'
            '<a:clrScheme name="Office">%s</a:clrScheme>'
            '<a:fontScheme name="Office"><a:majorFont><a:latin typeface="Times New Roman"/>'
            '<a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
            '<a:minorFont><a:latin typeface="Times New Roman"/><a:ea typeface=""/>'
            '<a:cs typeface=""/></a:minorFont></a:fontScheme>%s</a:themeElements>'
            '<a:objectDefaults/><a:extraClrSchemeLst/></a:theme>' % (A, scheme(), fmt))


def write(fig, path):
    with fig.scope():
        return _write(fig, path)


def _write(fig, path):
    d = Deck(fig)
    for it in fig.items:
        _emit_container(d, it, fig)
    _emit_edges(d, fig)
    _emit_notes(d, fig)
    _emit_legend(d, fig)

    slide = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<p:sld %s><p:cSld>%s<p:spTree>'
             '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
             '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
             '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
             '%s</p:spTree></p:cSld>'
             '<p:clrMapOvr><a:overrideClrMapping bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2"'
             ' accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4"'
             ' accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
             '</p:clrMapOvr></p:sld>' % (_PML, _BG, "".join(d.shapes)))

    pres = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:presentation %s saveSubsetFonts="1">'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            '<p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst>'
            '<p:sldSz cx="%s" cy="%s"/><p:notesSz cx="%s" cy="%s"/>'
            '</p:presentation>' % (_PML, e(d.W), e(d.H), e(d.H), e(d.W)))

    slide_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                  '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
                  '%s</Relationships>'
                  % "".join('<Relationship Id="%s" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="%s"/>'
                            % (rid, tgt) for rid, tgt in d.rels))

    zf = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED)
    try:
        zf.writestr("[Content_Types].xml", _content_types(d.media))
        zf.writestr("_rels/.rels", ROOT_RELS)
        zf.writestr("ppt/presentation.xml", pres)
        zf.writestr("ppt/_rels/presentation.xml.rels", PRES_RELS)
        zf.writestr("ppt/slideMasters/slideMaster1.xml", MASTER)
        zf.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", MASTER_RELS)
        zf.writestr("ppt/slideLayouts/slideLayout1.xml", LAYOUT)
        zf.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", LAYOUT_RELS)
        zf.writestr("ppt/slides/slide1.xml", slide)
        zf.writestr("ppt/slides/_rels/slide1.xml.rels", slide_rels)
        zf.writestr("ppt/theme/theme1.xml", _theme())
        for name, blob in d.media:
            zf.writestr("ppt/media/" + name, blob)
    finally:
        zf.close()
    return {"path": path, "shapes": len(d.shapes), "media": len(d.media),
            "connectors_glued": d.glued, "freeform_edges": d.freeform,
            "split_heads": d.split}
