"""Windows knowledge pages use the same text semantics without moving byte limits."""
from pathlib import Path

import pytest

from argus.webapi.routes.wiki import (
    _index_markdown,
    _page_meta,
    _principles_markdown,
    _read_page,
    _read_text,
)

PAGE = (
    "---\ntitle: Synthetic lesson\ndescription: Read the reference first\n"
    "kind: lesson\nsource: synthetic/project\ncreated: 2026-09-18\n---\n\n"
    "Reference body.\n"
)


def write_bytes(path: Path, text: str, newline: str) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.replace("\n", newline).encode("utf-8")
    path.write_bytes(data)
    return data


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_knowledge_page_metadata_and_body_accept_both_line_endings(tmp_path, newline):
    path = tmp_path / "pages" / "lesson.md"
    original = write_bytes(path, PAGE, newline)
    page = _read_page(tmp_path, "pages/lesson.md")
    assert page["title"] == "Synthetic lesson"
    assert page["description"] == "Read the reference first"
    assert page["kind"] == "lesson"
    assert page["source"] == "synthetic/project"
    assert page["created"] == "2026-09-18"
    assert page["content"] == "Reference body.\n"
    assert page["markdown"] == PAGE
    assert page["truncated"] is False
    assert path.read_bytes() == original


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_index_and_principles_keep_their_existing_text_contract(tmp_path, newline):
    index = write_bytes(tmp_path / "INDEX.md", "# Index\n\nReference.\n", newline)
    principles = write_bytes(tmp_path / "principles.md", PAGE, newline)
    assert _index_markdown(tmp_path) == "# Index\n\nReference.\n"
    assert _principles_markdown(tmp_path) == "Reference body.\n"
    assert (tmp_path / "INDEX.md").read_bytes() == index
    assert (tmp_path / "principles.md").read_bytes() == principles


@pytest.mark.parametrize("limit", [0, 3, 5, 7, 11, 32])
def test_windows_normalization_occurs_after_original_byte_truncation(tmp_path, limit):
    path = tmp_path / "bounded.md"
    original = b"one\r\ntwo\r\nthree\r\n"
    path.write_bytes(original)
    text, truncated = _read_text(path, limit)
    assert text == original[:limit].decode("utf-8").replace("\r\n", "\n")
    assert truncated is (len(original) > limit)
    assert path.read_bytes() == original


def test_legacy_page_fallback_is_unchanged(tmp_path):
    path = tmp_path / "legacy.md"
    write_bytes(path, "# Legacy page\n\nBody.\n", "\r\n")
    text, _ = _read_text(path, 1000)
    assert _page_meta(text, "legacy") == {
        "title": "Legacy page", "description": "", "kind": "page", "source": "", "created": "",
    }
