#!/usr/bin/env python3
"""Deterministic manuscript auditor for Argus paper tasks.

Checks compiled PDF + LaTeX/BibTeX sources for the layout, font, citation,
page-budget, and formatting defects that make a paper look unfinished:
1. LaTeX log: fatal errors, undefined citations/references, Overfull \\hbox > threshold.
2. PDF metadata & text: glued title words across linebreaks, unresolved [?] / ??,
   unintended CJK characters in English manuscripts.
3. Fonts (pdffonts): unembedded fonts and Type 3 bitmap fonts.
4. Hero figure placement: warns/fails if Figure 1 lands past page 2 in conference papers.
5. Body page budget (--max-body-pages): detects orphan body lines spilling onto page N+1
   before References, or an underfilled final body page.
6. Sparse interior pages (pdftoppm -gray): flags pages before the final page whose vertical
   ink span is below threshold (typically caused by stray \\clearpage or unplaced floats).
7. Source hygiene (--tex-dir): unreferenced figure/table \\label{}s and uncited .bib keys.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run_cmd(cmd: list[str], timeout: int = 60) -> str:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
        return res.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def check_log(log_path: Path, overfull_pt: float, errors: list[str], warnings: list[str]) -> None:
    if not log_path.is_file():
        warnings.append(f"LaTeX log not found at {log_path}; skipped log checks")
        return
    log = log_path.read_text(errors="replace")
    undef_cit = sorted(set(re.findall(r"Citation `([^']+)' on page", log)))
    if undef_cit:
        errors.append("Undefined citations in log: " + ", ".join(undef_cit[:12]))
    undef_ref = sorted(set(re.findall(r"Reference `([^']+)' on page", log)))
    if undef_ref:
        errors.append("Undefined references in log: " + ", ".join(undef_ref[:12]))
    if "LaTeX Warning: There were multiply-defined labels" in log:
        errors.append("Multiply-defined LaTeX labels detected in log")
    bad_boxes = []
    for match in re.finditer(r"Overfull \\hbox \(([0-9.]+)pt too wide\) in paragraph at lines ([0-9-]+)", log):
        pts = float(match.group(1))
        if pts > overfull_pt:
            bad_boxes.append(f"{pts:.1f}pt (lines {match.group(2)})")
    if bad_boxes:
        errors.append(f"Overfull \\hbox > {overfull_pt:.1f}pt ({len(bad_boxes)}): " + ", ".join(bad_boxes[:8]))


def check_fonts(pdf: Path, errors: list[str]) -> None:
    if not shutil.which("pdffonts"):
        return
    out = run_cmd(["pdffonts", str(pdf)])
    lines = out.splitlines()[2:]
    type3, unembedded = [], []
    for line in lines:
        if not line.strip():
            continue
        if re.search(r"\bType 3\b", line):
            type3.append(line.split()[0])
        cols = line.split()
        # Columns end with: emb sub uni object ID (e.g. yes yes yes 134 0)
        if len(cols) >= 5 and cols[-5] in ("yes", "no") and cols[-5] == "no":
            unembedded.append(cols[0])
    if type3:
        errors.append("Type 3 bitmap fonts found (set pdf.fonttype=42 in matplotlib): " + ", ".join(sorted(set(type3))[:8]))
    if unembedded:
        errors.append("Unembedded fonts found in PDF: " + ", ".join(sorted(set(unembedded))[:8]))


def check_pdf_text_and_budget(
    pdf: Path,
    lang: str,
    max_body_pages: int,
    hero_max_page: int,
    errors: list[str],
    warnings: list[str],
) -> tuple[int, int | None]:
    total_pages = 0
    if shutil.which("pdfinfo"):
        info = run_cmd(["pdfinfo", str(pdf)])
        m_pages = re.search(r"^Pages:\s+(\d+)", info, re.M)
        if m_pages:
            total_pages = int(m_pages.group(1))
        m_title = re.search(r"^Title:\s*(.+)$", info, re.M)
        if m_title:
            title = m_title.group(1).strip()
            # Detect accidental glued lowercase+lowercase across linebreaks is hard,
            # but glued word before common prepositions or camel-glued linebreaks shows up often.
            glued = re.findall(r"\b[A-Z][a-z]{3,}(?:under|with|from|via|for|over|into|across|through|without)\b", title)
            if glued:
                errors.append(f"PDF metadata Title has glued words ({', '.join(glued)}); use \\texorpdfstring in \\title")

    if not shutil.which("pdftotext"):
        return total_pages, None

    layout = run_cmd(["pdftotext", "-layout", str(pdf), "-"])
    pages = layout.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()

    marks = re.findall(r"\[\?[^\]]*\]|\?\?", layout)
    if marks:
        errors.append(f"Rendered PDF contains {len(marks)} unresolved [?] or ?? markers")

    if lang == "en":
        cjk = re.findall(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", layout)
        if cjk:
            errors.append(f"English manuscript contains {len(cjk)} CJK character(s) in rendered PDF")

    fig1_page = None
    for idx, page in enumerate(pages, 1):
        if re.search(r"\b(?:Figure|Fig\.)\s*1\s*[.:]", page):
            fig1_page = idx
            break
    if hero_max_page > 0 and fig1_page is not None:
        if fig1_page > hero_max_page:
            errors.append(
                f"Figure 1 appears on page {fig1_page} (max allowed: page {hero_max_page}); "
                "anchor the hero teaser on page 1 (or top of page 2)"
            )
        elif fig1_page == 2:
            warnings.append("Figure 1 is on page 2; page 1 above or beside the abstract/intro has stronger visual impact")

    ref_page = None
    ref_line_idx = None
    for idx, page in enumerate(pages, 1):
        lines = page.splitlines()
        hit = next(
            (
                i
                for i, ln in enumerate(lines)
                if re.match(r"^\s*(?:\d+\.?\s+)?(References|REFERENCES|Bibliography|Impact Statement|Broader Impact|Acknowledgments?)\s*$", ln.strip())
                or re.match(r"^\s*(References|REFERENCES)\s{3,}", ln)
            ),
            None,
        )
        if hit is not None:
            ref_page, ref_line_idx = idx, hit
            break

    if max_body_pages > 0 and ref_page is not None:
        lines = pages[ref_page - 1].splitlines()
        before = [ln for ln in lines[:ref_line_idx] if ln.strip() and not re.match(r"^\s*\d+\s*$", ln)]
        # Exclude running header line at index 0
        body_before = before[1:] if len(before) > 1 else before
        if ref_page == max_body_pages + 1 and 0 < len(body_before) <= 16:
            errors.append(
                f"Main body spills {len(body_before)} line(s) onto page {ref_page} before References; "
                f"tighten layout so Conclusion finishes cleanly on page {max_body_pages}"
            )
        elif ref_page > max_body_pages + 1 or (ref_page == max_body_pages + 1 and len(body_before) > 16):
            errors.append(
                f"Main body exceeds the {max_body_pages}-page limit (References starts on page {ref_page})"
            )
        elif ref_page <= max_body_pages:
            last_body_idx = ref_page if body_before else ref_page - 1
            if last_body_idx < max_body_pages:
                warnings.append(
                    f"Main body ends on page {last_body_idx}, short of the {max_body_pages}-page budget"
                )
            elif not body_before:
                # References starts at top of page max_body_pages + 1 -> check fill of page max_body_pages
                pass

    return total_pages or len(pages), ref_page


def check_sparse_pages(pdf: Path, pages_text: list[str], threshold: float, warnings: list[str]) -> None:
    if not shutil.which("pdftoppm"):
        return
    with tempfile.TemporaryDirectory(prefix="argus-pdf-sparse-") as tmp:
        subprocess.run(
            ["pdftoppm", "-r", "40", "-gray", str(pdf), f"{tmp}/p"],
            capture_output=True,
            timeout=120,
        )
        pgm_files = sorted(Path(tmp).glob("p-*.pgm"), key=lambda p: int(p.stem.split("-")[-1]))
        if len(pgm_files) <= 1:
            return
        sparse = []
        for path in pgm_files[:-1]:
            num = int(path.stem.split("-")[-1])
            page_str = pages_text[num - 1] if num <= len(pages_text) else ""
            # Skip the tail page right before Appendix if it is the end of References
            if re.search(r"\b(?:doi:|arXiv:|Proceedings of|Journal of)\b", page_str) and num + 1 <= len(pages_text):
                next_str = pages_text[num]
                if re.search(r"^\s*[A-Z](?:\.\d+)?\s+\S", next_str, re.M) or "Appendix" in next_str:
                    continue
            raw = path.read_bytes()
            parts = raw.split(maxsplit=4)
            if len(parts) < 5:
                continue
            width, height, pixels = int(parts[1]), int(parts[2]), parts[4]
            pixels = pixels[-width * height :]
            top, bottom = int(height * 0.08), int(height * 0.92)
            x0, x1 = int(width * 0.10), int(width * 0.90)
            used = sum(1 for y in range(top, bottom) if min(pixels[y * width + x0 : y * width + x1]) < 235)
            ratio = used / max(1, bottom - top)
            if ratio < threshold:
                sparse.append(f"p.{num} ({ratio * 100:.0f}%)")
        if sparse:
            warnings.append(
                "Sparse interior pages (<"
                + f"{threshold * 100:.0f}% vertical fill; check stray \\clearpage or unplaced floats): "
                + ", ".join(sparse[:10])
            )


def check_sources(tex_dir: Path, errors: list[str], warnings: list[str]) -> None:
    tex_files = [p for p in sorted(tex_dir.rglob("*.tex")) if not any(part.startswith("_") for part in p.parts)]
    if not tex_files:
        return
    combined = "\n".join(
        re.sub(r"(?<!\\)%.*", "", p.read_text(errors="replace")) for p in tex_files
    )
    labels = set(re.findall(r"\\label\{((?:fig|tab):[^}]+)\}", combined))
    refs = set()
    for group in re.findall(r"\\(?:c|C|auto|eq|page)?ref\*?\{([^}]+)\}", combined):
        for item in group.split(","):
            refs.add(item.strip())
    unref = sorted(labels - refs)
    if unref:
        errors.append("Figures/tables with \\label never referenced in text: " + ", ".join(unref[:10]))

    cited = set()
    for group in re.findall(r"\\cite[a-zA-Z*]*\s*(?:\[[^\]]*\]\s*)*\{([^}]+)\}", combined):
        for key in group.split(","):
            if key.strip():
                cited.add(key.strip())
    if r"\nocite{*}" in combined:
        warnings.append("\\nocite{*} is present; cite only works actually discussed in the manuscript")

    bib_keys = set()
    for bib in sorted(tex_dir.rglob("*.bib")):
        text = bib.read_text(errors="replace")
        for match in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
            if match.group(1).lower() not in ("comment", "string", "preamble"):
                bib_keys.add(match.group(2).strip())
    uncited = sorted(bib_keys - cited)
    if uncited and r"\nocite{*}" not in combined:
        warnings.append(f"Uncited entries in .bib ({len(uncited)}): " + ", ".join(uncited[:10]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit compiled academic paper PDF and LaTeX sources.")
    parser.add_argument("pdf", type=Path, help="Path to compiled main.pdf")
    parser.add_argument("--tex-dir", type=Path, default=None, help="Directory holding main.tex and .bib files")
    parser.add_argument("--lang", choices=("en", "zh"), default="en", help="Manuscript language (default: en)")
    parser.add_argument("--max-body-pages", type=int, default=8, help="Main-body page limit before References (0 to skip)")
    parser.add_argument("--hero-max-page", type=int, default=2, help="Latest page allowed for Figure 1 (0 to skip)")
    parser.add_argument("--overfull-pt", type=float, default=3.0, help="Max tolerated Overfull \\hbox in pt (default: 3.0)")
    parser.add_argument("--sparse-threshold", type=float, default=0.55, help="Min vertical fill for interior pages")
    args = parser.parse_args()

    if not args.pdf.is_file():
        print(f"FAIL: PDF not found: {args.pdf}", file=sys.stderr)
        return 1

    tex_dir = args.tex_dir or args.pdf.parent
    log_path = args.pdf.with_suffix(".log")
    errors: list[str] = []
    warnings: list[str] = []

    check_log(log_path, args.overfull_pt, errors, warnings)
    check_fonts(args.pdf, errors)
    total_pages, ref_page = check_pdf_text_and_budget(
        args.pdf, args.lang, args.max_body_pages, args.hero_max_page, errors, warnings
    )
    if shutil.which("pdftotext"):
        pages_text = run_cmd(["pdftotext", "-layout", str(args.pdf), "-"]).split("\f")
        check_sparse_pages(args.pdf, pages_text, args.sparse_threshold, warnings)
    if tex_dir.is_dir():
        check_sources(tex_dir, errors, warnings)

    print(
        f"Paper audit: {args.pdf} ({total_pages} pages"
        + (f", References on p.{ref_page}" if ref_page else "")
        + f") -> {len(errors)} error(s), {len(warnings)} warning(s)"
    )
    for err in errors:
        print(f"  ERROR: {err}")
    for warn in warnings:
        print(f"  WARN:  {warn}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
