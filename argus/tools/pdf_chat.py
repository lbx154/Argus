"""PDF-chat tool: progressive, section-aware reading of academic PDFs.

ARIS-deepxiv-inspired but self-contained — no external SDK. The agent
should not load the entire PDF up front; instead use this tool to walk
the paper in steps:

    head     → page count + extracted section TOC + abstract preview
    brief    → abstract + first paragraph of the introduction
    section  → text of one named section (case-insensitive match)
    page     → text of a specific page range
    full     → entire concatenated text (large; use sparingly)

Source PDFs can be:
- a local path
- an arXiv ID (e.g. ``2509.12345``) — fetched from arxiv.org/pdf/<id>.pdf
  and cached under ``<cache_dir>/<id>.pdf``

Text extraction prefers the ``pdftotext`` CLI (cleaner column handling)
with a ``pypdf`` fallback when the CLI is missing.

CLI examples:
    python -m argus_skill.tools.pdf_chat head paper/main.pdf
    python -m argus_skill.tools.pdf_chat section paper/main.pdf "Method"
    python -m argus_skill.tools.pdf_chat brief 2509.12345
    python -m argus_skill.tools.pdf_chat page paper/main.pdf --start 1 --end 2
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from ..core.paths import global_root, resolve_runtime_path

# Section heading detector. We accept LaTeX-style ``1 Introduction`` and
# bare numerals (``2.1 Background``). Heuristic — perfect parsing isn't
# possible from extracted text, so the tool surfaces best-effort matches
# and lets the agent re-query with a different keyword if needed.
_RE_SECTION_HEAD = re.compile(
    r"^\s*((?:[A-Z]\.|[0-9]+(?:\.[0-9]+)*)?\s*"
    r"(?:Abstract|Introduction|Background|Related Work|Method(?:s|ology)?|"
    r"Approach|Model|Experiments?|Experimental Setup|Setup|Results?|"
    r"Analysis|Ablations?|Discussion|Limitations?|Conclusions?|"
    r"References|Appendix(?:\s*[A-Z])?|Acknowledg(?:e?)ments?))\s*$",
    re.MULTILINE | re.IGNORECASE,
)

ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
_CACHE_OVERRIDE = os.environ.get("ARGUS_SKILL_PDF_CACHE", "").strip()
DEFAULT_CACHE = (
    resolve_runtime_path(_CACHE_OVERRIDE, context="ARGUS_SKILL_PDF_CACHE")
    if _CACHE_OVERRIDE
    else global_root() / "pdf_cache"
)
# This bounds one stdout/context payload; it does not stop PDF extraction.
MAX_FULL_CHARS = 200_000


@dataclass
class SectionHit:
    title: str
    start_offset: int


@dataclass
class PaperView:
    source: str
    pages: int
    text: str
    sections: list[SectionHit] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------


def _resolve_source(source: str, *, cache_dir: Path) -> Path:
    """Return a local PDF path for ``source``. arXiv IDs are downloaded
    and cached; local paths are returned as-is."""
    if ARXIV_ID_RE.match(source):
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / f"{source}.pdf"
        if not target.exists():
            url = f"https://arxiv.org/pdf/{source}.pdf"
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "argus-skill/pdf_chat"},
                )
                # Socket timeout detects a stalled remote transport; extraction is unlimited.
                with urllib.request.urlopen(req, timeout=60) as resp:
                    target.write_bytes(resp.read())
            except (urllib.error.URLError, TimeoutError) as exc:
                raise RuntimeError(
                    f"failed to fetch arXiv PDF {source}: {exc}"
                ) from exc
        return target
    p = Path(source).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"PDF not found: {source}")
    return p


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _extract_with_pdftotext(pdf: Path) -> tuple[str, int]:
    """Use the pdftotext CLI; cleaner column handling. Returns (text, page_count)."""
    out = subprocess.run(
        ["pdftotext", "-layout", "-q", str(pdf), "-"],
        capture_output=True, check=False,
    )
    if out.returncode != 0:
        raise RuntimeError(
            f"pdftotext exited {out.returncode}: {out.stderr.decode(errors='ignore')[:200]}"
        )
    text = out.stdout.decode("utf-8", errors="ignore")
    # pdftotext separates pages with form-feed \x0c.
    page_count = text.count("\x0c") or 1
    return text, page_count


def _extract_with_pypdf(pdf: Path) -> tuple[str, int]:
    import importlib

    pypdf = importlib.import_module("pypdf")
    reader = pypdf.PdfReader(str(pdf))
    pages = []
    for i, p in enumerate(reader.pages):
        try:
            pages.append(p.extract_text() or "")
        except Exception:  # noqa: BLE001
            pages.append("")
    return "\x0c".join(pages), len(reader.pages)


def _extract(pdf: Path) -> tuple[str, int]:
    pdftotext_result: tuple[str, int] | None = None
    if shutil.which("pdftotext"):
        try:
            pdftotext_result = _extract_with_pdftotext(pdf)
        except (RuntimeError, FileNotFoundError):
            pdftotext_result = None
        else:
            # Fast path: pdftotext already produced detectable section heads.
            if _RE_SECTION_HEAD.search(pdftotext_result[0]):
                return pdftotext_result
    # pdftotext is absent/failed, or produced no detectable headings (some
    # builds drop simple reportlab heading lines). Try pypdf as a fallback —
    # but never let a missing optional dependency, or a pypdf result that is
    # no better, discard a usable pdftotext extraction.
    try:
        pypdf_result = _extract_with_pypdf(pdf)
    except Exception:  # noqa: BLE001 — pypdf is optional and may fail to parse
        if pdftotext_result is not None:
            return pdftotext_result
        raise
    if pdftotext_result is not None and not _RE_SECTION_HEAD.search(pypdf_result[0]):
        # pypdf did not recover headings either; keep the richer -layout text.
        return pdftotext_result
    return pypdf_result


def _detect_sections(text: str) -> list[SectionHit]:
    hits: list[SectionHit] = []
    for m in _RE_SECTION_HEAD.finditer(text):
        hits.append(SectionHit(title=m.group(1).strip(), start_offset=m.start()))
    return hits


def open_pdf(source: str, *, cache_dir: Path = DEFAULT_CACHE) -> PaperView:
    pdf = _resolve_source(source, cache_dir=cache_dir)
    text, page_count = _extract(pdf)
    return PaperView(
        source=str(pdf),
        pages=page_count,
        text=text,
        sections=_detect_sections(text),
    )


# ---------------------------------------------------------------------------
# View builders (head / brief / section / page / full)
# ---------------------------------------------------------------------------


def view_head(view: PaperView) -> dict[str, object]:
    """Page count + section TOC + first-2-page preview."""
    pages = view.text.split("\x0c")
    preview = "\x0c".join(pages[:2])[:4000]
    return {
        "source": view.source,
        "pages": view.pages,
        "sections": [s.title for s in view.sections],
        "preview_first_two_pages": preview,
    }


def view_brief(view: PaperView) -> dict[str, object]:
    """Abstract + first ~600 chars of Introduction."""
    abstract = str(view_section(view, "Abstract").get("text", ""))
    intro = str(view_section(view, "Introduction").get("text", ""))
    return {
        "source": view.source,
        "abstract": (abstract or "").strip()[:2000],
        "introduction_lead": (intro or "").strip()[:800],
    }


def view_section(view: PaperView, name: str) -> dict[str, object]:
    """Return one section's text. Match is case-insensitive substring."""
    name_l = name.strip().lower()
    matches = [
        i for i, s in enumerate(view.sections)
        if name_l in s.title.lower()
    ]
    if not matches:
        return {
            "source": view.source,
            "section_query": name,
            "text": "",
            "note": (
                f"no section matched {name!r}; available: "
                + ", ".join(s.title for s in view.sections)
            ),
        }
    idx = matches[0]
    start = view.sections[idx].start_offset
    end = (
        view.sections[idx + 1].start_offset
        if idx + 1 < len(view.sections)
        else len(view.text)
    )
    return {
        "source": view.source,
        "section_query": name,
        "section_title": view.sections[idx].title,
        "text": view.text[start:end].strip(),
    }


def view_page(view: PaperView, *, start: int, end: int | None = None) -> dict[str, object]:
    end = end or start
    pages = view.text.split("\x0c")
    if start < 1 or start > len(pages):
        raise ValueError(f"page {start} out of range (paper has {len(pages)} pages)")
    end = max(start, min(end, len(pages)))
    body = "\x0c".join(pages[start - 1:end])
    return {
        "source": view.source,
        "page_start": start,
        "page_end": end,
        "text": body.strip(),
    }


def view_full(view: PaperView) -> dict[str, object]:
    text = view.text
    truncated = False
    if len(text) > MAX_FULL_CHARS:
        text = text[:MAX_FULL_CHARS]
        truncated = True
    return {
        "source": view.source,
        "pages": view.pages,
        "text": text,
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="argus-skill pdf_chat",
        description=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def _add_source(p: argparse.ArgumentParser) -> None:
        p.add_argument("source", help="local PDF path or arXiv id (e.g. 2509.12345)")
        p.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)

    p_head = sub.add_parser("head", help="page count + section map + first 2 pages")
    _add_source(p_head)

    p_brief = sub.add_parser("brief", help="abstract + intro lead")
    _add_source(p_brief)

    p_section = sub.add_parser("section", help="one named section")
    _add_source(p_section)
    p_section.add_argument("section_name", help="section name (case-insensitive)")

    p_page = sub.add_parser("page", help="text of a page range")
    _add_source(p_page)
    p_page.add_argument("--start", type=int, required=True)
    p_page.add_argument("--end", type=int, default=None)

    p_full = sub.add_parser("full", help="entire concatenated text (truncated at 200k chars)")
    _add_source(p_full)

    args = parser.parse_args(argv)

    try:
        view = open_pdf(args.source, cache_dir=args.cache_dir)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "head":
        out = view_head(view)
    elif args.cmd == "brief":
        out = view_brief(view)
    elif args.cmd == "section":
        out = view_section(view, args.section_name)
    elif args.cmd == "page":
        try:
            out = view_page(view, start=args.start, end=args.end)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    else:
        out = view_full(view)

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
