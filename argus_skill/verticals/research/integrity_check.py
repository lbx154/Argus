"""Run the decidable integrity checks over a paper project.

Two checks that the stage checklists currently describe in prose and leave to
the agent to perform by hand:

``citations``
    ``run.score_variance`` aside, the bibliography rules — every cited key
    resolves, every entry is complete and verified — were only ever enforced by
    asking. This resolves them against the actual ``.tex`` and ``.bib``.

``scores``
    ``run.score_variance`` asks the agent to run
    ``jq -r .score ... | sort -u | wc -l`` and interpret the number. Same check,
    except a non-zero exit cannot be talked past.

Neither replaces the reviewer. They settle the mechanical half so the reviewer
spends its attention on the half that needs judgement — whether the entry
describes the paper it claims to, whether the scorer measures the right thing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .integrity_gate import IntegrityIssue, citation_integrity, scorer_integrity

__all__ = ["check_citations", "check_scores", "main"]

_BIBLIOGRAPHY_RE = re.compile(r"\\bibliography\s*\{([^}]+)\}")
_ADD_BIB_RESOURCE_RE = re.compile(
    r"\\addbibresource\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}"
)
_LATEX_COMMENT_RE = re.compile(r"(?<!\\)%.*?$", re.MULTILINE)
_INPUT_RE = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _manuscript_tex_paths(root: Path) -> list[Path]:
    """Return the main manuscript and its project-contained inputs/includes."""

    main = next(
        (
            candidate
            for candidate in (root / "main.tex", root / "submission" / "main.tex")
            if candidate.is_file()
        ),
        None,
    )
    if main is None:
        return sorted(root.rglob("*.tex"))

    root_resolved = root.resolve()
    document_root = main.parent.resolve()
    visited: set[Path] = set()
    paths: list[Path] = []

    def visit(path: Path) -> None:
        resolved = path.resolve()
        if resolved in visited:
            return
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            return
        if not resolved.is_file():
            return
        visited.add(resolved)
        paths.append(resolved)
        source = _LATEX_COMMENT_RE.sub("", _read(resolved))
        for match in _INPUT_RE.finditer(source):
            resource = Path(match.group(1).strip())
            for candidate in (document_root / resource, resolved.parent / resource):
                if not candidate.suffix:
                    candidate = candidate.with_suffix(".tex")
                candidate = candidate.resolve()
                try:
                    candidate.relative_to(root_resolved)
                except ValueError:
                    continue
                if candidate.is_file():
                    visit(candidate)
                    break

    visit(main)
    return paths


def _declared_bibliographies(
    root: Path,
    tex_paths: list[Path],
) -> tuple[list[Path], list[IntegrityIssue]]:
    """Resolve bibliography resources declared by the reachable manuscript."""

    declarations: list[tuple[Path, str]] = []
    for tex_path in tex_paths:
        source = _LATEX_COMMENT_RE.sub("", _read(tex_path))
        for match in _BIBLIOGRAPHY_RE.finditer(source):
            declarations.extend(
                (tex_path, item.strip())
                for item in match.group(1).split(",")
                if item.strip()
            )
        declarations.extend(
            (tex_path, match.group(1).strip())
            for match in _ADD_BIB_RESOURCE_RE.finditer(source)
            if match.group(1).strip()
        )

    # Keep legacy projects working when their manuscript does not declare a
    # bibliography explicitly; declaration-aware projects use only named files.
    if not declarations:
        return sorted(root.rglob("*.bib")), []

    paths: list[Path] = []
    issues: list[IntegrityIssue] = []
    seen: set[tuple[Path, str]] = set()
    root_resolved = root.resolve()
    for tex_path, raw_name in declarations:
        key = (tex_path.resolve(), raw_name)
        if key in seen:
            continue
        seen.add(key)
        resource = Path(raw_name)
        if not resource.suffix:
            resource = resource.with_suffix(".bib")
        resolved: Path | None = None
        escaped = False
        for candidate in (root / resource, tex_path.parent / resource):
            candidate = candidate.resolve()
            try:
                candidate.relative_to(root_resolved)
            except ValueError:
                escaped = True
                continue
            if candidate.is_file():
                resolved = candidate
                break
        if resolved is not None:
            if resolved not in paths:
                paths.append(resolved)
            continue
        code = "bibliography_path_escape" if escaped else "missing_bibliography"
        message = (
            f"declared bibliography {raw_name!r} resolves outside the paper root"
            if escaped
            else f"declared bibliography {raw_name!r} does not exist"
        )
        issues.append(IntegrityIssue(code, "blocker", message, raw_name))
    return paths, issues


def check_citations(project_root: Path, *, require_all_cited: bool = False) -> list[IntegrityIssue]:
    paper = project_root / "paper"
    root = paper if paper.is_dir() else project_root
    tex_paths = _manuscript_tex_paths(root)
    tex_sources = [
        _LATEX_COMMENT_RE.sub("", _read(path))
        for path in tex_paths
    ]
    bib_paths, resource_issues = _declared_bibliographies(root, tex_paths)
    bib_source = "\n".join(_read(path) for path in bib_paths)
    if not tex_sources and not bib_source:
        return []
    issues = resource_issues + citation_integrity(
        tex_sources, bib_source, require_all_entries_cited=require_all_cited
    )
    issues.sort(key=lambda issue: (not issue.blocking, issue.code, issue.subject))
    return issues


def _scores_in(path: Path) -> list[float]:
    scores: list[float] = []
    for line in _read(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        value = row.get("score") if isinstance(row, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            scores.append(float(value))
    return scores


def check_scores(project_root: Path, *, min_samples: int = 4) -> list[IntegrityIssue]:
    """Flag scored-row files whose scorer produced no distinguishable output.

    ``min_samples`` defaults to 4 to match the checklist's ">3 rows" wording:
    below that, identical scores are plausible rather than suspicious.
    """
    issues: list[IntegrityIssue] = []
    for path in sorted(project_root.rglob("scored_rows.jsonl")):
        scores = _scores_in(path)
        issues.extend(
            scorer_integrity(
                scores,
                min_samples=min_samples,
                label=str(path.relative_to(project_root)),
            )
        )
    return issues


def _report(issues: list[IntegrityIssue], subject: str) -> int:
    blockers = [issue for issue in issues if issue.blocking]
    for issue in issues:
        stream = sys.stderr if issue.blocking else sys.stdout
        prefix = "ERROR" if issue.blocking else "note"
        print(f"{prefix}: {issue.code}: {issue.message}", file=stream)
    if blockers:
        return 2
    print(f"{subject}: no blocking integrity issues")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    citations = sub.add_parser("citations")
    citations.add_argument("--project-root", type=Path, default=Path.cwd())
    citations.add_argument(
        "--require-all-cited",
        action="store_true",
        help="also report entries that are never cited (advisory)",
    )
    scores = sub.add_parser("scores")
    scores.add_argument("--project-root", type=Path, default=Path.cwd())
    scores.add_argument("--min-samples", type=int, default=4)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.project_root)
    if args.command == "citations":
        return _report(
            check_citations(root, require_all_cited=args.require_all_cited), "citations"
        )
    return _report(check_scores(root, min_samples=args.min_samples), "scored rows")


if __name__ == "__main__":
    raise SystemExit(main())
