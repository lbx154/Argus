#!/usr/bin/env python3
"""Export a (hand-edited) PPTX to the paper PDF without losing quality.

    python3 export_pptx.py fig.pptx [-o fig.pdf] [--width 496.8] [--via libreoffice|keynote]

Once a person has edited the PPTX it is the only source of the figure, and
the PDF in the paper must come from it.  The export is where quality is lost:

  * LibreOffice's default PDF filter downsamples pictures to 300 dpi and
    re-encodes them as JPEG (a 4000 ppi occupancy crop came out at 298 ppi);
  * WPS (macOS) keeps the resolution but still stores JPEG;
  * Keynote keeps everything when the image quality is "Best".

This runs LibreOffice with the lossless options (no downsampling, lossless
compression, quality 100) or, on a Mac, Keynote with Best quality, then runs
``audit_pdf.py`` on the result.  WPS has no command line; export from it by
hand with picture quality set to the highest, then run audit_pdf.py.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# The filter options LibreOffice needs for a lossless figure PDF.  The same
# dictionary works for impress_pdf_Export (PPTX) and draw_pdf_Export (SVG).
LOSSLESS = {
    "ReduceImageResolution": {"type": "boolean", "value": "false"},
    "UseLosslessCompression": {"type": "boolean", "value": "true"},
    "Quality": {"type": "long", "value": "100"},
    "EmbedStandardFonts": {"type": "boolean", "value": "true"},
}


def soffice():
    for n in ("soffice", "libreoffice"):
        p = shutil.which(n)
        if p:
            return p
    mac = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    return mac if os.path.exists(mac) else None


def lossless_filter(kind="impress"):
    return "pdf:%s_pdf_Export:%s" % (kind, json.dumps(LOSSLESS, separators=(",", ":")))


def via_libreoffice(src, out, kind="impress"):
    so = soffice()
    if not so:
        return False, "LibreOffice (soffice) not found"
    out_dir = os.path.dirname(os.path.abspath(out)) or "."
    stem = os.path.splitext(os.path.basename(src))[0]
    r = subprocess.run([so, "--headless", "--norestore", "--convert-to", lossless_filter(kind),
                        "--outdir", out_dir, os.path.abspath(src)],
                       capture_output=True, timeout=240)
    produced = os.path.join(out_dir, stem + ".pdf")
    if r.returncode != 0 or not os.path.exists(produced):
        return False, (r.stderr or b"").decode("utf-8", "replace")[-300:]
    if os.path.abspath(produced) != os.path.abspath(out):
        shutil.move(produced, out)
    return True, "libreoffice (lossless)"


def via_keynote(src, out):
    if sys.platform != "darwin" or not os.path.exists("/Applications/Keynote.app"):
        return False, "Keynote is only available on macOS"
    script = ('tell application "Keynote"\n'
              'set d to open POSIX file "%s"\n'
              'delay 1\n'
              'export d to POSIX file "%s" as PDF with properties {PDF image quality:Best}\n'
              'close d saving no\n'
              'end tell\n' % (os.path.abspath(src), os.path.abspath(out)))
    r = subprocess.run(["osascript", "-"], input=script.encode(), capture_output=True,
                       timeout=240)
    if r.returncode != 0 or not os.path.exists(out):
        return False, (r.stderr or b"").decode("utf-8", "replace")[-300:]
    return True, "keynote (best quality)"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pptx")
    ap.add_argument("-o", "--out", help="output PDF (default: next to the PPTX)")
    ap.add_argument("--width", type=float, help="printed width in pt, for the audit")
    ap.add_argument("--via", choices=("libreoffice", "keynote"), default="libreoffice")
    ap.add_argument("--no-audit", action="store_true")
    args = ap.parse_args(argv)
    out = args.out or os.path.splitext(args.pptx)[0] + ".pdf"
    ok, how = (via_keynote if args.via == "keynote" else via_libreoffice)(args.pptx, out)
    if not ok:
        print("export failed: %s" % how, file=sys.stderr)
        return 1
    print("wrote %s via %s" % (out, how))
    if args.no_audit:
        return 0
    try:
        import audit_pdf
    except Exception:
        return 0
    if audit_pdf.fitz is None:
        print("  (install PyMuPDF to audit the PDF: pip install pymupdf)")
        return 0
    rows, meta = audit_pdf.audit(out, args.width, source=args.pptx)
    bad = [r for r in rows if r["level"] in ("error", "warning")]
    for r in bad:
        print("  %s %s %s" % ("FAIL" if r["level"] == "error" else "WARN", r["code"], r["message"]))
    print("  audit: %d error(s), %d warning(s)" % (
        sum(r["level"] == "error" for r in rows), sum(r["level"] == "warning" for r in rows)))
    return 1 if any(r["level"] == "error" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
