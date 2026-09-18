"""Parse CRLF metadata without changing the raw recall document or its identity."""
import hashlib

import pytest

from argus.life.knowledge_recall import KnowledgeRoot, MarkdownKnowledgeRecall, _front_matter

PAGE = (
    "---\ntitle: Synthetic lesson\ndescription: Reference before guessing\n"
    "kind: lesson\nsource: synthetic/project\ncreated: 2026-09-18\n---\n\n"
    "quartz reference body\n"
)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_recall_metadata_uses_text_line_endings_but_identity_uses_original_bytes(tmp_path, newline):
    pages = tmp_path / "pages"
    pages.mkdir()
    path = pages / "lesson.md"
    original = PAGE.replace("\n", newline).encode("utf-8")
    path.write_bytes(original)
    root = KnowledgeRoot("Wiki", pages, tmp_path)
    recall = MarkdownKnowledgeRecall(tmp_path / "recall.sqlite3", [root])
    document, remaining = recall._load(root, path, pages, len(original), 1000, 10000)
    assert document is not None
    assert document.meta.title == "Synthetic lesson"
    assert document.meta.description == "Reference before guessing"
    assert document.meta.page_kind == "lesson"
    assert document.meta.source == "synthetic/project"
    assert document.meta.created == "2026-09-18"
    assert document.content == original.decode("utf-8")
    assert document.digest == hashlib.sha256(original).hexdigest()
    assert remaining == 10000 - len(original)
    assert path.read_bytes() == original


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_oversized_recall_document_still_refused(tmp_path, newline):
    pages = tmp_path / "pages"
    pages.mkdir()
    path = pages / "lesson.md"
    original = PAGE.replace("\n", newline).encode("utf-8")
    path.write_bytes(original)
    root = KnowledgeRoot("Wiki", pages, tmp_path)
    recall = MarkdownKnowledgeRecall(tmp_path / "recall.sqlite3", [root], max_file_bytes=len(original) - 1)
    document, remaining = recall._load(root, path, pages, len(original), 1000, 10000)
    assert document is None
    assert remaining == 10000
    assert path.read_bytes() == original


@pytest.mark.parametrize("content", ["no front matter", "---\r\nnot terminated", "---\r\n[not, a, mapping]\r\n---\r\nbody"])
def test_invalid_or_absent_metadata_remains_non_authoritative(content):
    assert _front_matter(content)[0] == {}
