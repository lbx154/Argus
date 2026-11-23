#!/usr/bin/env python3
"""Audit the figure PDF that goes into the paper.

    python3 audit_pdf.py fig.pdf [--width 496.8] [--page 1] [--source fig.pptx]
                         [--json] [--strict]

The PDF is the only file a reviewer ever sees, and the export step is where a
figure quietly loses quality: LibreOffice's default PDF filter downsamples
embedded pictures to 300 dpi and re-encodes them as JPEG; WPS keeps the
resolution but still uses JPEG; a font may not be embedded.  This reads the
PDF with PyMuPDF and reports, at the printed width:

  text-too-small   any glyph run under 6.0 pt (warning under 6.5 pt);
                   sub/superscripts are judged against 4.5 pt
  script-too-low   a subscript whose baseline is more than 35 % of its own
                   size below its letter's -- it reads as detached
  image-low-ppi    an image under 300 ppi (warning under 600 ppi)
  raster-shadow    images that are one flat colour under a soft mask: a
                   shadow or transparency the exporter rasterised, not a photo
  image-jpeg       an image stored with DCT (JPEG) or JPX compression; a
                   camera frame at its native size, or a picture that was
                   already JPEG in --source, is only a note
  font-not-embedded
  type3-font       bitmap Type 3 fonts, which some exporters substitute
  page-is-raster   the whole page is one picture, so nothing is vector

``--width`` is the printed width in points (CVPR: 496.8 double, 237.1
single).  Without it the page is assumed to print at its own size.

Requires PyMuPDF (``pip install pymupdf``).
"""

import argparse
import json
import os
import sys

try:
    import fitz
except ImportError:                                            # pragma: no cover
    fitz = None

MIN_PT, WARN_PT, MIN_SCRIPT_PT = 6.0, 6.5, 4.5
MIN_PPI, WARN_PPI = 300, 600
MAX_SUB_DROP = 0.35          # x the subscript's own printed size

# Native frame sizes of the driving and street-scene cameras whose photos go
# into figures.  They are JPEG at the source; keeping them JPEG loses nothing.
CAMERAS = {
    (1600, 900): "nuScenes", (1920, 1280): "Waymo front", (1920, 886): "Waymo side",
    (1242, 375): "KITTI", (1241, 376): "KITTI", (1224, 370): "KITTI", (1238, 374): "KITTI",
    (1226, 370): "KITTI", (1408, 376): "KITTI-360", (2048, 1550): "Argoverse 2",
    (1550, 2048): "Argoverse 2", (2048, 1024): "Cityscapes", (1936, 1216): "DDAD",
    (1920, 1020): "ONCE", (1920, 1080): "a 1080p camera", (1280, 720): "a 720p camera",
    (1224, 1024): "Lyft",
}


def source_jpegs(pptx):
    """Pixel sizes of the pictures a PPTX already stores as JPEG."""
    import zipfile
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cvprfig import imgsize
    out = set()
    with zipfile.ZipFile(pptx) as z:
        for n in z.namelist():
            if n.startswith("ppt/media/") and n.lower().endswith((".jpg", ".jpeg")):
                wh = imgsize.size_bytes(z.read(n))
                if wh:
                    out.add(tuple(wh))
    return out


def _flat_masked(doc, xref):
    """True for an image that is one flat colour under a soft mask -- what a
    rasterised shadow or transparency looks like."""
    try:
        if doc.xref_get_key(xref, "SMask")[0] == "null":
            return False
        pix = fitz.Pixmap(doc, xref)
        while max(pix.width, pix.height) > 256:
            pix.shrink(1)
        return pix.color_count() <= 2
    except Exception:
        return False


def _script_drops(spans):
    """``(drop / own size, base, script)`` for every subscript span: one at
    least 20 % smaller than the span just before it on the same line, sitting
    below it."""
    out = []
    for s in spans:
        x0 = s["bbox"][0]
        prev = [b for b in spans if b is not s and b["size"] >= 1.2 * s["size"]
                and -0.6 * s["size"] <= x0 - b["bbox"][2] <= 1.5
                and abs(b["origin"][1] - s["origin"][1]) < 0.8 * b["size"]]
        if not prev:
            continue
        b = min(prev, key=lambda b: abs(x0 - b["bbox"][2]))
        drop = s["origin"][1] - b["origin"][1]
        if drop > 0.05 * b["size"]:
            out.append((drop / s["size"], b, s))
    return out


def audit(path, width=None, page_no=1, source=None):
    rows = []

    def add(level, code, msg):
        rows.append({"level": level, "code": code, "message": msg})

    doc = fitz.open(path)
    page = doc[page_no - 1]
    W, H = page.rect.width, page.rect.height
    if width is None:
        width = W
        add("note", "width-assumed",
            "no --width given; assuming the page prints at its own width (%.1f pt)" % W)
    k = width / W if W else 1.0

    # ---------------------------------------------------------- text
    spans, raw = [], []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            line = [s for s in ln["spans"] if s["text"].strip()]
            if not line:
                continue
            raw.extend(line)
            big = max(s["size"] for s in line)
            for s in line:
                script = bool(s["flags"] & 1) or s["size"] < 0.8 * big
                spans.append((s["size"] * k, script, s["text"].strip(), s["font"]))
    small, warn = [], []
    for size, script, txt, font in spans:
        lim = MIN_SCRIPT_PT if script else MIN_PT
        if size < lim - 0.05:
            small.append((size, txt, script))
        elif not script and size < WARN_PT - 0.05:
            warn.append((size, txt))
    for size, txt, script in sorted(small)[:12]:
        add("error", "text-too-small", "%r prints at %.1f pt%s"
            % (txt[:30], size, " (script, floor %.1f)" % MIN_SCRIPT_PT if script else ""))
    if len(small) > 12:
        add("error", "text-too-small", "... and %d more runs" % (len(small) - 12))
    if warn:
        warn.sort()
        add("warning", "text-small",
            "%d run(s) print between %.1f and %.1f pt (warning below %.1f): %s"
            % (len(warn), warn[0][0], warn[-1][0], WARN_PT,
               ", ".join(repr(t[:16]) for _, t in warn[:6])))
    low = sorted((v for v in _script_drops(raw) if v[0] > MAX_SUB_DROP), key=lambda v: -v[0])
    if low:
        add("warning", "script-too-low",
            "%d subscript(s) sit %s %% of their own size below their letter's baseline "
            "(over %.0f %% reads as detached): %s. From a PPTX exported by LibreOffice the "
            "cause is a shift beyond about -20 %%, which LibreOffice applies to the run's own "
            "size -- audit_pptx.py --fix"
            % (len(low), "%.0f" % (100 * low[0][0]) if round(100 * low[-1][0]) == round(
                100 * low[0][0]) else "%.0f-%.0f" % (100 * low[-1][0], 100 * low[0][0]),
               100 * MAX_SUB_DROP,
               ", ".join(sorted(set("%s_%s" % (b["text"].strip()[-1:], s["text"].strip())
                                    for _, b, s in low))[:8])))
    if spans:
        base = sorted(s for s, sc, _, _ in spans if not sc) or [0]
        add("note", "type-summary", "printed type: min %.1f, median %.1f, max %.1f pt over "
            "%d runs" % (base[0], base[len(base) // 2], base[-1], len(spans)))

    # --------------------------------------------------------- images
    page_area = W * H
    jpeg_src = source_jpegs(source) if source else set()
    shadows = []
    for info in page.get_image_info(xrefs=True):
        xref = info.get("xref", 0)
        bbox = fitz.Rect(info["bbox"])
        pw = info.get("width", 0)
        if bbox.width <= 0 or not pw:
            continue
        inches = bbox.width * k / 72.0
        ppi = pw / inches
        flt = ""
        if xref:
            try:
                flt = doc.xref_get_key(xref, "Filter")[1] or ""
            except Exception:
                flt = ""
        ph = info.get("height", 0)
        where = "image %dx%d px at (%.0f, %.0f)" % (pw, ph, bbox.x0, bbox.y0)
        if xref and (ppi < WARN_PPI or bbox.width * bbox.height > 0.9 * page_area) \
                and _flat_masked(doc, xref):
            shadows.append(ppi)
            continue
        if ppi < MIN_PPI:
            add("error", "image-low-ppi", "%s prints at %.0f ppi (< %d)" % (where, ppi, MIN_PPI))
        elif ppi < WARN_PPI:
            add("warning", "image-soft", "%s prints at %.0f ppi (< %d)" % (where, ppi, WARN_PPI))
        if "DCT" in flt and (pw, ph) in CAMERAS:
            add("note", "image-jpeg",
                "%s is JPEG at the native size of %s frames; camera photos are JPEG at the "
                "source, so this costs nothing" % (where, CAMERAS[(pw, ph)]))
        elif "DCT" in flt and (pw, ph) in jpeg_src:
            add("note", "image-jpeg",
                "%s is JPEG because it already was in %s; the export did not re-encode it"
                % (where, os.path.basename(source)))
        elif "DCT" in flt or "JPX" in flt:
            add("warning", "image-jpeg",
                "%s is stored %s (lossy); export lossless -- see export_pptx.py"
                % (where, "JPEG" if "DCT" in flt else "JPEG 2000"))
        if bbox.width * bbox.height > 0.9 * page_area:
            add("error", "page-is-raster",
                "%s covers the whole page: the figure is a picture, not vector art" % where)

    if shadows:
        add("error", "raster-shadow",
            "%d image(s) are one flat colour under a soft mask, printed at %.0f-%.0f ppi: "
            "shadows or transparency the exporter rasterised, not photos. From a PPTX this is "
            "usually a shadow inherited from the theme (<a:effectRef idx=\"1|2\">) that "
            "LibreOffice draws and PowerPoint/Keynote hide; run audit_pptx.py on the PPTX, "
            "or audit_pptx.py --fix OUT.pptx, and export again"
            % (len(shadows), min(shadows), max(shadows)))

    # ---------------------------------------------------------- fonts
    # cairo (rsvg-convert) puts bare spaces in a Type 3 font; only fonts that
    # carry visible glyphs matter.
    visible_t3 = False
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            for s_ in ln["spans"]:
                if s_["font"].startswith("Type3") and s_["text"].strip():
                    visible_t3 = True
    for f in page.get_fonts(full=True):
        xref, ext, typ, basefont = f[0], f[1], f[2], f[3]
        if ext in ("n/a", "", None) and typ != "Type3":
            add("error", "font-not-embedded",
                "font %s (%s) is not embedded; the venue checker rejects this" % (basefont, typ))
        if typ == "Type3" and visible_t3:
            add("warning", "type3-font",
                "font %s is a Type 3 (bitmap) font; text will look rough when zoomed"
                % basefont)
    meta = {"page_pt": [round(W, 1), round(H, 1)], "print_width_pt": round(width, 1),
            "scale": round(k, 4), "pages": len(doc)}
    if len(doc) > 1:
        add("note", "multi-page", "%d pages; audited page %d" % (len(doc), page_no))
    return rows, meta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--width", type=float, help="printed width in pt")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--source", metavar="PPTX",
                    help="the PPTX the PDF was exported from; pictures it already stores as "
                         "JPEG are not blamed on the export")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="fail on warnings too")
    args = ap.parse_args(argv)
    if fitz is None:
        print("audit_pdf.py needs PyMuPDF: pip install pymupdf", file=sys.stderr)
        return 2
    rows, meta = audit(args.pdf, args.width, args.page, args.source)
    c = {"error": 0, "warning": 0, "note": 0}
    for r in rows:
        c[r["level"]] += 1
    if args.json:
        print(json.dumps({"counts": c, "findings": rows, "meta": meta}, indent=2))
    else:
        icon = {"error": "FAIL", "warning": "WARN", "note": "note"}
        order = {"error": 0, "warning": 1, "note": 2}
        for r in sorted(rows, key=lambda r: order[r["level"]]):
            print("%-4s %-18s %s" % (icon[r["level"]], r["code"], r["message"]))
        print("\n%d error(s), %d warning(s), %d note(s) | page %.0f x %.0f pt printed at "
              "%.1f pt (x%.3f)" % (c["error"], c["warning"], c["note"], meta["page_pt"][0],
                                   meta["page_pt"][1], meta["print_width_pt"], meta["scale"]))
    if c["error"] or (args.strict and c["warning"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
