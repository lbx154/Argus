# Retrieve, rerank, extract: an illustrative method

This small example demonstrates pipeline drawing from executable code and method
prose. It is not a trained model or an experimental research result.

The input is a natural-language query and a corpus of three short documents.
Lowercase word tokenization forms a set for the query and for each document.
First, lexical retrieval selects two candidates by the number of shared query
tokens. Next, reranking orders these candidates by Jaccard similarity, the
intersection size divided by the union size. Selection retains the highest
scoring document. Extraction returns its first sentence and document identifier;
there is no language-model answer generation or additional verification stage.

The diagram should show the query and corpus feeding retrieval, two candidate
documents, the Jaccard reranker, one selected source, and the grounded excerpt.
The query also supplies the token set used during reranking. Highlight reranking
to distinguish the two scoring operations. Do not imply this simple pipeline
is a novel scientific contribution or that its accuracy has been evaluated.
