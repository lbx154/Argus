"""Stage operator-provided material as a minimal semantic Wiki page."""
from __future__ import annotations

import re
from pathlib import Path

from ...wiki.schema import WikiPage
from ...wiki.store import WikiStore

_PLAINTEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".text", ""}
_SEMANTIC_STEM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _extract_text(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix in _PLAINTEXT_SUFFIXES:
        return path.read_text(encoding="utf-8", errors="replace"), "plaintext"
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"failed to extract PDF text from {path}: {exc}") from exc
        if not text.strip():
            raise ValueError(f"extracted no text from {path}")
        return text, "pypdf"
    raise ValueError(f"unsupported material format {suffix!r} for {path}")


def ingest_material(
    path: Path,
    store: WikiStore,
    *,
    ingested_by: str = "learn@manual",
    tags: list[str] | None = None,
    today: object | None = None,
) -> dict:
    _ = (ingested_by, tags, today)
    path = Path(path)
    if not _SEMANTIC_STEM.fullmatch(path.stem):
        raise ValueError(
            "rename the material to an explicit semantic filename before ingest"
        )
    text, extractor = _extract_text(path)
    semantic_path = Path("materials") / f"{path.stem}.md"
    destination = store.root / "pages" / semantic_path
    written = not destination.exists()
    if written:
        store.write_page(
            semantic_path,
            WikiPage(
                title=path.stem,
                description="Operator-provided material for Agent study.",
                content=text,
            ),
        )
    return {
        "source_id": semantic_path.with_suffix("").as_posix(),
        "source_path": str(path),
        "extractor": extractor,
        "char_count": len(text),
        "title": path.stem,
        "written": written,
    }


__all__ = ["ingest_material"]
