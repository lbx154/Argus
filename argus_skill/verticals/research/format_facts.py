"""Layout-aware paper-format fact extractor for the research vertical.

Given a paper PDF (an exemplar or this paper's own ``main.pdf``), compute
a small set of objective, comparable structure metrics:

* ``total_pages``
* ``section_count``  + per-section page-start
* ``figure_count``, ``table_count`` from real PDF layout blocks when possible,
  with in-text references as a fallback
* two-column and blank-page diagnostics
* page content coverage and extraction reliability
* ``citation_count`` (in-text cite occurrences from extracted text)
* ``citations_per_page`` (density)
* ``abstract_chars``
* ``related_work_chars``
* ``conclusion_chars``
* ``references_page`` (first page where bibliography begins) and
  ``references_pages`` (count of body pages dedicated to references)

The extractor reports observations; it does not decide whether a paper is good
or force it to imitate an exemplar. Reviewer judgment owns that decision.

Used by:

* ``argus_skill.verticals.research.format_facts`` CLI (run on any PDF)
* ``argus_skill.verticals.research.exemplar_grounding`` enforces that each exemplar
  has ``format_facts`` and the paper's own facts are within reasonable
  tolerances of the primary exemplar's.

CLI:
    python -m argus_skill.verticals.research.format_facts <pdf> [--json]
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Re-use pdf_chat as the fallback so missing layout dependencies stay explicit.
from ...tools.pdf_chat import _extract

# Review-mode ACL PDFs often include line numbers and two-column spillover
# on the same extracted line, so section headings may appear either at the
# start of a line or after a wide intra-line gap. We therefore accept an
# optional line-number / section-number prefix and stop the match before the
# next wide spacing run instead of requiring a clean one-column line.
_RE_SECTION_HEAD = re.compile(
    r"(?m)(?:^|\x0c|[ \t]{6,})[ \t]*"
    r"(?:(?:\d+|[A-Z])\s+){0,3}"
    r"((?:Abstract|Introduction|Background|Related Works?|Method(?:s|ology)?|"
    r"Approach|Model|Experiments?|Experimental Setup|Setup|Results?|"
    r"Analysis|Ablations?|Discussion|Failure Cases?|Limitations?|"
    r"Ethical Considerations?|Conclusions?|References|Bibliography|"
    r"Reproducibility Appendix|Appendix(?:\s*[A-Z])?|"
    r"Acknowledg(?:e?)ments?))"
    r"(?=(?:[ \t]{2,}|\s*$))",
    re.IGNORECASE,
)

_RE_FIGURE_REF = re.compile(
    r"\b(?:Fig(?:ure)?\.?|FIG\.?)\s*(\d+)", re.IGNORECASE
)
_RE_TABLE_REF = re.compile(r"\bTable\s*(\d+)", re.IGNORECASE)
# Loose author-year and numeric citation patterns. Author-year:
# `(Smith and Jones, 2024)` or `(Smith et al., 2024; Lee, 2023)`. Numeric:
# `[12]` / `[3, 5, 9]`. Avoid matching `[a]` markup or `(1)` equation
# numbers by demanding a 4-digit year inside parens.
_RE_CITE_AUTHOR_YEAR = re.compile(
    r"\([^()]*?(?:19|20)\d{2}[a-z]?[^()]*?\)"
)
_RE_CITE_NUMERIC = re.compile(r"\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]")

_REFERENCES_TITLES = ("references", "bibliography")
_ABSTRACT_TITLES = ("abstract",)
_INTRO_TITLES = ("introduction",)
_RELATED_TITLES = ("related work", "background and related work", "prior work")
_CONCLUSION_TITLES = ("conclusion", "conclusions", "discussion and conclusion")


@dataclass
class FormatFacts:
    source: str
    extraction_method: str = "text_fallback"
    layout_reliable: bool = False
    total_pages: int = 0
    section_titles: list[str] = field(default_factory=list)
    section_count: int = 0
    figure_count: int = 0
    figure_max_index: int = 0
    table_count: int = 0
    table_max_index: int = 0
    citation_count: int = 0
    citations_per_page: float = 0.0
    abstract_chars: int = 0
    intro_chars: int = 0
    related_work_chars: int = 0
    conclusion_chars: int = 0
    references_page: int | None = None
    references_pages: int = 0
    body_pages_before_references: int = 0
    image_count: int = 0
    detected_table_count: int = 0
    two_column_pages: int = 0
    blank_pages: int = 0
    content_coverage_mean: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _section_spans(text: str) -> list[tuple[str, int, int]]:
    """Return ``[(title, start, end)]`` for every top-level section heading."""
    matches = list(_RE_SECTION_HEAD.finditer(text))
    spans = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        spans.append((title, start, end))
    return spans


def _find_section_chars(spans, title_keywords) -> int:
    title_keywords = tuple(k.lower() for k in title_keywords)
    for title, start, end in spans:
        t = title.lower()
        if any(k in t for k in title_keywords):
            return end - start
    return 0


def _canonical_section_title(title: str) -> str:
    """Map literal headings to coarse venue-level section buckets."""
    t = re.sub(r"\s+", " ", title.strip().lower())
    if t.startswith("abstract"):
        return "abstract"
    if t.startswith("introduction") or t.startswith("background"):
        return "introduction"
    if t.startswith("related work") or t.startswith("related works"):
        return "related work"
    if (
        t.startswith("method")
        or t.startswith("approach")
        or t.startswith("model")
    ):
        return "method"
    if t.startswith("experiment") or t.startswith("setup"):
        return "experiments"
    if (
        t.startswith("result")
        or t.startswith("analysis")
        or t.startswith("ablation")
        or t.startswith("discussion")
        or t.startswith("failure case")
    ):
        return "results_and_analysis"
    if t.startswith("conclusion"):
        return "conclusion"
    if (
        t.startswith("limitation")
        or t.startswith("ethical consideration")
        or t.startswith("acknowledg")
    ):
        return "end_matter"
    if t.startswith("reference") or t.startswith("bibliography"):
        return "references"
    if "appendix" in t:
        return "appendix"
    return t


def _page_of_offset(text: str, offset: int) -> int:
    """Pages are split on form-feed ``\\x0c``. Return 1-indexed page number."""
    if offset <= 0:
        return 1
    return text.count("\x0c", 0, offset) + 1


def _count_unique_indexed(matches) -> tuple[int, int]:
    """Return (count_unique_indices, max_index) from regex matches that
    capture an integer in group 1."""
    nums = set()
    for m in matches:
        try:
            nums.add(int(m.group(1)))
        except (TypeError, ValueError):
            continue
    if not nums:
        return 0, 0
    return len(nums), max(nums)


@dataclass
class _LayoutObservations:
    text: str
    total_pages: int
    image_count: int
    table_count: int
    two_column_pages: int
    blank_pages: int
    content_coverage_mean: float
    reliable: bool
    warnings: list[str] = field(default_factory=list)


def _looks_two_column(
    blocks: list[tuple[float, float, float, float, str]],
    page_width: float,
    page_height: float,
) -> bool:
    """Detect two substantial, vertically overlapping text columns."""
    useful = [
        block
        for block in blocks
        if len(block[4].strip()) >= 40
        and (block[2] - block[0]) <= page_width * 0.72
    ]
    left = [block for block in useful if block[2] <= page_width * 0.60]
    right = [block for block in useful if block[0] >= page_width * 0.40]
    if not left or not right:
        return False
    left_top, left_bottom = min(b[1] for b in left), max(b[3] for b in left)
    right_top, right_bottom = min(b[1] for b in right), max(b[3] for b in right)
    overlap = max(0.0, min(left_bottom, right_bottom) - max(left_top, right_top))
    return overlap >= page_height * 0.20


def _layout_observations(pdf_path: Path) -> _LayoutObservations | None:
    """Read real page geometry with PyMuPDF; return ``None`` when unavailable."""
    # `import fitz` prints its deprecation notice on stdout, which lands in the
    # middle of `--json` output and makes it unparseable. The modern name is
    # quiet; the old one stays as a fallback for older PyMuPDF.
    try:
        import pymupdf as fitz  # type: ignore[import-not-found]
    except ImportError:
        try:
            import fitz  # type: ignore[import-not-found]
        except ImportError:
            return None

    warnings: list[str] = []
    page_texts: list[str] = []
    image_hashes: set[str] = set()
    detected_tables = 0
    two_column_pages = 0
    blank_pages = 0
    coverages: list[float] = []
    try:
        document = fitz.open(pdf_path)
    except Exception as exc:  # noqa: BLE001
        return _LayoutObservations(
            text="",
            total_pages=0,
            image_count=0,
            table_count=0,
            two_column_pages=0,
            blank_pages=0,
            content_coverage_mean=0.0,
            reliable=False,
            warnings=[f"layout extraction failed: {type(exc).__name__}: {exc}"],
        )

    try:
        for page in document:
            page_dict = page.get_text("dict", sort=True)
            page_text = page.get_text("text", sort=True)
            page_texts.append(page_text)
            page_area = max(float(page.rect.width * page.rect.height), 1.0)
            text_blocks: list[tuple[float, float, float, float, str]] = []
            occupied_area = 0.0
            page_images = 0
            for block in page_dict.get("blocks", []):
                bbox = block.get("bbox") or ()
                if len(bbox) != 4:
                    continue
                x0, y0, x1, y1 = (float(value) for value in bbox)
                area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
                occupied_area += area
                if block.get("type") == 1:
                    if area >= page_area * 0.01:
                        image = block.get("image")
                        digest_source = (
                            bytes(image)
                            if isinstance(image, (bytes, bytearray))
                            else repr((round(x1 - x0), round(y1 - y0))).encode()
                        )
                        image_hashes.add(hashlib.sha256(digest_source).hexdigest())
                        page_images += 1
                    continue
                lines = block.get("lines") or []
                text = "".join(
                    str(span.get("text") or "")
                    for line in lines
                    for span in (line.get("spans") or [])
                )
                if text.strip():
                    text_blocks.append((x0, y0, x1, y1, text))
            if _looks_two_column(
                text_blocks,
                float(page.rect.width),
                float(page.rect.height),
            ):
                two_column_pages += 1
            if len(page_text.strip()) < 40 and page_images == 0:
                blank_pages += 1
            coverages.append(min(1.0, occupied_area / page_area))
            try:
                # PyMuPDF 1.27 prints an optional-package recommendation to
                # stdout here, which would corrupt this module's JSON CLI.
                with contextlib.redirect_stdout(io.StringIO()):
                    detected_tables += len(page.find_tables().tables)
            except Exception:  # noqa: BLE001
                warnings.append(
                    f"table detection unavailable on page {page.number + 1}"
                )
    finally:
        document.close()

    nonempty_pages = sum(bool(text.strip()) for text in page_texts)
    # Prefer the text fallback when layout extraction loses whole pages. Newer
    # PyMuPDF versions can return a technically non-empty but visibly partial
    # stream for minimal or unusual PDFs; treating half the pages as sufficient
    # produced confident zero-section reports. Allow an occasional blank page,
    # not systematic loss.
    allowed_blank_pages = max(1, len(page_texts) // 10)
    reliable = (
        bool(page_texts)
        and blank_pages <= allowed_blank_pages
        and nonempty_pages >= max(1, len(page_texts) - allowed_blank_pages)
    )
    if not reliable:
        warnings.append("layout text extraction was sparse; verify the PDF visually")
    return _LayoutObservations(
        text="\x0c".join(page_texts),
        total_pages=len(page_texts),
        image_count=len(image_hashes),
        table_count=detected_tables,
        two_column_pages=two_column_pages,
        blank_pages=blank_pages,
        content_coverage_mean=(
            round(sum(coverages) / len(coverages), 3) if coverages else 0.0
        ),
        reliable=reliable,
        warnings=list(dict.fromkeys(warnings)),
    )


def extract_format_facts(pdf_path: Path) -> FormatFacts:
    """Compute structured format facts for ``pdf_path``."""
    layout = _layout_observations(pdf_path)
    if layout is not None and layout.reliable:
        text, page_count = layout.text, layout.total_pages
        facts = FormatFacts(
            source=str(pdf_path),
            extraction_method="pymupdf_layout",
            layout_reliable=True,
            total_pages=page_count,
            image_count=layout.image_count,
            detected_table_count=layout.table_count,
            two_column_pages=layout.two_column_pages,
            blank_pages=layout.blank_pages,
            content_coverage_mean=layout.content_coverage_mean,
            warnings=layout.warnings,
        )
    else:
        text, page_count = _extract(pdf_path)
        facts = FormatFacts(source=str(pdf_path), total_pages=page_count)
        if layout is None:
            facts.warnings.append(
                "PyMuPDF unavailable; layout facts use text-only fallback"
            )
        else:
            facts.warnings.extend(layout.warnings)

    spans = _section_spans(text)
    facts.section_titles = [t for t, _, _ in spans]
    facts.section_count = len(
        {_canonical_section_title(t) for t in facts.section_titles}
    )

    # Figures / tables (in-text references)
    referenced_figures, facts.figure_max_index = _count_unique_indexed(
        _RE_FIGURE_REF.finditer(text)
    )
    referenced_tables, facts.table_max_index = _count_unique_indexed(
        _RE_TABLE_REF.finditer(text)
    )
    facts.figure_count = max(facts.image_count, referenced_figures)
    facts.table_count = max(facts.detected_table_count, referenced_tables)

    # Citations
    cite_count = (
        sum(1 for _ in _RE_CITE_AUTHOR_YEAR.finditer(text))
        + sum(1 for _ in _RE_CITE_NUMERIC.finditer(text))
    )
    facts.citation_count = cite_count
    if page_count > 0:
        facts.citations_per_page = round(cite_count / page_count, 2)

    # Section character counts (excluding the heading itself)
    facts.abstract_chars = _find_section_chars(spans, _ABSTRACT_TITLES)
    facts.intro_chars = _find_section_chars(spans, _INTRO_TITLES)
    facts.related_work_chars = _find_section_chars(spans, _RELATED_TITLES)
    facts.conclusion_chars = _find_section_chars(spans, _CONCLUSION_TITLES)

    # References page
    for title, start, end in spans:
        if any(k in title.lower() for k in _REFERENCES_TITLES):
            facts.references_page = _page_of_offset(text, start)
            ref_text = text[start:end]
            facts.references_pages = ref_text.count("\x0c") + 1
            break
    if facts.references_page is not None:
        facts.body_pages_before_references = max(
            0, facts.references_page - 1
        )
    else:
        facts.body_pages_before_references = page_count

    return facts


# ---------------------------------------------------------------------------
# Diff helpers (used by exemplar_grounding gate)
# ---------------------------------------------------------------------------


# Advisory comparison windows. They highlight large structural differences for
# Reviewer inspection; they never decide paper quality.
DEFAULT_TOLERANCES: dict[str, dict] = {
    # numeric_field: {abs: <int>, rel: <float 0..1>}
    "total_pages":               {"abs": 2, "rel": 0.40},
    "section_count":             {"abs": 2, "rel": 0.50},
    "figure_count":              {"abs": 2, "rel": 0.70},
    "table_count":               {"abs": 2, "rel": 0.70},
    "citations_per_page":        {"abs": 2.0, "rel": 0.80},
    "body_pages_before_references": {"abs": 2, "rel": 0.40},
}


@dataclass
class DiffFinding:
    field: str
    paper_value: float
    exemplar_value: float
    delta_abs: float
    delta_rel: float
    within_tolerance: bool


def diff_against_exemplar(
    paper: dict,
    exemplar: dict,
    tolerances: dict[str, dict] | None = None,
) -> list[DiffFinding]:
    """Compare two FormatFacts dicts on the dimensions in ``tolerances``.

    Returns one observation per checked field. Missing fields are skipped.
    """
    tol = tolerances or DEFAULT_TOLERANCES
    findings: list[DiffFinding] = []
    for field_name, limits in tol.items():
        p = paper.get(field_name)
        e = exemplar.get(field_name)
        if p is None or e is None:
            continue
        try:
            p_v = float(p)
            e_v = float(e)
        except (TypeError, ValueError):
            continue
        delta_abs = abs(p_v - e_v)
        denom = max(abs(e_v), 1e-6)
        delta_rel = delta_abs / denom
        within = (
            delta_abs <= limits.get("abs", float("inf"))
            or delta_rel <= limits.get("rel", float("inf"))
        )
        findings.append(DiffFinding(
            field=field_name,
            paper_value=p_v,
            exemplar_value=e_v,
            delta_abs=round(delta_abs, 3),
            delta_rel=round(delta_rel, 3),
            within_tolerance=within,
        ))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", help="path to a paper PDF")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of human text")
    parser.add_argument("--write", type=Path, default=None,
                        help="also write JSON to this path")
    args = parser.parse_args(argv)

    pdf = Path(args.pdf).expanduser()
    if not pdf.exists():
        print(f"error: PDF not found: {pdf}", file=sys.stderr)
        return 2

    facts = extract_format_facts(pdf)
    data = facts.to_dict()

    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(f"Format facts for {facts.source}")
        print(f"  extraction:         {facts.extraction_method}")
        print(f"  layout reliable:    {facts.layout_reliable}")
        print(f"  total pages:        {facts.total_pages}")
        print(f"  sections:           {facts.section_count}  ({facts.section_titles})")
        print(f"  figures (refs):     {facts.figure_count} (max idx {facts.figure_max_index})")
        print(f"  tables (refs):      {facts.table_count} (max idx {facts.table_max_index})")
        print(f"  citations:          {facts.citation_count}  ({facts.citations_per_page}/page)")
        print(f"  abstract chars:     {facts.abstract_chars}")
        print(f"  intro chars:        {facts.intro_chars}")
        print(f"  related-work chars: {facts.related_work_chars}")
        print(f"  conclusion chars:   {facts.conclusion_chars}")
        print(f"  references at page: {facts.references_page}")
        print(f"  references pages:   {facts.references_pages}")
        print(f"  body pages:         {facts.body_pages_before_references}")
        print(f"  detected images:    {facts.image_count}")
        print(f"  detected tables:    {facts.detected_table_count}")
        print(f"  two-column pages:   {facts.two_column_pages}")
        print(f"  blank pages:        {facts.blank_pages}")
        print(f"  content coverage:   {facts.content_coverage_mean}")
        for warning in facts.warnings:
            print(f"  warning:            {warning}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
