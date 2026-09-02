"""Paper structural minimums validator.

Anti-fab structural gate (the same class as evidence_chain / F4). Rejects
Paper/Review rounds whose ``paper/main.tex`` is structurally
not-a-paper: zero real external figures, no reader-facing Figure 1 overview,
zero in-text citations, zero references, missing Related Work, or missing
Conclusion.

**This is a venue-minimum check, not a quality judgment.** EMNLP/ACL
papers without ≥1 figure, in-text citations, or a Related Work section
do not constitute a valid submission — they fail at the venue regardless
of what any reviewer agent thinks of the prose. That's the harness's job
(see ``docs/PRINCIPLES.md``): the floor is structural; the ceiling is the
reviewer's.

Thresholds here are deliberately well below typical EMNLP norms (e.g.
``min_cited_bib=8`` when a real EMNLP paper has 35+) so the gate only
fires on genuinely broken drafts, not on mediocre-but-formed ones. The
reviewer agent still rules on whether 12 citations is enough for the
topic.

CLI:
    python -m argus_skill.verticals.research.paper_structural_minimums \\
        --project-root .

Exits non-zero (with a human-readable summary on stdout) when any
minimum is violated.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .venue_profiles import VenueProfile

# Venue-floor thresholds. Bump only with the operator's agreement —
# raising these turns the gate into a quality judgment.
MIN_FIGURES = 1
MIN_INTEXT_CITES = 8
MIN_CITED_BIB_ENTRIES = 8
MIN_RELATED_WORK_CHARS = 800

# Figure keywords identify a reader-facing overview directly from the included
# path or figure body.
_TEASER_KEYWORDS = ("teaser", "hero", "figure1", "figure_1", "fig1", "fig_1")
_PIPELINE_KEYWORDS = (
    "pipeline", "method", "architecture", "framework",
    "overview", "system", "workflow", "schematic", "taxonomy",
    "mechanism", "approach", "concept",
)

# Section heading variants we accept. Case-insensitive substring match
# on the brace contents; this is intentionally loose so e.g.
# ``\section*{Related Work and Background}`` still counts.
_RELATED_WORK_TITLES = ("related work", "background and related work", "prior work")
_CONCLUSION_TITLES = ("conclusion", "conclusions", "discussion and conclusion")
_APPENDIX_TITLES = ("appendix", "appendices", "supplementary material",
                    "reproducibility appendix")


# Python's float repr runs to seventeen significant digits and no measurement
# carries that, so a decimal this long in a paper is a value that was printed
# rather than reported: 521/750 reached one draft as 0.6946666666666667.
_RE_FLOAT_REPR = re.compile(r"\d\.\d{10,}")

_RE_INCLUDEGRAPHICS = re.compile(
    r"\\(?:includegraphics|includesvg)\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}"
)
_RE_FIGURE_ENV = re.compile(
    r"\\begin\s*\{\s*figure\*?\s*\}(.*?)"
    r"\\end\s*\{\s*figure\*?\s*\}",
    re.DOTALL,
)
_RE_CITE = re.compile(r"\\cite[a-zA-Z*]*\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
_RE_SECTION = re.compile(r"\\section\*?\s*\{([^}]+)\}")
_RE_BIBKEY = re.compile(r"^\s*@\w+\s*\{\s*([^,\s]+)\s*,", re.MULTILINE)
# `\appendix` command turns subsequent \section into Appendix A, B, …
_RE_APPENDIX_CMD = re.compile(r"\\appendix\b")
_RE_INPUT = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")

# AAAI-only preamble/compliance probes (used only when the venue profile asks).
# _RE_PDFINFO requires the mandatory /TemplateVersion key inside the block (an
# empty \pdfinfo{} is non-compliant); [^}]* spans newlines since [^}] includes \n.
_RE_PDFINFO = re.compile(r"\\pdfinfo\s*\{[^}]*TemplateVersion")
_RE_BIBLIOGRAPHYSTYLE = re.compile(r"\\bibliographystyle\s*\{")
_RE_USEPACKAGE = re.compile(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
_RE_NOCOPYRIGHT = re.compile(r"\\nocopyright\b")
# Require the literal "checklist" — a bare "Reproducibility Statement"/"appendix"
# is not the AAAI Reproducibility Checklist.
_REPRO_CHECKLIST_TITLES = ("reproducibility checklist",)


@dataclass
class StructuralIssue:
    code: str
    detail: str


@dataclass
class StructuralReport:
    main_tex_path: Path | None
    figures_found: int = 0
    figures_missing_files: list[str] = field(default_factory=list)
    cite_keys: set[str] = field(default_factory=set)
    bib_entries: int = 0
    bib_entries_cited: int = 0
    related_work_chars: int = 0
    has_conclusion: bool = False
    has_appendix: bool = False
    included_overview_figures: list[str] = field(default_factory=list)
    issues: list[StructuralIssue] = field(default_factory=list)
    # Rendered, never blocking. "This is not yet a paper" and "here is
    # something worth knowing about the paper" are different statements, and
    # only the first belongs in a gate: a spare figure on disk does not make a
    # manuscript stop being a manuscript.
    notes: list[StructuralIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_text(self) -> str:
        lines = []
        if not self.ok:
            lines.append(
                f"{len(self.issues)} structural minimum(s) violated; "
                f"draft is not yet a paper:"
            )
            for issue in self.issues:
                lines.append(f"  [{issue.code}] {issue.detail}")
            lines.append("")
        for note in self.notes:
            lines.append(f"  ({note.code}) {note.detail}")
        if self.notes:
            lines.append("")
        lines.append("Structural counts:")
        lines.append(f"  figures (\\includegraphics resolved): {self.figures_found}")
        if self.figures_missing_files:
            lines.append(
                f"  figures referenced but file missing: "
                f"{len(self.figures_missing_files)} "
                f"({', '.join(self.figures_missing_files[:3])}"
                f"{'...' if len(self.figures_missing_files) > 3 else ''})"
            )
        lines.append(f"  unique in-text cite keys: {len(self.cite_keys)}")
        lines.append(
            f"  refs.bib entries: {self.bib_entries} "
            f"({self.bib_entries_cited} cited from body)"
        )
        lines.append(f"  related-work prose chars: {self.related_work_chars}")
        lines.append(f"  has conclusion: {self.has_conclusion}")
        lines.append(f"  has appendix: {self.has_appendix}")
        lines.append(
            "  included Figure 1 overview assets: "
            + (
                ", ".join(self.included_overview_figures)
                if self.included_overview_figures
                else "(none)"
            )
        )
        return "\n".join(lines)


def _find_main_tex(project_root: Path) -> Path | None:
    candidates = [
        project_root / "paper" / "main.tex",
        project_root / "paper" / "submission" / "main.tex",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _read_all_tex(main_tex: Path) -> str:
    r"""Read the actual recursive ``\input`` / ``\include`` graph."""
    document_root = main_tex.parent.resolve()
    paper_root = next(
        (parent for parent in (document_root, *document_root.parents) if parent.name == "paper"),
        document_root,
    )
    visited: set[Path] = set()

    def resolve_include(raw: str, including: Path) -> Path | None:
        candidate_raw = Path(raw.strip())
        candidates = (
            document_root / candidate_raw,
            paper_root / candidate_raw,
            including.parent / candidate_raw,
        )
        for candidate in candidates:
            if candidate.suffix == "":
                candidate = candidate.with_suffix(".tex")
            resolved = candidate.resolve()
            try:
                resolved.relative_to(paper_root)
            except ValueError:
                continue
            if resolved.is_file():
                return resolved
        return None

    def expand(path: Path) -> str:
        resolved = path.resolve()
        if resolved in visited:
            return ""
        try:
            resolved.relative_to(paper_root)
        except ValueError:
            return ""
        if not resolved.is_file():
            return ""
        visited.add(resolved)
        text = _strip_comments(
            resolved.read_text(encoding="utf-8", errors="ignore")
        )

        def replace(match: re.Match[str]) -> str:
            child = resolve_include(match.group(1), resolved)
            return expand(child) if child is not None else match.group(0)

        return _RE_INPUT.sub(replace, text)

    return expand(main_tex)


def _strip_comments(tex: str) -> str:
    # Drop LaTeX line comments so a `% \cite{ghost}` example doesn't count.
    out = []
    for line in tex.splitlines():
        idx = 0
        while idx < len(line):
            c = line[idx]
            if c == "\\":
                idx += 2
                continue
            if c == "%":
                break
            idx += 1
        out.append(line[:idx])
    return "\n".join(out)


def _section_span(tex: str, titles: tuple[str, ...]) -> tuple[int, int] | None:
    """Return (start, end) character offsets of a section body, or None."""
    matches = list(_RE_SECTION.finditer(tex))
    for i, m in enumerate(matches):
        title = m.group(1).strip().lower()
        if any(t in title for t in titles):
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(tex)
            return start, end
    return None


def _resolve_figure(paper_dir: Path, ref: str) -> Path | None:
    """Resolve a `\\includegraphics{ref}` to an actual file. LaTeX lets you
    drop the extension; we probe the common ones."""
    ref = ref.strip()
    candidates = [paper_dir / ref]
    if "." not in Path(ref).name:
        for ext in (".pdf", ".png", ".jpg", ".jpeg", ".svg", ".eps"):
            candidates.append(paper_dir / f"{ref}{ext}")
    # Also probe the figures/ subdir if the ref is relative without dir.
    if "/" not in ref:
        candidates.append(paper_dir / "figures" / ref)
        if "." not in Path(ref).name:
            for ext in (".pdf", ".png", ".jpg", ".jpeg", ".svg", ".eps"):
                candidates.append(paper_dir / "figures" / f"{ref}{ext}")
    for c in candidates:
        if c.exists():
            return c
    return None


def _read_bib(paper_dir: Path) -> tuple[int, set[str]]:
    """Return (total_entry_count, set_of_keys) across the common bib paths."""
    keys: set[str] = set()
    total = 0
    for name in ("refs.bib", "references.bib", "bibliography.bib"):
        bib = paper_dir / name
        if not bib.exists():
            continue
        text = bib.read_text(encoding="utf-8", errors="ignore")
        for m in _RE_BIBKEY.finditer(text):
            keys.add(m.group(1).strip())
            total += 1
    return total, keys


def _unreferenced_figures(paper_dir: Path, referenced: set[str]) -> list[str]:
    """Figure files on disk that nothing in the manuscript includes.

    Campaigns draw figures and then leave them there. One had thirty-one images
    beside a paper using four; two others rewrote their manuscripts and dropped
    every figure include, ending at zero with images sitting in the same
    directory. The count alone never said so, and drawing a figure is the
    expensive half. Build artifacts are excluded because they are outputs of
    compiling the paper, not candidates for it.
    """
    # Counted by figure rather than by file: a .svg beside its .pdf is one
    # drawing exported twice, not two unused figures.
    names: dict[str, str] = {}
    for entry in sorted((paper_dir / "figures").glob("*")):
        if entry.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".svg"}:
            continue
        if entry.stem.lower().startswith(("rendered_", "main", "pdf_build")):
            continue
        if entry.name in referenced or entry.stem in referenced:
            continue
        names.setdefault(entry.stem, entry.name)
    return list(names.values())


def validate_paper_structural_minimums(
    project_root: Path,
    *,
    state_root: Path | None = None,
) -> StructuralReport:
    from .venue_profiles import resolve_venue_profile

    venue = None
    venue_error: KeyError | None = None
    try:
        venue = resolve_venue_profile(state_root or project_root)
    except KeyError as exc:
        venue_error = exc
    main_tex = _find_main_tex(project_root)
    if main_tex is None:
        report = StructuralReport(main_tex_path=None)
        if venue_error is not None:
            report.issues.append(
                StructuralIssue(
                    code="unresolved_venue_profile",
                    detail=str(venue_error),
                )
            )
        report.issues.append(
            StructuralIssue(
                code="no_main_tex",
                detail=(
                    "paper/main.tex not found — Paper requires a "
                    "LaTeX source file before structural checks can run"
                ),
            )
        )
        return report

    paper_dir = main_tex.parent
    raw = _read_all_tex(main_tex)
    tex = _strip_comments(raw)

    report = StructuralReport(main_tex_path=main_tex)
    if venue_error is not None:
        report.issues.append(
            StructuralIssue(
                code="unresolved_venue_profile",
                detail=str(venue_error),
            )
        )

    # Figures.
    float_dumps = _RE_FLOAT_REPR.findall(tex)
    if float_dumps:
        report.issues.append(
            StructuralIssue(
                code="unrounded_float_repr",
                detail=(
                    f"{len(float_dumps)} number(s) are printed at full float "
                    f"precision, for example {float_dumps[0]}; round each to the "
                    "precision its evidence supports"
                ),
            )
        )

    figure_refs = [m.group(1) for m in _RE_INCLUDEGRAPHICS.finditer(tex)]
    resolved_figure_paths: dict[str, str] = {}
    for ref in figure_refs:
        resolved = _resolve_figure(paper_dir, ref)
        if resolved is None:
            report.figures_missing_files.append(ref)
        else:
            report.figures_found += 1
            try:
                relative = resolved.resolve().relative_to(project_root.resolve()).as_posix()
                resolved_figure_paths[ref] = relative
            except ValueError:
                report.issues.append(
                    StructuralIssue(
                        code="figure_path_escape",
                        detail=f"figure resolves outside project root: {resolved}",
                    )
                )

    if report.figures_missing_files:
        report.issues.append(
            StructuralIssue(
                code="missing_figure_files",
                detail=(
                    "figure command(s) reference missing files: "
                    + ", ".join(report.figures_missing_files[:5])
                ),
            )
        )

    unused = _unreferenced_figures(
        paper_dir,
        {Path(ref).name for ref in figure_refs} | {Path(ref).stem for ref in figure_refs},
    )
    if unused:
        report.notes.append(
            StructuralIssue(
                code="figures_drawn_but_unused",
                detail=(
                    f"{len(unused)} figure file(s) exist in paper/figures that "
                    f"nothing in main.tex includes, for example {unused[0]}; "
                    "the drawing is already paid for"
                ),
            )
        )

    if report.figures_found < MIN_FIGURES:
        report.issues.append(
            StructuralIssue(
                code="no_figures",
                detail=(
                    f"only {report.figures_found} \\includegraphics figure(s) "
                    f"resolve to real files (minimum {MIN_FIGURES}); research "
                    "papers require at least one figure or system diagram — "
                    "use the research-visualization router before declaring "
                    "the draft done"
                ),
            )
        )

    # Determine figure semantics directly from the manuscript and included files.
    inferred_overview_paths: set[str] = set()
    role_keywords = _TEASER_KEYWORDS + _PIPELINE_KEYWORDS
    for ref, relative in resolved_figure_paths.items():
        signal = re.sub(r"[^a-z0-9]+", " ", ref.lower())
        if any(keyword.replace("_", " ") in signal for keyword in role_keywords):
            inferred_overview_paths.add(relative)
    for match in _RE_FIGURE_ENV.finditer(tex):
        block = match.group(1)
        signal = re.sub(r"[^a-z0-9]+", " ", block.lower())
        if not any(keyword.replace("_", " ") in signal for keyword in role_keywords):
            continue
        for ref_match in _RE_INCLUDEGRAPHICS.finditer(block):
            relative = resolved_figure_paths.get(ref_match.group(1))
            if relative:
                inferred_overview_paths.add(relative)
    report.included_overview_figures = sorted(
        inferred_overview_paths
    )
    if not report.included_overview_figures:
        report.issues.append(
            StructuralIssue(
                code="missing_figure1_overview",
                detail=(
                    "no real external figure embedded in the paper is identified "
                    "as a teaser, method, pipeline, architecture, framework, "
                    "taxonomy, or mechanism overview. Author a reader-facing "
                    "Figure 1 through the Research Visualization Router. image-2 "
                    "is optional: PPT Master, browser-rendered HTML, FigureSpec, "
                    "Draw.io, Mermaid/Graphviz, or another truthful renderer is "
                    "valid. A LaTeX table, prose box, or \\rule bar chart inside "
                    "a figure environment does not satisfy this gate"
                ),
            )
        )
    # Citations (body).
    for m in _RE_CITE.finditer(tex):
        for key in m.group(1).split(","):
            key = key.strip()
            if key:
                report.cite_keys.add(key)

    if len(report.cite_keys) < MIN_INTEXT_CITES:
        report.issues.append(
            StructuralIssue(
                code="too_few_citations",
                detail=(
                    f"only {len(report.cite_keys)} unique \\cite key(s) in "
                    f"body (minimum {MIN_INTEXT_CITES}); a paper without "
                    "in-text citations cannot be reviewed as related-to-prior-"
                    "work — add the claim-critical primary citations directly"
                ),
            )
        )

    # refs.bib reachable from body.
    bib_total, bib_keys = _read_bib(paper_dir)
    report.bib_entries = bib_total
    report.bib_entries_cited = len(report.cite_keys & bib_keys)
    if report.bib_entries_cited < MIN_CITED_BIB_ENTRIES:
        report.issues.append(
            StructuralIssue(
                code="too_few_bib_entries_cited",
                detail=(
                    f"only {report.bib_entries_cited} bib entries are actually "
                    f"cited from the body (minimum {MIN_CITED_BIB_ENTRIES}); "
                    f"refs.bib has {bib_total} entries — either they aren't "
                    "referenced or the keys don't match"
                ),
            )
        )

    # Related Work section.
    rw = _section_span(tex, _RELATED_WORK_TITLES)
    if rw is None:
        report.issues.append(
            StructuralIssue(
                code="no_related_work_section",
                detail=(
                    "no \\section{Related Work} (or equivalent) found — every "
                    "research paper requires one"
                ),
            )
        )
    else:
        start, end = rw
        report.related_work_chars = end - start
        if report.related_work_chars < MIN_RELATED_WORK_CHARS:
            report.issues.append(
                StructuralIssue(
                    code="related_work_too_short",
                    detail=(
                        f"Related Work section is only "
                        f"{report.related_work_chars} chars (minimum "
                        f"{MIN_RELATED_WORK_CHARS}, ≈ one paragraph); the "
                        "norm is ≥1 page"
                    ),
                )
            )

    # Conclusion section.
    concl = _section_span(tex, _CONCLUSION_TITLES)
    report.has_conclusion = concl is not None
    if not report.has_conclusion:
        report.issues.append(
            StructuralIssue(
                code="no_conclusion_section",
                detail="no \\section{Conclusion} (or equivalent) found",
            )
        )

    # Appendix (operator policy: every paper must ship with an appendix —
    # reproducibility details, prompts, additional results, failure cases).
    # Accept either the LaTeX ``\appendix`` command (which converts the
    # next \section into Appendix A) OR a section titled Appendix /
    # Appendices / Supplementary Material / Reproducibility Appendix.
    has_appendix_cmd = bool(_RE_APPENDIX_CMD.search(tex))
    has_appendix_section = _section_span(tex, _APPENDIX_TITLES) is not None
    report.has_appendix = has_appendix_cmd or has_appendix_section
    if not report.has_appendix:
        report.issues.append(
            StructuralIssue(
                code="no_appendix_section",
                detail=(
                    "no appendix found — operator policy: every paper must "
                    "include an appendix (reproducibility details, prompts, "
                    "additional results, or failure cases). Add either "
                    "\\appendix before the supplementary sections, or a "
                    "\\section{Appendix} block after References"
                ),
            )
        )

    # Venue-specific LaTeX-compliance checks. Gated entirely behind the venue
    # profile's flags, so EMNLP/ACL projects (the default) never see them. They
    # activate only for venues whose preamble contract differs — currently
    # AAAI, whose aaai2026.sty is strict.
    if venue is not None:
        _append_venue_compliance_issues(report, tex, venue)

    return report


def _append_venue_compliance_issues(
    report: StructuralReport, tex: str, venue: VenueProfile
) -> None:
    """Append AAAI-style preamble/compliance issues when the profile requires.

    All checks are guarded by individual VenueProfile flags rather than a
    hardcoded ``venue == AAAI`` so the contract lives in one place. EMNLP's
    profile leaves every flag off, so no issue here ever fires for EMNLP.
    """
    used_packages: set[str] = set()
    for m in _RE_USEPACKAGE.finditer(tex):
        for name in m.group(1).split(","):
            cleaned = name.strip()
            if cleaned:
                used_packages.add(cleaned)

    style_package = venue.style_package
    if venue.requires_style_package and style_package not in used_packages:
        report.issues.append(
            StructuralIssue(
                code="missing_aaai_style_package",
                detail=(
                    f"main.tex does not \\usepackage{{{style_package}}} — a "
                    f"{venue.display_name} paper must load the "
                    f"official {style_package}.sty style file (with "
                    "\\documentclass[letterpaper]{article} and times/helvet/courier)"
                ),
            )
        )

    if venue.requires_pdfinfo and not _RE_PDFINFO.search(tex):
        report.issues.append(
            StructuralIssue(
                code="missing_pdfinfo_block",
                detail=(
                    "main.tex is missing the mandatory \\pdfinfo{...} block "
                    "(with /TemplateVersion). Copy it verbatim from the official "
                    f"{venue.display_name} template — "
                    f"{style_package}.sty requires it"
                ),
            )
        )

    if not venue.emit_bibliographystyle and _RE_BIBLIOGRAPHYSTYLE.search(tex):
        report.issues.append(
            StructuralIssue(
                code="forbidden_bibliographystyle",
                detail=(
                    "main.tex emits a \\bibliographystyle command, but "
                    f"{style_package}.sty already sets the bibliography style; a "
                    "manual \\bibliographystyle raises 'Illegal, another "
                    "\\bibstyle command'. Remove it and use \\bibliography{...} "
                    f"with {style_package}.bst"
                ),
            )
        )

    forbidden = sorted(
        p for p in venue.forbidden_packages if p in used_packages
    )
    if forbidden:
        report.issues.append(
            StructuralIssue(
                code="forbidden_package_present",
                detail=(
                    f"main.tex loads package(s) incompatible with {style_package}.sty: "
                    f"{', '.join(forbidden)}. Remove them — they break the official "
                    f"{venue.display_name} style"
                ),
            )
        )

    if venue.forbids_nocopyright and _RE_NOCOPYRIGHT.search(tex):
        report.issues.append(
            StructuralIssue(
                code="uses_nocopyright",
                detail=(
                    "main.tex uses \\nocopyright, which is forbidden for "
                    f"{venue.display_name} — the copyright "
                    "notice is part of the style and may not be disabled"
                ),
            )
        )

    if (
        venue.requires_reproducibility_checklist
        and _section_span(tex, _REPRO_CHECKLIST_TITLES) is None
    ):
        report.issues.append(
            StructuralIssue(
                code="missing_reproducibility_checklist",
                detail=(
                    "no Reproducibility Checklist section found — "
                    f"{venue.display_name} requires the "
                    "reproducibility checklist in the PDF after References. Add a "
                    "\\section*{Reproducibility Checklist} answering the official items"
                ),
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = validate_paper_structural_minimums(args.project_root.resolve())

    if args.json:
        payload = {
            "ok": report.ok,
            "main_tex": str(report.main_tex_path) if report.main_tex_path else None,
            "figures_found": report.figures_found,
            "figures_missing_files": report.figures_missing_files,
            "included_overview_figures": report.included_overview_figures,
            "unique_cite_keys": sorted(report.cite_keys),
            "bib_entries": report.bib_entries,
            "bib_entries_cited": report.bib_entries_cited,
            "related_work_chars": report.related_work_chars,
            "has_conclusion": report.has_conclusion,
            "issues": [
                {"code": i.code, "detail": i.detail} for i in report.issues
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(report.to_text())

    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
