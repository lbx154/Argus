from argus.life.knowledge_recall import KnowledgeRoot, MarkdownKnowledgeRecall

PAGE = """---
title: CRISPR-Cas9 molecular cleavage
description: HNH and RuvC cleave complementary DNA strands.
kind: survey
source: chat/science
---
## Question
请学习一些科学知识，然后给我解释。
## What we concluded
The HNH domain cleaves the complementary DNA strand.
## Sources
https://example.org/science
"""


def _recall(tmp_path, **kwargs):
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "crispr.md").write_text(PAGE)
    return MarkdownKnowledgeRecall(tmp_path / "index.db", [KnowledgeRoot("Wiki", pages, tmp_path)], **kwargs)


def test_request_words_provenance_and_page_headers_do_not_recall_unrelated_knowledge(tmp_path):
    recall = _recall(tmp_path)
    for query in ["后台学习队列 SQLite task_id", "What is the weather in Tokyo?", "请问你学到了什么", "source chat survey created confidence"]:
        assert not recall.recall(query).hits, query
    assert recall.recall("Cas9 HNH DNA").hits[0].document.meta.title.startswith("CRISPR")
    # Actual citations remain useful search evidence; only source metadata is omitted.
    assert recall.recall("https://example.org/science").hits
