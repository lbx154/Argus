"""Outcome check for the physics vertical's compiled paper deliverable.

The manuscript stage is complete when its requested paper has a non-empty LaTeX
source and a current, valid-looking compiled PDF. Scientific quality, novelty,
structure, evidence sufficiency, and presentation are judged by the independent
Reviewer from the deliverable itself; they are not encoded as proxy quotas here.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

MANUSCRIPT_SOURCE = Path("MANUSCRIPT.tex")
MANUSCRIPT_PDF = Path("MANUSCRIPT.pdf")


def _nonempty(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def verify_compiled_manuscript(project_root: object) -> list[str]:
    """Return concrete failures of the requested compiled-paper outcome."""
    root = Path(str(project_root or "."))
    source = root / MANUSCRIPT_SOURCE
    pdf = root / MANUSCRIPT_PDF
    failures: list[str] = []

    if not _nonempty(source):
        failures.append(f"missing/empty {MANUSCRIPT_SOURCE}")
    if not _nonempty(pdf):
        failures.append(f"missing/empty {MANUSCRIPT_PDF}")
        return failures

    try:
        with pdf.open("rb") as stream:
            if not stream.read(5).startswith(b"%PDF-"):
                failures.append(f"{MANUSCRIPT_PDF} is not a PDF produced by a successful build")
        if _nonempty(source) and pdf.stat().st_mtime < source.stat().st_mtime:
            failures.append(f"{MANUSCRIPT_PDF} is older than {MANUSCRIPT_SOURCE}; rebuild the paper")
    except OSError as exc:
        failures.append(f"cannot inspect {MANUSCRIPT_PDF}: {exc}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="physics-manuscript")
    parser.add_argument("command", choices=["check"], nargs="?", default="check")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args(argv)

    failures = verify_compiled_manuscript(args.project_root)
    if failures:
        print("manuscript stage: compiled paper is not ready:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("manuscript stage: compiled paper is ready")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["MANUSCRIPT_SOURCE", "MANUSCRIPT_PDF", "verify_compiled_manuscript", "main"]
