"""Edge routing.

Real pipeline figures use three routing patterns and essentially nothing else:
a straight shot between facing ports, an orthogonal dog-leg when the ports are
offset, and a bus that leaves a node sideways, runs parallel to the flow and
re-enters (the classic residual / skip connection).  Anything more elaborate
reads as auto-generated, so the router deliberately stops here.

v3: every edge is routed exactly once, by ``plan()``, and the SVG, PPTX and
VSDX writers and the auditor all read that plan.  Two guarantees were added
because hand-edited PPTX files broke on them:

* the last segment of a bent arrow is at least ``style.final_run(lw)`` long --
  WPS aims a line-end head by stepping back one head length along the path,
  so a shorter last run puts the head at 45 degrees;
* dog-legs that jog in the same corridor get their own lane,
  ``style.GEOM["lane"]`` apart, instead of all jogging at the midpoint and
  drawing on top of each other.
"""

from . import style, svg
from .svg import fmt

SIDE_VEC = {"n": (0, -1), "s": (0, 1), "e": (1, 0), "w": (-1, 0), "c": (0, 0)}


def resolve_ref(ref, nodes):
    """``"id"`` or ``"id.side"`` -> (item, side or None)."""
    if isinstance(ref, (list, tuple)):
        return None, None
    ref = str(ref)
    side = None
    if "." in ref:
        head, tail = ref.rsplit(".", 1)
        if tail.lower() in ("n", "s", "e", "w", "c", "nw", "ne", "sw", "se"):
            ref, side = head, tail.lower()
    item = nodes.get(ref)
    if item is None:
        raise KeyError("edge references unknown node id %r" % ref)
    return item, side


def auto_sides(a, b):
    dx = b.cx - a.cx
    dy = b.cy - a.cy
    ax_overlap = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
    ay_overlap = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
    if abs(dx) >= abs(dy) or (ay_overlap > 0 and abs(dx) > 2):
        return ("e", "w") if dx >= 0 else ("w", "e")
    return ("s", "n") if dy >= 0 else ("n", "s")


MIN_FIRST = 3.0   # shortest visible run before a jog, in points


def _jog(lo, hi, mid, need_lo, need_hi):
    """Position of the cross-segment of a dog-leg between ``lo`` and ``hi``.

    ``need_lo``/``need_hi`` are the minimum runs at each end (an arrowed end
    needs ``final_run``).  When both cannot be met the arrowed end gets what
    is left after a ``MIN_FIRST`` stub; the auditor and the PPTX writer then
    treat that arrow as short.
    """
    span = hi - lo
    sign = 1.0 if span >= 0 else -1.0
    span = abs(span)
    if span < 0.5:
        return None
    pos = span * mid
    if need_hi and span - pos < need_hi:
        pos = span - need_hi
    first = max(need_lo, MIN_FIRST)
    if pos < first:
        pos = min(first, span / 2.0)
    if need_lo and need_hi and span < need_lo + need_hi:
        pos = span / 2.0
    return lo + sign * pos


NEAR_MISS = 6.0     # a jog smaller than this reads as a drawing error


def _near_straight(a, sa, b, sb, p0, p1):
    """Facing ports a few points out of line: draw one straight arrow inside
    the overlap of the two sides instead of a 2-pt dog-leg, which reads as a
    slip of the mouse.  Only when both sides overlap by a comfortable margin."""
    vx, vy = SIDE_VEC.get(sa, (0, 0))
    wx, wy = SIDE_VEC.get(sb, (0, 0))
    if not (hasattr(a, "body") and hasattr(b, "body")):
        return None
    body = lambda it: it.body()
    ax, ay, aw, ah = body(a)
    bx, by, bw, bh = body(b)
    if vx and wx and vx == -wx and (p1[0] - p0[0]) * vx > 0 and abs(p0[1] - p1[1]) < NEAR_MISS:
        lo, hi = max(ay, by) + 2.0, min(ay + ah, by + bh) - 2.0
        if hi - lo >= 2.0:
            y = min(max((p0[1] + p1[1]) / 2.0, lo), hi)
            return [(p0[0], y), (p1[0], y)]
    if vy and wy and vy == -wy and (p1[1] - p0[1]) * vy > 0 and abs(p0[0] - p1[0]) < NEAR_MISS:
        lo, hi = max(ax, bx) + 2.0, min(ax + aw, bx + bw) - 2.0
        if hi - lo >= 2.0:
            x = min(max((p0[0] + p1[0]) / 2.0, lo), hi)
            return [(x, p0[1]), (x, p1[1])]
    return None


def _faces(a, side, b):
    """Does leaving ``a`` through ``side`` head towards ``b``?"""
    vx, vy = SIDE_VEC[side]
    return (b.cx - a.cx) * vx + (b.cy - a.cy) * vy > 0 and \
        (abs(b.cx - a.cx) > a.w / 2.0 if vx else abs(b.cy - a.cy) > a.h / 2.0)


def _ortho(p0, sa, p1, sb, stub, mid, need0=0.0, need1=0.0):
    """Standard orthogonal connector: leave along the port normal, turn at
    most twice, arrive along the target port normal."""
    vx, vy = SIDE_VEC.get(sa, (0, 0))
    wx, wy = SIDE_VEC.get(sb, (0, 0))
    h_out, h_in = bool(vx), bool(wx)
    if h_out and h_in and vx == -wx and (p1[0] - p0[0]) * vx > 0:
        mx = _jog(p0[0], p1[0], mid, need0, need1)
        if mx is not None:
            return [p0, (mx, p0[1]), (mx, p1[1]), p1]
    if not h_out and not h_in and vy == -wy and (p1[1] - p0[1]) * vy > 0:
        my = _jog(p0[1], p1[1], mid, need0, need1)
        if my is not None:
            return [p0, (p0[0], my), (p1[0], my), p1]
    a = (p0[0] + vx * stub, p0[1] + vy * stub)
    b = (p1[0] + wx * stub, p1[1] + wy * stub)
    # Both ports face the same way (e -> e, n -> n ...): go round the outside
    # of whichever box sticks out further, never through the target.
    if h_out and h_in and vx == wx:
        m = (max if vx > 0 else min)(p0[0], p1[0]) + vx * max(stub, need1)
        return [p0, (m, p0[1]), (m, p1[1]), p1]
    if not h_out and not h_in and vy == wy and vy:
        m = (max if vy > 0 else min)(p0[1], p1[1]) + vy * max(stub, need1)
        return [p0, (p0[0], m), (p1[0], m), p1]
    if h_out and h_in:
        mx = a[0] + (b[0] - a[0]) * mid
        pts = [p0, a, (mx, a[1]), (mx, b[1]), b, p1]
    elif not h_out and not h_in:
        my = a[1] + (b[1] - a[1]) * mid
        pts = [p0, a, (a[0], my), (b[0], my), b, p1]
    elif h_out:
        pts = [p0, a, (b[0], a[1]), b, p1]
    else:
        pts = [p0, a, (a[0], b[1]), b, p1]
    return pts


def _path_points(a, sa, b, sb, e, need0=0.0, need1=0.0, mid=None):
    p0 = a.port(sa)
    p1 = b.port(sb)
    route = e.get("route", "auto")
    stub = float(e.get("stub", style.GEOM["port_stub"]))
    if mid is None:
        mid = float(e.get("mid", 0.5))

    if e.get("via"):
        pts = [p0]
        for v in e["via"]:
            if isinstance(v, (list, tuple)) and len(v) == 2:
                pts.append((float(v[0]), float(v[1])))
        pts.append(p1)
        return pts

    if route == "straight":
        return [p0, p1]

    # already facing each other on one axis -- a single straight run reads best
    if route == "auto":
        vx, vy = SIDE_VEC.get(sa, (0, 0))
        wx, wy = SIDE_VEC.get(sb, (0, 0))
        aligned_x = abs(p0[0] - p1[0]) < 0.4 and vy and wy and vy == -wy
        aligned_y = abs(p0[1] - p1[1]) < 0.4 and vx and wx and vx == -wx
        if aligned_x or aligned_y:
            return [p0, p1]
        near = _near_straight(a, sa, b, sb, p0, p1)
        if near:
            return near

    if route in ("bus", "loop"):
        bend = float(e.get("bend", 12.0))
        vx, vy = SIDE_VEC.get(sa, (1, 0))
        wx, wy = SIDE_VEC.get(sb, (-1, 0))
        bend1 = max(bend, need1) if need1 else bend
        m0 = (p0[0] + vx * bend, p0[1] + vy * bend)
        m1 = (p1[0] + wx * bend1, p1[1] + wy * bend1)
        if vx:
            return [p0, m0, (m0[0], m1[1]), m1, p1]
        return [p0, m0, (m1[0], m0[1]), m1, p1]

    if route == "hv":
        va = (p0[0] + SIDE_VEC.get(sa, (1, 0))[0] * stub,
              p0[1] + SIDE_VEC.get(sa, (1, 0))[1] * stub)
        vb = (p1[0] + SIDE_VEC.get(sb, (-1, 0))[0] * stub,
              p1[1] + SIDE_VEC.get(sb, (-1, 0))[1] * stub)
        return [p0, va, (vb[0], va[1]), vb, p1]
    if route == "vh":
        va = (p0[0] + SIDE_VEC.get(sa, (0, 1))[0] * stub,
              p0[1] + SIDE_VEC.get(sa, (0, 1))[1] * stub)
        vb = (p1[0] + SIDE_VEC.get(sb, (0, -1))[0] * stub,
              p1[1] + SIDE_VEC.get(sb, (0, -1))[1] * stub)
        return [p0, va, (va[0], vb[1]), vb, p1]

    return _ortho(p0, sa, p1, sb, stub, mid, need0, need1)


def _dedup(pts):
    """Drop repeated points, then collapse collinear runs."""
    out = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - out[-1][0]) > 0.05 or abs(p[1] - out[-1][1]) > 0.05:
            out.append(p)
    if len(out) < 3:
        return out
    slim = [out[0]]
    for i in range(1, len(out) - 1):
        ax, ay = slim[-1]
        bx, by = out[i]
        cx, cy = out[i + 1]
        cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(cross) > 0.05:
            slim.append(out[i])
    slim.append(out[-1])
    return slim


class Route(object):
    """One edge, routed: everything a writer or the auditor needs."""
    __slots__ = ("spec", "index", "a", "sa", "b", "sb", "pts", "color", "lw",
                 "dash", "arrow", "head", "preset", "need_end", "need_start",
                 "short_end", "short_start", "lane")

    @property
    def arrow_end(self):
        return self.arrow in ("end", "both")

    @property
    def arrow_start(self):
        return self.arrow in ("start", "both")


def _seg_len(p, q):
    return abs(q[0] - p[0]) + abs(q[1] - p[1])


def edge_style(e):
    color = style.resolve_color(e.get("color"), style.INK, ink=True)
    lw = style.stroke_width(e.get("lw", "flow"), style.STROKE["flow"])
    dash = e.get("dash")
    if dash is True:
        dash = style.DASH["flow"]
    elif isinstance(dash, str):
        dash = style.DASH.get(dash, dash)
    return color, lw, dash or None


def plan(spec_edges, nodes, app=style.DEFAULT_ARROW_APP, bad=None):
    """Route every edge once.  Returns a list of ``Route``.  Edges naming an
    unknown node are skipped and reported into ``bad`` when it is given."""
    routes = []
    for k, e in enumerate(spec_edges or []):
        r = Route()
        r.spec, r.index = e, k
        try:
            r.a, r.sa = resolve_ref(e["from"], nodes)
            r.b, r.sb = resolve_ref(e["to"], nodes)
        except KeyError as exc:
            if bad is None:
                raise
            bad.append((k, str(exc)))
            continue
        if r.sa is None or r.sb is None:
            da, db = auto_sides(r.a, r.b)
            opp = {"e": "w", "w": "e", "n": "s", "s": "n"}
            # One side given: leave (or arrive) facing it when the geometry
            # allows, instead of guessing the other end independently.
            if r.sb in opp and r.sa is None and _faces(r.a, opp[r.sb], r.b):
                da = opp[r.sb]
            if r.sa in opp and r.sb is None and _faces(r.a, r.sa, r.b):
                db = opp[r.sa]
            r.sa = r.sa or da
            r.sb = r.sb or db
        r.color, r.lw, r.dash = edge_style(e)
        r.arrow = e.get("arrow", "end")
        r.preset = style.pick_preset(r.lw, app=app)
        r.head = style.head_size(r.lw, r.preset, app)
        need = style.final_run(r.lw, app)
        r.need_end = need if r.arrow_end else 0.0
        r.need_start = need if r.arrow_start else 0.0
        r.lane = None
        r.pts = _dedup(_path_points(r.a, r.sa, r.b, r.sb, e, r.need_start, r.need_end))
        routes.append(r)
    _assign_lanes(routes, nodes)
    for r in routes:
        _mark_short(r)
    return routes


def _mark_short(r):
    pts = r.pts
    bent = len(pts) > 2
    lim = 2.0 * style.worst_head(r.lw)
    r.short_end = bool(bent and r.arrow_end and _seg_len(pts[-2], pts[-1]) < lim)
    r.short_start = bool(bent and r.arrow_start and _seg_len(pts[0], pts[1]) < lim)


def _dogleg(r):
    """(axis, jog, lo, hi, span) for a two-bend H-V-H / V-H-V route."""
    p = r.pts
    if len(p) != 4:
        return None
    if abs(p[0][1] - p[1][1]) < 0.05 and abs(p[2][1] - p[3][1]) < 0.05:
        return ("x", p[1][0], p[0][0], p[3][0], (min(p[1][1], p[2][1]), max(p[1][1], p[2][1])))
    if abs(p[0][0] - p[1][0]) < 0.05 and abs(p[2][0] - p[3][0]) < 0.05:
        return ("y", p[1][1], p[0][1], p[3][1], (min(p[1][0], p[2][0]), max(p[1][0], p[2][0])))
    return None


def _assign_lanes(routes, nodes):
    """Spread dog-legs that would jog on top of each other.

    Edges that share a port (a fan-out from one source, a fan-in to one
    target) form a bundle and keep one common jog: the trunk.  Two bundles
    conflict when their cross-segments lie on the same axis within a lane
    width and their spans overlap; conflicting bundles are re-jogged
    ``lane`` apart, ordered so their runs do not cross, inside the window
    that leaves every member its required final run.
    """
    lane = style.GEOM["lane"]
    cand = []
    for r in routes:
        if r.spec.get("mid") is not None or r.spec.get("via") or \
                r.spec.get("route", "auto") not in ("auto", "ortho"):
            continue
        d = _dogleg(r)
        if d:
            cand.append([r, d])
    # bundles: edges sharing a port, merged only while one jog can serve them all
    bundles = [[c] for c in cand]
    merged = True
    while merged:
        merged = False
        for i in range(len(bundles)):
            for k in range(i + 1, len(bundles)):
                A, B = bundles[i], bundles[k]
                if A[0][1][0] != B[0][1][0]:
                    continue
                if not any(_share_port(x[0], y[0]) for x in A for y in B):
                    continue
                lo, hi = _window(A + B)
                if lo > hi:
                    continue
                bundles[i] = A + B
                del bundles[k]
                merged = True
                break
            if merged:
                break
    units = []
    for members in bundles:
        axis = members[0][1][0]
        jog = sum(m[1][1] for m in members) / float(len(members))
        span = (min(m[1][4][0] for m in members), max(m[1][4][1] for m in members))
        units.append({"m": members, "axis": axis, "jog": jog, "span": span})
    # a bundle's own members must agree on one jog
    for u in units:
        if len(u["m"]) > 1:
            lo, hi = _window(u["m"])
            u["jog"] = min(max(u["jog"], lo), hi)
    # conflicting bundles
    done = set()
    for i, u in enumerate(units):
        if i in done:
            continue
        group = [u]
        for k in range(i + 1, len(units)):
            v = units[k]
            if k in done or v["axis"] != u["axis"]:
                continue
            if any(abs(v["jog"] - g["jog"]) < lane and
                   min(v["span"][1], g["span"][1]) > max(v["span"][0], g["span"][0]) - 0.1
                   for g in group):
                group.append(v)
                done.add(k)
        if len(group) > 1 and _window([m for g in group for m in g["m"]])[0] > \
                _window([m for g in group for m in g["m"]])[1]:
            group = [u]           # no common window: leave these jogs alone
        if len(group) > 1:
            lo, hi = _window([m for g in group for m in g["m"]])
            n = len(group)
            width = (n - 1) * lane
            centre = sum(g["jog"] for g in group) / float(n)
            if hi - lo >= width:
                start = min(max(centre - width / 2.0, lo), hi - width)
            else:
                start = (lo + hi) / 2.0 - width / 2.0
            ax = 1 if u["axis"] == "x" else 0
            down = sum(1 for g in group for m in g["m"]
                       if m[0].pts[-1][ax] - m[0].pts[0][ax] > 0)
            total = sum(len(g["m"]) for g in group)
            group.sort(key=lambda g: min(m[0].pts[0][ax] for m in g["m"]),
                       reverse=down * 2 >= total)
            first = group[0]["m"][0][1]
            forward = first[3] >= first[2]
            for k, g in enumerate(group):
                g["jog"] = start + k * lane if forward else start + (n - 1 - k) * lane
        for k, g in enumerate(group):
            for r, d in g["m"]:
                p = r.pts
                pos = g["jog"]
                if d[0] == "x":
                    r.pts = [p[0], (pos, p[0][1]), (pos, p[3][1]), p[3]]
                else:
                    r.pts = [p[0], (p[0][0], pos), (p[3][0], pos), p[3]]
                r.lane = k


def _window(members):
    """Range of jog positions that keeps every member's end runs."""
    lo, hi = -1e9, 1e9
    for r, d in members:
        a, b = d[2], d[3]
        if a <= b:
            lo = max(lo, a + max(r.need_start, MIN_FIRST))
            hi = min(hi, b - r.need_end)
        else:
            lo = max(lo, b + r.need_end)
            hi = min(hi, a - max(r.need_start, MIN_FIRST))
    return lo, hi


def _share_port(r1, r2):
    return ((r1.a is r2.a and r1.sa == r2.sa) or (r1.b is r2.b and r1.sb == r2.sb))


def draw(cv, r, ctx):
    """Draw one planned route into the SVG canvas."""
    e = r.spec
    marker_end = marker_start = ""
    kind = e.get("head", "tri")
    if r.arrow_end:
        marker_end = " marker-end='url(#%s)'" % cv.marker(r.color, r.lw, kind, r.head)
    if r.arrow_start:
        marker_start = " marker-start='url(#%s)'" % cv.marker(r.color, r.lw, kind, r.head)

    d = "M" + " L".join("%s,%s" % (fmt(x), fmt(y)) for x, y in r.pts)
    cv.add("<path d='%s' fill='none' stroke='%s' stroke-width='%s' stroke-linejoin='miter'"
           " stroke-linecap='butt'%s%s%s/>"
           % (d, r.color, fmt(r.lw), " stroke-dasharray='%s'" % r.dash if r.dash else "",
              marker_start, marker_end))

    if e.get("label"):
        _label(cv, e, r.pts, r.color, ctx)


def label_anchor(e, pts):
    """Point, and whether the carrying segment is horizontal, for a label."""
    t = float(e.get("label_pos", 0.5))
    segs = []
    total = 0.0
    for i in range(len(pts) - 1):
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        L = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        segs.append((L, pts[i], pts[i + 1]))
        total += L
    target = total * t
    acc = 0.0
    px, py, horiz = pts[0][0], pts[0][1], True
    for k, (L, p, q) in enumerate(segs):
        if acc + L >= target or k == len(segs) - 1:
            r = 0 if L == 0 else min(1.0, (target - acc) / L)
            px = p[0] + (q[0] - p[0]) * r
            py = p[1] + (q[1] - p[1]) * r
            horiz = abs(q[0] - p[0]) >= abs(q[1] - p[1])
            break
        acc += L
    return px, py, horiz


def label_box(e, pts, color, font):
    """Where an edge label goes: (x, y, anchor, size, colour)."""
    px, py, horiz = label_anchor(e, pts)
    size = float(e.get("label_size", style.TYPE["caption"]["size"]))
    rot = e.get("label_rotate", 0)
    dx = float(e.get("label_dx", 0.0))
    dy = float(e.get("label_dy", -(size * 0.62) if horiz else 0.0))
    anchor = e.get("label_anchor", "middle" if horiz else "start")
    if not horiz and not e.get("label_dx"):
        dx = size * 0.5
    if rot in (90, -90, 270) and not horiz:
        # runs along a vertical wire, on its left unless told otherwise
        anchor = e.get("label_anchor", "middle")
        dx = float(e.get("label_dx", -size * 0.62))
        dy = float(e.get("label_dy", 0.0))
    tc = style.resolve_color(e.get("label_color"),
                             color if e.get("label_color_match", True) else style.INK, ink=True)
    return px + dx, py + dy, anchor, size, tc


def _label(cv, e, pts, color, ctx):
    x, y, anchor, size, tc = label_box(e, pts, color, ctx["font"])
    svg.draw_text(cv, x, y, e["label"], size,
                  e.get("label_font", ctx["font"]), e.get("label_bold", False),
                  e.get("label_italic", False), tc, anchor, "middle",
                  e.get("label_rotate", 0))
