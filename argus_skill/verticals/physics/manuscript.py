"""Mandatory research-paper (manuscript-stage) delivery contract for physics.

The physics vertical is FIVE stages: ``scope -> model -> execute -> review ->
manuscript``. ``manuscript`` is the hard terminal stage: a completed physics
mission's deliverable is a standard, discipline-agnostic research-paper package —
NOT a scope/model/execute/review stage log. This module is the paper-package
verifier + reviewer contract for that terminal stage.

The terminal deliverable is organised in THREE layers:

* **Verification source layer** — machine-checkable evidence: MANUSCRIPT.md,
  CLAIMS.csv, FIGURE_LEGENDS.md, REFERENCES.bib/references.md, METHODS_DETAIL.md,
  REPRODUCIBILITY.md, REVIEW.md, figures/ (and, by convention, tables/,
  source_data/, scripts/). Checked by :func:`verify_manuscript_deliverables`.
* **Paper composition layer** — a journal-style, LaTeX-compiled paper:
  MANUSCRIPT.tex, MANUSCRIPT.pdf, SUPPLEMENT.tex, SUPPLEMENT.pdf, and
  PAPER_BUILD_LOG.md. Checked by :func:`verify_paper_style_deliverables`.
* **Optional presentation layer** — HTML_DEMO/index.html or PRESENTATION/index.html
  for a manager-facing view. This layer is GUIDANCE ONLY: it never affects the
  manuscript gate, so its absence does not fail the verifier.

There is no optional "paper-target" mode, no marker file, and no environment
variable: the verifiers ALWAYS check and the CLI ALWAYS fails closed when a
deliverable is missing.

Design principle: paper structure is read PRIMARILY from the LaTeX source
(``MANUSCRIPT.tex`` / ``SUPPLEMENT.tex``), because LaTeX commands and
environments are unambiguous; extracted PDF text is used only as an auxiliary,
confirming signal. The gate does NOT lock the paper into one citation style, one
section title, or an exact word count. It fails on *obvious* defects (missing
files, no citations at all, no equations, tables absent, captions absurdly long,
engineering/workflow tokens leaking into the paper, dangerously thin sections);
everything graded (upper word bands, style choice, caption 80-180 target,
per-section polish) is the reviewer's job — see :func:`manuscript_review_items`.

Nothing here is tied to any physics subfield. Every rule applies equally to
theory, numerics, experiment, computation, data analysis, instrumentation,
materials, astrophysics, fluids, quantum, condensed matter, etc. — the contract
only ever names generic research-paper artifacts (manuscript, figures, legends,
references, claim ledger, reproducibility, availability statements, supplement),
never a specific model, material, particle, topology, instrument, or object.

Two consumers share these verifiers:
  * System A — the ``manuscript`` stage shell check runs ``manuscript check``
    (this module's CLI): fails closed on any missing/incomplete deliverable.
  * System B — the reviewer markdown checklist (:func:`manuscript_review_items`)
    audits paper-level quality the shell cannot (section logic, no-overclaim,
    figure->claim binding, methods reproducibility, graded word bands).
"""
from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# The delivery contract (what the terminal manuscript stage requires).         #
# --------------------------------------------------------------------------- #
MIN_FIGURES = 6
MIN_REFERENCES = 8

#: Manuscript functional modules. Each entry is (label, (heading synonyms,)).
#: A Nature/Science-style research article, not a "scope/model/execute/review"
#: stage log. Synonyms keep the contract discipline-agnostic.
MANUSCRIPT_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Abstract/Summary", ("abstract", "summary")),
    ("Introduction", ("introduction",)),
    ("Background/Related Work", ("background", "related work", "prior work", "literature")),
    ("Model/Theory/System", ("model", "theory", "physical system", "formulation", "governing")),
    ("Methods", ("methods", "methodology", "materials and methods", "experimental setup", "numerical methods", "computational")),
    ("Results", ("results", "findings")),
    ("Discussion", ("discussion",)),
    ("Limitations", ("limitations", "caveats", "threats to validity")),
    ("Conclusion", ("conclusion", "concluding")),
    ("References", ("references", "bibliography")),
    ("Data Availability", ("data availability", "data and code availability", "data & code availability")),
    ("Code Availability", ("code availability", "data and code availability", "data & code availability")),
)

#: Required CLAIMS ledger columns (a claim <-> evidence <-> boundary map).
CLAIMS_COLUMNS: tuple[str, ...] = (
    "claim_id",
    "claim_text",
    "claim_type",
    "evidence_type",
    "evidence_pointer",
    "status",
    "boundary",
    "reviewer_notes",
)

#: The exact CLAIMS.csv header line agents must emit (no synonyms accepted).
CLAIMS_HEADER: str = ",".join(CLAIMS_COLUMNS)

#: Common wrong column names -> the exact column they must be renamed to. These
#: are NOT accepted as synonyms; the message tells the agent to rename them.
_CLAIMS_RENAME_HINTS: tuple[tuple[str, str], ...] = (
    ("claim", "claim_text"),
    ("evidence", "evidence_pointer (and add evidence_type)"),
    ("notes", "reviewer_notes"),
    ("claim_status", "status"),
)

#: Source-layer deliverable files (relative path, human description). The HTML
#: demo is NOT here: presentation is an optional layer that never gates.
REQUIRED_FILES: tuple[tuple[str, str], ...] = (
    ("MANUSCRIPT.md", "Nature/Science-style research manuscript (not a stage log)"),
    ("FIGURE_LEGENDS.md", "formal figure legends (title, panels, symbols, provenance, claim)"),
    ("CLAIMS.csv", "claim<->evidence<->boundary ledger"),
    ("REPRODUCIBILITY.md", "commands, versions, seeds, params, data, runtime, provenance"),
    ("METHODS_DETAIL.md", "supplementary methods sufficient to reproduce"),
    ("REVIEW.md", "reviewer audit incl. a paper-style delivery audit section"),
)

#: Paper-composition-layer deliverable files.
PAPER_REQUIRED_FILES: tuple[tuple[str, str], ...] = (
    ("MANUSCRIPT.tex", "LaTeX source of the paper main text"),
    ("MANUSCRIPT.pdf", "compiled journal-style PDF (non-empty, text-based)"),
    ("SUPPLEMENT.tex", "LaTeX source of the supplement"),
    ("SUPPLEMENT.pdf", "compiled supplement PDF (non-empty, text-based)"),
    ("PAPER_BUILD_LOG.md", "LaTeX build log: engine, passes, warnings, versions"),
)

# --------------------------------------------------------------------------- #
# Paper-style thresholds (single source of truth; tests import these).         #
# The structure is read from the .tex; the PDF text is only a confirming        #
# signal. Thresholds are deliberately lenient to avoid false failures.          #
# --------------------------------------------------------------------------- #
MIN_CITE_COMMANDS = 8          # \cite-family call sites in MANUSCRIPT.tex
MIN_DISPLAY_EQUATIONS = 4      # numbered display-equation environments in .tex
MIN_EQ_CITATIONS = 3           # in-text equation-number references in the PDF
MIN_MAIN_TABLES = 2            # table floats in MANUSCRIPT.tex
MIN_SUPP_TABLES = 2            # table floats in SUPPLEMENT.tex
MIN_TABLE_CITATIONS = 2        # distinct "Table N" references in the PDF
MIN_SUPP_CITATIONS = 3         # "Supplementary ..." references in the PDF
FIGURE_CAPTION_HARD_CAP = 250  # per-figure caption word hard cap
MIN_INTRO_WORDS = 600          # Introduction anti-thin floor (MANUSCRIPT.md)
MIN_RESULTS_WORDS = 1200       # Results anti-thin floor (MANUSCRIPT.md)
RAW_MATH_SIMPLE_MAX = 6        # tolerated count of simple ASCII subscripts in body
FIG_TEX_END_FRACTION = 0.85    # figures beyond this fraction of body == "dumped at end"

#: The reviewer-side heading that MUST appear in REVIEW.md (and nowhere in the
#: paper itself). Pinned exactly, like CLAIMS_HEADER, across agent-facing text.
PAPER_AUDIT_HEADING = "Paper-Style Delivery Audit"

#: Supplement content categories, checked by SYNONYM (never by exact title).
SUPPLEMENT_CONTENT: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Reproducibility", ("reproducib", "reproduction", "reproduce", "computational detail", "environment version", "seeds")),
    ("Claim audit", ("claim audit", "claim-audit", "evidence ledger", "claim-evidence", "claim to evidence", "claim/evidence")),
    ("Methods detail", ("supplementary methods", "methods detail", "method detail", "numerical detail", "derivation", "additional methods")),
)

#: Engineering/workflow tokens that must never appear in the paper's main text
#: (matched case-insensitively, anywhere before the References section).
MAIN_TEXT_FORBIDDEN_ALWAYS: tuple[str, ...] = (
    "artifact", "verifier", "stage_check", "project_done", "argus",
    "workspace", "generated by", "source table",
    "claims.csv", "review.md", "methods_detail.md", "reproducibility.md",
)

#: Path/extension tokens allowed only inside Data/Code availability (and the
#: Supplement, which is a separate PDF) — forbidden in the main narrative.
MAIN_TEXT_FORBIDDEN_PATHS: tuple[str, ...] = ("scripts/", "data/", ".json", ".csv")

_FIG_RE = re.compile(r"^fig(?:ure)?[ _-]?0*(\d+)", re.IGNORECASE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")

# LaTeX-structure regexes (read from the .tex source).
_CITE_RE = re.compile(
    r"\\(?:cite|citep|citet|citealp|citealt|citenum|citeauthor|supercite|autocite|textcite|parencite|footcite)\b"
)
_BIB_MECHANISM_RE = re.compile(
    r"\\bibliography\{|\\printbibliography|\\begin\{thebibliography\}|\\bibitem\b"
)
_EQ_ENV_RE = re.compile(r"\\begin\{(?:equation|align|gather|multline|eqnarray)\}")
_TABLE_ENV_RE = re.compile(r"\\begin\{table\*?\}")
_TABULAR_RE = re.compile(r"\\begin\{tabular\}")
_FIGURE_ENV_RE = re.compile(r"\\begin\{figure\*?\}")
_BIB_OFFSET_RE = re.compile(
    r"\\bibliography\{|\\printbibliography|\\begin\{thebibliography\}|\\section\*?\{\s*references",
    re.IGNORECASE,
)

# PDF-text (auxiliary) regexes.
_PDF_REFS_RE = re.compile(r"(?i)\b(references|bibliography)\b")
_AVAIL_RE = re.compile(r"(?i)data (?:and|&) code availability|data availability|code availability")
_EQ_CITE_RE = re.compile(r"(?i)\beq(?:s|n|ns)?\.?\s*\(?\s*\d+|\bequations?\s+\(?\s*\d+")
_TABLE_REF_RE = re.compile(r"(?i)\btable\s*~?\s*(\d+)")
_SUPP_CITE_RE = re.compile(
    r"(?i)\bsupp(?:lementary|lement|l)?\.?\s*"
    r"(?:section|table|figure|fig\.?|methods?|note|materials?|information|eq(?:uation)?)\b"
)
_CITE_CMD_IN_PDF_RE = re.compile(r"\\cite")
_BIBKEY_LEAK_RE = re.compile(r"\[[A-Za-z][A-Za-z]+\d{4}\w*\]")
_ABS_PATH_RE = re.compile(r"(?:^|[\s(=])/(?:home|data|Users|tmp|mnt|var|root|opt|srv)/\S+", re.MULTILINE)
_RAW_MATH_OBVIOUS: tuple[re.Pattern[str], ...] = (
    re.compile(r"exp\(\s*-?\s*i", re.IGNORECASE),
    re.compile(r"\bsum_[A-Za-z0-9]"),
    re.compile(r"\bint_[A-Za-z0-9]"),
    re.compile(r"\bprod_[A-Za-z0-9]"),
    re.compile(r"[A-Za-z]\w*_[A-Za-z0-9]+\s*=\s*[A-Za-z0-9]"),
)
_RAW_MATH_SIMPLE_RE = re.compile(r"[A-Za-z]_[A-Za-z0-9]")


# --------------------------------------------------------------------------- #
# Small helpers                                                                #
# --------------------------------------------------------------------------- #
def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _headings(text: str) -> list[str]:
    return [m.group(2).strip().lower() for m in (_HEADING_RE.match(ln) for ln in text.splitlines()) if m]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _figure_numbers(figures_dir: Path) -> set[int]:
    nums: set[int] = set()
    if not figures_dir.is_dir():
        return nums
    for child in figures_dir.iterdir():
        if not child.is_file():
            continue
        m = _FIG_RE.match(child.name)
        if m:
            nums.add(int(m.group(1)))
    return nums


def _reference_count(root: Path) -> int:
    bib = root / "REFERENCES.bib"
    if bib.is_file():
        try:
            return len(re.findall(r"(?m)^\s*@\w+\s*\{", bib.read_text(encoding="utf-8")))
        except OSError:
            return 0
    md = root / "references.md"
    if md.is_file():
        try:
            body = md.read_text(encoding="utf-8")
        except OSError:
            return 0
        # count list entries: [1] .. / 1. .. / - .. — excluding NEEDS_VERIFICATION lines.
        entries = [
            ln for ln in body.splitlines()
            if re.match(r"^\s*(\[\d+\]|\d+\.|[-*])\s+\S", ln)
            and "NEEDS_VERIFICATION" not in ln.upper()
        ]
        return len(entries)
    return 0


def _pdf_text(path: Path) -> str | None:
    """Extract text from a PDF (``pdftotext -layout`` then ``pypdf``).

    Returns the extracted text, or ``None`` when neither extractor yields any
    text. Callers treat ``None`` on an existing, non-empty PDF as a failure
    (image-only or broken PDF), never as a silent skip.
    """
    if not path.is_file():
        return None
    exe = shutil.which("pdftotext")
    if exe:
        try:
            result = subprocess.run(
                [exe, "-layout", "-q", str(path), "-"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(str(path))
        chunks = []
        for page in reader.pages:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:  # pragma: no cover - defensive per-page guard
                continue
        text = "\n".join(chunks)
        if text.strip():
            return text
    except Exception:  # pragma: no cover - pypdf optional / malformed pdf
        pass
    return None


def _section_word_count(md_text: str, synonyms: tuple[str, ...]) -> int | None:
    """Word count of the first MANUSCRIPT.md section whose heading matches a
    synonym, up to the next same-or-higher-level heading. ``None`` if absent."""
    lines = md_text.splitlines()
    target_idx: int | None = None
    target_level = 0
    for i, ln in enumerate(lines):
        m = _HEADING_RE.match(ln)
        if not m:
            continue
        if any(syn in m.group(2).strip().lower() for syn in synonyms):
            target_idx = i
            target_level = len(m.group(1))
            break
    if target_idx is None:
        return None
    body: list[str] = []
    for ln in lines[target_idx + 1:]:
        m = _HEADING_RE.match(ln)
        if m and len(m.group(1)) <= target_level:
            break
        body.append(ln)
    return len(" ".join(body).split())


def _figure_caption_wordcounts(legends_text: str) -> dict[int, int]:
    """Per-figure caption word counts, split on 'Figure N' / 'Fig. N' markers."""
    start = re.compile(r"(?im)^\s*#*\s*(?:figure|fig)\.?\s*0*(\d+)\b")
    counts: dict[int, int] = {}
    cur: int | None = None
    buf: list[str] = []
    for ln in legends_text.splitlines():
        m = start.match(ln)
        if m:
            if cur is not None:
                counts[cur] = len(" ".join(buf).split())
            cur = int(m.group(1))
            buf = [ln]
        elif cur is not None:
            buf.append(ln)
    if cur is not None:
        counts[cur] = len(" ".join(buf).split())
    return counts


def _region(raw: str, start_re: re.Pattern[str], end_re: re.Pattern[str]) -> str:
    ms = start_re.search(raw)
    if not ms:
        return ""
    me = end_re.search(raw, ms.end())
    return raw[ms.start(): me.start() if me else len(raw)]


# --------------------------------------------------------------------------- #
# Source layer (System A, part 1): the machine-checkable research package.     #
# --------------------------------------------------------------------------- #
def verify_manuscript_deliverables(project_root: object) -> list[str]:
    """Return a list of source-layer contract violations (empty == satisfied).

    ALWAYS runs — the manuscript stage is the physics vertical's mandatory
    terminal deliverable, so there is no inactive pass-through. Structural +
    deterministic only; deeper paper-quality judgement (no-overclaim,
    figure->claim binding, reproducible methods) is the reviewer's job — see
    :func:`manuscript_review_items`.
    """
    root = Path(str(project_root or "."))
    failures: list[str] = []

    # (a) required files exist and are non-empty
    for rel, desc in REQUIRED_FILES:
        p = root / rel
        if not p.is_file() or p.stat().st_size == 0:
            failures.append(f"missing/empty {rel} ({desc})")

    # (b) manuscript is a real paper: every functional module present
    manuscript = root / "MANUSCRIPT.md"
    if manuscript.is_file():
        try:
            heads = _headings(manuscript.read_text(encoding="utf-8"))
        except OSError:
            heads = []
        blob = " || ".join(heads)
        for label, synonyms in MANUSCRIPT_SECTIONS:
            if not any(syn in blob for syn in synonyms):
                failures.append(f"MANUSCRIPT.md missing a '{label}' section")

    # (c) >= MIN_FIGURES numbered figure files
    fig_nums = _figure_numbers(root / "figures")
    if len(fig_nums) < MIN_FIGURES:
        failures.append(
            f"figures/ has {len(fig_nums)} numbered figures; need >= {MIN_FIGURES} "
            f"(fig1_*, fig2_*, ... fig{MIN_FIGURES}_*)"
        )

    # (d) >= MIN_REFERENCES resolvable references
    n_refs = _reference_count(root)
    if n_refs < MIN_REFERENCES:
        failures.append(
            f"REFERENCES.bib/references.md has {n_refs} entries; need >= {MIN_REFERENCES} "
            f"(exclude NEEDS_VERIFICATION placeholders; do not fabricate)"
        )

    # (e) CLAIMS.csv carries the full ledger schema — exact column names, no synonyms
    claims = root / "CLAIMS.csv"
    if claims.is_file():
        try:
            with claims.open(newline="", encoding="utf-8") as fh:
                header = next(csv.reader(fh), [])
        except (OSError, StopIteration):
            header = []
        detected = [c.strip() for c in header]
        present = {c.lower() for c in detected}
        missing = [c for c in CLAIMS_COLUMNS if c not in present]
        if missing:
            renames = [
                f"rename '{wrong}' -> '{right}'"
                for wrong, right in _CLAIMS_RENAME_HINTS
                if wrong in present and right.split()[0] in missing
            ]
            failures.append(
                "CLAIMS.csv header is wrong (exact column names are required; "
                "synonyms are NOT accepted). "
                f"expected: {CLAIMS_HEADER} | "
                f"detected: {','.join(detected) or '(none)'} | "
                f"missing: {', '.join(missing)}. "
                "Common mistakes 'claim' and 'evidence' are rejected and must be renamed"
                + (f" ({'; '.join(renames)})" if renames else "")
                + "."
            )

    return failures


# --------------------------------------------------------------------------- #
# Paper composition layer (System A, part 2): the journal-style PDF paper.     #
# --------------------------------------------------------------------------- #
def verify_paper_style_deliverables(project_root: object) -> list[str]:
    """Return a list of paper-composition-layer violations (empty == satisfied).

    Structure is read primarily from ``MANUSCRIPT.tex`` / ``SUPPLEMENT.tex``;
    extracted PDF text is an auxiliary, confirming signal. All messages are
    prefixed ``"[paper] "``. Thresholds are lenient so a legitimate paper is not
    blocked; graded quality lives in :func:`manuscript_review_items`.
    """
    root = Path(str(project_root or "."))
    failures: list[str] = []

    def fail(msg: str) -> None:
        failures.append(f"[paper] {msg}")

    # (1-4) required paper files exist and are non-empty
    for rel, desc in PAPER_REQUIRED_FILES:
        p = root / rel
        if not p.is_file() or p.stat().st_size == 0:
            fail(f"missing/empty {rel} ({desc})")

    tex = _read_text(root / "MANUSCRIPT.tex")
    supp_tex = _read_text(root / "SUPPLEMENT.tex")

    # Extract PDF text once (fail-closed only for an existing, non-empty PDF).
    def _extract(name: str) -> str | None:
        p = root / name
        if p.is_file() and p.stat().st_size > 0:
            text = _pdf_text(p)
            if text is None:
                fail(
                    f"{name} produced no extractable text (need a text-based PDF; "
                    "pdftotext and pypdf both failed)"
                )
            return text
        return None

    man_raw = _extract("MANUSCRIPT.pdf")
    supp_raw = _extract("SUPPLEMENT.pdf")
    pdf = _norm(man_raw) if man_raw else ""

    # ---- structural checks read from MANUSCRIPT.tex ---------------------- #
    if tex:
        # (5a) a bibliography mechanism is present
        if not _BIB_MECHANISM_RE.search(tex):
            fail(
                "MANUSCRIPT.tex has no bibliography mechanism "
                "(\\bibliography{...}, \\printbibliography, or \\begin{thebibliography})"
            )
        # (7a) enough citation call sites (style-agnostic: [n] or superscript)
        n_cite = len(_CITE_RE.findall(tex))
        if n_cite < MIN_CITE_COMMANDS:
            fail(
                f"MANUSCRIPT.tex has {n_cite} \\cite-family citations; need >= "
                f"{MIN_CITE_COMMANDS} (numbered [n] or superscript, resolved via "
                "\\cite/\\citep/\\citet/\\supercite)"
            )
        # (8a) numbered display equations
        n_eq = len(_EQ_ENV_RE.findall(tex))
        if n_eq < MIN_DISPLAY_EQUATIONS:
            fail(
                f"MANUSCRIPT.tex has {n_eq} numbered display-equation environments; "
                f"need >= {MIN_DISPLAY_EQUATIONS} (non-starred equation/align/gather/"
                "multline/eqnarray, each \\label'd)"
            )
        # (9) main tables
        n_tab = len(_TABLE_ENV_RE.findall(tex))
        if n_tab < MIN_MAIN_TABLES:
            n_tab = max(n_tab, len(_TABULAR_RE.findall(tex)))
        if n_tab < MIN_MAIN_TABLES:
            fail(
                f"MANUSCRIPT.tex has {n_tab} table floats; need >= {MIN_MAIN_TABLES} "
                "main tables (\\begin{table} or table*)"
            )
        # (12) figures must not all be dumped at the end
        _check_figures_not_at_end(tex, fail)

    # ---- SUPPLEMENT.tex structural checks -------------------------------- #
    if supp_tex:
        n_supp_tab = len(_TABLE_ENV_RE.findall(supp_tex))
        if n_supp_tab < MIN_SUPP_TABLES:
            n_supp_tab = max(n_supp_tab, len(_TABULAR_RE.findall(supp_tex)))
        if n_supp_tab < MIN_SUPP_TABLES:
            fail(
                f"SUPPLEMENT.tex has {n_supp_tab} table floats; need >= "
                f"{MIN_SUPP_TABLES} supplementary tables"
            )

    # ---- Supplement content categories (by synonym, never exact title) --- #
    supp_blob = (_norm(supp_raw) + " " + _norm(supp_tex)).lower()
    if supp_blob.strip():
        for label, synonyms in SUPPLEMENT_CONTENT:
            if not any(s in supp_blob for s in synonyms):
                fail(
                    f"SUPPLEMENT is missing a '{label}' section "
                    f"(accepted synonyms: {', '.join(synonyms)})"
                )

    # ---- confirming checks read from MANUSCRIPT.pdf text ----------------- #
    if pdf:
        refs = list(_PDF_REFS_RE.finditer(pdf))
        ref_off = refs[-1].start() if refs else None
        avail_m = _AVAIL_RE.search(pdf)
        avail_off = avail_m.start() if avail_m else None
        before_refs = pdf[:ref_off] if ref_off is not None else pdf
        before_avail = pdf[:avail_off] if avail_off is not None else before_refs

        # (5b) References section present in the PDF
        if ref_off is None:
            fail("MANUSCRIPT.pdf has no References/Bibliography section heading")

        # (7b) no unresolved BibTeX key / raw \cite leaking into the PDF
        if _CITE_CMD_IN_PDF_RE.search(pdf) or _BIBKEY_LEAK_RE.search(pdf):
            fail(
                "MANUSCRIPT.pdf leaks an unresolved citation (a literal \\cite{...} or "
                "an author-year key such as [Su1979Solitons]); compile the bibliography "
                "so citations render as numbers"
            )

        # (8b) in-text equation-number references
        n_eq_cite = len(_EQ_CITE_RE.findall(before_refs))
        if n_eq_cite < MIN_EQ_CITATIONS:
            fail(
                f"MANUSCRIPT.pdf references equation numbers {n_eq_cite} time(s) "
                f"(e.g. 'Eq. (3)'); need >= {MIN_EQ_CITATIONS}"
            )

        # (8c) no obvious un-rendered ASCII math in the main narrative
        math_region = before_avail
        if any(rx.search(math_region) for rx in _RAW_MATH_OBVIOUS):
            fail(
                "MANUSCRIPT.pdf main text contains an un-rendered ASCII formula "
                "(e.g. 'exp(-i H_2 tau_2)', 'sum_n'); render mathematics with LaTeX"
            )
        elif len(_RAW_MATH_SIMPLE_RE.findall(math_region)) >= RAW_MATH_SIMPLE_MAX:
            fail(
                "MANUSCRIPT.pdf main text has many un-rendered subscript tokens like "
                f"'H_1' (>= {RAW_MATH_SIMPLE_MAX}); render mathematics with LaTeX"
            )

        # (10) tables referenced in text
        n_tab_ref = len(set(_TABLE_REF_RE.findall(before_refs)))
        if n_tab_ref < MIN_TABLE_CITATIONS:
            fail(
                f"MANUSCRIPT.pdf references {n_tab_ref} distinct table(s) (e.g. "
                f"'Table 1'); need >= {MIN_TABLE_CITATIONS}"
            )

        # (11) every numbered figure is cited
        fig_nums = _figure_numbers(root / "figures")
        k = min(len(fig_nums), MIN_FIGURES)
        missing_figs = [
            i for i in range(1, k + 1)
            if not re.search(rf"(?i)\bfig(?:ure)?\.?\s*0*{i}\b", before_refs)
        ]
        if missing_figs:
            fail(
                f"MANUSCRIPT.pdf does not cite figure(s) {missing_figs} "
                "(reference each numbered figure as 'Fig. N' near its discussion)"
            )

        # (14) forbidden strings — tier A everywhere, tier B outside availability
        low_before_refs = before_refs.lower()
        low_before_avail = before_avail.lower()
        for tok in MAIN_TEXT_FORBIDDEN_ALWAYS:
            if tok in low_before_refs:
                fail(
                    f"MANUSCRIPT.pdf main text contains the forbidden token {tok!r} "
                    "(engineering/workflow terms must not appear in the paper)"
                )
        for tok in MAIN_TEXT_FORBIDDEN_PATHS:
            if tok in low_before_avail:
                fail(
                    f"MANUSCRIPT.pdf main text contains the path/extension token "
                    f"{tok!r} (allowed only inside Data/Code availability or the Supplement)"
                )

        # (15) Data & Code availability present + clean
        combined = re.search(r"(?i)data (?:and|&) code availability", pdf)
        has_data = bool(combined) or bool(re.search(r"(?i)data availability", pdf))
        has_code = bool(combined) or bool(re.search(r"(?i)code availability", pdf))
        if not (has_data and has_code):
            fail("MANUSCRIPT.pdf is missing a Data availability and/or Code availability statement")
        if man_raw:
            avail_raw = _region(man_raw, _AVAIL_RE, _PDF_REFS_RE)
            if avail_raw:
                if _ABS_PATH_RE.search(avail_raw):
                    fail(
                        "Data/Code availability contains an absolute local path; describe "
                        "data/code in words and move paths to the Supplement"
                    )
                if any(len(line) > 200 for line in avail_raw.splitlines()):
                    fail(
                        "Data/Code availability contains a long command/text block "
                        "(>200 chars on a line); move detailed commands to the Supplement"
                    )

        # (16a) the Supplement is cross-referenced from the main text
        n_supp_cite = len(_SUPP_CITE_RE.findall(before_refs))
        if n_supp_cite < MIN_SUPP_CITATIONS:
            fail(
                f"MANUSCRIPT.pdf cites the Supplement {n_supp_cite} time(s) "
                "(Supplementary Section/Table/Figure/Methods); need >= "
                f"{MIN_SUPP_CITATIONS}"
            )

    # (13) figure captions within the hard word cap
    legends = _read_text(root / "FIGURE_LEGENDS.md")
    if legends:
        for n, wc in _figure_caption_wordcounts(legends).items():
            if wc > FIGURE_CAPTION_HARD_CAP:
                fail(
                    f"figure {n} caption in FIGURE_LEGENDS.md is {wc} words "
                    f"(> {FIGURE_CAPTION_HARD_CAP}-word hard cap)"
                )

    # (W) section thickness floors (anti-thin only; no upper cap)
    md_text = _read_text(root / "MANUSCRIPT.md")
    if md_text:
        intro = _section_word_count(md_text, ("introduction",))
        if intro is not None and intro < MIN_INTRO_WORDS:
            fail(f"MANUSCRIPT.md Introduction is {intro} words; need >= {MIN_INTRO_WORDS} (too thin)")
        results = _section_word_count(md_text, ("results", "findings"))
        if results is not None and results < MIN_RESULTS_WORDS:
            fail(f"MANUSCRIPT.md Results is {results} words; need >= {MIN_RESULTS_WORDS} (too thin)")

    # (18) REVIEW.md carries the pinned paper-style delivery audit section
    review = root / "REVIEW.md"
    if review.is_file():
        if PAPER_AUDIT_HEADING.lower() not in " || ".join(_headings(_read_text(review))):
            fail(f"REVIEW.md is missing the required '{PAPER_AUDIT_HEADING}' section heading")

    return failures


def _check_figures_not_at_end(tex: str, fail) -> None:
    """Fail (strongly) only if every figure environment is placed after the
    bibliography, or beyond ``FIG_TEX_END_FRACTION`` of the pre-bibliography
    body. Lenient by design: ordinary LaTeX float drift must not mis-fire."""
    positions = [m.start() for m in _FIGURE_ENV_RE.finditer(tex)]
    if not positions:
        return  # figure *files* are checked in the source layer
    bib = _BIB_OFFSET_RE.search(tex)
    bib_off = bib.start() if bib else len(tex)
    body_len = bib_off if bib_off > 0 else len(tex)
    all_after_bib = all(p >= bib_off for p in positions)
    all_beyond_frac = all(p >= body_len * FIG_TEX_END_FRACTION for p in positions)
    if all_after_bib or all_beyond_frac:
        fail(
            f"all {len(positions)} figure environment(s) in MANUSCRIPT.tex are dumped at "
            "the end; place each figure near the text that discusses it"
        )


def verify_all_deliverables(project_root: object) -> list[str]:
    """Both layers: the source research package + the paper composition."""
    return verify_manuscript_deliverables(project_root) + verify_paper_style_deliverables(project_root)


# --------------------------------------------------------------------------- #
# Reviewer contract text (System B: LLM audit prose).                          #
# --------------------------------------------------------------------------- #
def manuscript_review_items() -> str:
    """The mandatory manuscript-stage audit (generic research rules, no subfield)."""
    return (
        "MANUSCRIPT AUDIT (terminal stage — the physics mission's final deliverable "
        "is a Nature/Science-style research-paper package delivered in three layers: a "
        "machine-checkable source layer, a LaTeX-compiled paper layer (MANUSCRIPT.tex/pdf "
        "+ SUPPLEMENT.tex/pdf + PAPER_BUILD_LOG.md), and an OPTIONAL presentation layer "
        "that never gates). Require and audit: "
        "(1) MANUSCRIPT.md with a cross-disciplinary Abstract/Summary, an Introduction "
        "stating the gap, a results-organized main text, reproducible Methods, a "
        "figure-driven Results narrative, Discussion, explicit Limitations, Conclusion, "
        "real resolvable References, and Data & Code Availability statements. "
        "(2) >= 6 numbered figures with formal legends (title, panel labels, axis/units, "
        "uncertainty/statistics, data/script provenance, the claim each supports). "
        "(3) >= 8 real, resolvable references matching in-text citations; unverifiable "
        "sources marked NEEDS_VERIFICATION (only in REVIEW.md or the Supplement), never "
        "fabricated into the reference list. "
        "(4) a CLAIMS.csv ledger binding every headline claim to equation/figure/table/"
        "script/dataset/citation with a supported|partial|no-go|inconclusive|unknown "
        "status and a boundary; CLAIMS.csv MUST use exactly this 8-column header (no "
        "synonyms; 'claim' and 'evidence' are rejected and must be renamed): "
        "claim_id,claim_text,claim_type,evidence_type,evidence_pointer,status,boundary,reviewer_notes. "
        "(5) REPRODUCIBILITY.md (commands, versions, seeds, parameter ranges, input/"
        "generated data, figure scripts, runtime, agent/human provenance). "
        "(6) PAPER COMPOSITION: MANUSCRIPT.tex compiling to a journal-style MANUSCRIPT.pdf "
        "and SUPPLEMENT.tex to SUPPLEMENT.pdf, with PAPER_BUILD_LOG.md. Default profile is "
        "a two-column article layout (physics_two_column_article); use a single-column "
        "12pt double-spaced review draft (broad_science_review_draft) only when the task "
        "asks for a Nature/Science initial-submission style. Target section thickness: "
        "Abstract 150-220, Introduction 700-1000, Model/Theory 600-1000, Methods 500-900, "
        "Results 1400-2300 (>= 3 sub-sections, each citing a figure or table), Discussion "
        "600-1100 (>= 3 paragraphs: interpretation, relation to prior work, open points), "
        "Limitations 300-700 (>= 3 distinct kinds), Conclusion 200-450; total body "
        "3800-5500; formal runs cite 12-30 references. "
        "(7) PAPER LANGUAGE: scientific-paper prose, not an engineering report — no "
        "file-location, script-output, or checker vocabulary in the main text; numbered "
        "citations in a single consistent style; every display equation LaTeX-rendered, "
        "numbered and \\label'd, with >= 3 in-text 'Eq. (n)' references and no raw ASCII "
        "math; figures placed near the text that discusses them and each cited as 'Fig. N'; "
        "captions 80-180 words (<= 250 hard) with no provenance/paths inside them. "
        "(8) SUPPLEMENT carries the technical detail the main text should not: a "
        "reproducibility section, a claim-audit / evidence-ledger section, methods detail, "
        "and supplementary tables, cross-referenced from the main text >= 3 times; script "
        "names, file names, hashes and commands belong here (and in Data/Code availability), "
        "not in the main narrative. "
        "(9) REVIEW.md contains a section titled exactly '## Paper-Style Delivery Audit' "
        "recording the paper-layer verdicts (PDF/Supplement present; citations, equations, "
        "tables, figures, availability, no-overclaim). This heading appears in REVIEW.md "
        "only, never in the paper. "
        "Reject: finite numerics presented as universal proof; synthetic/toy results "
        "presented as real-system or real-experiment validation; workflow metadata used as "
        "physical evidence; unsupported novelty/discovery/first/mechanism/universal/SOTA "
        "claims not bound to evidence and marked supported; prior-literature results "
        "repackaged as new findings without a stated contribution boundary "
        "(reproduction/verification/extension/diagnostic/benchmark); experimental-validation "
        "claims without real measured data; real-system fit claims without real observations; "
        "phase-diagram/general-trend claims from a single parameter point; stability claims "
        "without uncertainty/robustness checks; figures not bound to claims; Methods too "
        "thin to reproduce; missing data/code availability; unit/dimension/boundary/"
        "initial-condition errors."
    )


# --------------------------------------------------------------------------- #
# CLI used by the manuscript-stage shell check (System A). Always fails closed. #
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="physics-manuscript")
    parser.add_argument("command", choices=["check"], nargs="?", default="check")
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--layer", choices=["all", "source", "paper"], default="all",
        help="which delivery layer(s) to verify (default: all)",
    )
    args = parser.parse_args(argv)

    root = Path(args.project_root)
    if args.layer == "source":
        failures = verify_manuscript_deliverables(root)
    elif args.layer == "paper":
        failures = verify_paper_style_deliverables(root)
    else:
        failures = verify_all_deliverables(root)

    if failures:
        print("manuscript stage: research-paper delivery contract NOT satisfied:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("manuscript stage: research-paper delivery contract satisfied")
    return 0


__all__ = [
    "MIN_FIGURES",
    "MIN_REFERENCES",
    "MANUSCRIPT_SECTIONS",
    "CLAIMS_COLUMNS",
    "CLAIMS_HEADER",
    "REQUIRED_FILES",
    "PAPER_REQUIRED_FILES",
    "MIN_CITE_COMMANDS",
    "MIN_DISPLAY_EQUATIONS",
    "MIN_EQ_CITATIONS",
    "MIN_MAIN_TABLES",
    "MIN_SUPP_TABLES",
    "MIN_TABLE_CITATIONS",
    "MIN_SUPP_CITATIONS",
    "FIGURE_CAPTION_HARD_CAP",
    "MIN_INTRO_WORDS",
    "MIN_RESULTS_WORDS",
    "RAW_MATH_SIMPLE_MAX",
    "FIG_TEX_END_FRACTION",
    "PAPER_AUDIT_HEADING",
    "SUPPLEMENT_CONTENT",
    "MAIN_TEXT_FORBIDDEN_ALWAYS",
    "MAIN_TEXT_FORBIDDEN_PATHS",
    "verify_manuscript_deliverables",
    "verify_paper_style_deliverables",
    "verify_all_deliverables",
    "manuscript_review_items",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
