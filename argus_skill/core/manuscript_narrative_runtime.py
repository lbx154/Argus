"""Internal snapshot and isolation support for manuscript narrative review.

Nothing in this module writes a project-facing review artifact. Snapshot state
lives under the vertical/session state root; cold readers receive a temporary
workspace containing one rendered PDF and nothing else.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

_INTERNAL_DIR = Path(".argus/internal/narrative-runtime")
_EXCLUDED_NAMES = frozenset(
    {
        "REVIEW.md",
        "academic_language_review.json",
        "academic_language_review.md",
        "academic_language_review_history.jsonl",
    }
)
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
    before_sha256: str
    after_sha256: str


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


def manuscript_closure_sha256(project_root: Path | str) -> str:
    """Hash the reader-facing source closure without Git or review artifacts."""
    paper_root = Path(project_root).expanduser().resolve() / "paper"
    digest = hashlib.sha256()
    for path in _included_files(paper_root):
        relative = path.relative_to(paper_root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


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


def _copy_closure(project_root: Path, destination: Path) -> str:
    paper_root = project_root / "paper"
    digest = manuscript_closure_sha256(project_root)
    for source in _included_files(paper_root):
        relative = source.relative_to(paper_root)
        target = destination / "paper" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return digest


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
        before_sha256 = _copy_closure(project, temporary / "before")
        payload = {
            "version": 1,
            "recorded_at": datetime.now(UTC).isoformat(),
            "before_sha256": before_sha256,
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
    """Capture one content-addressed post-edit closure and return the pair."""
    project = Path(project_root).expanduser().resolve()
    root = Path(snapshot_root).expanduser().resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    before_sha256 = str(manifest["before_sha256"])
    after_sha256 = manuscript_closure_sha256(project)
    after_root = root / "after" / after_sha256
    after_paper = after_root / "paper"
    if not after_paper.is_dir():
        after_parent = root / "after"
        after_parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{after_sha256}.", dir=after_parent)
        )
        try:
            copied_sha256 = _copy_closure(project, temporary)
            if copied_sha256 != after_sha256:
                raise RuntimeError("paper changed while the post-edit snapshot was copied")
            try:
                temporary.rename(after_root)
            except FileExistsError:
                shutil.rmtree(temporary)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
    return NarrativeSnapshotPair(
        before_paper=root / "before" / "paper",
        after_paper=after_paper,
        before_sha256=before_sha256,
        after_sha256=after_sha256,
    )


@contextmanager
def isolated_pdf_workspace(project_root: Path | str) -> Iterator[Path]:
    """Yield a temporary workspace whose sole paper input is ``main.pdf``."""
    source = Path(project_root).expanduser().resolve() / "paper" / "main.pdf"
    if not source.is_file():
        raise FileNotFoundError(f"rendered paper is missing: {source}")
    with tempfile.TemporaryDirectory(prefix="argus-cold-read-") as raw_root:
        root = Path(raw_root)
        paper = root / "paper"
        paper.mkdir()
        shutil.copy2(source, paper / "main.pdf")
        yield root


__all__ = [
    "NarrativeSnapshotPair",
    "isolated_pdf_workspace",
    "manuscript_closure_sha256",
    "prepare_narrative_snapshot",
    "rendered_pdf_freshness",
    "snapshot_after_edit",
]
