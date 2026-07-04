"""Ingest operator-supplied learning material into the wiki's immutable source
(fact) layer, with an audited extraction manifest.

The material is untrusted DATA, never instructions. This module only extracts
text, records provenance (content hash, extractor, char count), and stores it
write-once as a ``SourceNote`` — everything a learning mission later claims must
trace back to these bytes. It makes no judgement about the content; that is the
agent/reviewer's job.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path

from ...wiki.schema import SourceNote
from ...wiki.store import WikiStore

_PLAINTEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".text", ""}


def _slug(stem: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    return s or "material"


def _extract_text(path: Path) -> tuple[str, str]:
    """Return ``(text, extractor_name)``. Best-effort and honest: an unsupported
    or unreadable format raises ``ValueError`` rather than silently ingesting
    garbage a learned claim would then cite."""
    suffix = path.suffix.lower()
    if suffix in _PLAINTEXT_SUFFIXES:
        return path.read_text(encoding="utf-8", errors="replace"), "plaintext"
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"cannot extract {path}: no PDF extractor (pypdf) available"
            ) from exc
        try:
            reader = PdfReader(str(path))
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"failed to extract PDF text from {path}: {exc}") from exc
        if not text.strip():
            raise ValueError(
                f"extracted no text from {path} (scanned/image PDF?) — convert to text first"
            )
        return text, "pypdf"
    raise ValueError(
        f"unsupported material format {suffix!r} for {path}; "
        "supported: .md/.txt/.rst/.pdf"
    )


def ingest_material(
    path: Path,
    store: WikiStore,
    *,
    ingested_by: str = "learn@manual",
    tags: list[str] | None = None,
    today: date | None = None,
) -> dict:
    """Extract ``path`` and store it as an immutable ``SourceNote``. Returns an
    extraction manifest (also the audit record). Re-ingesting identical material
    is a benign no-op (sources are write-once): ``written`` is ``False`` then.
    """
    path = Path(path)
    raw = path.read_bytes()
    text, extractor = _extract_text(path)
    sha = hashlib.sha256(raw).hexdigest()
    source_id = _slug(path.stem)
    when = today or date.today()
    note = SourceNote(
        id=source_id,
        title=path.stem,
        mission_id="",
        created_at=when,
        tags=list(tags or []),
        body=text,
    )
    written = True
    try:
        store.write_source(note)
    except FileExistsError:
        written = False
    return {
        "source_id": source_id,
        "source_path": str(path),
        "sha256": sha,
        "extractor": extractor,
        "char_count": len(text),
        "ingested_at": when.isoformat(),
        "ingested_by": ingested_by,
        "title": path.stem,
        "written": written,
    }


__all__ = ["ingest_material"]
