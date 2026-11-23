"""Spec -> laid-out figure -> SVG.

Design contract: the layout is computed directly in *final rendered points*,
so a 7 pt label in the spec is a 7 pt label on the printed page.  The canvas
width defaults to the target venue's column width, and slack is distributed
into the gaps between stages rather than by stretching the modules.
"""

import os

from . import edges as edgemod
from . import layout as L
from . import shapes, style, svg
from .svg import fmt


class Figure(object):
    def __init__(self, spec, base=None):
        self.spec = spec or {}
        self.base = base
        self.warnings = []
        self.meta = {}
        self.paper = {}
        self._load_paper()
        f = self.spec.get("figure") or {}
        self.type_k = float(f.get("type_scale", 1.0) or 1.0)
        if self.type_k != 1.0:
            _scale_sizes(self.spec, self.type_k)
        self.arrow_app = str(f.get("pptx_app", style.DEFAULT_ARROW_APP)).lower()
        if self.arrow_app not in style.ARROW_APPS:
            self.warnings.append("pptx_app %r is unknown; using %r"
                                 % (self.arrow_app, style.DEFAULT_ARROW_APP))
            self.arrow_app = style.DEFAULT_ARROW_APP
        self.pptx_heads = str(f.get("pptx_heads", "native")).lower()
        # "native": glued Office connectors (PowerPoint, WPS, LibreOffice);
        # "freeform": plain paths -- for Keynote, which mis-draws rotated
        # (vertical-first) bent connectors on import.
        self.pptx_connectors = str(f.get("pptx_connectors", "native")).lower()
        with self.scope():
            self._build()

    # ------------------------------------------------------------- paper
    def _load_paper(self):
        """Merge a shared ``paper.yaml`` (``paper: path``) into this spec.

        One file per paper holds what must not drift between its figures: the
        role -> colour map, the symbol table, the font and the venue.  Keys in
        the figure's own spec win.
        """
        ref = self.spec.get("paper")
        if isinstance(ref, str):
            from .miniyaml import load_path
            path = ref if os.path.isabs(ref) else os.path.join(self.base or "", ref)
            if not os.path.exists(path):
                self.warnings.append("paper file %s not found" % ref)
                return
            self.paper = load_path(path) or {}
            self.paper["_path"] = path
        elif isinstance(ref, dict):
            self.paper = ref
        else:
            return
        fig = self.spec.setdefault("figure", {})
        for k in ("venue", "font", "pptx_app", "pptx_heads", "pptx_connectors", "type_scale"):
            if k in self.paper and k not in fig:
                fig[k] = self.paper[k]
        sym = dict(self.paper.get("symbols") or {})
        sym.update(self.spec.get("symbols") or {})
        if sym:
            self.spec["symbols"] = sym

    def scope(self):
        """Context in which this figure's type scale and role map apply."""
        return style.scope(self.type_k, (self.paper or {}).get("roles"),
                           (self.spec.get("roles") or {}))

    # ------------------------------------------------------------- build
    def _build(self):
        f = self.spec.get("figure") or {}
        venue = str(f.get("venue", "cvpr")).lower()
        widths = style.VENUE_WIDTH.get(venue, style.VENUE_WIDTH["generic"])
        w = f.get("width", "double")
        if isinstance(w, str):
            target = widths.get(w, widths["double"])
        else:
            target = float(w)
        self.target = target * float(f.get("width_frac", 1.0))
        self.font = f.get("font", "times")
        self.pad = float(f.get("pad", 2.0))
        # Extra room reserved outside the panel band -- used when a legend or a
        # key sits beside the first stage title, as page-1 teasers usually do.
        self.pad_top = self.pad + float(f.get("pad_top", 0.0))
        self.pad_bottom = self.pad + float(f.get("pad_bottom", 0.0))
        ctx = {"font": self.font, "base": self.base, "venue": venue}
        self.ctx = ctx

        defaults = self.spec.get("defaults") or {}
        panels = self.spec.get("panels")
        if panels is None:
            body = self.spec.get("body") or self.spec.get("nodes") or []
            panels = [{"id": "_root", "frame": "none", "body": body}]

        arrange = str(self.spec.get("layout", "row")).lower()
        self.items = [L.build_item(_as_container(p), defaults, ctx, 1) for p in panels]
        for it in self.items:
            L.measure(it, ctx)

        # title band above the panel row
        # Room above the first row for stage titles, which are drawn outside
        # the frame they belong to.  This has to look *inside* the top-level
        # items as well as at them: a banded layout (see code2fig) wraps titled
        # groups in an untitled band, and reserving nothing then clips every
        # title on the top row.
        self.title_band = max((_above_title_h(it) for it in self.items), default=0.0)
        self.bottom_band = 0.0
        for it in self.items:
            if it.spec.get("title") and it.spec.get("title_pos") == "below":
                ts = float(it.spec.get("title_size", style.TYPE["stage_title"]["size"]))
                self.bottom_band = max(self.bottom_band, ts + style.GEOM["stage_title_gap"])

        gap = float(self.spec.get("gap", style.GEOM["stage_gap"]))
        if arrange == "col":
            self._stack_col(gap)
        elif arrange == "grid":
            self._stack_grid(gap, int(self.spec.get("cols", 2)))
        else:
            self._stack_row(gap)

        self.nodes = L.index(self.items)
        self._apply_absolute()
        self.bad_edges = []
        self.routes = edgemod.plan(self.spec.get("edges"), self.nodes, self.arrow_app,
                                   self.bad_edges)
        if arrange not in ("col", "grid") and len(self.items) > 1 \
                and not self.spec.get("fixed_gaps"):
            mins = [0.0] * len(self.gaps)
            for _ in range(4):        # lanes spread -> demand grows; settle
                need = self.corridor_demand()
                if not any(m > g + 0.05 for m, g in zip(need, self.gaps)):
                    break
                mins = [max(a, b) for a, b in zip(mins, need)]
                self._stack_row(gap, mins)
                self.nodes = L.index(self.items)
                self._apply_absolute()
                self.bad_edges = []
                self.routes = edgemod.plan(self.spec.get("edges"), self.nodes,
                                           self.arrow_app, self.bad_edges)

    def corridor_demand(self):
        """Width each gap between top-level stages needs for the wires that
        jog inside it: one lane per wire, plus the final run of an arrow."""
        out = []
        lane = style.GEOM["lane"]
        for i in range(len(self.items) - 1):
            x0 = self.items[i].x + self.items[i].w
            x1 = self.items[i + 1].x
            jogs, need = set(), 0.0
            for r in getattr(self, "routes", []):
                for p, q in zip(r.pts, r.pts[1:]):
                    if abs(p[0] - q[0]) < 0.05 and x0 - 0.05 <= p[0] <= x1 + 0.05:
                        jogs.add(round(p[0], 1))
                        need = max(need, r.need_end, r.need_start)
            demand = 0.0
            if jogs:
                demand = (len(jogs) - 1) * lane + need + edgemod.MIN_FIRST + 0.5
            out.append(demand)
        return out

    def _stack_row(self, gap, mins=None):
        n_g = max(0, len(self.items) - 1)
        gaps = [max(gap, (mins or [0.0] * n_g)[i]) for i in range(n_g)]
        nat = sum(i.w for i in self.items) + sum(gaps)
        inner = self.target - 2 * self.pad
        slack = inner - nat
        if slack > 0 and len(self.items) > 1 and self.spec.get("fit", "fill") == "fill":
            # Give the slack to the narrowest gaps first so a corridor that had
            # to be widened does not end up wider than its neighbours.
            target = sorted(gaps)
            pool = slack
            level = target[0]
            for k in range(1, len(target) + 1):
                nxt = target[k] if k < len(target) else float("inf")
                cost = (nxt - level) * k
                if cost >= pool:
                    level += pool / k
                    pool = 0.0
                    break
                pool -= cost
                level = nxt
            gaps = [max(g, level) for g in gaps]
            slack = 0.0
        self.gaps = gaps
        x = self.pad + (max(slack, 0.0) / 2.0 if len(self.items) == 1 else 0.0)
        y = self.pad_top + self.title_band
        top = max((i.h for i in self.items), default=0.0)
        for k, it in enumerate(self.items):
            va = it.spec.get("valign", "top")
            oy = 0.0 if va == "top" else (top - it.h if va == "bottom" else (top - it.h) / 2.0)
            L.place(it, x, y + oy, self.ctx)
            x += it.w + (gaps[k] if k < len(gaps) else 0.0)
        self.natural_w = max(self.pad * 2 + nat, x + self.pad)
        self.natural_h = (self.pad_top + self.pad_bottom + self.title_band + top
                          + self.bottom_band)

    def _stack_col(self, gap):
        self.gaps = []
        y = self.pad_top + self.title_band
        wide = max((i.w for i in self.items), default=0.0)
        inner = max(self.target - 2 * self.pad, wide)
        for it in self.items:
            ha = it.spec.get("halign", "center")
            ox = 0.0 if ha == "left" else (inner - it.w if ha == "right" else (inner - it.w) / 2.0)
            L.place(it, self.pad + ox, y, self.ctx)
            y += it.h + gap + (self.title_band if it is not self.items[-1] and self.title_band else 0)
        self.natural_w = self.pad * 2 + inner
        self.natural_h = y - gap + self.pad_bottom + self.bottom_band

    def _stack_grid(self, gap, cols):
        # rows of equal-width columns; each row gets its own title band
        self.gaps = []
        rows = [self.items[i:i + cols] for i in range(0, len(self.items), cols)]
        colw = [0.0] * cols
        for r in rows:
            for j, it in enumerate(r):
                colw[j] = max(colw[j], it.w)
        y = self.pad_top + self.title_band
        for r in rows:
            x = self.pad
            rh = max(i.h for i in r)
            for j, it in enumerate(r):
                L.place(it, x + (colw[j] - it.w) / 2.0, y, self.ctx)
                x += colw[j] + gap
            y += rh + gap + self.title_band
        self.natural_w = self.pad * 2 + sum(colw) + gap * (cols - 1)
        self.natural_h = y - gap + self.pad_bottom

    def _apply_absolute(self):
        """Nodes may pin themselves with ``at: [x, y]`` in canvas coordinates,
        or line up with another node: ``align_x: other`` puts this node's
        centre directly under (or over) ``other``'s, ``align_y`` level with it.
        Alignment survives any change of type size, where hand-tuned spacer
        widths do not."""
        index = L.index(self.items)
        for it in L.walk(self.items):
            at = it.spec.get("at")
            if isinstance(at, (list, tuple)) and len(at) == 2:
                dx = float(at[0]) - it.x
                dy = float(at[1]) - it.y
                for sub in L.walk([it]):
                    sub.x += dx
                    sub.y += dy
        for it in L.walk(self.items):
            for key in ("align_x", "align_y"):
                ref = it.spec.get(key)
                if not ref:
                    continue
                other = index.get(str(ref))
                if other is None:
                    self.warnings.append("%s: %s refers to unknown node %r" % (it.id, key, ref))
                    continue
                ox, oy, ow, oh = other.body()
                mx, my, mw, mh = it.body()
                dx = (ox + ow / 2.0) - (mx + mw / 2.0) if key == "align_x" else 0.0
                dy = (oy + oh / 2.0) - (my + mh / 2.0) if key == "align_y" else 0.0
                for sub in L.walk([it]):
                    sub.x += dx
                    sub.y += dy

    @property
    def fit_scale(self):
        """Downscale the export will apply, known right after layout.

        Available without rendering so a caller can reshape a draft before
        committing to it -- see code2fig.emit.fit.
        """
        nat_w = max(self.natural_w, self.target)
        return self.target / nat_w if nat_w > self.target + 0.01 else 1.0

    # ------------------------------------------------------------ render
    def render(self):
        """Draw the figure once and cache it; every exporter shares the result."""
        if getattr(self, "_canvas", None) is not None:
            return self._canvas
        with self.scope():
            return self._render()

    def _render(self):
        nat_w = max(self.natural_w, self.target)
        nat_h = self.natural_h
        f = self.spec.get("figure") or {}
        if f.get("height"):
            nat_h = max(nat_h, float(f["height"]))

        cv = svg.Canvas(nat_w, nat_h, style.resolve_color(f.get("bg"), "#FFFFFF"))
        self.scale = self.target / nat_w if nat_w > self.target + 0.01 else 1.0

        for it in self.items:
            self._draw_container(cv, it)

        for r in self.routes:
            edgemod.draw(cv, r, self.ctx)

        for n in (self.spec.get("notes") or []):
            self._draw_note(cv, n)

        legend = self.spec.get("legend")
        if legend:
            self._draw_legend(cv, legend, nat_w, nat_h)

        cv.width = self.target
        cv.height = nat_h * self.scale
        self._viewbox = (nat_w, nat_h)
        self.meta = {
            "canvas_pt": [round(self.target, 2), round(nat_h * self.scale, 2)],
            "natural_pt": [round(nat_w, 2), round(nat_h, 2)],
            "scale": round(self.scale, 4),
            "node_count": sum(1 for i in L.walk(self.items) if i.kind == "node"),
            "edge_count": len(self.spec.get("edges") or []),
            "type_scale": self.type_k,
            "pptx_app": self.arrow_app,
        }
        self._canvas = cv
        short = [r for r in self.routes if r.short_end or r.short_start]
        if short:
            self.warnings.append(
                "%d bent arrow(s) end in a run shorter than two arrowheads (%s); WPS "
                "draws those heads at 45 degrees -- widen the gap or route straight"
                % (len(short), ", ".join("%s->%s" % (r.spec["from"], r.spec["to"])
                                         for r in short[:4])))
        if self.scale < 0.999:
            eff = style.TYPE["node"]["size"] * self.scale
            self.warnings.append(
                "Layout is %.1f pt wide but the target column is %.1f pt, so the figure is "
                "scaled to %.0f%%; body labels render at ~%.1f pt."
                % (nat_w, self.target, self.scale * 100, eff))
            if eff < style.MIN_RENDERED_PT:
                self.warnings.append(
                    "Effective label size %.1f pt is below the %.1f pt legibility floor -- "
                    "split the figure or shorten labels." % (eff, style.MIN_RENDERED_PT))
        return cv

    def tostring(self):
        cv = self.render()
        head = ("<svg xmlns='http://www.w3.org/2000/svg' "
                "xmlns:xlink='http://www.w3.org/1999/xlink' version='1.1' "
                "width='%spt' height='%spt' viewBox='0 0 %s %s'>"
                % (fmt(cv.width), fmt(cv.height), fmt(self._viewbox[0]), fmt(self._viewbox[1])))
        parts = [head]
        if cv.defs:
            parts.append("<defs>%s</defs>" % "".join(cv.defs))
        if cv.bg and cv.bg != "none":
            parts.append("<rect x='0' y='0' width='%s' height='%s' fill='%s'/>"
                         % (fmt(self._viewbox[0]), fmt(self._viewbox[1]), cv.bg))
        parts.extend(cv.body)
        parts.append("</svg>")
        return "\n".join(parts)

    # ----------------------------------------------------------- drawing
    def _draw_container(self, cv, it):
        s = it.spec
        frame = s.get("frame", "none")
        fill = style.resolve_color(s.get("fill"), "none")
        if frame not in (None, "none", False) or fill != "none":
            stroke = style.resolve_color(s.get("frame_color"),
                                         style.FRAME_GREY if frame == "dashed" else style.INK)
            if frame in (None, "none", False):
                stroke = "none"
            dash = None
            if frame in ("dashed", True):
                dash = style.DASH["frame"]
            elif frame == "region":
                dash = style.DASH["region"]
                stroke = style.resolve_color(s.get("frame_color"), style.FRAME_GREY)
            lw = style.stroke_width(s.get("frame_lw"), style.STROKE["frame"])
            r = float(s.get("corner", style.GEOM["frame_corner"]))
            svg.rect(cv, it.x, it.y, it.w, it.h, r, fill, stroke, lw, dash,
                     s.get("frame_opacity"))

        title = s.get("title")
        if title:
            pos = s.get("title_pos", "above")
            ts = float(s.get("title_size", style.TYPE["stage_title"]["size"]))
            tc = style.resolve_color(s.get("title_color"), style.INK, ink=True)
            bold = s.get("title_bold", style.TYPE["stage_title"]["weight"] == "bold")
            ital = s.get("title_italic", style.TYPE["stage_title"]["style"] == "italic")
            ha = s.get("title_align", "center")
            tx = {"left": it.x + 2.0, "right": it.x + it.w - 2.0}.get(ha, it.x + it.w / 2.0)
            anch = {"left": "start", "right": "end"}.get(ha, "middle")
            nl = len(str(title).split("\n"))
            if pos == "above":
                ty = it.y - style.GEOM["stage_title_gap"] - ts * (nl - 0.5) * 1.18
                svg.draw_text(cv, tx, ty, title, ts, self.font, bold, ital, tc, anch, "middle")
            elif pos == "below":
                svg.draw_text(cv, tx, it.y + it.h + style.GEOM["stage_title_gap"] + ts * 0.5,
                              title, ts, self.font, bold, ital, tc, anch, "middle")
            elif pos == "inside-bottom":
                svg.draw_text(cv, tx, it.y + it.h - style.GEOM["stage_pad"] - ts * 0.5,
                              title, ts, self.font, bold, ital, tc, anch, "middle")
            else:  # inside-top
                svg.draw_text(cv, tx, it.y + s.get("_pad", (6, 0, 0, 0))[0] + ts * 0.5,
                              title, ts, self.font, bold, ital, tc, anch, "middle")

        if s.get("badge"):
            bs = float(s.get("badge_size", style.TYPE["annot"]["size"]))
            svg.draw_text(cv, it.x + it.w - 3.0, it.y + bs * 0.9, s["badge"], bs,
                          self.font, False, True,
                          style.resolve_color(s.get("badge_color"), style.INK), "end", "middle")

        for c in it.children:
            if c.kind == "node":
                shapes.draw(cv, c, self.ctx)
            else:
                self._draw_container(cv, c)

    def _draw_note(self, cv, n):
        if not isinstance(n, dict):
            return
        size = float(n.get("size", style.TYPE["annot"]["size"]))
        color = style.resolve_color(n.get("color"), style.INK, ink=True)
        anchor = n.get("anchor", "middle")
        x = y = None
        if n.get("at"):
            x, y = float(n["at"][0]), float(n["at"][1])
        elif n.get("near"):
            item, side = edgemod.resolve_ref(n["near"], self.nodes)
            x, y = item.port(side or "n")
        if x is None:
            return
        x += float(n.get("dx", 0.0))
        y += float(n.get("dy", 0.0))
        svg.draw_text(cv, x, y, n.get("text", ""), size, n.get("font", self.font),
                      n.get("bold", False), n.get("italic", style.TYPE["annot"]["style"] == "italic"),
                      color, anchor, "middle", n.get("rotate", 0))

    def legend_layout(self, lg=None, w=None, h=None):
        """Place the legend: a list of ``(entry, x, y, size, kind, colour,
        text_width)`` plus the bounding box.  Shared by the SVG and PPTX
        writers so the two cannot disagree, and so the PPTX can group it."""
        from . import text as T
        lg = lg if lg is not None else (self.spec.get("legend") or {})
        w = w if w is not None else self._viewbox[0]
        h = h if h is not None else self._viewbox[1]
        items = lg.get("items") or []
        size = float(lg.get("size", style.TYPE["caption"]["size"]))
        gap = float(lg.get("gap", 3.2))
        sw = float(lg.get("swatch", 13.0))
        pad = float(lg.get("pad", 3.0))
        vertical = lg.get("dir", "col") == "col"
        rows = [(e, T.line_width(e.get("text", ""), size, self.font, False, False))
                for e in items]
        bw = max((sw + 3.0 + t for _, t in rows), default=0.0)
        bh = len(rows) * (size + gap) - gap
        if not vertical:
            bw = sum(sw + 3.0 + t for _, t in rows) + gap * (len(rows) - 1)
            bh = size
        pos = lg.get("at")
        if pos:
            x0, y0 = float(pos[0]), float(pos[1])
        else:
            corner = lg.get("corner", "ne")
            x0 = w - bw - pad - 4 if "e" in corner else pad + 4
            y0 = pad + 4 if "n" in corner else h - bh - pad - 4
        out = []
        cx, cy = x0, y0
        for e, tw in rows:
            out.append((e, cx, cy, size, e.get("kind", "line"),
                        style.resolve_color(e.get("color"), style.INK, ink=True), tw))
            if vertical:
                cy += size + gap
            else:
                cx += sw + 3.0 + tw + gap
        return {"rows": out, "box": (x0 - pad, y0 - pad, bw + 2 * pad, bh + 2 * pad),
                "swatch": sw, "framed": bool(lg.get("box", False))}

    def _draw_legend(self, cv, lg, w, h):
        self._viewbox = (w, h)
        lay = self.legend_layout(lg, w, h)
        sw = lay["swatch"]
        if lay["framed"]:
            bx, by, bw, bh = lay["box"]
            svg.rect(cv, bx, by, bw, bh, 2.0, "#FFFFFF", style.FRAME_GREY,
                     style.STROKE["hairline"])
        for e, cx, cy, size, kind, col, tw in lay["rows"]:
            ymid = cy + size / 2.0
            if kind in ("line", "arrow", "dashed"):
                dash = None
                if kind == "dashed" or e.get("dash"):
                    dash = style.DASH.get(e.get("dash"), style.DASH["flow"]) \
                        if isinstance(e.get("dash"), str) else style.DASH["flow"]
                lw = style.stroke_width(e.get("lw"), style.STROKE["flow"])
                mk = ""
                if e.get("arrow", kind != "line"):
                    mk = " marker-end='url(#%s)'" % cv.marker(
                        col, lw, "tri", style.arrow_geometry(lw, self.arrow_app))
                cv.add("<path d='M%s,%s L%s,%s' fill='none' stroke='%s' stroke-width='%s'%s%s/>"
                       % (fmt(cx), fmt(ymid), fmt(cx + sw), fmt(ymid), col, fmt(lw),
                          " stroke-dasharray='%s'" % dash if dash else "", mk))
            else:
                svg.rect(cv, cx, cy + size * 0.1, sw, size * 0.8, 1.2,
                         style.resolve_color(e.get("fill"), col), style.INK, style.STROKE["hairline"])
            svg.draw_text(cv, cx + sw + 3.0, ymid, e.get("text", ""), size, self.font,
                          False, False, style.resolve_color(e.get("text_color"), style.INK),
                          "start", "middle")


def _above_title_h(item, depth=0):
    """Height an "above" title needs, for this container or any it holds.

    Deliberately conservative: it does not check whether the titled container
    actually sits at the top edge, because titles are measured before anything
    is placed.  Over-reserving costs a few points of white space; under-
    reserving clips text off the canvas.
    """
    h = 0.0
    s = getattr(item, "spec", None) or {}
    if s.get("title") and s.get("title_pos", "above") == "above":
        ts = float(s.get("title_size", style.TYPE["stage_title"]["size"]))
        nlines = len(str(s["title"]).split("\n"))
        h = ts * (1.0 + 0.18 * (nlines - 1)) * nlines + style.GEOM["stage_title_gap"]
    # Titles of nested containers are reserved inside their parent by the
    # layout (layout._above_title), so only this item's own title counts.
    return h


def _scale_sizes(node, k):
    """Multiply every explicit font size in a spec by ``k`` (type_scale)."""
    if isinstance(node, dict):
        for key, v in list(node.items()):
            if key in style.SIZE_KEYS and isinstance(v, (int, float)) \
                    and not isinstance(v, bool):
                node[key] = round(float(v) * k, 3)
            elif isinstance(v, (dict, list)) and key not in ("symbols", "paper"):
                _scale_sizes(v, k)
    elif isinstance(node, list):
        for v in node:
            _scale_sizes(v, k)


def _as_container(p):
    if not isinstance(p, dict):
        return {"body": []}
    p = dict(p)
    if "body" not in p and "row" not in p and "col" not in p and "stack" not in p:
        p["body"] = []
    return p
