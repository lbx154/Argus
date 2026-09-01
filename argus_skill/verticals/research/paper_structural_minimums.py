"""Paper structural minimums validator.

Anti-fab structural gate (the same class as evidence_chain / F4). Rejects
draft/review/submission rounds whose ``paper/main.tex`` is structurally
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

# Figure roles do not prescribe a renderer. They ensure the paper has a real
# reader-facing Figure 1 overview rather than a table, prose box, or ``\rule``
# bars placed inside a LaTeX ``figure`` environment. The canonical manifest is
# renderer neutral; legacy IMAGE2_FIGURES.json remains readable for existing
# projects.
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
# `\label{fig:foo}` inside a `figure` environment — used to cross-check
# that figures appearing in main.tex have a placeholder in
# paper/DRAFT_OUTLINE.md (the Draft-first contract). See
# argus_skill/skills/draft_outline.py for context.
_RE_FIG_LABEL = re.compile(r"\\label\s*\{\s*fig:([^}]+?)\s*\}")
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
    image2_manifest_path: Path | None = None
    has_teaser_figure: bool = False
    has_pipeline_figure: bool = False
    image2_role_summary: dict[str, str] = field(default_factory=dict)
    figure_manifest_path: Path | None = None
    figure_role_summary: dict[str, str] = field(default_factory=dict)
    figure_renderer_summary: dict[str, str] = field(default_factory=dict)
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
            f"  reported teaser/hero figure: {self.has_teaser_figure}; "
            f"pipeline/method figure: {self.has_pipeline_figure}"
        )
        lines.append(
            "  included Figure 1 overview assets: "
            + (
                ", ".join(self.included_overview_figures)
                if self.included_overview_figures
                else "(none)"
            )
        )
        if self.figure_role_summary:
            lines.append(
                "  figure role assignments: "
                + ", ".join(
                    f"{n}={r}" for n, r in sorted(self.figure_role_summary.items())
                )
            )
        if self.figure_renderer_summary:
            lines.append(
                "  figure renderers: "
                + ", ".join(
                    f"{n}={r}" for n, r in sorted(self.figure_renderer_summary.items())
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


def _classify_image2_role(name: str) -> str | None:
    """Return ``"teaser"`` / ``"pipeline"`` / None for an IMAGE2_FIGURES.json
    entry name. Matching is case-insensitive substring against the
    role-keyword tables. A figure that matches BOTH categories (e.g.
    ``method_teaser``) is classified as teaser — the more specific reader-
    facing role wins."""
    n = name.lower()
    if any(k in n for k in _TEASER_KEYWORDS):
        return "teaser"
    if any(k in n for k in _PIPELINE_KEYWORDS):
        return "pipeline"
    return None


def _scan_image2_manifest(paper_dir: Path) -> tuple[
    Path | None,
    bool,
    bool,
    dict[str, str],
    list[str],
    dict[str, tuple[str, str]],
]:
    """Read ``paper/figures/IMAGE2_FIGURES.json`` and return whether a
    teaser and pipeline figure each exist on disk.

    The image-2 / paper-framework-figure-studio-pro skills both write to
    this manifest. Roles are inferred from each entry's ``name`` field
    using the keyword tables above — we deliberately do NOT require a
    dedicated ``role`` schema field so the existing skill prompts don't
    have to be retrofitted in lockstep. An entry pointing at a
    non-existent file does not count.
    """
    manifest = paper_dir / "figures" / "IMAGE2_FIGURES.json"
    if not manifest.exists():
        return None, False, False, {}, [], {}
    project_root = paper_dir.parent.resolve()
    try:
        manifest.resolve().relative_to(project_root)
    except ValueError:
        return (
            manifest,
            False,
            False,
            {},
            ["legacy manifest resolves outside project root"],
            {},
        )
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return (
            manifest,
            False,
            False,
            {},
            [f"malformed legacy manifest: {exc}"],
            {},
        )
    figures = data.get("figures") if isinstance(data, dict) else None
    if not isinstance(figures, list) or not figures:
        return (
            manifest,
            False,
            False,
            {},
            ["legacy manifest has no figure entries"],
            {},
        )

    has_teaser = False
    has_pipeline = False
    role_summary: dict[str, str] = {}
    issues: list[str] = []
    real_entries = 0
    records: dict[str, tuple[str, str]] = {}
    for entry in figures:
        if not isinstance(entry, dict):
            issues.append("legacy figure entry is not an object")
            continue
        # Accept any of the conventional id fields. paper-illustration-image2
        # writes ``name``; paper-framework-figure-studio-pro writes
        # ``figure_id``; some downstream tools use ``id``. The role
        # classifier doesn't care which key carried it.
        name = str(
            entry.get("name")
            or entry.get("figure_id")
            or entry.get("id")
            or ""
        ).strip()
        if not name:
            issues.append("legacy figure entry has no name/figure_id/id")
            continue
        file_field = str(entry.get("file") or entry.get("output_path") or "").strip()
        candidates: list[Path] = []
        if file_field:
            if Path(file_field).is_absolute():
                candidates.append(Path(file_field))
            else:
                # paths in the manifest are typically relative to project
                # root (e.g. ``paper/figures/x.png``) but tolerate
                # paper-relative ones too.
                candidates.append(paper_dir.parent / file_field)
                candidates.append(paper_dir / file_field)
        contained_candidates: list[Path] = []
        for candidate in candidates:
            try:
                candidate.resolve().relative_to(project_root)
            except ValueError:
                continue
            contained_candidates.append(candidate)
        if not any(p.is_file() for p in contained_candidates):
            role_summary[name] = "missing_file"
            issues.append(f"{name}: file is missing or outside project root")
            continue
        output_path = next(p.resolve() for p in contained_candidates if p.is_file())
        output_relative = output_path.relative_to(project_root).as_posix()
        output_hash = str(entry.get("output_sha256") or "").strip().lower()
        records[name] = (output_relative, output_hash)
        real_entries += 1
        role = _classify_image2_role(name)
        if role == "teaser":
            has_teaser = True
            role_summary[name] = "teaser"
        elif role == "pipeline":
            has_pipeline = True
            role_summary[name] = "pipeline"
        else:
            role_summary[name] = "other"
    if real_entries == 0:
        issues.append("legacy manifest has no real project-contained figure")
    return manifest, has_teaser, has_pipeline, role_summary, issues, records


@dataclass
class _VisualManifestScan:
    path: Path | None = None
    legacy_image2_path: Path | None = None
    has_teaser: bool = False
    has_pipeline: bool = False
    roles: dict[str, str] = field(default_factory=dict)
    renderers: dict[str, str] = field(default_factory=dict)
    outputs: set[str] = field(default_factory=set)
    overview_outputs: set[str] = field(default_factory=set)
    issues: list[StructuralIssue] = field(default_factory=list)
    canonical: bool = False


def _role_classification(figure_id: str, role: str) -> str:
    explicit = role.strip().lower()
    if explicit in {"teaser", "hero", "figure1", "fig1"}:
        return "teaser"
    if explicit in {
        "pipeline",
        "method",
        "architecture",
        "framework",
        "overview",
        "system",
        "workflow",
        "schematic",
    }:
        return "pipeline"
    return _classify_image2_role(figure_id) or explicit or "other"


def _is_image2_renderer(renderer: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", renderer.lower())
    return normalized in {"image2", "gptimage2", "codeximage2"}


def _scan_visual_manifest_locked(
    project_root: Path,
    paper_dir: Path,
) -> _VisualManifestScan:
    from .figure_provenance import (
        FIGURE_PROVENANCE_PATH,
        validate_figure_provenance,
    )

    canonical = project_root / FIGURE_PROVENANCE_PATH
    if canonical.exists() or canonical.is_symlink():
        provenance = validate_figure_provenance(project_root)
        scan = _VisualManifestScan(
            path=canonical,
            canonical=True,
            outputs=set(provenance.output_paths),
        )
        for issue in provenance.issues:
            scan.issues.append(
                StructuralIssue(
                    code="invalid_figure_provenance",
                    detail=(
                        f"{issue.figure_id + ': ' if issue.figure_id else ''}"
                        f"{issue.code}: {issue.detail}"
                    ),
                )
            )
        for entry in provenance.entries:
            figure_id = str(entry.get("figure_id") or "").strip()
            role = _role_classification(
                figure_id,
                str(entry.get("role") or ""),
            )
            renderer = str(entry.get("renderer") or "").strip()
            scan.roles[figure_id] = role
            scan.renderers[figure_id] = renderer
            scan.has_teaser = scan.has_teaser or role == "teaser"
            scan.has_pipeline = scan.has_pipeline or role == "pipeline"
            if role in {"teaser", "pipeline"}:
                output = str(entry.get("output_path") or "").strip()
                if output:
                    scan.overview_outputs.add(output)
        image2_entries = [
            entry
            for entry in provenance.entries
            if _is_image2_renderer(str(entry.get("renderer") or ""))
        ]
        if image2_entries:
            (
                legacy_path,
                _legacy_teaser,
                _legacy_pipeline,
                _legacy_roles,
                legacy_issues,
                legacy_records,
            ) = _scan_image2_manifest(paper_dir)
            scan.legacy_image2_path = legacy_path
            if legacy_path is None:
                scan.issues.append(
                    StructuralIssue(
                        code="invalid_figure_provenance",
                        detail=(
                            "canonical image-2 entries require "
                            "paper/figures/IMAGE2_FIGURES.json"
                        ),
                    )
                )
            scan.issues.extend(
                StructuralIssue(
                    code="invalid_figure_provenance",
                    detail=detail,
                )
                for detail in legacy_issues
            )
            for entry in image2_entries:
                figure_id = str(entry.get("figure_id") or "").strip()
                canonical_output = str(entry.get("output_path") or "").strip()
                canonical_hash = str(entry.get("output_sha256") or "").strip().lower()
                legacy_record = legacy_records.get(figure_id)
                if legacy_record is None:
                    scan.issues.append(
                        StructuralIssue(
                            code="invalid_figure_provenance",
                            detail=f"{figure_id}: missing matching legacy image-2 entry",
                        )
                    )
                    continue
                legacy_output, legacy_hash = legacy_record
                if legacy_output != canonical_output or not legacy_hash:
                    scan.issues.append(
                        StructuralIssue(
                            code="invalid_figure_provenance",
                            detail=(
                                f"{figure_id}: legacy image-2 output path/hash "
                                "does not match canonical provenance"
                            ),
                        )
                    )
                elif legacy_hash != canonical_hash:
                    scan.issues.append(
                        StructuralIssue(
                            code="invalid_figure_provenance",
                            detail=f"{figure_id}: legacy image-2 hash mismatch",
                        )
                    )
        return scan

    legacy_path, has_teaser, has_pipeline, roles, legacy_issues, records = (
        _scan_image2_manifest(paper_dir)
    )
    if legacy_path is not None:
        scan = _VisualManifestScan(
            path=legacy_path,
            legacy_image2_path=legacy_path,
            has_teaser=has_teaser,
            has_pipeline=has_pipeline,
            roles=roles,
            renderers={
                figure_id: "image2"
                for figure_id, role in roles.items()
                if role != "missing_file"
            },
            overview_outputs={
                records[figure_id][0]
                for figure_id, role in roles.items()
                if role in {"teaser", "pipeline"} and figure_id in records
            },
        )
        scan.issues.extend(
            StructuralIssue(
                code="invalid_figure_provenance",
                detail=detail,
            )
            for detail in legacy_issues
        )
        return scan
    return _VisualManifestScan()


def _scan_visual_manifest(project_root: Path, paper_dir: Path) -> _VisualManifestScan:
    from .figure_provenance import figure_manifest_transaction

    with figure_manifest_transaction(project_root):
        return _scan_visual_manifest_locked(project_root, paper_dir)


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


def validate_paper_structural_minimums(project_root: Path) -> StructuralReport:
    from .venue_profiles import resolve_venue_profile

    venue = None
    venue_error: KeyError | None = None
    try:
        venue = resolve_venue_profile(project_root)
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
                    "paper/main.tex not found — draft stage requires a "
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

    # Draft-first cross-check. Every `\label{fig:foo}` in main.tex should
    # correspond to a placeholder in paper/DRAFT_OUTLINE.md. Orphans (in
    # tex but not in outline) imply the figure was added ad-hoc instead of
    # filling a pre-declared slot — which is exactly the failure mode that
    # produced the multimodal-v1 polish-loop drift. We surface them as
    # structural issues, not as a hard gate, so the calling site decides
    # severity.
    fig_labels = sorted({m.group(1).strip() for m in _RE_FIG_LABEL.finditer(tex)})
    try:
        from .draft_outline import cross_check_figure_ids, load_outline
        outline = load_outline(project_root)
        for orphan in cross_check_figure_ids(outline, fig_labels):
            report.issues.append(
                StructuralIssue(
                    code="draft_outline_figure_orphan",
                    detail=orphan.message,
                )
            )
    except Exception:  # noqa: BLE001
        # never let the cross-check break structural validation itself
        pass

    visual_scan = _scan_visual_manifest(project_root, paper_dir)
    report.figure_manifest_path = visual_scan.path
    report.image2_manifest_path = visual_scan.legacy_image2_path
    report.has_teaser_figure = visual_scan.has_teaser
    report.has_pipeline_figure = visual_scan.has_pipeline
    report.figure_role_summary = visual_scan.roles
    report.figure_renderer_summary = visual_scan.renderers
    report.image2_role_summary = visual_scan.roles
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
        | (
            set(resolved_figure_paths.values())
            & visual_scan.overview_outputs
        )
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
    # Figure provenance is advisory metadata for reproducibility and renderer
    # handoff. It is deliberately not a structural completion gate: the L2
    # Reviewer judges whether the rendered figures are clear and attractive
    # enough without creating provenance-driven polish loops.

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
                    "work — pull citations from research/LITERATURE_GROUNDING.json"
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
