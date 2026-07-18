#!/usr/bin/env python3
"""Render a self-contained HTML figure to vector PDF with headless Chrome."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_html", type=Path)
    parser.add_argument("output_pdf", type=Path)
    parser.add_argument("--virtual-time-budget-ms", type=int, default=3000)
    args = parser.parse_args()
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        parser.error("google-chrome or chromium is required")
    source = args.input_html.resolve()
    target = args.output_pdf.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        chrome,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--hide-scrollbars",
        f"--virtual-time-budget={args.virtual_time_budget_ms}",
        f"--print-to-pdf={target}",
        "--print-to-pdf-no-header",
        source.as_uri(),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode or not target.exists() or target.stat().st_size == 0:
        raise SystemExit(f"Chrome render failed: {completed.stderr}")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
