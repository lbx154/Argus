"""Internal snapshot and isolation support for manuscript narrative review.

Nothing in this module writes a project-facing review artifact. Snapshot state
lives under the vertical/session state root; cold readers receive a temporary
workspace containing only the rendered PDF and, when requested, readable
page images and text derived from that same PDF.
"""
from __future__ import annotations

import filecmp
import json
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .file_facts import file_facts

_INTERNAL_DIR = Path(".argus/internal/narrative-runtime")
_EXCLUDED_NAMES = frozenset({"REVIEW.md"})
_EXCLUDED_SUFFIXES = frozenset(
    {
        ".aux",
        ".fdb_latexmk",
        ".fls",
        ".log",
        ".out",
        ".synctex.gz",
        ".toc",
    }
)


@dataclass(frozen=True)
class NarrativeSnapshotPair:
    """Immutable before/after locations for one semantic-loss comparison."""

    before_paper: Path
    after_paper: Path
    #: The two copies hold byte-identical reader-facing closures.
    identical: bool


def _safe_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-.")
    return (normalized or "mission")[:96]


def _included_files(paper_root: Path) -> tuple[Path, ...]:
    if not paper_root.is_dir():
        raise FileNotFoundError(f"paper source directory is missing: {paper_root}")
    files: list[Path] = []
    for path in sorted(paper_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(paper_root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.name in _EXCLUDED_NAMES:
            continue
        suffix = ".synctex.gz" if path.name.endswith(".synctex.gz") else path.suffix
        if suffix in _EXCLUDED_SUFFIXES:
            continue
        files.append(path)
    return tuple(files)


def manuscript_closure_facts(project_root: Path | str) -> list[list[Any]]:
    """``[relative path, *file_facts]`` for each file of the reader-facing closure."""
    paper_root = Path(project_root).expanduser().resolve() / "paper"
    return [
        [path.relative_to(paper_root).as_posix(), *file_facts(path)]
        for path in _included_files(paper_root)
    ]


def closures_identical(first_root: Path | str, second_root: Path | str) -> bool:
    """Whether two projects hold the same closure files with the same bytes."""
    first = Path(first_root).expanduser().resolve() / "paper"
    second = Path(second_root).expanduser().resolve() / "paper"
    names = [path.relative_to(first).as_posix() for path in _included_files(first)]
    if names != [path.relative_to(second).as_posix() for path in _included_files(second)]:
        return False
    _match, mismatch, errors = filecmp.cmpfiles(first, second, names, shallow=False)
    return not mismatch and not errors


def rendered_pdf_freshness(project_root: Path | str) -> tuple[bool, str]:
    """Check that the cold-read PDF is not older than a manuscript input."""
    paper_root = Path(project_root).expanduser().resolve() / "paper"
    rendered = paper_root / "main.pdf"
    if not rendered.is_file():
        return False, "paper/main.pdf is missing"
    rendered_mtime = rendered.stat().st_mtime_ns
    stale_inputs = [
        path.relative_to(paper_root).as_posix()
        for path in _included_files(paper_root)
        if path != rendered and path.stat().st_mtime_ns > rendered_mtime
    ]
    if stale_inputs:
        preview = ", ".join(stale_inputs[:4])
        suffix = f" and {len(stale_inputs) - 4} more" if len(stale_inputs) > 4 else ""
        return False, f"paper/main.pdf is older than {preview}{suffix}"
    return True, "paper/main.pdf is current"


def _copy_closure(project_root: Path, destination: Path) -> list[list[Any]]:
    """Copy the closure; refuse a copy the project changed under."""
    paper_root = project_root / "paper"
    facts = manuscript_closure_facts(project_root)
    for source in _included_files(paper_root):
        relative = source.relative_to(paper_root)
        target = destination / "paper" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    if manuscript_closure_facts(project_root) != facts:
        raise RuntimeError("paper changed while the snapshot was copied")
    return facts


def prepare_narrative_snapshot(
    project_root: Path | str,
    state_root: Path | str,
    *,
    mission_id: str,
) -> Path:
    """Capture the pre-edit paper exactly once for this mission."""
    project = Path(project_root).expanduser().resolve()
    root = (
        Path(state_root).expanduser().resolve()
        / _INTERNAL_DIR
        / _safe_component(mission_id)
    )
    manifest = root / "manifest.json"
    before_paper = root / "before" / "paper"
    if manifest.is_file() and before_paper.is_dir():
        return root

    root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent))
    try:
        _copy_closure(project, temporary / "before")
        payload = {
            "version": 2,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        (temporary / "manifest.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            temporary.rename(root)
        except FileExistsError:
            # Another worker won the immutable-baseline race.
            shutil.rmtree(temporary)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return root


def snapshot_after_edit(
    project_root: Path | str,
    snapshot_root: Path | str,
) -> NarrativeSnapshotPair:
    """Capture the post-edit closure, reusing the latest copy it still matches."""
    project = Path(project_root).expanduser().resolve()
    root = Path(snapshot_root).expanduser().resolve()
    after_parent = root / "after"
    after_parent.mkdir(parents=True, exist_ok=True)
    copies = sorted(
        (path for path in after_parent.iterdir() if path.name.isdigit()),
        key=lambda path: int(path.name),
    )
    after_root = copies[-1] if copies and closures_identical(copies[-1], project) else None
    if after_root is None:
        temporary = Path(tempfile.mkdtemp(prefix=".after.", dir=after_parent))
        try:
            _copy_closure(project, temporary)
            sequence = int(copies[-1].name) if copies else 0
            while after_root is None:
                sequence += 1
                candidate = after_parent / f"{sequence:04d}"
                if not candidate.exists():
                    temporary.rename(candidate)
                    after_root = candidate
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
    return NarrativeSnapshotPair(
        before_paper=root / "before" / "paper",
        after_paper=after_root / "paper",
        identical=closures_identical(root / "before", after_root),
    )


def _prepare_readable_pdf(paper: Path) -> None:
    """Render on the host before a CLI reviewer receives the PDF.

    Some CLI file viewers expose PDFs as binary text. Only derivatives of the
    copied PDF enter this workspace; manuscript source, earlier reviews, and
    project previews remain excluded.
    """
    try:
        import pymupdf as fitz
    except ImportError as exc:
        raise RuntimeError(
            "PDF review requires PyMuPDF; install argus[paper]"
        ) from exc
    pages = paper / "pages"
    pages.mkdir()
    try:
        with fitz.open(paper / "main.pdf") as document:
            if document.needs_pass or not len(document):
                raise ValueError("the PDF is encrypted or contains no pages")
            text_pages = []
            for index, page in enumerate(document, 1):
                page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(
                    pages / f"page-{index:03d}.png"
                )
                text_pages.append(f"## PDF page {index}\n\n{page.get_text(sort=True)}")
            (paper / "main.txt").write_text("\n\n".join(text_pages), encoding="utf-8")
    except Exception as exc:
        raise RuntimeError(f"could not prepare rendered PDF review inputs: {exc}") from exc


@contextmanager
def isolated_pdf_workspace(
    project_root: Path | str, *, readable: bool = False,
) -> Iterator[Path]:
    """Yield an isolated PDF, optionally with its host-rendered pages and text."""
    source = Path(project_root).expanduser().resolve() / "paper" / "main.pdf"
    if not source.is_file():
        raise FileNotFoundError(f"rendered paper is missing: {source}")
    with tempfile.TemporaryDirectory(prefix="argus-cold-read-") as raw_root:
        root = Path(raw_root)
        paper = root / "paper"
        paper.mkdir()
        shutil.copy2(source, paper / "main.pdf")
        if readable:
            _prepare_readable_pdf(paper)
        yield root


__all__ = [
    "NarrativeSnapshotPair",
    "closures_identical",
    "isolated_pdf_workspace",
    "manuscript_closure_facts",
    "prepare_narrative_snapshot",
    "rendered_pdf_freshness",
    "snapshot_after_edit",
]
