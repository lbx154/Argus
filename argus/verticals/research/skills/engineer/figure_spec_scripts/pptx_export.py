"""Export a method figure's PDF and PNG from its PPT Master PPTX.

Method D and Method B end with a native PPTX at ``paper/figures/<stem>.pptx``
and a PDF of the same stem that the manuscript includes. This machine has
neither PowerPoint nor LibreOffice, so until now nobody could produce that
PDF from the PPTX; Engineers compiled a TikZ look-alike, or rendered an SVG
through Ghostscript, and put a PPTX beside it to satisfy the stem rule.

This script is the export step. It reads the PPTX with PPT Master's own
``pptx_to_svg.py`` (pure OOXML, no Office needed), wraps the slide SVG in a
page, and renders it through the vertical's browser renderer:

    python pptx_export.py --pptx paper/figures/fig1_method.pptx

writes ``paper/figures/fig1_method.pdf`` (vector, producer Skia/PDF) and
``paper/figures/fig1_method.png`` (raster, for inspection at manuscript
width), keeps the slide SVG and page under ``paper/figures/src/<stem>/``,
and records provenance. ``figure_lint`` reports a method-figure PDF whose
producer is not an export chain (TeX, matplotlib, Ghostscript, cairo)
beside a PPTX, so this is the only route that satisfies the stem rule.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SOURCE_ROOT = Path("paper/figures/src")
OUTPUT_ROOT = Path("paper/figures")
PX_PER_MM = 96.0 / 25.4
PNG_DPI = 220.0
_SLIDE_NAME = re.compile(r"slide_(\d+)\.svg$")
_ROOT_ATTR = re.compile(r"<svg\b[^>]*>", re.DOTALL)
_ATTR = re.compile(r'\b(width|height|viewBox)="([^"]*)"')

RENDERER = "pptx-browser"


def _ppt_master_script() -> Path:
    """PPT Master's pptx_to_svg.py: from the installed toolkit, or an override."""
    override = os.environ.get("ARGUS_PPT_MASTER_SKILL_ROOT")
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    try:
        from argus.tools.ppt_master import skill_root

        candidates.append(Path(skill_root()))
    except Exception:  # noqa: BLE001 - the toolkit may be installed without the package importable
        pass
    candidates.append(Path.home() / ".argus-skill" / "tools" / "ppt-master" / "skills" / "ppt-master")
    for root in candidates:
        script = root / "scripts" / "pptx_to_svg.py"
        if script.is_file():
            return script
    raise FileNotFoundError(
        "PPT Master is not installed (no scripts/pptx_to_svg.py under "
        + ", ".join(str(c) for c in candidates)
        + "); run `python -m argus.tools.ppt_master install` first"
    )


def _run_pptx_to_svg(pptx: Path, out_dir: Path) -> list[Path]:
    """Convert every slide to SVG with PPT Master; return the slide files in order."""
    script = _ppt_master_script()
    python = os.environ.get("ARGUS_SKILL_PYTHON") or sys.executable
    completed = subprocess.run(
        [python, str(script), str(pptx), "-o", str(out_dir)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"pptx_to_svg failed ({completed.returncode}): {completed.stderr.strip()[-800:]}")
    slides = sorted(
        (p for p in (out_dir / "svg").glob("slide_*.svg") if _SLIDE_NAME.search(p.name)),
        key=lambda p: int(_SLIDE_NAME.search(p.name).group(1)),  # type: ignore[union-attr]
    )
    if not slides:
        raise RuntimeError(f"pptx_to_svg wrote no slide SVG under {out_dir / 'svg'}")
    return slides


def slide_size_px(svg: str) -> tuple[float, float]:
    """Pixel size of the slide from the SVG root (width/height, else viewBox)."""
    root = _ROOT_ATTR.search(svg)
    if root is None:
        raise ValueError("not an SVG document")
    attrs = dict(_ATTR.findall(root.group(0)))
    if "viewBox" in attrs:
        parts = attrs["viewBox"].replace(",", " ").split()
        if len(parts) == 4:
            return float(parts[2]), float(parts[3])
    if "width" in attrs and "height" in attrs:
        return float(re.sub(r"[a-z%]+$", "", attrs["width"])), float(re.sub(r"[a-z%]+$", "", attrs["height"]))
    raise ValueError("SVG root has neither viewBox nor width/height")


def write_page(svg: str, *, project_root: Path, stem: str, width_px: float, height_px: float) -> tuple[Path, Path]:
    """Keep the slide SVG and a page wrapping it under paper/figures/src/<stem>/."""
    source_dir = project_root / SOURCE_ROOT / stem
    source_dir.mkdir(parents=True, exist_ok=True)
    svg_path = source_dir / "slide.svg"
    svg_path.write_text(svg if svg.endswith("\n") else svg + "\n", encoding="utf-8")
    html_path = source_dir / "index.html"
    html_path.write_text(
        "<!doctype html>\n<html><head><meta charset=\"utf-8\">\n"
        "<style>\n"
        "html,body{margin:0;padding:0;background:#fff}\n"
        # PPT Master emits the theme font as sans-serif; name the metric-compatible
        # faces so the render matches PowerPoint's Calibri/Arial line breaks.
        "svg text{font-family:Carlito,Calibri,'Liberation Sans',Arial,Helvetica,sans-serif}\n"
        f"[data-figure-root]{{width:{width_px:g}px;height:{height_px:g}px;overflow:hidden}}\n"
        "[data-figure-root] svg{width:100%;height:100%;display:block}\n"
        "</style></head><body>\n"
        f"<div data-figure-root data-figure-ready=\"true\">\n{svg}\n</div>\n"
        "</body></html>\n",
        encoding="utf-8",
    )
    return svg_path, html_path


def _load_browser_render():
    path = Path(__file__).resolve().parents[1] / "research_visual_scripts" / "browser_render.py"
    spec = importlib.util.spec_from_file_location("argus_browser_render", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"browser renderer missing at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def export_pptx(
    pptx: Path | str,
    *,
    project_root: Path | str = ".",
    slide: int = 1,
    png_width_mm: float = 170.0,
    role: str = "method",
    timeout_ms: int = 120_000,
) -> dict[str, Any]:
    """Export ``<stem>.pdf`` and ``<stem>.png`` beside the PPTX; record provenance.

    Returns the written paths and the slide's physical size in millimetres.
    """
    root = Path(project_root).resolve()
    pptx_path = Path(pptx)
    if not pptx_path.is_absolute():
        pptx_path = root / pptx_path
    if not pptx_path.is_file():
        raise FileNotFoundError(f"no PPTX at {pptx_path}")
    stem = pptx_path.stem
    with tempfile.TemporaryDirectory(prefix="pptx-export-") as tmp:
        slides = _run_pptx_to_svg(pptx_path, Path(tmp))
        if slide < 1 or slide > len(slides):
            raise ValueError(f"--slide {slide} out of range; the PPTX has {len(slides)} slide(s)")
        svg = slides[slide - 1].read_text(encoding="utf-8")
    width_px, height_px = slide_size_px(svg)
    svg_path, html_path = write_page(svg, project_root=root, stem=stem, width_px=width_px, height_px=height_px)
    out_dir = pptx_path.parent
    pdf_path = out_dir / f"{stem}.pdf"
    png_path = out_dir / f"{stem}.png"
    renderer = _load_browser_render()
    common = [
        "--input", str(html_path),
        "--selector", "[data-figure-root]",
        "--width", str(int(round(width_px))),
        "--height", str(int(round(height_px))),
        "--timeout-ms", str(timeout_ms),
    ]
    renderer.main([*common, "--output", str(pdf_path)])
    # The PNG is for looking at the figure at manuscript width: scale the
    # slide so that png_width_mm comes out at PNG_DPI.
    scale = max(1.0, round((png_width_mm / 25.4 * PNG_DPI) / width_px, 2))
    renderer.main([*common, "--output", str(png_path), "--device-scale-factor", str(scale)])
    written: dict[str, Any] = {
        "pptx": str(pptx_path),
        "slide": slide,
        "slides": len(slides),
        "svg": str(svg_path),
        "html": str(html_path),
        "pdf": str(pdf_path),
        "png": str(png_path),
        "width_mm": round(width_px / PX_PER_MM, 1),
        "height_mm": round(height_px / PX_PER_MM, 1),
    }
    try:
        from argus.verticals.research.figure_provenance import register_figure

        metadata = pdf_path.with_name(pdf_path.name + ".render.json")
        register_figure(
            project_root=root,
            figure_id=stem,
            role=role,
            renderer=RENDERER,
            source_path=pptx_path,
            output_path=pdf_path,
            inputs=[svg_path, html_path],
            render_metadata_path=metadata if metadata.is_file() else None,
            command=f"pptx_export.py --pptx {pptx_path.relative_to(root) if pptx_path.is_relative_to(root) else pptx_path}",
        )
    except Exception as exc:  # noqa: BLE001 - provenance is bookkeeping, the export exists
        print(f"figure provenance not recorded: {exc}", file=sys.stderr)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pptx", type=Path, required=True, help="the native PPTX, e.g. paper/figures/fig1_method.pptx")
    parser.add_argument("--slide", type=int, default=1, help="1-based slide to export (default 1)")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--png-width-mm", type=float, default=170.0, help="manuscript width the PNG is rendered for")
    parser.add_argument("--role", default="method")
    parser.add_argument("--timeout-ms", type=int, default=120_000)
    args = parser.parse_args(argv)
    written = export_pptx(
        args.pptx,
        project_root=args.project_root,
        slide=args.slide,
        png_width_mm=args.png_width_mm,
        role=args.role,
        timeout_ms=args.timeout_ms,
    )
    print(json.dumps(written, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
