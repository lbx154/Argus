#!/usr/bin/env python3
"""Render the HTML/SVG dense-intelligence horizon figure."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path


HERE = Path(__file__).resolve().parent
HTML_PATH = HERE / "horizon_mountain.html"
BASE_PATH = HERE / "horizon_mountain_illustrated_base.png"
SOURCE_PATH = HERE / "horizon_mountain_illustrated_source.png"
EXPLORER_PATH = HERE / "argus_explorer_binoculars.png"
GUIDE_PATH = HERE / "horizon_base_composition_guide_v2.svg"
PDF_PATH = HERE / "horizon_mountain.pdf"
SVG_PATH = HERE / "horizon_mountain.svg"
PNG_PATH = HERE / "horizon_mountain.png"
PROVENANCE_PATH = HERE / "horizon_mountain.provenance.json"
ANIME_DIR = HERE / "assets" / "anime"
ROLE_PATHS = [ANIME_DIR / f"{name}.png" for name in ("manager", "planner", "engineer", "reviewer")]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if not BASE_PATH.is_file() or not SOURCE_PATH.is_file() or not EXPLORER_PATH.is_file() or not GUIDE_PATH.is_file() or not all(path.is_file() for path in ROLE_PATHS):
        raise SystemExit("illustrated mountain or explorer asset is missing")
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        raise SystemExit("google-chrome or chromium is required")
    completed = subprocess.run(
        [chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--virtual-time-budget=2500", f"--print-to-pdf={PDF_PATH}", "--print-to-pdf-no-header", HTML_PATH.as_uri()],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not PDF_PATH.is_file():
        raise SystemExit(f"HTML render failed: {completed.stderr}")
    ghostscript = shutil.which("gs")
    if ghostscript:
        compatible = HERE / "horizon_mountain.compat.pdf"
        subprocess.run([ghostscript, "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5", f"-sOutputFile={compatible}", str(PDF_PATH)], check=True)
        compatible.replace(PDF_PATH)
    subprocess.run(["pdftocairo", "-svg", str(PDF_PATH), str(SVG_PATH)], check=True)
    subprocess.run(["pdftoppm", "-singlefile", "-png", "-r", "180", str(PDF_PATH), str(PNG_PATH.with_suffix(""))], check=True)
    provenance = {
        "figure_id": "dense-intelligence-horizon",
        "reader_question": "How do recurrent roles, Manager-controlled stages, persistent state, and a future training flywheel interact over a long horizon?",
        "claim": "The illustrated mountain visualizes a four-role state machine repeated across eight non-monotonic research stages, including rollback, rejected branches, persistent runtime state, and a hypothesized reviewed-trajectory-to-training flywheel.",
        "scope": "Conceptual illustration; geometry and stage height are not empirical scales, and the parameter-training flywheel is a future hypothesis rather than a reported result.",
        "stack": "gpt-image-2 mountain base with HTML/CSS/SVG semantic overlays and deterministic shared anime role characters.",
        "generation_prompt": "Two-image gpt-image-2 edit: Image 1 fixes a complete in-frame mountain silhouette with both slopes visible and the summit near 70% canvas width; Image 2 supplies only the restrained 2D hand-drawn editorial language. The original output uses unified navy linework, broad gouache-like teal/ochre/green/cream color blocks, simple cel shading, foreground vegetation, and clouds, while excluding copied reference objects, photorealism, concept-art lighting, 3D texture, low-poly facets, people, flags, paths, and text.",
        "character_prompt": "One full-body expedition researcher in restrained semi-anime editorial style, standing firmly with one boot on a low rock while holding binoculars and looking toward the upper-right summit; crisp silhouette on a flat magenta chroma-key background, with no scenery, text, logo, shadow, or extra character.",
        "style_reference": "https://github.com/RUC-NLPIR/Awesome-Long-Horizon-Agents/blob/main/assets/horizon.png (style only; composition and objects not copied)",
        "editable_sources": [HTML_PATH.name, *[str(path.relative_to(HERE)) for path in ROLE_PATHS]],
        "outputs": {str(path.relative_to(HERE)): sha256(path) for path in (GUIDE_PATH, SOURCE_PATH, BASE_PATH, EXPLORER_PATH, *ROLE_PATHS, HTML_PATH, PDF_PATH, SVG_PATH, PNG_PATH)},
    }
    PROVENANCE_PATH.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(PDF_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
