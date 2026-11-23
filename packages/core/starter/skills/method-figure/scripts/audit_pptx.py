#!/usr/bin/env python3
"""Audit a PPTX as it will be printed -- after a person has edited it.

    python3 audit_pptx.py fig.pptx [--width 496.8] [--spec spec.yaml | --ref fig.svg]
                          [--fix OUT.pptx] [--legend-in-images] [--json] [--strict]

``validate.py`` audits the spec.  But once a figure has been handed over as a
PPTX and moved around in WPS or PowerPoint, the spec no longer describes what
ships: arrows have been re-dragged, subscripts lost in a copy-paste, legend
entries orphaned, type resized.  This reads the PPTX itself (any PPTX -- this
engine's, FigGenie's, or one drawn by hand) and checks:

  short-final-segment   a bent arrow whose last run is too short for WPS to aim
                        the head (it draws it at 45 degrees)
  unglued-arrow         an arrow touching a module without being glued to it,
                        so moving the module leaves the arrow behind
  text-too-small        any run under 6.0 pt at the final printed width
  lost-subscript        a sub/superscript that the spec (or the source SVG) has
                        and the PPTX no longer does; without a reference, runs
                        such as "z1:4" that look like a flattened subscript
  legend-orphan         a legend entry whose line style or swatch the figure
                        no longer uses -- neither on a shape nor, for a class
                        legend, inside an embedded render
  image-low-ppi         an embedded picture under 300 ppi (warning under 600)
  script-too-low        a subscript shifted so far that LibreOffice prints it
                        detached from its letter
  raster-effect         shadow / glow / soft edge: LibreOffice rasterises these
                        at 96-350 ppi on export -- including a shadow the
                        shapes inherit from the theme
  no-background         no slide background -- Keynote renders it black

``--fix OUT.pptx`` writes a copy with the two mechanical repairs made --
theme effects switched off, detached subscripts raised -- and audits that.

Once a PPTX has been edited by hand it is the only source of the figure.
Export the paper PDF from it (export_pptx.py) and never regenerate it from
the spec, which would overwrite the edits.

Exit code 1 when an error is reported (or a warning, with --strict).
"""

import argparse
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cvprfig import imgcolor, ooxml, pptx, style, text as T       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_SUB_DROP = 0.35      # x the subscript's own printed size, as in audit_pdf.py


class Report(object):
    def __init__(self):
        self.rows = []

    def add(self, level, code, msg, where=None):
        self.rows.append({"level": level, "code": code, "message": msg, "where": where})

    def counts(self):
        c = {"error": 0, "warning": 0, "note": 0}
        for r in self.rows:
            c[r["level"]] += 1
        return c


def _label(sh):
    t = ooxml.text_of(sh).replace("\n", " ").strip()
    return "%s%s" % (sh["name"] or sh["tag"], (" %r" % t[:30]) if t else "")


def _seglen(p, q):
    return ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5


def _heads(sh):
    """Head length (pt) per application for this line's arrowhead(s)."""
    ln = sh["line"]
    out = {}
    for end in ("head", "tail"):
        h = ln[end]
        if not h:
            continue
        preset = (h[1] if h[1] in ("sm", "med", "lg") else "med",
                  h[2] if h[2] in ("sm", "med", "lg") else "med")
        out[end] = {app: style.head_size(ln["w"], preset, app)[0]
                    for app in ("office", "wps", "keynote")}
    return out


# ------------------------------------------------------------------ checks
def check_arrows(slide, scale, rep):
    boxes = [sh for sh in slide.shapes if not sh.get("open_line")
             and sh["box"][2] > 3 and sh["box"][3] > 3]
    by_id = {sh["id"]: sh for sh in slide.shapes}
    unglued, freeform = [], []
    for sh in ooxml.lines_with_heads(slide):
        pts = sh["paths"][0][0] if sh["paths"] else []
        if len(pts) < 2:
            continue
        heads = _heads(sh)
        for end, hs in heads.items():
            if len(pts) < 3:
                continue          # a straight arrow cannot be mis-aimed
            seg = _seglen(pts[-2], pts[-1]) if end == "tail" else _seglen(pts[1], pts[0])
            if seg < 2.0 * hs["wps"]:
                rep.add("error", "short-final-segment",
                        "%s: the run after the last bend is %.1f pt, under two arrowheads "
                        "(%.1f pt in WPS); WPS draws this head at 45 degrees"
                        % (_label(sh), seg, 2 * hs["wps"]), sh["name"])
            elif seg < 2.0 * hs["office"]:
                rep.add("warning", "short-final-segment",
                        "%s: the run after the last bend is %.1f pt; PowerPoint draws this "
                        "head %.1f pt long and may tilt it" % (_label(sh), seg, hs["office"]),
                        sh["name"])
        # glue
        for which, p, cxn in (("start", pts[0], sh["st"]), ("end", pts[-1], sh["end"])):
            if cxn:
                if cxn[0] not in by_id:
                    rep.add("warning", "glue-broken",
                            "%s is glued at its %s to shape id %s, which no longer exists"
                            % (_label(sh), which, cxn[0]), sh["name"])
                continue
            touch = [b for b in boxes if _on_edge(p, b["box"])]
            if touch:
                unglued.append("%s (%s, at %s)" % (_label(sh), which, _label(touch[0])))
        if sh["tag"] != "cxnSp":
            freeform.append(_label(sh))
    if unglued:
        rep.add("warning", "unglued-arrow",
                "%d arrow end(s) touch a module without being glued to it, so moving the "
                "module leaves them behind: %s" % (len(unglued), "; ".join(unglued[:6])))
    if freeform:
        rep.add("note", "freeform-arrow",
                "%d arrow(s) are freeform shapes, not connectors; they cannot follow a moved "
                "module (%s)" % (len(freeform), "; ".join(freeform[:5])))


def _on_edge(p, box, tol=1.2):
    x, y, w, h = box
    inside_x = x - tol <= p[0] <= x + w + tol
    inside_y = y - tol <= p[1] <= y + h + tol
    if not (inside_x and inside_y):
        return False
    return (min(abs(p[0] - x), abs(p[0] - x - w)) <= tol or
            min(abs(p[1] - y), abs(p[1] - y - h)) <= tol)


def check_text(slide, scale, rep):
    sizes = []
    small = []
    fonts = {}
    for sh in slide.shapes:
        for r in sh.get("runs", []):
            if r.get("br") or not r["text"].strip():
                continue
            sz = r["size"] or 18.0          # PowerPoint's default
            eff = sz * scale * (0.67 if r["baseline"] else 1.0)
            sizes.append(eff)
            if r["font"]:
                fonts[r["font"]] = fonts.get(r["font"], 0) + len(r["text"])
            lim_e = style.MIN_RENDERED_PT * (0.67 if r["baseline"] else 1.0)
            lim_w = style.WARN_RENDERED_PT * (0.67 if r["baseline"] else 1.0)
            if eff < lim_e:
                small.append(("error", eff, sh, r))
            elif eff < lim_w:
                small.append(("warning", eff, sh, r))
    # one finding per shape, at its smallest base-line run
    per = {}
    for level, eff, sh, r in small:
        k = id(sh)
        if r["baseline"]:
            eff = eff / 0.67           # report in base-line terms
        cur = per.get(k)
        if cur is None or eff < cur[1]:
            per[k] = (level if cur is None or level == "error" else cur[0], eff, sh)
        elif level == "error":
            per[k] = ("error", cur[1], sh)
    errs = sorted((v for v in per.values() if v[0] == "error"), key=lambda v: v[1])
    warns = sorted((v for v in per.values() if v[0] != "error"), key=lambda v: v[1])
    for level, eff, sh in errs:
        rep.add("error", "text-too-small", "%s prints at %.1f pt (floor %.1f pt)"
                % (_label(sh), eff, style.MIN_RENDERED_PT), sh["name"])
    if warns:
        rep.add("warning", "text-small",
                "%d text shape(s) print between %.1f and %.1f pt (warning below %.1f pt): %s"
                % (len(warns), warns[0][1], warns[-1][1], style.WARN_RENDERED_PT,
                   "; ".join(_label(sh) for _, _, sh in warns[:8])
                   + (" ..." if len(warns) > 8 else "")))
    latin = [f for f in fonts if not re.search(r"Symbol|Math|Wingding|Dings", f, re.I)]
    if len(latin) > 2:
        rep.add("warning", "mixed-fonts",
                "%d text fonts in one figure: %s" % (len(latin), ", ".join(sorted(latin))))
    if sizes:
        sizes.sort()
        rep.add("note", "type-summary",
                "printed type: min %.1f, median %.1f, max %.1f pt over %d runs; fonts %s"
                % (sizes[0], sizes[len(sizes) // 2], sizes[-1], len(sizes),
                   ", ".join("%s" % f for f in sorted(fonts, key=fonts.get, reverse=True)[:3])))


# A flattened subscript: one letter (accent allowed) followed by digits,
# an index range, or a primed index -- z1:4, O5:10, d1, x_t written as xt is
# undetectable and needs --spec or --ref.
FLAT = re.compile(r"(?<![A-Za-z0-9])([A-Za-zÀ-ɏḀ-ỿ][̀-ͯ]?)"
                  r"(\d+(?::\d+)?)(?![A-Za-z0-9])")


def _expected_scripts(ref):
    """(base letter, script text, sign) for every sub/superscript the source has."""
    out = []
    if ref.lower().endswith(".svg"):
        import xml.etree.ElementTree as ET
        tree = ET.parse(ref)
        for el in tree.iter():
            tag = el.tag.split("}")[-1]
            if tag != "tspan":
                continue
            bs = el.get("baseline-shift") or ""
            style_ = el.get("style") or ""
            m = re.search(r"baseline-shift:\s*([^;]+)", style_)
            bs = bs or (m.group(1) if m else "")
            if not bs:
                continue
            sign = 1 if (bs.startswith("super") or (bs[:1].isdigit() and not bs.startswith("-"))) else -1
            if (el.text or "").strip():
                out.append(("", el.text.strip(), sign))
        return out
    from cvprfig import load_path, Figure
    spec = load_path(ref)
    fig = Figure(spec, base=os.path.dirname(os.path.abspath(ref)))
    from validate import text_elements
    for _, txt, _, _ in text_elements(fig):
        runs = T.parse_runs(txt)
        for k, (chars, b, i, sc) in enumerate(runs):
            if sc:
                base = runs[k - 1][0][-1:] if k else ""
                out.append((base, chars.strip(), 1 if sc > 0 else -1))
    return out


def check_scripts(slide, ref, rep):
    have = {}
    flat = []
    for sh in slide.shapes:
        # Adjacent runs on the base line print as one word, so "z" + "1:4"
        # that lost its baseline reads as "z1:4" just like a merged run.
        plain_text, chunks = [], []
        for r in sh.get("runs", []):
            if r.get("br") or r["baseline"]:
                if plain_text:
                    chunks.append("".join(plain_text))
                    plain_text = []
                if r["baseline"] and not r.get("br"):
                    key = (r["text"].strip(), 1 if r["baseline"] > 0 else -1)
                    have[key] = have.get(key, 0) + 1
                continue
            plain_text.append(r["text"])
        if plain_text:
            chunks.append("".join(plain_text))
        for chunk in chunks:
            for m in FLAT.finditer(chunk):
                flat.append((m.group(0), sh))
    if ref:
        want = _expected_scripts(ref)
        need = {}
        for base, txt, sign in want:
            need[(txt, sign)] = need.get((txt, sign), 0) + 1
        missing = []
        for key, n in need.items():
            got = have.get(key, 0)
            if got < n:
                missing.extend([key] * (n - got))
        if missing:
            rep.add("error", "lost-subscript",
                    "%d sub/superscript(s) in %s are plain text in the PPTX: %s"
                    % (len(missing), os.path.basename(ref),
                       ", ".join(("_" if s < 0 else "^") + t for t, s in missing[:12])))
    if flat:
        rep.add("error" if not ref else "warning", "lost-subscript",
                "%d run(s) look like flattened subscripts (%s); set them as subscripts "
                "(baseline about -17000) or write them as $z_{1:4}$ in the spec"
                % (len(flat), ", ".join(sorted(set(t for t, _ in flat))[:12])))
    check_script_drop(slide, rep)


def _script_runs(sh):
    """``(run, body size, name)`` for every subscript run of a shape.  The
    body size is that of the nearest base-line run in the same paragraph;
    the name is the subscript with its base letter, e.g. ``d_1``."""
    out, para = [], []
    for r in sh.get("runs", []) + [{"br": True}]:
        if not r.get("br"):
            para.append(r)
            continue
        base = [(k, q["size"] or 18.0) for k, q in enumerate(para) if not q["baseline"]]
        for k, q in enumerate(para):
            if q["baseline"] < 0 and q["text"].strip():
                near = min(base, key=lambda b: (abs(b[0] - k), b[0] > k)) if base else None
                body = near[1] if near else (q["size"] or 18.0) / pptx.SCRIPT_COMPENSATION
                letter = para[k - 1]["text"].rstrip()[-1:] if k else ""
                out.append((q, body, "%s_%s" % (letter, q["text"].strip())))
        para = []
    return out


def check_script_drop(slide, rep):
    """LibreOffice prints a subscript at 58.5 % of its run size and moves it
    down by baseline x the run's own size, so the drop measured against the
    printed glyph is |baseline| / 0.585 whatever the sizes.  Past
    MAX_SUB_DROP the subscript reads as detached -- a lone "d_1" looks like "d" and "1"."""
    low = []
    for sh in slide.shapes:
        for r, body, name in _script_runs(sh):
            own = abs(r["baseline"]) / 100000.0 / pptx.LO_SCRIPT_SIZE
            if own > MAX_SUB_DROP + 1e-6:
                sz = r["size"] or 18.0
                low.append((own, abs(r["baseline"]) / 100000.0 * sz / body, r, sz, body, name))
    if not low:
        return
    low.sort(key=lambda v: -v[0])
    own, rel, r, sz, body, _ = low[0]
    rep.add("warning", "script-too-low",
            "%d subscript(s) are shifted down %s%d %%; LibreOffice prints them %.0f %% of their own "
            "height (%.2f x body) below the line, where they read as detached (%s). For a %.1f pt "
            "script on %.1f pt text use baseline=\"%d\" -- audit_pptx.py --fix OUT.pptx rewrites them"
            % (len(low), "up to " if len(set(v[2]["baseline"] for v in low)) > 1 else "",
               round(abs(r["baseline"]) / 1000.0), own * 100, rel,
               ", ".join(sorted(set(v[5] for v in low))[:8]), sz, body,
               pptx.script_baseline(-1, sz / body)))


def check_legend(slide, rep, pics=None, assume_in_images=False):
    """Find legend swatches -- a short horizontal line or a small filled box
    with a text label immediately to its right -- and check that the figure
    still uses each swatch's style somewhere else.  A colour no shape uses
    may still be explained by the pictures -- a class legend for occupancy or
    segmentation renders -- so those are looked into before an entry is
    called orphaned."""
    texts = [sh for sh in slide.shapes if ooxml.text_of(sh).strip()]
    lines = [sh for sh in slide.shapes if sh.get("open_line") and sh["paths"]
             and not sh["line"]["none"]]
    swatches = []
    for sh in lines + [s for s in slide.shapes if not s.get("open_line")]:
        x, y, w, h = sh["box"]
        is_line = sh.get("open_line")
        if is_line:
            pts = sh["paths"][0][0]
            if len(pts) != 2 or abs(pts[0][1] - pts[1][1]) > 0.3 or not (5 <= w <= 30):
                continue
        else:
            if not (4 <= w <= 18 and 2 <= h <= 10) or sh["fill"] in (None, "none"):
                continue
            if ooxml.text_of(sh).strip():
                continue
        ymid = y + h / 2.0
        for t in texts:
            tx, ty, tw, th = t["box"]
            if 0 <= tx - (x + w) <= 9 and abs(ty + th / 2.0 - ymid) <= 3.5 and tw < 200:
                swatches.append((sh, t))
                break
    in_group = [sw for sw in swatches if any("legend" in g.lower() for g in sw[0]["groups"])]
    if in_group:
        swatches = in_group           # the engine's (or a careful author's) legend group
    else:
        # A swatch floats: a line touching a module is an arrow, not a key.
        boxes = [b for b in slide.shapes if not b.get("open_line") and b["box"][2] > 3
                 and b["box"][3] > 3 and not ooxml.text_of(b).strip()]
        free = []
        for sh, t in swatches:
            if sh.get("open_line"):
                pts = sh["paths"][0][0]
                if any(_on_edge(p, b["box"], 0.8) for p in (pts[0], pts[-1]) for b in boxes
                       if b is not sh):
                    continue
            free.append((sh, t))
        # ... and a legend has entries stacked in a column or set in a row
        swatches = [(sh, t) for sh, t in free
                    if any(o is not sh and (abs(o["box"][0] - sh["box"][0]) < 1.0 or
                                            abs(o["box"][1] + o["box"][3] / 2.0
                                                - sh["box"][1] - sh["box"][3] / 2.0) < 1.0)
                           for o, _ in free)]
    if not swatches:
        return
    sw_ids = set(id(s) for s, _ in swatches)
    in_pics, unread, absent = [], [], []

    def orphan(label, col, what, sh):
        seen = imgcolor.lookup(col, pics, assume_in_images)
        if seen is True or (seen and seen[0]):
            in_pics.append((label, None if seen is True else seen[1]))
        elif seen is None:
            unread.append(label)
        elif seen:
            absent.append((label, col, what, sh, seen[1]))
        else:
            rep.add("error", "legend-orphan", what, sh["name"])

    for sh, t in swatches:
        label = ooxml.text_of(t).strip()
        if sh.get("open_line"):
            key = (sh["line"]["color"], bool(sh["line"]["dash"]))
            used = [o for o in lines if id(o) not in sw_ids and
                    (o["line"]["color"], bool(o["line"]["dash"])) == key]
            if not used:
                orphan(label, key[0], "legend entry %r shows a %s %s line that no other line in "
                       "the figure uses -- the element it explained was deleted"
                       % (label, "dashed" if key[1] else "solid", key[0]), sh)
        else:
            used = [o for o in slide.shapes if id(o) not in sw_ids and o.get("fill") == sh["fill"]
                    and o is not sh and o["box"][2] * o["box"][3] > sh["box"][2] * sh["box"][3]]
            if not used:
                orphan(label, sh["fill"], "legend entry %r shows fill %s, which no module uses "
                       "any more" % (label, sh["fill"]), sh)
    if in_pics:
        rep.add("note", "legend-in-images",
                "%d legend entr%s drawn only inside pictures (%s): %s"
                % (len(in_pics), "y is" if len(in_pics) == 1 else "ies are",
                   "--legend-in-images" if assume_in_images else
                   "largest share of one picture, counting shaded tones",
                   ", ".join(lb if v is None else "%s %.2f %%" % (lb, 100 * v)
                             for lb, v in in_pics)))
    if absent and len(in_pics) >= max(2, len(absent)):
        # A class legend: most entries are in the renders, these classes just
        # do not occur in the samples shown.  A full palette kept on purpose
        # for consistency across figures is a choice, not a deleted element.
        rep.add("warning", "legend-orphan",
                "%d class(es) of a picture legend occur in no picture: %s. If these samples "
                "simply do not contain them, drop them from this figure's legend, or keep the "
                "full palette on purpose to match the other figures"
                % (len(absent), ", ".join("%s %s (%.3f %% of a picture at most)" % (lb, col, 100 * v)
                                          for lb, col, _, _, v in absent)))
    else:
        for lb, col, what, sh, v in absent:
            rep.add("error", "legend-orphan", what + " (it covers %.3f %% of the picture where "
                    "it is most common)" % (100 * v), sh["name"])
    if unread:
        rep.add("warning", "legend-orphan",
                "%d legend entr%s (%s) match no shape; the slide has pictures, but neither Pillow "
                "nor PyMuPDF is installed to look inside them. Install one, or pass "
                "--legend-in-images if these colours are drawn in the pictures"
                % (len(unread), "y" if len(unread) == 1 else "ies", ", ".join(unread[:8])))


class Pictures(imgcolor.Pictures):
    """The pictures on a slide, read from the PPTX package."""

    def __init__(self, path, slide):
        seen = []
        for sh in slide.shapes:
            if sh["tag"] == "pic" and sh.get("media") and sh["media"] not in seen:
                seen.append(sh["media"])
        imgcolor.Pictures.__init__(self, seen)
        self.path = path

    def read(self, name):
        with zipfile.ZipFile(self.path) as z:
            return z.read(name) if name in z.namelist() else b""


def check_images(slide, scale, rep):
    for sh in slide.shapes:
        if sh["tag"] != "pic" or not sh.get("px"):
            continue
        l, t_, r, b = sh["crop"]
        px_w = sh["px"][0] * max(0.01, 1 - l - r)
        inches = sh["box"][2] * scale / 72.0
        if inches <= 0:
            continue
        ppi = px_w / inches
        if ppi < 300:
            rep.add("error", "image-low-ppi",
                    "%s (%s) is %.0f px across %.2f in -> %.0f ppi; it will print blurred"
                    % (sh["name"], os.path.basename(sh["media"]), px_w, inches, ppi), sh["name"])
        elif ppi < 600:
            rep.add("warning", "image-soft",
                    "%s (%s) prints at %.0f ppi (< 600)"
                    % (sh["name"], os.path.basename(sh["media"]), ppi), sh["name"])
        if sh.get("media_ext") in ("jpg", "jpeg"):
            rep.add("note", "jpeg-image",
                    "%s is a JPEG; occupancy renders and masks keep sharper edges as PNG"
                    % sh["name"], sh["name"])


def check_effects(slide, rep):
    eff = [sh for sh in slide.shapes if sh.get("effects")]
    if eff:
        kinds = sorted(set(k for sh in eff for k in sh["effects"]))
        rep.add("warning", "raster-effect",
                "%d shape(s) carry effects (%s); LibreOffice rasterises these at 96-350 ppi "
                "when exporting PDF, and they read as slide decoration: %s"
                % (len(eff), ", ".join(kinds), ", ".join(sh["name"] for sh in eff[:5])))
    th = [sh for sh in slide.shapes if sh.get("theme_effect")]
    if th:
        refs = sorted(set(sh["theme_effect"] for sh in th))
        rep.add("error", "raster-effect",
                "(theme) %d shape(s) inherit an effect from %s through <p:style><a:effectRef "
                "idx=\"%s\"> (%s). LibreOffice draws it even when the shape writes an empty "
                "<a:effectLst/>, and exports each one as a blurred 96-300 ppi grey picture; "
                "PowerPoint and Keynote do not show it, so it is invisible while editing. Set "
                "effectRef idx to 0 or empty the theme's effectStyleLst -- audit_pptx.py --fix "
                "OUT.pptx does both: %s"
                % (len(th), os.path.basename(slide.theme_part or "the theme"),
                   "|".join(str(k) for k, _ in refs), "; ".join("%d: %s" % r for r in refs),
                   ", ".join(_label(sh) for sh in th[:4]) + (" ..." if len(th) > 4 else "")))


def check_canvas(slide, rep):
    W, H = slide.size
    if not slide.has_bg:
        rep.add("warning", "no-background",
                "the slide has no background fill; Keynote draws such slides on black")
    out = [sh for sh in slide.shapes if sh["box"][0] < -1 or sh["box"][1] < -1
           or sh["box"][0] + sh["box"][2] > W + 1 or sh["box"][1] + sh["box"][3] > H + 1]
    if out:
        rep.add("warning", "off-slide",
                "%d shape(s) extend past the slide and will be cropped on export: %s"
                % (len(out), ", ".join(_label(sh) for sh in out[:4])))


def check_head_sizes(slide, rep):
    seen = {}
    for sh in ooxml.lines_with_heads(slide):
        for end, hs in _heads(sh).items():
            h = sh["line"][end]
            key = (round(sh["line"]["w"], 2), h[1], h[2])
            seen[key] = hs
    if seen:
        parts = ["%.2f pt %s/%s: LibreOffice/PowerPoint %.1f, WPS %.1f, Keynote %.1f pt"
                 % (k[0], k[1], k[2], v["office"], v["wps"], v["keynote"])
                 for k, v in sorted(seen.items())[:4]]
        rep.add("note", "arrowhead-size",
                "arrowhead length per application -- " + "; ".join(parts))


# --------------------------------------------------------------------- --fix
_PARA = re.compile(r"<a:p(?:\s[^>]*)?>.*?</a:p>", re.S)
_RPR = re.compile(r"<a:rPr\b[^>]*>")


def _attr(tag, name):
    m = re.search(r'\s%s="([^"]*)"' % name, tag)
    return m.group(1) if m else None


def _raise_subscripts(para, done):
    """Re-set every subscript in one paragraph that LibreOffice would print
    detached (see check_script_drop) to pptx.script_baseline."""
    runs = [(m, float(_attr(m.group(0), "sz") or 1800) / 100.0,
             int(_attr(m.group(0), "baseline") or 0)) for m in _RPR.finditer(para)]
    base = [(k, sz) for k, (_, sz, b) in enumerate(runs) if not b]
    out, last = [], 0
    for k, (m, sz, b) in enumerate(runs):
        if b >= 0 or abs(b) / 100000.0 / pptx.LO_SCRIPT_SIZE <= MAX_SUB_DROP + 1e-6:
            continue
        near = min(base, key=lambda q: (abs(q[0] - k), q[0] > k)) if base else None
        body = near[1] if near else sz / pptx.SCRIPT_COMPENSATION
        new = pptx.script_baseline(-1, sz / body)
        out += [para[last:m.start()],
                re.sub(r'(\sbaseline=")-?\d+"', r'\g<1>%d"' % new, m.group(0))]
        last = m.end()
        done["subscripts"].append(new)
    return "".join(out) + para[last:]


def fix(path, out, slide_no=1):
    """Copy the PPTX to ``out`` with the two mechanical repairs made and
    return what changed.  Theme effects: every <a:effectRef idx> that points
    at a non-empty theme effect style is set to 0, and the theme's effect
    styles are emptied so shapes drawn later do not pick them up either.
    Subscripts that LibreOffice would print detached are raised.  Every
    other byte is copied unchanged."""
    slide = ooxml.read(path, slide_no)
    bad = set(k + 1 for k, d in enumerate(slide.theme_effects) if d)
    done = {"effect_refs": 0, "theme_styles": len(bad), "subscripts": []}

    def ref0(m):
        if int(m.group(1)) not in bad:
            return m.group(0)
        done["effect_refs"] += 1
        return '<a:effectRef idx="0"'

    tmp = out + ".tmp"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for it in zin.infolist():
            data = zin.read(it.filename)
            if it.filename == slide.part:
                x = re.sub(r'<a:effectRef idx="(\d+)"', ref0, data.decode("utf-8"))
                x = _PARA.sub(lambda m: _raise_subscripts(m.group(0), done), x)
                data = x.encode("utf-8")
            elif it.filename == slide.theme_part and bad:
                data = re.sub(r"<a:effectStyle>.*?</a:effectStyle>",
                              "<a:effectStyle><a:effectLst/></a:effectStyle>",
                              data.decode("utf-8"), flags=re.S).encode("utf-8")
            zout.writestr(it, data)
    os.replace(tmp, out)
    return done


def audit(path, width=None, ref=None, legend_in_images=False):
    slide = ooxml.read(path)
    W = slide.size[0]
    rep = Report()
    if width is None and ref and not ref.lower().endswith(".svg"):
        from cvprfig import load_path, Figure
        fig = Figure(load_path(ref), base=os.path.dirname(os.path.abspath(ref)))
        width = fig.target
    if width is None:
        width = W
        rep.add("note", "width-assumed",
                "no --width given; assuming the slide prints at its own width (%.1f pt)" % W)
    scale = width / W if W else 1.0
    check_arrows(slide, scale, rep)
    check_text(slide, scale, rep)
    check_scripts(slide, ref, rep)
    check_legend(slide, rep, Pictures(path, slide), legend_in_images)
    check_images(slide, scale, rep)
    check_effects(slide, rep)
    check_canvas(slide, rep)
    check_head_sizes(slide, rep)
    meta = {"slide_pt": [round(slide.size[0], 1), round(slide.size[1], 1)],
            "print_width_pt": round(width, 1), "scale": round(scale, 4),
            "shapes": len(slide.shapes)}
    return rep, meta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pptx")
    ap.add_argument("--width", type=float,
                    help="printed width in pt (CVPR double column 496.8, single 237.1)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--spec", help="the method-figure spec the PPTX came from")
    g.add_argument("--ref", help="a source SVG (e.g. FigGenie's) to compare subscripts with")
    ap.add_argument("--fix", metavar="OUT.pptx",
                    help="write a copy with theme effects switched off and detached "
                         "subscripts raised, then audit the copy")
    ap.add_argument("--legend-in-images", action="store_true",
                    help="take legend colours that no shape uses as drawn inside the pictures")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="fail on warnings too")
    args = ap.parse_args(argv)
    target, fixed = args.pptx, None
    if args.fix:
        fixed = fix(args.pptx, args.fix)
        target = args.fix
    rep, meta = audit(target, args.width, args.spec or args.ref, args.legend_in_images)
    if fixed is not None:
        meta["fixed"] = dict(fixed, subscripts=len(fixed["subscripts"]), out=target)
    c = rep.counts()
    if args.json:
        print(json.dumps({"counts": c, "findings": rep.rows, "meta": meta}, indent=2))
    else:
        icon = {"error": "FAIL", "warning": "WARN", "note": "note"}
        order = {"error": 0, "warning": 1, "note": 2}
        if fixed is not None:
            sb = sorted(set(fixed["subscripts"]))
            print("fixed -> %s: %d theme effect reference(s) set to 0, %d theme effect style(s) "
                  "emptied, %d subscript(s) raised%s\n"
                  % (target, fixed["effect_refs"], fixed["theme_styles"],
                     len(fixed["subscripts"]),
                     " (baseline %s)" % "/".join(str(b) for b in sb) if sb else ""))
        for r in sorted(rep.rows, key=lambda r: order[r["level"]]):
            print("%-4s %-20s %s" % (icon[r["level"]], r["code"], r["message"]))
        print("\n%d error(s), %d warning(s), %d note(s) | slide %.0f x %.0f pt printed at "
              "%.1f pt (x%.3f)" % (c["error"], c["warning"], c["note"], meta["slide_pt"][0],
                                   meta["slide_pt"][1], meta["print_width_pt"], meta["scale"]))
    if c["error"] or (args.strict and c["warning"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
