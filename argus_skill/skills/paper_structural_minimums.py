"""Paper structural minimums validator.

Anti-fab structural gate (the same class as evidence_chain / F4). Rejects
draft/review/submission rounds whose ``paper/main.tex`` is structurally
not-a-paper: zero figures, zero in-text citations, zero references,
missing Related Work, missing Conclusion.

**This is a venue-minimum check, not a quality judgment.** EMNLP/ACL
papers without ≥1 figure, in-text citations, or a Related Work section
do not constitute a valid submission — they fail at the venue regardless
of what any reviewer agent thinks of the prose. That's the harness's job
(see ``nssmd/skills/04-harness-vs-agent-boundary.md``): the floor is
structural; the ceiling is the reviewer's.

Thresholds here are deliberately well below typical EMNLP norms (e.g.
``min_cited_bib=8`` when a real EMNLP paper has 35+) so the gate only
fires on genuinely broken drafts, not on mediocre-but-formed ones. The
reviewer agent still rules on whether 12 citations is enough for the
topic.

CLI:
    python -m argus_skill.skills.paper_structural_minimums \\
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

# Venue-floor thresholds. Bump only with the operator's agreement —
# raising these turns the gate into a quality judgment.
MIN_FIGURES = 1
MIN_INTEXT_CITES = 8
MIN_CITED_BIB_ENTRIES = 8
MIN_RELATED_WORK_CHARS = 800

# IMAGE2_FIGURES.json role classification. Reviewer-blocking minimum:
# EMNLP/ACL papers without (a) a teaser/hero/Figure-1 visual to anchor
# the contribution AND (b) a pipeline/method/architecture diagram to
# explain how the system works do not pass reviewer first-impression.
# These two categories live in the bundled image-2 / framework-figure
# studio skills (paper-illustration-image2.md +
# paper-framework-figure-studio-pro.md). The gate fires when the agent
# skipped invoking those skills (manifest missing, or no entry whose
# ``name`` matches the role keywords).
_TEASER_KEYWORDS = ("teaser", "hero", "figure1", "figure_1", "fig1", "fig_1")
_PIPELINE_KEYWORDS = (
    "pipeline", "method", "architecture", "framework",
    "overview", "system", "workflow", "schematic",
)

# Section heading variants we accept. Case-insensitive substring match
# on the brace contents; this is intentionally loose so e.g.
# ``\section*{Related Work and Background}`` still counts.
_RELATED_WORK_TITLES = ("related work", "background and related work", "prior work")
_CONCLUSION_TITLES = ("conclusion", "conclusions", "discussion and conclusion")
_APPENDIX_TITLES = ("appendix", "appendices", "supplementary material",
                    "reproducibility appendix")


_RE_INCLUDEGRAPHICS = re.compile(r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
_RE_CITE = re.compile(r"\\cite[a-zA-Z*]*\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
_RE_SECTION = re.compile(r"\\section\*?\s*\{([^}]+)\}")
_RE_BIBKEY = re.compile(r"^\s*@\w+\s*\{\s*([^,\s]+)\s*,", re.MULTILINE)
# `\appendix` command turns subsequent \section into Appendix A, B, …
_RE_APPENDIX_CMD = re.compile(r"\\appendix\b")


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
    issues: list[StructuralIssue] = field(default_factory=list)

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
            f"  image2 teaser/hero figure: {self.has_teaser_figure}; "
            f"pipeline/method figure: {self.has_pipeline_figure}"
        )
        if self.image2_role_summary:
            lines.append(
                "  image2 role assignments: "
                + ", ".join(
                    f"{n}={r}" for n, r in sorted(self.image2_role_summary.items())
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
    """Concatenate main.tex plus every `paper/sections/*.tex` so cite/figure
    scans see the whole body, not just the master file."""
    parts = [main_tex.read_text(encoding="utf-8", errors="ignore")]
    sections_dir = main_tex.parent / "sections"
    if sections_dir.is_dir():
        for tex in sorted(sections_dir.glob("*.tex")):
            parts.append(tex.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


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
    Path | None, bool, bool, dict[str, str]
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
        return None, False, False, {}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return manifest, False, False, {}
    figures = data.get("figures") if isinstance(data, dict) else None
    if not isinstance(figures, list):
        return manifest, False, False, {}

    has_teaser = False
    has_pipeline = False
    role_summary: dict[str, str] = {}
    for entry in figures:
        if not isinstance(entry, dict):
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
        if not any(p.exists() for p in candidates):
            role_summary[name] = "missing_file"
            continue
        role = _classify_image2_role(name)
        if role == "teaser":
            has_teaser = True
            role_summary[name] = "teaser"
        elif role == "pipeline":
            has_pipeline = True
            role_summary[name] = "pipeline"
        else:
            role_summary[name] = "other"
    return manifest, has_teaser, has_pipeline, role_summary


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


def validate_paper_structural_minimums(project_root: Path) -> StructuralReport:
    main_tex = _find_main_tex(project_root)
    if main_tex is None:
        report = StructuralReport(main_tex_path=None)
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

    # Figures.
    figure_refs = [m.group(1) for m in _RE_INCLUDEGRAPHICS.finditer(tex)]
    for ref in figure_refs:
        resolved = _resolve_figure(paper_dir, ref)
        if resolved is None:
            report.figures_missing_files.append(ref)
        else:
            report.figures_found += 1

    if report.figures_found < MIN_FIGURES:
        report.issues.append(
            StructuralIssue(
                code="no_figures",
                detail=(
                    f"only {report.figures_found} \\includegraphics figure(s) "
                    f"resolve to real files (minimum {MIN_FIGURES}); EMNLP/ACL "
                    "papers require at least one figure or system diagram — "
                    "invoke the figure-spec, paper-illustration-image2, or "
                    "paper-framework-figure-studio-pro skill before declaring "
                    "the draft done"
                ),
            )
        )

    # IMAGE2_FIGURES.json role coverage. Teaser/hero anchors the
    # contribution; pipeline/method explains how the system works. EMNLP
    # papers without BOTH lose reviewer first-impression. Existing skills
    # (paper-illustration-image2 + paper-framework-figure-studio-pro)
    # produce the manifest — gate fires when the agent skipped them.
    manifest_path, has_teaser, has_pipeline, role_summary = _scan_image2_manifest(
        paper_dir
    )
    report.image2_manifest_path = manifest_path
    report.has_teaser_figure = has_teaser
    report.has_pipeline_figure = has_pipeline
    report.image2_role_summary = role_summary
    if manifest_path is None:
        report.issues.append(
            StructuralIssue(
                code="missing_image2_manifest",
                detail=(
                    "paper/figures/IMAGE2_FIGURES.json not found — the "
                    "paper-illustration-image2 and "
                    "paper-framework-figure-studio-pro skills register "
                    "generated figures here; an empty manifest means "
                    "those skills were never invoked"
                ),
            )
        )
    else:
        if not has_teaser:
            report.issues.append(
                StructuralIssue(
                    code="missing_teaser_figure",
                    detail=(
                        "no teaser/hero/Figure-1 entry in IMAGE2_FIGURES.json — "
                        "EMNLP/ACL drafts need a contribution-anchoring teaser; "
                        "run the paper-illustration-image2 skill with a name "
                        "containing 'teaser', 'hero', or 'figure1'"
                    ),
                )
            )
        if not has_pipeline:
            report.issues.append(
                StructuralIssue(
                    code="missing_pipeline_figure",
                    detail=(
                        "no pipeline/method/architecture entry in "
                        "IMAGE2_FIGURES.json — reviewers cannot understand a "
                        "method without one; run the paper-framework-figure-"
                        "studio-pro skill with a name containing 'pipeline', "
                        "'method', 'architecture', or 'framework'"
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
                    "EMNLP/ACL paper requires one"
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
                        f"{MIN_RELATED_WORK_CHARS}, ≈ one paragraph); EMNLP "
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

    return report


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
