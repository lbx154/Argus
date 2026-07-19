#!/usr/bin/env python3
"""Export the editable PPT-master teaser to paper-facing vector formats."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPORT = HERE.parent
PPTX_SOURCE = HERE / "argus_teaser.pptx"
SVG_SOURCE = HERE / "argus_teaser.source.svg"
PDF_OUT = HERE / "argus_teaser.pdf"
SVG_OUT = HERE / "argus_teaser.svg"
PNG_OUT = HERE / "argus_teaser.png"
HTML_OUT = HERE / "argus_teaser.html"
MANIFEST_OUT = HERE / "argus_teaser.json"


def run(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.stderr or completed.stdout or "command failed")


def main() -> int:
    office = shutil.which("libreoffice") or shutil.which("soffice")
    ghostscript = shutil.which("gs")
    if not office:
        raise SystemExit("libreoffice or soffice is required to export argus_teaser.pptx")
    if not ghostscript:
        raise SystemExit("ghostscript is required to normalize the teaser PDF to version 1.5")
    if not PPTX_SOURCE.is_file() or not SVG_SOURCE.is_file():
        raise SystemExit("missing editable teaser source")

    with tempfile.TemporaryDirectory(prefix="argus-teaser-") as tmp:
        outdir = Path(tmp)
        run([office, "--headless", "--convert-to", "pdf", "--outdir", str(outdir), str(PPTX_SOURCE)])
        exported = outdir / "argus_teaser.pdf"
        if not exported.is_file():
            raise SystemExit("LibreOffice did not produce argus_teaser.pdf")
        run(
            [
                ghostscript,
                "-q",
                "-dNOPAUSE",
                "-dBATCH",
                "-dSAFER",
                "-dAutoRotatePages=/None",
                "-sDEVICE=pdfwrite",
                "-dCompatibilityLevel=1.5",
                "-dPDFSETTINGS=/prepress",
                f"-sOutputFile={PDF_OUT}",
                str(exported),
            ]
        )

    run(["pdftocairo", "-svg", str(PDF_OUT), str(SVG_OUT)])
    run(["pdftoppm", "-singlefile", "-png", "-r", "180", str(PDF_OUT), str(PNG_OUT.with_suffix(""))])

    HTML_OUT.write_text(
        """<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><title>Argus teaser preview</title>
<style>html,body{margin:0;background:#FBF7EE}object{display:block;width:100vw;height:auto}</style>
</head><body><object type=\"image/svg+xml\" data=\"argus_teaser.svg\">Argus teaser</object></body></html>
""",
        encoding="utf-8",
    )

    manifest = {
        "schema": "argus-teaser/v2",
        "figure": "argus_teaser",
        "reader_question": "How does Argus organize recurrent work, and what task-native results characterize the runtime?",
        "claim": "Argus couples a recurrent four-role control loop with persistent shared state and reports results across seven task-native evaluation cards.",
        "evidence": [
            "evidence/website_results.json",
            "evidence/swebench_pro/unified_experiment_summary.json",
        ],
        "editable_sources": [
            "argus_teaser.pptx",
            "argus_teaser.source.svg",
            "ppt_master/argus_teaser_wechat_20260719/design_spec.md",
        ],
        "vector_export": "argus_teaser.pdf",
        "raster_preview": "argus_teaser.png",
        "renderer": Path(office).name,
        "notes": "All seven cards retain independent task-native scales; no formulas appear in the teaser.",
    }
    MANIFEST_OUT.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(PDF_OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
