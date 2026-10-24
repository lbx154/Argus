"""Windows knowledge pages use the same text semantics without moving byte limits."""
from pathlib import Path

import pytest

from argus.webapi.routes.wiki import (
    _read_page,
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
