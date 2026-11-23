#!/usr/bin/env python3
"""Render a figure spec to SVG / PDF / PNG / EMF / VSDX / PPTX.

    python3 render.py spec.yaml -o out/overview --format svg,pdf,vsdx

The SVG is always written first and is the source of truth; the other formats
are produced either by a bundled writer (vsdx, pptx) or by whichever converter
is installed (LibreOffice, Inkscape, rsvg-convert, cairosvg, ImageMagick).

Once the PPTX has been handed over and edited by hand, stop running this on
the spec: it would overwrite the edits.  Export the PDF from the PPTX with
export_pptx.py and audit it with audit_pptx.py / audit_pdf.py.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cvprfig import Figure, load_path                 # noqa: E402
from cvprfig import vsdx as vsdx_writer               # noqa: E402
from cvprfig import pptx as pptx_writer               # noqa: E402

RASTER = ("png", "tiff", "tif", "jpg")
DEFAULT_DPI = 600


def which(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def run(cmd, timeout=180):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.returncode == 0, (r.stderr or b"").decode("utf-8", "replace")[-400:]
    except Exception as exc:                                    # pragma: no cover
        return False, str(exc)


def svg_to_pdf(svg_path, out_path):
    """Vector conversion.  Tried in quality order; returns (ok, tool).

    LibreOffice comes first: rsvg-convert (cairo/pango) mis-places the glyph
    after an italic "f" in Times New Roman on macOS -- "Deformable" prints as
    "Deƒrmable" -- while its text layer still reads correctly, so nothing
    but a look at the page catches it.  LibreOffice is run with the lossless
    filter options, since its default downsamples pictures to 300 dpi JPEG.
    """
    out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    stem = os.path.splitext(os.path.basename(svg_path))[0]

    so = which("soffice", "libreoffice")
    if not so and os.path.exists("/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        so = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if so:
        from export_pptx import lossless_filter
        ok, _ = run([so, "--headless", "--norestore", "--convert-to", lossless_filter("draw"),
                     "--outdir", out_dir, os.path.abspath(svg_path)])
        produced = os.path.join(out_dir, stem + ".pdf")
        if ok and os.path.exists(produced):
            if os.path.abspath(produced) != os.path.abspath(out_path):
                shutil.move(produced, out_path)
            return True, "libreoffice (lossless)"
    ink = which("inkscape")
    if ink:
        ok, _ = run([ink, "--export-type=pdf", "--export-filename=%s" % out_path, svg_path])
        if ok and os.path.exists(out_path):
            return True, "inkscape"
    rs = which("rsvg-convert")
    if rs:
        ok, _ = run([rs, "-f", "pdf", "-o", out_path, svg_path])
        if ok and os.path.exists(out_path):
            return True, "rsvg-convert (check italic f-ligatures by eye)"
    try:
        import cairosvg
        cairosvg.svg2pdf(url=svg_path, write_to=out_path)
        return True, "cairosvg"
    except Exception:
        pass
    return False, "no SVG->PDF converter found (install LibreOffice, Inkscape or librsvg)"


def rasterise(svg_path, pdf_path, out_path, fmt, dpi):
    """Produce a raster at a real, verified pixel density.

    LibreOffice's PNG filter silently ignores the requested DPI and emits a
    96 dpi screenshot, which is useless for a camera-ready figure.  So the
    raster is always taken from the *PDF* with a rasteriser that honours a
    density flag, and the result's pixel size is checked against the
    expectation before it is accepted.
    """
    ink = which("inkscape")
    if ink and fmt == "png":
        ok, _ = run([ink, "--export-type=png", "--export-filename=%s" % out_path,
                     "--export-dpi=%d" % dpi, svg_path])
        if ok and os.path.exists(out_path):
            return True, "inkscape @%d dpi" % dpi

    if pdf_path and os.path.exists(pdf_path):
        ppm = which("pdftoppm")
        if ppm:
            flag = {"png": "-png", "tiff": "-tiff", "tif": "-tiff", "jpg": "-jpeg"}.get(fmt, "-png")
            stem = os.path.splitext(out_path)[0] + "__r"
            ok, _ = run([ppm, flag, "-r", str(dpi), "-f", "1", "-l", "1",
                         "-singlefile", pdf_path, stem])
            produced = stem + {"tif": ".tif", "tiff": ".tif", "jpg": ".jpg"}.get(fmt, ".png")
            if not os.path.exists(produced):
                for ext in (".png", ".tif", ".jpg"):
                    if os.path.exists(stem + ext):
                        produced = stem + ext
                        break
            if ok and os.path.exists(produced):
                shutil.move(produced, out_path)
                return True, "pdftoppm @%d dpi" % dpi
        mg = which("magick", "convert")
        if mg:
            ok, _ = run([mg, "-density", str(dpi), pdf_path, "-background", "white",
                         "-alpha", "remove", "-alpha", "off", out_path])
            if ok and os.path.exists(out_path):
                return True, "imagemagick @%d dpi" % dpi

    mg = which("magick", "convert")
    if mg:
        ok, _ = run([mg, "-density", str(dpi), "-background", "white", svg_path,
                     "-flatten", out_path])
        if ok and os.path.exists(out_path):
            return True, "imagemagick(svg) @%d dpi" % dpi
    try:
        import cairosvg
        cairosvg.svg2png(url=svg_path, write_to=out_path, dpi=dpi)
        return True, "cairosvg @%d dpi" % dpi
    except Exception:
        pass
    return False, "no rasteriser honouring --dpi (install poppler-utils or ImageMagick)"


def png_size(path):
    """Pixel size of a PNG, read from the IHDR chunk -- no dependencies."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(33)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        import struct
        return struct.unpack(">II", head[16:24])
    except Exception:
        return None


def svg_to_emf(svg_path, out_path):
    out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    stem = os.path.splitext(os.path.basename(svg_path))[0]
    ink = which("inkscape")
    if ink:
        ok, _ = run([ink, "--export-type=emf", "--export-filename=%s" % out_path, svg_path])
        if ok and os.path.exists(out_path):
            return True, "inkscape"
    so = which("soffice", "libreoffice")
    if so:
        ok, _ = run([so, "--headless", "--norestore", "--convert-to", "emf",
                     "--outdir", out_dir, os.path.abspath(svg_path)])
        produced = os.path.join(out_dir, stem + ".emf")
        if ok and os.path.exists(produced):
            if os.path.abspath(produced) != os.path.abspath(out_path):
                shutil.move(produced, out_path)
            return True, "libreoffice"
    return False, "no SVG->EMF converter found (install Inkscape or LibreOffice)"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", help="path to the .yaml/.json figure spec")
    ap.add_argument("-o", "--out", help="output path stem (default: alongside the spec)")
    ap.add_argument("-f", "--format", default="svg,pdf",
                    help="comma list: svg,pdf,png,tiff,emf,vsdx,pptx (default svg,pdf)")
    ap.add_argument("--dpi", type=int, default=DEFAULT_DPI,
                help="raster density; 600 is camera-ready, 1200 for a poster")
    ap.add_argument("--json", action="store_true", help="emit a machine-readable report")
    args = ap.parse_args(argv)

    spec = load_path(args.spec)
    base = os.path.dirname(os.path.abspath(args.spec))
    fig = Figure(spec, base=base)
    svg_text = fig.tostring()

    stem = args.out or os.path.join(base, (spec.get("figure") or {}).get("id", "figure"))
    out_dir = os.path.dirname(os.path.abspath(stem)) or "."
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    formats = [f.strip().lower() for f in args.format.split(",") if f.strip()]
    if "svg" not in formats:
        formats.insert(0, "svg")

    written, notes = [], []
    svg_path = stem + ".svg"
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg_text)
    written.append(svg_path)

    # A PDF is produced whenever a raster is wanted: rasterising the PDF is the
    # only route that reliably honours a DPI setting.
    need_pdf = "pdf" in formats or any(f in RASTER for f in formats)
    pdf_path = stem + ".pdf"
    pdf_ok = False
    if need_pdf:
        pdf_ok, how = svg_to_pdf(svg_path, pdf_path)
        if pdf_ok:
            if "pdf" in formats:
                written.append(pdf_path)
                notes.append("pdf via %s" % how)
        else:
            notes.append("SKIPPED pdf: %s" % how)

    for fmt in formats:
        if fmt in ("svg", "pdf"):
            continue
        target = stem + "." + fmt
        if fmt == "vsdx":
            info = vsdx_writer.write(fig, target)
            written.append(target)
            if info.get("image_placeholders"):
                notes.append("vsdx: %d image slot(s) exported as placeholders (%s)"
                             % (len(info["image_placeholders"]),
                                ", ".join(info["image_placeholders"][:3])))
            continue
        if fmt == "pptx":
            info = pptx_writer.write(fig, target)
            written.append(target)
            notes.append("pptx: %d shapes, %d embedded image(s), %d glued connector(s)"
                         % (info["shapes"], info["media"], info["connectors_glued"]))
            if info["freeform_edges"]:
                notes.append("pptx: %d edge(s) have 3+ bends and stay freeform (move them by "
                             "hand if you move their modules): %s"
                             % (len(info["freeform_edges"]),
                                "; ".join(info["freeform_edges"][:4])))
            if info["split_heads"]:
                notes.append("pptx: %d arrowhead(s) given their own straight segment because "
                             "the last bend is too close to the target" % len(info["split_heads"]))
            continue
        if fmt == "emf":
            ok, how = svg_to_emf(svg_path, target)
            notes.append(("emf via %s" % how) if ok else ("SKIPPED emf: %s" % how))
            if ok:
                written.append(target)
            continue
        if fmt in RASTER:
            ok, how = rasterise(svg_path, pdf_path if pdf_ok else None, target, fmt, args.dpi)
            if not ok:
                notes.append("SKIPPED %s: %s" % (fmt, how))
                continue
            written.append(target)
            size = png_size(target) if fmt == "png" else None
            if size:
                want_w = fig.meta["canvas_pt"][0] * args.dpi / 72.0
                notes.append("%s via %s -- %d x %d px" % (fmt, how, size[0], size[1]))
                if size[0] < want_w * 0.9:
                    notes.append("WARNING %s is %d px wide but %.0f px was requested; "
                                 "the converter ignored the density setting"
                                 % (fmt, size[0], want_w))
            else:
                notes.append("%s via %s" % (fmt, how))
            continue
        notes.append("SKIPPED %s: unsupported format" % fmt)

    if not pdf_ok and "pdf" in formats and pdf_path in written:
        written.remove(pdf_path)
    if pdf_ok and "pdf" in formats:
        try:
            import audit_pdf
            if audit_pdf.fitz is not None:
                rows, _ = audit_pdf.audit(pdf_path, fig.meta["canvas_pt"][0])
                bad = [r for r in rows if r["level"] in ("error", "warning")]
                notes.append("pdf audit: %d error(s), %d warning(s)%s"
                             % (sum(r["level"] == "error" for r in rows),
                                sum(r["level"] == "warning" for r in rows),
                                (" -- " + bad[0]["message"]) if bad else ""))
        except Exception as exc:                                # pragma: no cover
            notes.append("pdf audit skipped: %s" % exc)

    report = {"written": written, "notes": notes, "warnings": fig.warnings,
              "meta": fig.meta}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for p in written:
            print("wrote %s" % p)
        for n in notes:
            print("  %s" % n)
        for w in fig.warnings:
            print("  WARNING: %s" % w)
        m = fig.meta
        print("  canvas %.1f x %.1f pt | %d nodes | %d edges | scale %.3f"
              % (m["canvas_pt"][0], m["canvas_pt"][1], m["node_count"],
                 m["edge_count"], m["scale"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
