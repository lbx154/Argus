"""Tests for argus_skill.tools.pdf_chat (Step 5 — PDF chat).

Builds a tiny real PDF on the fly with pypdf so the extraction path is
exercised end-to-end without depending on a fixture file.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pypdf = pytest.importorskip("pypdf")

from argus_skill.tools.pdf_chat import (
    open_pdf,
    view_brief,
    view_full,
    view_head,
    view_page,
    view_section,
)


def _build_pdf(path: Path, *, pages: list[str]) -> None:
    """Write a multi-page PDF whose pages contain ``pages`` text."""
    # Use reportlab if available, otherwise hand-build a minimal PDF via
    # pypdf's writer + page-from-text. pypdf alone can't synthesize new
    # text content; fall back to a hand-rolled stream PDF that pdftotext
    # can still parse.
    try:
        from reportlab.pdfgen import canvas  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover - reportlab missing
        _build_pdf_lowlevel(path, pages=pages)
        return
    c = canvas.Canvas(str(path))
    for page in pages:
        y = 800
        for line in page.splitlines():
            c.drawString(72, y, line)
            y -= 14
        c.showPage()
    c.save()


def _build_pdf_lowlevel(path: Path, *, pages: list[str]) -> None:
    """Minimal hand-built PDF — one text block per page, no fonts metrics.

    pdftotext is tolerant enough to extract these. Not a general-purpose
    encoder; only sufficient for testing the chat tool's plumbing.
    """
    # Build objects
    obj_parts: list[bytes] = []
    offsets: list[int] = []
    out: list[bytes] = []

    def _add(body: bytes) -> int:
        idx = len(obj_parts) + 1
        obj_parts.append(b"%d 0 obj\n%s\nendobj\n" % (idx, body))
        return idx

    # Standard Type1 font
    font_id = _add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    # Page content streams + page objects
    page_ids: list[int] = []
    for page_text in pages:
        # Escape parens and backslashes for PDF string literal
        safe_lines = []
        for line in page_text.splitlines():
            safe = (
                line.replace("\\", r"\\")
                    .replace("(", r"\(")
                    .replace(")", r"\)")
            )
            safe_lines.append(f"({safe}) Tj T*")
        body = (
            "BT /F1 12 Tf 14 TL 72 800 Td "
            + " ".join(safe_lines)
            + " ET"
        ).encode("latin-1", errors="replace")
        stream_id = _add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(body), body))
        # Page object references Pages catalog (forward ref; placeholder)
        pid = _add(
            b"<< /Type /Page /Parent 0 0 R /MediaBox [0 0 612 792] "
            b"/Contents %d 0 R /Resources << /Font << /F1 %d 0 R >> >> >>"
            % (stream_id, font_id)
        )
        page_ids.append(pid)

    kids = b" ".join(b"%d 0 R" % p for p in page_ids)
    pages_id = _add(
        b"<< /Type /Pages /Count %d /Kids [%s] >>" % (len(page_ids), kids)
    )
    # Patch the page objects' /Parent refs
    for i, pid in enumerate(page_ids):
        old = b"/Parent 0 0 R"
        new = b"/Parent %d 0 R" % pages_id
        obj_parts[pid - 1] = obj_parts[pid - 1].replace(old, new, 1)

    catalog_id = _add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    # Assemble
    out.append(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    for part in obj_parts:
        offsets.append(sum(len(b) for b in out))
        out.append(part)
    xref_offset = sum(len(b) for b in out)
    out.append(b"xref\n0 %d\n0000000000 65535 f \n" % (len(obj_parts) + 1))
    for off in offsets:
        out.append(b"%010d 00000 n \n" % off)
    out.append(
        b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF"
        % (len(obj_parts) + 1, catalog_id, xref_offset)
    )

    path.write_bytes(b"".join(out))


@pytest.fixture
def tiny_pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "tiny.pdf"
    _build_pdf(pdf, pages=[
        "Abstract\n"
        "This is the abstract of the toy paper. We test PDF chat.\n",
        "1 Introduction\n"
        "Large language models can be tested with toy PDFs.\n"
        "This sentence is the lead of the introduction.\n",
        "2 Method\n"
        "We propose a method consisting of three steps.\n",
        "3 Experiments\n"
        "Results on TOY-BENCH are reported in Table 1.\n",
        "4 Conclusion\n"
        "PDF chat is functional.\n",
    ])
    return pdf


# ---------------------------------------------------------------------------
# Open + extraction
# ---------------------------------------------------------------------------


def test_open_pdf_reports_page_count_and_sections(tiny_pdf: Path) -> None:
    view = open_pdf(str(tiny_pdf))
    assert view.pages == 5
    titles = " | ".join(s.title for s in view.sections).lower()
    # Detector should pick at least a few of these standard heads
    for keyword in ("abstract", "introduction", "method", "experiments", "conclusion"):
        assert keyword in titles, f"missing {keyword!r} in detected sections"


def test_view_head_has_section_toc(tiny_pdf: Path) -> None:
    out = view_head(open_pdf(str(tiny_pdf)))
    assert out["pages"] == 5
    assert isinstance(out["sections"], list) and len(out["sections"]) >= 3
    assert "abstract" in (" ".join(out["sections"])).lower()


def test_view_brief_returns_abstract_and_intro(tiny_pdf: Path) -> None:
    out = view_brief(open_pdf(str(tiny_pdf)))
    assert "abstract" in out["abstract"].lower()
    assert "introduction" in out["introduction_lead"].lower() or (
        "language models" in out["introduction_lead"].lower()
    )


def test_view_section_method(tiny_pdf: Path) -> None:
    out = view_section(open_pdf(str(tiny_pdf)), "Method")
    assert "three steps" in out["text"].lower()
    assert "method" in out["section_title"].lower()


def test_view_section_missing_returns_helpful_note(tiny_pdf: Path) -> None:
    out = view_section(open_pdf(str(tiny_pdf)), "Nonexistent Section Title XYZ")
    assert out["text"] == ""
    assert "no section matched" in out["note"].lower()
    assert "available" in out["note"].lower()


def test_view_page_range(tiny_pdf: Path) -> None:
    view = open_pdf(str(tiny_pdf))
    out = view_page(view, start=2, end=3)
    assert out["page_start"] == 2 and out["page_end"] == 3
    txt = out["text"].lower()
    assert "introduction" in txt or "method" in txt


def test_view_page_out_of_range_raises(tiny_pdf: Path) -> None:
    view = open_pdf(str(tiny_pdf))
    with pytest.raises(ValueError):
        view_page(view, start=99)


def test_view_full_returns_whole_text(tiny_pdf: Path) -> None:
    out = view_full(open_pdf(str(tiny_pdf)))
    assert out["pages"] == 5
    assert "TOY-BENCH" in out["text"] or "toy-bench" in out["text"].lower()
    assert out["truncated"] is False


# ---------------------------------------------------------------------------
# Source resolution: missing file errors cleanly
# ---------------------------------------------------------------------------


def test_missing_local_pdf_raises_filenotfound() -> None:
    with pytest.raises(FileNotFoundError):
        open_pdf("/tmp/__definitely_does_not_exist__.pdf")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_head_emits_json(tiny_pdf: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill.tools.pdf_chat", "head", str(tiny_pdf)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    import json as _json
    payload = _json.loads(proc.stdout)
    assert payload["pages"] == 5
    assert isinstance(payload["sections"], list)


def test_cli_section_subcommand(tiny_pdf: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "argus_skill.tools.pdf_chat",
         "section", str(tiny_pdf), "Conclusion"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "functional" in proc.stdout.lower()


# ---------------------------------------------------------------------------
# _extract fallback hardening (regression: do not crash / discard text when
# pdftotext succeeds but produces no detectable section heads).
# ---------------------------------------------------------------------------


def test_extract_keeps_pdftotext_text_when_pypdf_unavailable(monkeypatch) -> None:
    from argus_skill.tools import pdf_chat

    monkeypatch.setattr(pdf_chat.shutil, "which", lambda _name: "/usr/bin/pdftotext")
    monkeypatch.setattr(
        pdf_chat, "_extract_with_pdftotext", lambda _pdf: ("body without any heads", 3)
    )

    def _boom(_pdf):
        raise ModuleNotFoundError("No module named 'pypdf'")

    monkeypatch.setattr(pdf_chat, "_extract_with_pypdf", _boom)

    text, pages = pdf_chat._extract(Path("/nonexistent.pdf"))
    assert text == "body without any heads"
    assert pages == 3


def test_extract_uses_pypdf_when_it_recovers_headings(monkeypatch) -> None:
    from argus_skill.tools import pdf_chat

    monkeypatch.setattr(pdf_chat.shutil, "which", lambda _name: "/usr/bin/pdftotext")
    monkeypatch.setattr(
        pdf_chat, "_extract_with_pdftotext", lambda _pdf: ("garbled no heads", 2)
    )
    monkeypatch.setattr(
        pdf_chat, "_extract_with_pypdf", lambda _pdf: ("Abstract\nrecovered body", 2)
    )

    text, _pages = pdf_chat._extract(Path("/nonexistent.pdf"))
    assert text == "Abstract\nrecovered body"


def test_extract_keeps_pdftotext_when_pypdf_no_better(monkeypatch) -> None:
    from argus_skill.tools import pdf_chat

    monkeypatch.setattr(pdf_chat.shutil, "which", lambda _name: "/usr/bin/pdftotext")
    monkeypatch.setattr(
        pdf_chat, "_extract_with_pdftotext", lambda _pdf: ("rich layout text", 4)
    )
    monkeypatch.setattr(
        pdf_chat, "_extract_with_pypdf", lambda _pdf: ("worse text", 4)
    )

    text, pages = pdf_chat._extract(Path("/nonexistent.pdf"))
    assert text == "rich layout text"
    assert pages == 4
