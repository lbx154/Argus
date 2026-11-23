#!/usr/bin/env python3
"""Render figure or manuscript PDFs at 300 DPI and slice into 2x2 overlapping zoom tiles.

Small overlaps (a legend touching a dashed line, a clipped subscript, a 45-degree arrowhead)
are easy to miss in a whole-page thumbnail and obvious in 2x2 zoomed quadrants. Run this on
every figure PDF before handing off, and inspect the generated PNGs with Read.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def render_and_tile(pdf: Path, out_dir: Path, dpi: int) -> list[str]:
    issues: list[str] = []
    stem = out_dir / pdf.stem
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(dpi), str(pdf), str(stem)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    pages = sorted(out_dir.glob(f"{pdf.stem}-*.png"))
    try:
        from PIL import Image
    except ImportError:
        return issues

    for png in pages:
        img = Image.open(png).convert("RGB")
        w, h = img.size
        # Border-clipping check: non-white ink right on the outer 2px border
        px = img.load()
        clipped_edges = []
        if any(min(px[x, 0]) < 220 or min(px[x, 1]) < 220 for x in range(w)):
            clipped_edges.append("top")
        if any(min(px[x, h - 1]) < 220 or min(px[x, h - 2]) < 220 for x in range(w)):
            clipped_edges.append("bottom")
        if any(min(px[0, y]) < 220 or min(px[1, y]) < 220 for y in range(h)):
            clipped_edges.append("left")
        if any(min(px[w - 1, y]) < 220 or min(px[w - 2, y]) < 220 for y in range(h)):
            clipped_edges.append("right")
        if clipped_edges:
            issues.append(f"{png.name}: ink touches {', '.join(clipped_edges)} edge(s) within 2px (possible clipping)")

        # 2x2 overlapping quadrants (55% width x 55% height each so seam regions appear intact)
        xw, yh = int(w * 0.55), int(h * 0.55)
        boxes = {
            "q1_tl": (0, 0, xw, yh),
            "q2_tr": (w - xw, 0, w, yh),
            "q3_bl": (0, h - yh, xw, h),
            "q4_br": (w - xw, h - yh, w, h),
        }
        for tag, box in boxes.items():
            img.crop(box).save(png.with_name(f"{png.stem}_{tag}.png"))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Render PDFs at 300 DPI into full + 2x2 zoomed quadrant PNGs.")
    parser.add_argument("pdfs", nargs="+", type=Path, help="PDF file(s) to render and tile")
    parser.add_argument("--out", type=Path, required=True, help="Output directory for PNG previews and tiles")
    parser.add_argument("--dpi", type=int, default=300, help="Render DPI (default: 300)")
    args = parser.parse_args()

    if not shutil.which("pdftoppm"):
        print("FAIL: pdftoppm not found on PATH", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    all_issues: list[str] = []
    count = 0
    for pdf in args.pdfs:
        if not pdf.is_file():
            print(f"WARN: skipping missing file {pdf}", file=sys.stderr)
            continue
        count += 1
        all_issues.extend(render_and_tile(pdf, args.out, args.dpi))

    print(f"Rendered and tiled {count} PDF(s) into {args.out} ({len(all_issues)} clipping warning(s))")
    for item in all_issues:
        print(f"  WARN: {item}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
