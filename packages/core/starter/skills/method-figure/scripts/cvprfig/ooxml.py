"""Read back a .pptx -- ours, another tool's, or one a person has edited.

The auditors need the slide as it will be drawn, not as it was specified: the
absolute path of every line (group transforms, flips and rotation applied),
its arrowheads, every text run with its size and baseline shift, every
picture with its pixel size and printed size, and anything that triggers a
raster fallback.  Standard library only; nothing here writes.

Coordinates are points, origin top-left, y down -- the same frame the engine
lays out in.
"""

import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET

from . import imgsize

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
EMU = 12700.0
A = "{%s}" % NS["a"]
P = "{%s}" % NS["p"]
R = "{%s}" % NS["r"]


def pt(v, default=0.0):
    try:
        return float(v) / EMU
    except (TypeError, ValueError):
        return default


class Affine(object):
    """x' = a x + b y + c ;  y' = d x + e y + f"""
    __slots__ = ("m",)

    def __init__(self, m=(1, 0, 0, 0, 1, 0)):
        self.m = m

    def __call__(self, p):
        a, b, c, d, e, f = self.m
        return (a * p[0] + b * p[1] + c, d * p[0] + e * p[1] + f)

    def then(self, o):
        """Apply self, then o."""
        a, b, c, d, e, f = self.m
        A_, B, C, D, E, F = o.m
        return Affine((A_ * a + B * d, A_ * b + B * e, A_ * c + B * f + C,
                       D * a + E * d, D * b + E * e, D * c + E * f + F))

    @property
    def sx(self):
        a, b, _, d, e, _ = self.m
        return (abs(a * e - b * d)) ** 0.5 or 1.0


def _xfrm(el):
    x = el.find(A + "xfrm") if el is not None else None
    if x is None:
        return None
    off = x.find(A + "off")
    ext = x.find(A + "ext")
    d = {"x": pt(off.get("x")) if off is not None else 0.0,
         "y": pt(off.get("y")) if off is not None else 0.0,
         "w": pt(ext.get("cx")) if ext is not None else 0.0,
         "h": pt(ext.get("cy")) if ext is not None else 0.0,
         "rot": int(x.get("rot", "0") or 0) / 60000.0,
         "fh": x.get("flipH") in ("1", "true"), "fv": x.get("flipV") in ("1", "true")}
    co, ce = x.find(A + "chOff"), x.find(A + "chExt")
    if co is not None and ce is not None:
        d.update(cx=pt(co.get("x")), cy=pt(co.get("y")),
                 cw=pt(ce.get("cx")), ch=pt(ce.get("cy")))
    return d


def _local(xf):
    """Shape-local (0..w, 0..h) -> parent frame: flip, rotate about centre."""
    import math
    w, h = xf["w"], xf["h"]
    cx, cy = xf["x"] + w / 2.0, xf["y"] + h / 2.0
    sx = -1.0 if xf["fh"] else 1.0
    sy = -1.0 if xf["fv"] else 1.0
    t = math.radians(xf["rot"])
    c, s = math.cos(t), math.sin(t)
    # p -> (p - centre_local) * flip -> rotate -> + centre
    return Affine((c * sx, -s * sy, cx - (c * sx * w / 2.0 - s * sy * h / 2.0),
                   s * sx, c * sy, cy - (s * sx * w / 2.0 + c * sy * h / 2.0)))


def _group_map(xf):
    if xf is None or not xf.get("cw") or not xf.get("ch"):
        return Affine()
    kx = xf["w"] / xf["cw"] if xf["cw"] else 1.0
    ky = xf["h"] / xf["ch"] if xf["ch"] else 1.0
    return Affine((kx, 0, xf["x"] - xf["cx"] * kx, 0, ky, xf["y"] - xf["cy"] * ky))


# ------------------------------------------------------------- geometry
def _preset_path(prst, w, h, adj):
    a1 = adj.get("adj1", 50000) / 100000.0
    a2 = adj.get("adj2", 50000) / 100000.0
    a3 = adj.get("adj3", 50000) / 100000.0
    if prst in ("line", "straightConnector1"):
        return [(0, 0), (w, h)]
    if prst in ("bentConnector2",):
        return [(0, 0), (w, 0), (w, h)]
    if prst in ("bentConnector3", "curvedConnector3"):
        return [(0, 0), (w * a1, 0), (w * a1, h), (w, h)]
    if prst in ("bentConnector4", "curvedConnector4"):
        return [(0, 0), (w * a1, 0), (w * a1, h * a2), (w, h * a2), (w, h)]
    if prst in ("bentConnector5", "curvedConnector5"):
        return [(0, 0), (w * a1, 0), (w * a1, h * a2), (w * a3, h * a2), (w * a3, h), (w, h)]
    if prst == "curvedConnector2":
        return [(0, 0), (w, 0), (w, h)]
    return None


def _cust_paths(geom, w, h):
    """Polylines of a custGeom (curves reduced to their control polygon end
    points, which keeps the end tangent -- the part arrowheads care about)."""
    out = []
    for path in geom.iter(A + "path"):
        pw = float(path.get("w") or 0) or None
        ph = float(path.get("h") or 0) or None
        kx = (w * EMU / pw) if pw else 1.0
        ky = (h * EMU / ph) if ph else 1.0
        cur = []
        closed = False
        for cmd in path:
            tag = cmd.tag.replace(A, "")
            pts_ = [(float(q.get("x", 0)) * kx / EMU, float(q.get("y", 0)) * ky / EMU)
                    for q in cmd.findall(A + "pt")]
            if tag == "moveTo":
                if len(cur) > 1:
                    out.append((cur, closed))
                cur, closed = list(pts_[:1]), False
            elif tag == "lnTo":
                cur.extend(pts_)
            elif tag in ("cubicBezTo", "quadBezTo"):
                # keep the last control point so the end tangent survives
                cur.extend(pts_[-2:] if len(pts_) >= 2 else pts_)
            elif tag == "close":
                closed = True
        if len(cur) > 1:
            out.append((cur, closed or path.get("fill") not in ("none",) and False))
    return out


# -------------------------------------------------------------- reading
class Slide(object):
    def __init__(self):
        self.size = (0.0, 0.0)
        self.shapes = []
        self.has_bg = False
        self.media = {}
        self.part = None
        self.theme_part = None
        self.theme_effects = []


def _rels(z, part):
    d, f = posixpath.split(part)
    rp = posixpath.join(d, "_rels", f + ".rels")
    out = {}
    if rp in z.namelist():
        for r in ET.fromstring(z.read(rp)):
            tgt = r.get("Target")
            if r.get("TargetMode") != "External":
                tgt = posixpath.normpath(posixpath.join(d, tgt))
            out[r.get("Id")] = tgt
    return out


def theme_part(z, slide_part):
    """The theme the slide's master uses (slide -> layout -> master -> theme)."""
    part = slide_part
    for kind in ("slideLayout", "slideMaster", "theme"):
        nxt = [t for t in _rels(z, part).values() if "/%s" % kind in t]
        if not nxt:
            break
        part = nxt[0]
        if kind == "theme":
            return part if part in z.namelist() else None
    th = sorted(n for n in z.namelist() if n.startswith("ppt/theme/theme"))
    return th[0] if th else None


def theme_effects(theme):
    """One entry per <a:effectStyle>, in order: a short description such as
    'outerShdw 38%' of what it draws, or '' for an empty style."""
    out = []
    for es in theme.iter(A + "effectStyle"):
        kinds = []
        el = es.find(A + "effectLst")
        for c in (el if el is not None else []):
            k = c.tag.replace(A, "")
            al = c.find(".//" + A + "alpha")
            if al is not None and al.get("val"):
                k += " %d%%" % round(int(al.get("val")) / 1000.0)
            kinds.append(k)
        if es.find(A + "effectDag") is not None:
            kinds.append("effectDag")
        if es.find(A + "sp3d") is not None:
            kinds.append("sp3d")
        out.append(", ".join(kinds))
    return out


def read(path, slide_no=1):
    z = zipfile.ZipFile(path)
    pres = ET.fromstring(z.read("ppt/presentation.xml"))
    sz = pres.find(P + "sldSz")
    s = Slide()
    s.size = (pt(sz.get("cx")), pt(sz.get("cy")))
    prel = _rels(z, "ppt/presentation.xml")
    ids = [x.get(R + "id") for x in pres.find(P + "sldIdLst")]
    part = prel[ids[slide_no - 1]]
    s.part = part
    root = ET.fromstring(z.read(part))
    rels = _rels(z, part)
    # background: slide, layout or master
    chain = [root]
    lay = [t for t in rels.values() if "slideLayout" in t]
    if lay:
        lroot = ET.fromstring(z.read(lay[0]))
        chain.append(lroot)
        mrel = [t for t in _rels(z, lay[0]).values() if "slideMaster" in t]
        if mrel:
            chain.append(ET.fromstring(z.read(mrel[0])))
    s.has_bg = any(c.find(P + "cSld/" + P + "bg") is not None for c in chain)
    # theme effect styles, to resolve <p:style><a:effectRef idx>
    s.theme_part = theme_part(z, part)
    if s.theme_part:
        s.theme_effects = theme_effects(ET.fromstring(z.read(s.theme_part)))
    tree = root.find(P + "cSld/" + P + "spTree")
    _walk(tree, Affine(), s, rels, z, [])
    return s


def _ln_props(sppr, style_el):
    ln = sppr.find(A + "ln") if sppr is not None else None
    out = {"w": None, "color": None, "dash": None, "head": None, "tail": None,
           "none": False}
    if ln is not None:
        if ln.get("w"):
            out["w"] = pt(ln.get("w"))
        if ln.find(A + "noFill") is not None:
            out["none"] = True
        c = ln.find(A + "solidFill/" + A + "srgbClr")
        if c is not None:
            out["color"] = "#" + c.get("val").upper()
        d = ln.find(A + "prstDash")
        if d is not None and d.get("val") not in ("solid", None):
            out["dash"] = d.get("val")
        if ln.find(A + "custDash") is not None:
            out["dash"] = "custom"
        for key, tag in (("head", "headEnd"), ("tail", "tailEnd")):
            e_ = ln.find(A + tag)
            if e_ is not None and e_.get("type") not in (None, "none"):
                out[key] = (e_.get("type"), e_.get("w", "med"), e_.get("len", "med"))
    if out["w"] is None:
        out["w"] = 0.75          # Office default line
    if out["color"] is None and style_el is not None:
        out["color"] = "#THEME"
    return out


def _effects(sppr):
    """Effects the shape itself declares."""
    if sppr is not None:
        el = sppr.find(A + "effectLst")
        if el is not None:
            return [c.tag.replace(A, "") for c in el]
        if sppr.find(A + "effectDag") is not None:
            return ["effectDag"]
    return []


def _theme_effect(style_el, s):
    """``(idx, description)`` of the theme effect a <p:style><a:effectRef>
    points at, or None.  An empty <a:effectLst/> on the shape cancels it in
    PowerPoint and Keynote, but LibreOffice draws it anyway (measured: 139
    of 139 FigGenie shapes exported with a shadow), so it is reported
    regardless of that override."""
    ref = style_el.find(A + "effectRef") if style_el is not None else None
    if ref is None:
        return None
    k = int(ref.get("idx", "0") or 0)
    if 0 < k <= len(s.theme_effects) and s.theme_effects[k - 1]:
        return k, s.theme_effects[k - 1]
    return None


def _text(el):
    """Paragraphs -> runs (text, size pt or None, baseline, b, i, font)."""
    tb = el.find(P + "txBody")
    if tb is None:
        return []
    runs = []
    for para in tb.findall(A + "p"):
        for r in para:
            tag = r.tag.replace(A, "")
            if tag not in ("r", "fld"):
                continue
            rp = r.find(A + "rPr")
            t = r.find(A + "t")
            txt = t.text if t is not None and t.text else ""
            if not txt:
                continue
            sz = rp.get("sz") if rp is not None else None
            base = int(rp.get("baseline", "0") or 0) if rp is not None else 0
            lat = rp.find(A + "latin") if rp is not None else None
            runs.append({"text": txt, "size": float(sz) / 100.0 if sz else None,
                         "baseline": base,
                         "b": rp is not None and rp.get("b") in ("1", "true"),
                         "i": rp is not None and rp.get("i") in ("1", "true"),
                         "font": lat.get("typeface") if lat is not None else None})
        runs.append({"text": "\n", "size": None, "baseline": 0, "b": False, "i": False,
                     "font": None, "br": True})
    while runs and runs[-1].get("br"):
        runs.pop()
    return runs


def _walk(tree, tf, s, rels, z, groups):
    for el in tree:
        tag = el.tag.replace(P, "")
        if tag == "grpSp":
            gpr = el.find(P + "grpSpPr")
            xf = _xfrm(gpr)
            nv = el.find(P + "nvGrpSpPr/" + P + "cNvPr")
            name = nv.get("name", "") if nv is not None else ""
            inner = _group_map(xf)
            if xf is not None and (xf["rot"] or xf["fh"] or xf["fv"]):
                outer = _local(dict(xf, w=xf["w"], h=xf["h"]))
                # child frame -> group's unrotated box -> rotated
                to_box = Affine((1, 0, -xf["x"], 0, 1, -xf["y"]))
                inner = inner.then(to_box).then(outer)
            _walk(el, inner.then(tf), s, rels, z, groups + [name])
            continue
        if tag not in ("sp", "cxnSp", "pic", "graphicFrame"):
            continue
        nv = el.find(".//" + P + "cNvPr")
        sh = {"tag": tag, "id": nv.get("id") if nv is not None else None,
              "name": nv.get("name", "") if nv is not None else "", "groups": groups}
        sppr = el.find(P + "spPr")
        style_el = el.find(P + "style")
        xf = _xfrm(sppr) if sppr is not None else None
        if tag == "graphicFrame":
            xf = _xfrm(el.find(P + "xfrm")) if el.find(P + "xfrm") is not None else None
            if xf is None:
                gx = el.find(P + "xfrm")
            sh["kind"] = "frame"
        if xf is None:
            continue
        full = _local(xf).then(tf)
        sh["xf"] = xf
        corners = [full(p) for p in ((0, 0), (xf["w"], 0), (xf["w"], xf["h"]), (0, xf["h"]))]
        xs = [c[0] for c in corners]; ys = [c[1] for c in corners]
        sh["box"] = (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        sh["scale"] = full.sx
        sh["effects"] = _effects(sppr)
        sh["theme_effect"] = _theme_effect(style_el, s) if not sh["effects"] else None
        geom = None
        if sppr is not None:
            pg = sppr.find(A + "prstGeom")
            cg = sppr.find(A + "custGeom")
            if pg is not None:
                adj = {}
                for gd in pg.iter(A + "gd"):
                    m = re.match(r"val\s+(-?\d+)", gd.get("fmla", ""))
                    if m:
                        adj[gd.get("name")] = int(m.group(1))
                geom = ("prst", pg.get("prst"), adj)
            elif cg is not None:
                geom = ("cust", cg, None)
        sh["geom"] = geom[1] if geom and geom[0] == "prst" else ("custGeom" if geom else None)
        sh["fill"] = None
        if sppr is not None:
            f = sppr.find(A + "solidFill/" + A + "srgbClr")
            if f is not None:
                sh["fill"] = "#" + f.get("val").upper()
            if sppr.find(A + "noFill") is not None:
                sh["fill"] = "none"
        sh["line"] = _ln_props(sppr, style_el)
        # path
        sh["paths"] = []
        if geom and geom[0] == "prst":
            pp = _preset_path(geom[1], xf["w"], xf["h"], geom[2])
            if pp:
                sh["paths"] = [([full(p) for p in pp], False)]
        elif geom and geom[0] == "cust":
            for pts_, closed in _cust_paths(geom[1], xf["w"], xf["h"]):
                sh["paths"].append(([full(p) for p in pts_], closed))
        sh["open_line"] = bool(sh["paths"]) and (
            tag == "cxnSp" or (geom and geom[0] == "prst" and geom[1] == "line")
            or (geom and geom[0] == "cust" and sh["fill"] in (None, "none")
                and not any(c for _, c in sh["paths"])))
        # connections
        cx = el.find(P + "nvCxnSpPr/" + P + "cNvCxnSpPr")
        sh["st"] = sh["end"] = None
        if cx is not None:
            st, en = cx.find(A + "stCxn"), cx.find(A + "endCxn")
            sh["st"] = (st.get("id"), st.get("idx")) if st is not None else None
            sh["end"] = (en.get("id"), en.get("idx")) if en is not None else None
        sh["runs"] = _text(el)
        if tag == "pic":
            blip = el.find(".//" + A + "blip")
            rid = blip.get(R + "embed") if blip is not None else None
            target = rels.get(rid)
            sh["media"] = target
            sh["px"] = None
            if target and target in z.namelist():
                blob = z.read(target)
                sh["px"] = imgsize.size_bytes(blob)
                sh["media_bytes"] = len(blob)
                sh["media_ext"] = target.rsplit(".", 1)[-1].lower()
            crop = el.find(".//" + A + "srcRect")
            sh["crop"] = (tuple(int(crop.get(k, "0") or 0) / 100000.0 for k in "ltrb")
                          if crop is not None else (0, 0, 0, 0))
        s.shapes.append(sh)


def lines_with_heads(slide):
    """Open line shapes that draw at least one arrowhead."""
    return [sh for sh in slide.shapes
            if sh.get("open_line") and (sh["line"]["head"] or sh["line"]["tail"])
            and not sh["line"]["none"]]


def text_of(sh):
    return "".join(r["text"] for r in sh.get("runs", []))
