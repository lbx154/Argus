#!/usr/bin/env python3
"""Do LibreOffice and the engine agree on where every arrow goes?

    python3 tests/compat/lo_roundtrip.py [spec.yaml ...]   (default: every template)

Renders each spec to PPTX, has LibreOffice re-open it and export a PDF, and
checks that every planned route is drawn along the same path.  LibreOffice
re-routes glued connectors from their connection sites on import, so this
catches wrong site numbering, wrong rotation/flip handling, and sites that do
not sit on the ports.  Needs LibreOffice (soffice) and PyMuPDF; skips cleanly
without them.  Not part of the standard-library test suite.
"""

import glob
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from cvprfig import Figure, load_path, pptx  # noqa: E402

try:
    import fitz
except ImportError:
    fitz = None


def segments(pdf, width):
    pg = fitz.open(pdf)[0]
    k = pg.rect.width / width
    segs, heads = [], []
    for dr in pg.get_drawings():
        r = dr["rect"]
        if dr.get("fill") is not None and r.width <= 12 and r.height <= 12:
            heads.append((r.x0 / k - .3, r.y0 / k - .3, r.x1 / k + .3, r.y1 / k + .3))
        if dr.get("fill") is not None and dr.get("color") is None and r.width > 8 and r.height > 8:
            continue
        for it in dr["items"]:
            if it[0] == "l":
                segs.append((it[1].x / k, it[1].y / k, it[2].x / k, it[2].y / k))
    return segs, heads


def covered(p, q, segs, heads):
    L = abs(p[0] - q[0]) + abs(p[1] - q[1])
    if L < 0.5:
        return True
    n = max(2, int(L))
    hit = 0
    for i in range(n + 1):
        t = i / float(n)
        x, y = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
        if any(h[0] <= x <= h[2] and h[1] <= y <= h[3] for h in heads):
            hit += 1
            continue
        for ax, ay, bx, by in segs:
            dx, dy = bx - ax, by - ay
            LL = (dx * dx + dy * dy) or 1.0
            tt = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / LL))
            if ((ax + tt * dx - x) ** 2 + (ay + tt * dy - y) ** 2) ** .5 < 0.4:
                hit += 1
                break
    return hit >= 0.9 * (n + 1)


def main(argv):
    so = shutil.which("soffice") or shutil.which("libreoffice")
    if not so or fitz is None:
        print("skipped: needs LibreOffice and PyMuPDF")
        return 0
    specs = argv or sorted(glob.glob(os.path.join(ROOT, "templates", "*.yaml")))
    tmp = tempfile.mkdtemp()
    bad_total = total = 0
    for spec in specs:
        name = os.path.splitext(os.path.basename(spec))[0]
        fig = Figure(load_path(spec), base=os.path.dirname(os.path.abspath(spec)))
        fig.render()
        out = os.path.join(tmp, name + ".pptx")
        pptx.write(fig, out)
        subprocess.run([so, "--headless", "--convert-to", "pdf", "--outdir", tmp, out],
                       capture_output=True, timeout=240)
        segs, heads = segments(os.path.join(tmp, name + ".pdf"), fig._viewbox[0])
        bad = [r for r in fig.routes
               if not all(covered(p, q, segs, heads) for p, q in zip(r.pts, r.pts[1:]))]
        total += len(fig.routes)
        bad_total += len(bad)
        print("%-24s %3d routes, %d differ%s" % (name, len(fig.routes), len(bad),
              "".join("\n    %s -> %s" % (r.spec["from"], r.spec["to"]) for r in bad)))
    print("\n%d of %d routes drawn where the engine put them" % (total - bad_total, total))
    return 1 if bad_total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
