"""SVG renderer: shape library, edge router and text setter.

Output is plain SVG 1.1 with no external references, so it opens unchanged in
Illustrator, Inkscape, LibreOffice, PowerPoint and Visio, and converts cleanly
to PDF for LaTeX.
"""

import base64
import math
import os
import xml.sax.saxutils as sx

from . import style, text as T

ESC = sx.escape


def fmt(v):
    return ("%.3f" % float(v)).rstrip("0").rstrip(".")


def pts(seq):
    return " ".join("%s,%s" % (fmt(a), fmt(b)) for a, b in seq)


class Canvas(object):
    def __init__(self, width, height, bg="#FFFFFF"):
        self.width = width
        self.height = height
        self.bg = bg
        self.body = []
        self.defs = []
        self._mid = 0

    def add(self, s):
        self.body.append(s)

    def marker(self, color, width, kind="tri", head=None):
        """Arrowhead.  ``head`` is the (length, width) in points the PPTX
        writer will ask PowerPoint/LibreOffice for (style.arrow_geometry), so
        the SVG and the PPTX draw the same head."""
        key = "a%d" % self._mid
        self._mid += 1
        hl, hw = head or style.arrow_geometry(width)
        width = max(width, 0.05)
        L = hl / width
        W = hw / width
        if kind == "open":
            path = ("<path d='M0,%s L%s,%s L0,%s' fill='none' stroke='%s' "
                    "stroke-width='1.1' stroke-linejoin='miter'/>"
                    % (fmt(-W / 2), fmt(L), 0, fmt(W / 2), color))
        else:
            path = "<path d='M0,%s L%s,0 L0,%s Z' fill='%s'/>" % (fmt(-W / 2), fmt(L), fmt(W / 2), color)
        self.defs.append(
            "<marker id='%s' markerWidth='%s' markerHeight='%s' refX='%s' refY='0' "
            "orient='auto' markerUnits='strokeWidth' viewBox='%s %s %s %s'>%s</marker>"
            % (key, fmt(L + 1), fmt(W + 1), fmt(L), fmt(-W / 2 - 0.5), fmt(-W / 2 - 0.5),
               fmt(L + 1), fmt(W + 1), path))
        return key

    def tostring(self, title=None):
        head = ("<svg xmlns='http://www.w3.org/2000/svg' "
                "xmlns:xlink='http://www.w3.org/1999/xlink' version='1.1' "
                "width='%spt' height='%spt' viewBox='0 0 %s %s'>"
                % (fmt(self.width), fmt(self.height), fmt(self.width), fmt(self.height)))
        parts = [head]
        if title:
            parts.append("<title>%s</title>" % ESC(title))
        if self.defs:
            parts.append("<defs>%s</defs>" % "".join(self.defs))
        if self.bg and self.bg != "none":
            parts.append("<rect x='0' y='0' width='%s' height='%s' fill='%s'/>"
                         % (fmt(self.width), fmt(self.height), self.bg))
        parts.extend(self.body)
        parts.append("</svg>")
        return "\n".join(parts)


# ------------------------------------------------------------------- text
ANCHOR = {"start": "start", "left": "start", "middle": "middle", "center": "middle",
          "end": "end", "right": "end"}


def draw_text(cv, x, y, label, size, font="times", bold=False, italic=False,
              color=style.INK, anchor="middle", baseline="middle", rotate=0,
              leading=None, opacity=None):
    """Set a multi-line, inline-markup label.  ``y`` is the block centre when
    baseline == 'middle', else the first baseline."""
    if label in (None, ""):
        return
    lines = str(label).split("\n")
    lead = (leading or style.GEOM["line_gap"]) * size
    n = len(lines)
    if baseline == "middle":
        first = y - (n - 1) * lead / 2.0 + size * 0.34
    elif baseline == "top":
        first = y + size * 0.86
    else:
        first = y
    stack = style.FONT_STACKS.get(font, style.FONT_STACKS["times"])
    tr = ""
    if rotate:
        tr = " transform='rotate(%s %s %s)'" % (fmt(rotate), fmt(x), fmt(y))
    op = " opacity='%s'" % fmt(opacity) if opacity is not None else ""
    out = ["<g font-family='%s' fill='%s'%s%s>" % (stack, color, tr, op)]
    for i, line in enumerate(lines):
        ly = first + i * lead
        runs = T.parse_runs(line, bold, italic)
        if len(runs) == 1 and not runs[0][3]:
            chars, b, it, _ = runs[0]
            out.append("<text x='%s' y='%s' font-size='%s'%s%s text-anchor='%s'"
                       " xml:space='preserve'>%s</text>"
                       % (fmt(x), fmt(ly), fmt(size),
                          " font-weight='bold'" if b else "",
                          " font-style='italic'" if it else "",
                          ANCHOR.get(anchor, "middle"), ESC(chars)))
            continue
        total = T.line_width(line, size, font, bold, italic)
        if ANCHOR.get(anchor, "middle") == "middle":
            cx = x - total / 2.0
        elif ANCHOR.get(anchor) == "end":
            cx = x - total
        else:
            cx = x
        for chars, b, it, script in runs:
            if not chars:
                continue
            s = size * (T.SCRIPT_SCALE if script else 1.0)
            # Converters drop leading/trailing blanks of a <text> element, so
            # step over them here instead of relying on xml:space.
            lead = len(chars) - len(chars.lstrip())
            for ch in chars[:lead]:
                cx += T.glyph_width(ch, font, b, it) * s / 1000.0
            tail = chars[lead:]
            stripped = tail.rstrip()
            trail_w = sum(T.glyph_width(ch, font, b, it) for ch in tail[len(stripped):]) * s / 1000.0
            chars = stripped
            if not chars:
                cx += trail_w
                continue
            dy = 0.0
            if script > 0:
                dy = -size * T.SCRIPT_RISE
            elif script < 0:
                dy = size * T.SCRIPT_DROP
            out.append("<text x='%s' y='%s' font-size='%s'%s%s text-anchor='start'"
                       " xml:space='preserve'>%s</text>"
                       % (fmt(cx), fmt(ly + dy), fmt(s),
                          " font-weight='bold'" if b else "",
                          " font-style='italic'" if it else "", ESC(chars)))
            for ch in chars:
                cx += T.glyph_width(ch, font, b, it) * s / 1000.0
            cx += trail_w
    out.append("</g>")
    cv.add("".join(out))


# ------------------------------------------------------------------ shapes
def _sattrs(fill, stroke, width, dash=None, opacity=None):
    a = ["fill='%s'" % (fill or "none")]
    if stroke and stroke != "none" and width:
        a.append("stroke='%s'" % stroke)
        a.append("stroke-width='%s'" % fmt(width))
        a.append("stroke-linejoin='round'")
        if dash:
            a.append("stroke-dasharray='%s'" % dash)
    if opacity is not None:
        a.append("opacity='%s'" % fmt(opacity))
    return " ".join(a)


def rect(cv, x, y, w, h, r, fill, stroke, sw, dash=None, opacity=None):
    cv.add("<rect x='%s' y='%s' width='%s' height='%s' rx='%s' ry='%s' %s/>"
           % (fmt(x), fmt(y), fmt(w), fmt(h), fmt(r), fmt(r),
              _sattrs(fill, stroke, sw, dash, opacity)))


def poly(cv, points, fill, stroke, sw, dash=None, closed=True, opacity=None):
    tag = "polygon" if closed else "polyline"
    cv.add("<%s points='%s' %s/>" % (tag, pts(points), _sattrs(fill, stroke, sw, dash, opacity)))


def line(cv, x1, y1, x2, y2, stroke, sw, dash=None, cap="butt"):
    cv.add("<line x1='%s' y1='%s' x2='%s' y2='%s' stroke='%s' stroke-width='%s'%s"
           " stroke-linecap='%s'/>"
           % (fmt(x1), fmt(y1), fmt(x2), fmt(y2), stroke, fmt(sw),
              " stroke-dasharray='%s'" % dash if dash else "", cap))


def circle(cv, cx, cy, r, fill, stroke, sw, dash=None):
    cv.add("<circle cx='%s' cy='%s' r='%s' %s/>"
           % (fmt(cx), fmt(cy), fmt(r), _sattrs(fill, stroke, sw, dash)))


def _shade(hexcol, factor):
    hexcol = hexcol.lstrip("#")
    if len(hexcol) != 6:
        return "#" + hexcol
    r, g, b = (int(hexcol[i:i + 2], 16) for i in (0, 2, 4))
    if factor < 1:
        r, g, b = (int(c * factor) for c in (r, g, b))
    else:
        r, g, b = (int(c + (255 - c) * (factor - 1)) for c in (r, g, b))
    return "#%02X%02X%02X" % (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
