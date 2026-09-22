from argus.life.failure_experience_index import RecallDocument
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


def test_old_cached_terms_are_replaced_without_changing_the_saved_page(tmp_path):
    recall = _recall(tmp_path)
    doc = recall._snapshot()[0]
    old = RecallDocument(doc.id, int(doc.digest[:15], 16), doc.digest, doc.content, doc.content)
    recall.index.sync([old], "old projection")
    assert recall.index.scores("what", source_digest="old projection")[doc.id].direct > 0
    assert not recall.recall("What is the weather?").hits
    assert doc.path.read_text() == PAGE


def test_small_positive_embedding_similarity_does_not_force_an_unrelated_hit(tmp_path):
    class WeakSimilarity:
        identifier = "weak-similarity"
        dimensions = 2

        def embed(self, text):
            return [1, 0] if "CRISPR" in text else [0.1, 1]

    recall = _recall(tmp_path, embedder=WeakSimilarity())
    assert not recall.recall("Tokyo weather").hits


def test_current_procedure_is_searchable_but_retired_history_is_not(tmp_path):
    recall = _recall(tmp_path)
    skill = tmp_path / "pages" / "batch.md"
    skill.write_text("---\nname: Build batches\ndescription: Group verified records\n---\n"
                     "## Steps\nUse a frobnicator to validate records.\n"
                     "## History\nThe retired quux strategy was removed.\n")
    assert recall.recall("frobnicator").hits[0].document.path == skill
    assert not recall.recall("quux").hits
