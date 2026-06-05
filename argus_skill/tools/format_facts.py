"""Structured paper-format fact extractor.

Given a paper PDF (an exemplar or this paper's own ``main.pdf``), compute
a small set of objective, comparable structure metrics:

* ``total_pages``
* ``section_count``  + per-section page-start
* ``figure_count``, ``table_count`` (counted from in-text references like
  ``Figure 3`` / ``Table 2`` — best-effort regex; misses purely
  side-anchored floats but captures the discussion shape, which is what
  reviewers actually grade)
* ``citation_count`` (in-text cite occurrences from extracted text)
* ``citations_per_page`` (density)
* ``abstract_chars``
* ``related_work_chars``
* ``conclusion_chars``
* ``references_page`` (first page where bibliography begins) and
  ``references_pages`` (count of body pages dedicated to references)

The contract is deliberately narrow: facts a reviewer can eyeball on a
PDF and an automated diff can compare. We do NOT try to extract figure
images or render LaTeX — those are out of scope.

Used by:

* ``argus_skill.tools.format_facts`` CLI (run on any PDF)
* ``argus_skill.skills.exemplar_grounding`` enforces that each exemplar
  has ``format_facts`` and the paper's own facts are within reasonable
  tolerances of the primary exemplar's.

CLI:
    python -m argus_skill.tools.format_facts <pdf> [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Re-use pdf_chat's extraction so we don't fork the pdftotext/pypdf logic.
from .pdf_chat import _extract  # type: ignore[attr-defined]

# Same heading detector as pdf_chat — keep a local copy so this module
# stays usable even if pdf_chat's regex changes.
_RE_SECTION_HEAD = re.compile(
    r"^\s*((?:[A-Z]\.|[0-9]+(?:\.[0-9]+)*)?\s*"
    r"(?:Abstract|Introduction|Background|Related Work|Method(?:s|ology)?|"
    r"Approach|Model|Experiments?|Experimental Setup|Setup|Results?|"
    r"Analysis|Ablations?|Discussion|Limitations?|Conclusions?|"
    r"References|Bibliography|Appendix(?:\s*[A-Z])?|Acknowledg(?:e?)ments?))\s*$",
    re.MULTILINE | re.IGNORECASE,
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


def extract_format_facts(pdf_path: Path) -> FormatFacts:
    """Compute structured format facts for ``pdf_path``."""
    text, page_count = _extract(pdf_path)
    facts = FormatFacts(source=str(pdf_path), total_pages=page_count)

    spans = _section_spans(text)
    facts.section_titles = [t for t, _, _ in spans]
    # De-duplicate near-identical titles (e.g. case + whitespace variants)
    facts.section_count = len({t.strip().lower() for t in facts.section_titles})

    # Figures / tables (in-text references)
    facts.figure_count, facts.figure_max_index = _count_unique_indexed(
        _RE_FIGURE_REF.finditer(text)
    )
    facts.table_count, facts.table_max_index = _count_unique_indexed(
        _RE_TABLE_REF.finditer(text)
    )

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


# Tolerance contract for conformance check. Generous on purpose — the
# floor is "you wrote a paper that looks like a paper in the same venue",
# not "you matched a specific exemplar's dimensions to the page". A
# missing key on either side counts as a "skip", not a failure.
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

    Returns one DiffFinding per checked field. Fields missing on either
    side are skipped (not a violation — we don't penalise an exemplar
    that wasn't run through this extractor in the same version).
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
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
