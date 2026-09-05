"""Small executable drawing demo: lexical retrieval, reranking, extraction."""

import json
import re

DOCUMENTS = [
    "Retrieval selects relevant documents to ground answers.",
    "Retrieved evidence helps retrieval systems produce grounded answers.",
    "Image rendering converts vector drawings into publication figures.",
]
QUERY = "How does retrieval ground answers?"


def tokens(text):
    return set(re.findall(r"[a-z]+", text.lower()))


def answer(query, documents, top_k=2):
    query_tokens = tokens(query)
    candidates = sorted(
        range(len(documents)),
        key=lambda i: len(query_tokens & tokens(documents[i])),
        reverse=True,
    )[:top_k]
    reranked = sorted(
        candidates,
        key=lambda i: len(query_tokens & tokens(documents[i]))
        / max(1, len(query_tokens | tokens(documents[i]))),
        reverse=True,
    )
    selected = reranked[0]
    return {
        "query": query,
        "candidates": candidates,
        "reranked": reranked,
        "source_id": selected,
        "answer": documents[selected].split(".")[0] + ".",
    }


if __name__ == "__main__":
    print(json.dumps(answer(QUERY, DOCUMENTS), indent=2))
